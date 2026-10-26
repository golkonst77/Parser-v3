import copy
import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch,AsyncMock
import db
import engine
from target_collection import Collection,SourceStopped,card_stream,export_results,source_config


class TargetTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent/'target_count_results')
        self.old=db.DB_FILE;db.DB_FILE=str(Path(self.temp.name)/'test.db')
        self.seed=Path(self.temp.name)/'cards.csv'
        self.seed.write_text('link href,clinic-card__title,clinic-card__city\n',encoding='utf-8')
        db.init_db(self.seed)
        self.cfg=dict(name='Медицинские клиники',source='medadvisor',input_csv=str(self.seed),check_sites=True,filter_state=True)
        self.checked=[];self.responses={}
        outer=self
        class Checker:
            def check_unsaved(self,company):
                outer.checked.append(company['url'])
                options=outer.responses.get(company['url'],{})
                result=dict(checked_url=company['site'],final_url=options.get('final_url',company['site']),availability=options.get('availability','available'),ownership=options.get('brand','unknown'),brand_status=options.get('brand','unknown'),branch_status='unknown',checked_at=db.now(),http_status=200,error=None,duration_ms=1,evidence=[],attempt=1,
                    quick_screen=dict(status='unknown',checked_at=db.now(),duration_ms=0,features=dict.fromkeys(db.SCREEN_FEATURES,'unknown'),errors=['fixture'],details={}))
                result['ownership_details']=options.get('ownership_details',{})
                return result
        self.checker=Checker()

    def tearDown(self):
        db.DB_FILE=self.old;self.temp.cleanup()

    def cards(self,items):
        with self.seed.open('w',encoding='utf-8',newline='') as f:
            writer=csv.writer(f);writer.writerow(['link href','clinic-card__title','clinic-card__city'])
            for i,item in enumerate(items,1):
                item['url']='https://medadvisor.ru/moskva/clinic/'+str(i)
                writer.writerow([item['url'],item.get('name','Клиника '+str(i)),'Москва'])
        return {r['url']:r for r in items}

    def run_collection(self,items,n=2,max_cards=200,stream=True):
        records=self.cards(items)
        collection=Collection(n,max_cards)
        config=dict(self.cfg,link_re=r'https://medadvisor\.ru/[^/]+/clinic/\d+')
        urls=card_stream(config,n,None,collection) if stream else iter(records)
        def fetch(url):
            row=records[url]
            if row.get('error'):raise row['error']
            text='\n'.join(filter(None,[('Телефон: '+row['phone']) if row.get('phone') else None,
                 ('Email: '+row['email']) if row.get('email') else None,('Сайт: '+row['site']) if row.get('site') else None]))
            return text,{}
        with patch('engine.time.sleep'):
            result,stats=engine.process_cards(urls,config,fetch,self.checker,target_count=n,collection=collection)
        urls.close() if hasattr(urls,'close') else None
        return collection,stats

    def test_refill_beyond_2n_and_only_accepted_unique(self):
        items=[dict(phone='+79991234567'),dict(site='https://no-contact.test'),
               dict(site='https://down.test',phone='+79991234567'),
               dict(site='https://parked.test',email='a@parked.test'),
               dict(site='https://one.test',phone='+79991234567'),
               dict(site='https://www.one.test/branch',phone='+79991234567'),
               dict(site='https://two.test',email='a@two.test'),
               dict(site='https://unused.test',phone='+79991234567')]
        self.responses={'https://medadvisor.ru/moskva/clinic/3':dict(availability='timeout'),
                        'https://medadvisor.ru/moskva/clinic/4':dict(brand='mismatch')}
        collection,stats=self.run_collection(items)
        self.assertEqual(len(collection.accepted),2);self.assertEqual(stats['cards_reviewed'],7)
        self.assertEqual(stats['source_batches'],2);self.assertTrue(stats['target_achieved'])
        self.assertEqual(stats['stop_reason'],'target_reached');self.assertEqual(stats['rejected'],5)
        self.assertEqual(stats['rejected_by_reason'],dict(site_missing_or_invalid=1,contacts_missing=1,site_unavailable_or_unchecked=1,site_identity_mismatch=1,duplicate_organization=1))
        self.assertNotIn(items[5]['url'],self.checked);self.assertNotIn(items[7]['url'],self.checked)
        with db.get_connection() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM collection_decisions').fetchone()[0],7)
            self.assertEqual(c.execute('SELECT count(*) FROM quick_screens').fetchone()[0],2)
        path=Path(self.temp.name)/'accepted.csv';export_results(collection,path)
        self.assertTrue(path.read_bytes().startswith(b'\xef\xbb\xbf'))
        with path.open(encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f,delimiter=';'))
        self.assertEqual(len(rows),2)
        # Unknown screening and a zero priority do not exclude otherwise usable cards.
        self.assertTrue(all(r['Статус скрининга']=='unknown' and r['Рейтинг']=='0' for r in rows))

    def test_exhaustion_returns_actual_count(self):
        collection,stats=self.run_collection([dict(site='https://one.test',phone='+79991234567'),dict(phone='+79991234567')],n=5)
        self.assertEqual(len(collection.accepted),1);self.assertFalse(stats['target_achieved'])
        self.assertEqual(stats['stop_reason'],'source_exhausted')
        with db.get_connection() as c:
            saved=json.loads(c.execute('SELECT stats_json FROM runs').fetchone()[0])
            self.assertEqual(saved['accepted'],1);self.assertEqual(saved['target'],5)

    def test_safety_limit_does_not_fetch_next_card(self):
        collection,stats=self.run_collection([dict(phone='+79991234567') for _ in range(5)],n=2,max_cards=3)
        self.assertEqual(stats['cards_reviewed'],3);self.assertEqual(stats['stop_reason'],'safety_card_limit')
        with db.get_connection() as c:self.assertEqual(c.execute('SELECT count(*) FROM companies').fetchone()[0],0)

    def test_access_stop_retains_already_accepted(self):
        collection,stats=self.run_collection([dict(site='https://one.test',phone='+79991234567'),dict(error=SourceStopped('access_restricted','HTTP 429')),dict(site='https://two.test',phone='+79991234567')],n=2)
        self.assertEqual(len(collection.accepted),1);self.assertEqual(stats['cards_reviewed'],2)
        self.assertEqual(stats['stop_reason'],'access_restricted')
        with db.get_connection() as c:self.assertEqual(c.execute('SELECT count(*) FROM companies').fetchone()[0],1)

    def test_duplicates_only_of_accepted_and_redirect_aliases(self):
        items=[dict(site='https://empty.test'),dict(site='https://empty.test',phone='+79991234567'),
               dict(site='https://alias.test',phone='+79991234567'),dict(site='https://other.test',phone='+79991234567')]
        self.responses={'https://medadvisor.ru/moskva/clinic/3':dict(final_url='https://empty.test/')}
        collection,stats=self.run_collection(items)
        self.assertEqual(stats['cards_reviewed'],4);self.assertEqual(stats['accepted'],2)
        self.assertEqual(stats['rejected_by_reason']['duplicate_organization'],1)

    def test_stale_success_and_contacts_do_not_count(self):
        url='https://medadvisor.ru/moskva/clinic/1'
        db.add_or_update_company(url,self.cfg['name'],'Старая клиника','+79991234567',None,'https://old.test','Работает')
        collection,stats=self.run_collection([dict()],n=1)
        self.assertEqual(stats['accepted'],0);self.assertEqual(self.checked,[])
        with db.get_connection() as c:self.assertEqual(c.execute('SELECT site FROM companies').fetchone()[0],'https://old.test')

    def test_missing_name_and_no_score_gate(self):
        collection,stats=self.run_collection([dict(name='',site='https://unnamed.test',phone='+79991234567'),dict(name='Городская больница',site='https://good.test',phone='+79991234567')],n=1)
        self.assertEqual(stats['cards_reviewed'],2);self.assertEqual(stats['accepted'],1)
        self.assertEqual(stats['rejected_by_reason'],dict(name_missing=1))

    def test_automatic_source_and_russia(self):
        self.assertEqual(source_config(dict(name='Медицинские клиники',source='orgpage'))['source'],'medadvisor')
        chosen=source_config(dict(name='Стоматологические клиники',source='medadvisor',search_url='https://medadvisor.ru/search'))
        self.assertEqual(chosen['source'],'orgpage');self.assertIn('orgpage.ru',chosen['search_url'])
        self.assertIn('Россия',__import__('urllib.parse',fromlist=['unquote']).unquote(chosen['search_url']))

    def test_confirmed_protection_is_saved_and_exported_with_unknown_screen(self):
        self.responses={'https://medadvisor.ru/moskva/clinic/'+str(i):dict(
            availability=status,ownership_details={'protection_page_confirmed':True})
            for i,status in ((1,'captcha'),(2,'blocked'))}
        collection,stats=self.run_collection([
            dict(site='https://captcha.test',phone='+79991234567'),
            dict(site='https://protected.test',phone='+79991234567'),
            dict(site='https://available.test',phone='+79991234567')],n=3)
        self.assertEqual(stats['accepted'],3)
        path=Path(self.temp.name)/'out.csv';export_results(collection,path)
        with path.open(encoding='utf-8-sig',newline='') as f:
            rows=list(csv.DictReader(f,delimiter=';'))
        self.assertEqual(len(rows),3)
        self.assertEqual([r['Доступность сайта'] for r in rows],['captcha','blocked','available'])
        self.assertTrue(all(r['Статус скрининга']=='unknown' for r in rows))
        with db.get_connection() as conn:
            screens=conn.execute('SELECT * FROM quick_screens').fetchall()
            self.assertTrue(all(r[field]=='unknown' for r in screens for field in db.SCREEN_FEATURES))

    def test_unconfirmed_protection_and_real_failures_are_rejected(self):
        row={'Название':'Пример','Сайт':'https://example.test','Телефон':'+79991234567',
             'URL агрегатора':'https://medadvisor.ru/moskva/clinic/1'}
        rules=Collection(1,1)
        for status in ('captcha','blocked','dns_error','ssl_error','timeout','unavailable','http_error','network_error','invalid_url'):
            with self.subTest(status=status):
                row['Проверка сайта']=dict(availability=status,final_url=row['Сайт'])
                self.assertIn('site_unavailable_or_unchecked',rules.rejection_reasons(row,'ok',True))
        row['Проверка сайта']=dict(availability='blocked',final_url=row['Сайт'],ownership_details={'protection_page_confirmed':True})
        self.assertEqual(Collection(1,1,require_site_topic=True).rejection_reasons(row,'ok',True),[])
        row['Проверка сайта']['brand_status']='mismatch'
        self.assertIn('site_identity_mismatch',rules.rejection_reasons(row,'ok',True))


class ProtectionPageTests(unittest.IsolatedAsyncioTestCase):
    async def test_protection_evidence_and_unknown_screen(self):
        from site_checker import BrowserSiteChecker
        from types import SimpleNamespace
        for title,text,challenge,code,status,confirmed in [
            ('CAPTCHA','Verify you are human',True,403,'captcha',True),
            ('Access denied','You have been blocked',False,403,'blocked',True),
            ('Forbidden','',False,403,'blocked',False)]:
            with self.subTest(title=title):
                page=AsyncMock();page.url='https://example.test/'
                page.set_default_timeout=Mock();page.goto.return_value=SimpleNamespace(status=code)
                page.evaluate.return_value=dict(title=title,text=text,challenge=challenge)
                context=AsyncMock();context.new_page.return_value=page
                browser=AsyncMock();browser.new_context.return_value=context
                checker=BrowserSiteChecker(browser=browser,retries=0)
                result=await checker.check(dict(name='Пример',site=page.url))
                self.assertEqual(result['availability'],status)
                self.assertIs(result['ownership_details']['protection_page_confirmed'],confirmed)
                self.assertEqual(result['quick_screen']['status'],'unknown')
                self.assertTrue(all(v=='unknown' for v in result['quick_screen']['features'].values()))
                page.evaluate.assert_awaited_once()


class CursorTests(unittest.TestCase):
    def test_orgpage_keeps_page_tail_across_batches(self):
        from types import SimpleNamespace
        pages=[['https://www.orgpage.ru/moskva/clinic-'+str(i)+'.html' for i in range(1,4)],
               ['https://www.orgpage.ru/moskva/clinic-'+str(i)+'.html' for i in range(4,7)]]
        page=Mock();page.locator.return_value.all.return_value=[];state={'index':0}
        def goto(url,**kwargs):
            state['index']=1 if 'page=2' in url else 0;page.url=url
            return SimpleNamespace(status=200)
        page.goto.side_effect=goto;page.evaluate.return_value='normal directory';page.inner_text.return_value='normal directory'
        page.eval_on_selector_all.side_effect=lambda *a:pages[state['index']]
        element=Mock();element.get_attribute.return_value='https://www.orgpage.ru/search.html?page=2'
        page.query_selector.side_effect=lambda *a:element if state['index']==0 else None
        context=Mock();context.new_page.return_value=page;browser=Mock();browser.new_context.return_value=context
        collection=Collection(2,200);collection.stats=dict(source_pages=0,source_batches=0,source_links_buffered=0)
        config=source_config(dict(name='Салоны красоты',source='orgpage',search_url='https://www.orgpage.ru/search.html?q=x'))
        values=[]
        with patch('target_collection.time.sleep'):
            iterator=card_stream(config,2,browser,collection)
            with self.assertRaises(SourceStopped) as stopped:
                while True:values.append(next(iterator))
        self.assertEqual(stopped.exception.reason,'source_exhausted')
        self.assertEqual(values,[db.canonical_url(u) for p in pages for u in p])
        self.assertEqual(collection.stats['source_batches'],2);context.close.assert_called_once()

    def test_source_block_does_not_consume_buffered_card_urls(self):
        collection=Collection(2,200);collection.stats=dict(source_batches=0,source_links_buffered=0)
        def source(*args):
            yield 'https://orgpage.ru/moskva/clinic-1.html'
            raise SourceStopped('access_restricted')
        with patch('target_collection._orgpage_urls',source):
            iterator=card_stream(dict(source='orgpage'),2,None,collection)
            with self.assertRaises(SourceStopped):next(iterator)
        self.assertEqual(collection.stats['source_links_buffered'],0)


if __name__=='__main__':unittest.main()
