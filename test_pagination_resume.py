import csv
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import db
import engine
from target_collection import Collection, SourceStopped, card_stream, resume_state, run_target, source_config
import test_target_count as target_tests
import test_start_page as start_tests


class ResumeTests(unittest.TestCase):
    setUp=target_tests.TargetTests.setUp
    tearDown=target_tests.TargetTests.tearDown
    cards=target_tests.TargetTests.cards
    run_collection=target_tests.TargetTests.run_collection

    def initial(self,partial=False):
        collection,_=self.run_collection([dict(site='https://old.test',phone='+79991234567'),dict()],n=2)
        proof=dict(requested_page=1,verified=True,
                   cards=['https://medadvisor.ru/moskva/clinic/1','https://medadvisor.ru/moskva/clinic/2'],
                   next_url='https://medadvisor.ru/russia/search?page=2')
        if partial:proof['cards'].append('https://medadvisor.ru/moskva/clinic/3')
        collection.stats['catalog_pages']=[proof]
        with db.get_connection() as c:
            c.execute('UPDATE runs SET stats_json=? WHERE id=?',(json.dumps(collection.stats),collection.run_id))
        return source_config(dict(self.cfg,resume=True,output=str(Path(self.temp.name)/'resumed.csv')))

    def test_resume_skips_accepted_and_rejected_cards_and_preserves_export(self):
        config=self.initial()
        state=resume_state(config)
        self.assertEqual((state['initial'],len(state['reviewed']),state['start_page']),(1,2,2))
        with db.get_connection() as c:before=tuple(c.execute('SELECT * FROM companies').fetchone())
        records=self.cards([dict(site='https://old.test',phone='+79991234567'),dict(),
                            dict(site='https://new.test',phone='+79991234567'),
                            dict(site='https://unused.test',phone='+79991234567')])
        fetched=[]
        def fetch(url):
            fetched.append(url)
            self.assertEqual(url,'https://medadvisor.ru/moskva/clinic/3')
            return 'Телефон: +79991234567\nСайт: https://new.test',dict(name='New clinic')
        def enrich(_,cfg,*,target_count,collection):
            self.assertEqual(target_count,1)
            return engine.process_cards(iter(records),cfg,fetch,self.checker,target_count=target_count,collection=collection)
        with patch('engine.enrich_and_check',side_effect=enrich):_,stats=run_target(config,2)
        self.assertEqual((stats['accepted'],stats['cards_reviewed'],stats['exported_total']),(1,1,2))
        self.assertEqual(len(fetched),1)
        with db.get_connection() as c:self.assertEqual(tuple(c.execute('SELECT * FROM companies ORDER BY rowid LIMIT 1').fetchone()),before)
        with Path(config['output']).open(encoding='utf-8-sig',newline='') as f:
            rows=list(csv.DictReader(f,delimiter=';'))
        self.assertEqual(len({r['Официальный сайт'] for r in rows}),2)

    def test_partial_buffer_replays_first_incomplete_medical_page(self):
        self.assertEqual(resume_state(self.initial(partial=True))['start_page'],1)

    def test_already_reached_target_does_not_open_browser(self):
        config=self.initial()
        with patch('engine.enrich_and_check',side_effect=AssertionError('Network must not start')):
            _,stats=run_target(config,1)
        self.assertEqual(stats['stop_reason'],'target_already_reached')
        self.assertEqual(stats['exported_total'],1)

    def test_mixed_industries_are_not_resumed(self):
        config=self.initial()
        db.add_or_update_company('https://orgpage.ru/city/other-1.html','Other','Other',None,None,None,None)
        with self.assertRaises(ValueError):resume_state(config)


class PaginationTests(unittest.TestCase):
    def orgpage(self,pages,max_pages=200):
        page=Mock();index=[0]
        page.locator.return_value.all.return_value=[]
        page.inner_text.return_value='Каталог организаций'
        def goto(url,**kw):
            index[0]=1 if 'page=2' in url else 0;page.url=url
            return SimpleNamespace(status=200)
        page.goto.side_effect=goto
        page.eval_on_selector_all.side_effect=lambda *a:pages[index[0]]
        element=Mock();element.get_attribute.return_value='https://www.orgpage.ru/search.html?page=2'
        page.query_selector.side_effect=lambda *a:element if index[0]==0 and len(pages)>1 else None
        context=Mock();context.new_page.return_value=page
        browser=Mock();browser.new_context.return_value=context
        config=source_config(dict(name='Салоны красоты и косметология',search_url='https://www.orgpage.ru/search.html?q=x',max_pages=max_pages))
        collection=Collection(2,200);collection.stats=dict(source_pages=0,source_batches=0,source_links_buffered=0)
        return page,card_stream(config,2,browser,collection),collection

    def consume(self,stream):
        values=[]
        with self.assertRaises(SourceStopped) as stopped:
            while True:values.append(next(stream))
        return values,stopped.exception.reason

    def test_orgpage_page_limit_is_not_exhaustion(self):
        a='https://orgpage.ru/city/one-1.html';b='https://orgpage.ru/city/two-2.html'
        page,stream,collection=self.orgpage([[a],[b]],max_pages=1)
        with patch('target_collection.time.sleep'):values,reason=self.consume(stream)
        self.assertEqual((values,reason),([a],'safety_page_limit'))
        self.assertEqual(page.goto.call_count,1)

    def test_orgpage_reordered_repeated_page_stops(self):
        a='https://orgpage.ru/city/one-1.html';b='https://orgpage.ru/city/two-2.html'
        _,stream,_=self.orgpage([[a,b],[b,a]])
        with patch('target_collection.time.sleep'):values,reason=self.consume(stream)
        self.assertEqual((values,reason),([a,b],'pagination_stalled'))

    def test_orgpage_empty_terminal_is_exhausted(self):
        _,stream,_=self.orgpage([[]])
        with patch('target_collection.time.sleep'):values,reason=self.consume(stream)
        self.assertEqual((values,reason),([],'source_exhausted'))

    def test_medical_page_limit_does_not_request_next_page(self):
        helper=start_tests.CatalogPageTests();browser,_,visited=helper.browser()
        with patch('medadvisor_catalog.time.sleep'):
            values,reason=self.consume(card_stream(dict(helper.config(),max_pages=1),3,browser,helper.collection()))
        self.assertEqual(reason,'safety_page_limit');self.assertEqual(len(values),1);self.assertEqual(len(visited),1)

    def test_medical_repeated_page_stops(self):
        helper=start_tests.CatalogPageTests();browser,context,visited=helper.browser()
        page=context.new_page.return_value;original=page.evaluate.side_effect
        def snapshot(*args):
            data=original(*args);data['cards'][0]['url']='https://medadvisor.ru/moskva/clinic/5001'
            return data
        page.evaluate.side_effect=snapshot
        with patch('medadvisor_catalog.time.sleep'):
            values,reason=self.consume(card_stream(helper.config(),3,browser,helper.collection()))
        self.assertEqual(reason,'pagination_stalled');self.assertEqual(len(values),1)

    def test_buffer_is_bounded_and_skips_previously_reviewed_urls(self):
        pulled=[]
        def source(*args):
            for i in range(600):
                pulled.append(i);yield 'https://orgpage.ru/city/card-'+str(i)+'.html'
        reviewed={'https://orgpage.ru/city/card-'+str(i)+'.html' for i in range(10)}
        config=dict(source='orgpage',_reviewed_urls=reviewed)
        collection=Collection(1000,10000);collection.stats=dict(source_batches=0,source_links_buffered=0)
        with patch('target_collection._orgpage_urls',source):
            stream=card_stream(config,1000,None,collection)
            self.assertEqual(next(stream),'https://orgpage.ru/city/card-10.html')
            self.assertEqual(len(pulled),210);stream.close()

    def test_cli_exposes_bounded_page_limit(self):
        _,run=start_tests.InteractivePageTests().invoke(['--industry','2','--target','1000','--max-pages','180'])
        self.assertEqual(run.call_args.args[0]['max_pages'],180)


if __name__=='__main__':unittest.main()
