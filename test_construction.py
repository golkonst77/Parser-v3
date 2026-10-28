import unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
from industries import INDUSTRIES
from target_collection import Collection,SourceStopped,_orgpage_queries,card_stream,source_config


class ConstructionTests(unittest.TestCase):
    def setup_queries(self):
        config=source_config(dict(INDUSTRIES[3],query_batch_size=1))
        config['search_queries']=[dict(name='Первое',query='first'),dict(name='Второе',query='second')]
        collection=Collection(20,200)
        collection.stats=dict(source_pages=0,source_batches=0,source_links_buffered=0)
        return config,collection,Mock(),Mock()

    def test_config_and_existing_industries(self):
        self.assertEqual(INDUSTRIES[3]['name'],'Строительство и ремонт')
        self.assertEqual(INDUSTRIES[3]['status'],'ready')
        self.assertEqual(INDUSTRIES[3]['output'],'construction_WORKING_ONLY.csv')
        self.assertNotIn('search_queries',INDUSTRIES[3])
        self.assertEqual(INDUSTRIES[3]['catalog_title'],'Строительные компании')
        self.assertNotIn('search_queries',INDUSTRIES[1]);self.assertNotIn('search_queries',INDUSTRIES[2])
        self.assertEqual(INDUSTRIES[2]['output'],'beauty_WORKING_ONLY.csv')

    def test_queries_resume_and_deduplicate_with_shared_context(self):
        config,collection,browser,context=self.setup_queries()
        sequence={'first':['https://orgpage.ru/x/one-1.html','https://orgpage.ru/x/two-2.html'],
                  'second':['https://orgpage.ru/x/one-1.html','https://orgpage.ru/x/three-3.html']}
        calls=[];closed=[]
        def source(b,local,c,ctx):
            q=local['_source_query'];calls.append((b,c,ctx,local['search_url']))
            try:yield from sequence[q]
            finally:closed.append(q)
        with patch('target_collection._orgpage_urls',side_effect=source):
            result=list(_orgpage_queries(browser,config,collection,context))
        self.assertEqual(result,['https://orgpage.ru/x/one-1.html','https://orgpage.ru/x/two-2.html','https://orgpage.ru/x/three-3.html'])
        self.assertEqual(collection.stats['queries']['second']['duplicate_links'],1)
        self.assertTrue(all(call[1] is collection and call[2] is context for call in calls))
        self.assertIn('q=first',calls[0][3]);self.assertIn('q=second',calls[1][3])
        self.assertEqual(collection.catalog_metadata[result[0]]['_source_query'],'first')
        self.assertEqual(set(closed),{'first','second'});context.close.assert_not_called()

    def test_empty_first_query_continues_to_second(self):
        config,collection,browser,context=self.setup_queries()
        def source(b,local,c,ctx):
            if local['_source_query']=='second':yield 'https://orgpage.ru/x/two-2.html'
        with patch('target_collection._orgpage_urls',side_effect=source):
            self.assertEqual(list(_orgpage_queries(browser,config,collection,context)),['https://orgpage.ru/x/two-2.html'])
        self.assertTrue(collection.stats['queries']['first']['exhausted'])

    def test_real_access_failure_stops_all_queries(self):
        config,collection,browser,context=self.setup_queries()
        calls=[]
        def source(b,local,c,ctx):
            calls.append(local['_source_query'])
            raise SourceStopped('access_restricted','HTTP 429')
            yield
        with patch('target_collection._orgpage_urls',side_effect=source),self.assertRaises(SourceStopped) as caught:
            list(_orgpage_queries(browser,config,collection,context))
        self.assertEqual(caught.exception.reason,'access_restricted');self.assertEqual(calls,['first'])

    def test_beauty_retains_single_query_path(self):
        config=source_config(INDUSTRIES[2]);collection=Collection(1,200)
        collection.stats=dict(source_pages=0,source_batches=0,source_links_buffered=0)
        with patch('target_collection._orgpage_queries',side_effect=AssertionError('beauty changed')),patch('target_collection._orgpage_urls',return_value=(u for u in ['https://orgpage.ru/x/one-1.html'])):
            stream=card_stream(config,1,Mock(),collection)
            # Use a generator, as the production Orgpage source has close().
            with self.assertRaises(SourceStopped):
                self.assertEqual(next(stream),'https://orgpage.ru/x/one-1.html');next(stream)

    def test_target_and_query_attribution_are_global(self):
        from test_target_count import TargetTests
        fixture=TargetTests('test_missing_name_and_no_score_gate');fixture.setUp()
        original=Collection.begin
        def begin(collection,stats,run_id):
            original(collection,stats,run_id)
            stats['queries']={q:dict(reviewed=0,accepted=0,rejected=0) for q in ('first','second')}
            collection.catalog_metadata={'https://medadvisor.ru/moskva/clinic/1':{'_source_query':'first'},
                                         'https://medadvisor.ru/moskva/clinic/2':{'_source_query':'second'}}
        try:
            with patch.object(Collection,'begin',begin):
                collection,stats=fixture.run_collection([dict(site='https://one.test',phone='+79991234567'),dict(site='https://two.test',phone='+79991234567'),dict(site='https://unused.test',phone='+79991234567')],n=2)
            self.assertEqual(stats['accepted'],2);self.assertEqual(stats['cards_reviewed'],2)
            self.assertEqual(stats['queries']['first']['accepted'],1);self.assertEqual(stats['queries']['second']['accepted'],1)
        finally:fixture.tearDown()

    def test_construction_rejects_unrelated_redirect_without_changing_beauty(self):
        row={'Название':'Строитель','URL агрегатора':'https://orgpage.ru/x/one-1.html',
             'Сайт':'https://builder.test','Телефон':'+79991234567',
             'Проверка сайта':dict(availability='available',brand_status='unknown',final_url='https://umi.ru/help/')}
        for strict,expected in ((True,False),(False,True)):
            collection=Collection(20,200,require_same_site_domain=strict)
            collection.stats=dict(cards_reviewed=0,accepted=0,rejected=0,rejected_by_reason={},all_rejection_reasons={})
            with patch('target_collection.db.get_connection'):
                self.assertEqual(collection.consider(row,'ok',True),expected)
            if strict:self.assertEqual(collection.stats['rejected_by_reason'],{'site_redirected_to_other_domain':1})

    def test_topic_evidence_is_required_only_when_configured(self):
        row={'Название':'Строитель','URL агрегатора':'https://orgpage.ru/x/one-1.html',
             'Сайт':'https://builder.test','Телефон':'+79991234567',
             'Проверка сайта':dict(availability='available',brand_status='unknown',final_url='https://builder.test',ownership_details={'industry_check':{'matched':False}})}
        for required,expected in ((True,False),(False,True)):
            collection=Collection(20,200,require_site_topic=required)
            collection.stats=dict(cards_reviewed=0,accepted=0,rejected=0,rejected_by_reason={},all_rejection_reasons={})
            with patch('target_collection.db.get_connection'):
                self.assertEqual(collection.consider(row,'ok',True),expected)
            if required:self.assertIn('industry_not_confirmed_on_site',collection.stats['rejected_by_reason'])


class ConstructionSnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def test_topic_from_real_snapshot_path_does_not_change_other_industries(self):
        from unittest.mock import AsyncMock
        from site_checker import BrowserSiteChecker
        checker=BrowserSiteChecker(browser=Mock())
        terms=INDUSTRIES[3]['site_topic_terms']
        for title,topic,expected in [('Строительство домов под ключ',terms,True),
                                     ('Онлайн казино и игровые автоматы',terms,False),
                                     ('Настройки cookie и бонусы казино',terms,False),
                                     ('Оконный завод',terms,True),
                                     ('Любой сайт',None,None)]:
            page=AsyncMock();page.set_default_timeout=Mock();page.url='https://builder.test/'
            page.goto.return_value=SimpleNamespace(status=200)
            page.evaluate.return_value=dict(title=title,text='Контакты организации',challenge=False)
            context=AsyncMock();context.new_page.return_value=page
            company=dict(name='Строитель',site='https://builder.test',url='https://orgpage.ru/x/one-1.html')
            if topic:company['site_topic_terms']=topic
            result={}
            await checker._navigate(context,'https://builder.test/',company,result)
            proof=result['ownership_details'].get('industry_check')
            if expected is None:self.assertIsNone(proof)
            else:self.assertEqual(proof['matched'],expected)
            self.assertEqual(page.goto.call_count,1)
