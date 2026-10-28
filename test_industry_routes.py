import contextlib
import csv
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
from types import SimpleNamespace
from urllib.parse import unquote,urlsplit

import db
import engine
import main
import parser_run
from industries import INDUSTRIES
from target_collection import Collection,SourceStopped,_orgpage_urls,source_config


class IndustryRoutesTests(unittest.TestCase):
    def test_eight_sources_and_distinct_russian_categories(self):
        self.assertEqual(set(INDUSTRIES),set(range(1,9)))
        categories=[]
        for number,config in INDUSTRIES.items():
            with self.subTest(number=number):
                route=source_config(config)
                self.assertEqual(route['source'],'medadvisor' if number==1 else 'orgpage')
                self.assertEqual(config['status'],'ready')
                if number==1:
                    self.assertEqual(route['start_page'],1)
                    self.assertIn('/russia/search',route['search_url'])
                else:
                    self.assertNotIn('input_csv',route)
                    self.assertEqual(urlsplit(route['search_url']).hostname,'www.orgpage.ru')
                    self.assertTrue(urlsplit(route['search_url']).path.startswith('/rossiya/'))
                    self.assertIn('catalog_title',route)
                    self.assertNotIn('loc=',route['search_url'])
                    categories.append(unquote(urlsplit(route['search_url']).path))
        self.assertEqual(len(set(categories)),7)
        self.assertIn('строительные_компании',source_config(INDUSTRIES[3])['search_url'])
        self.assertNotIn('search_queries',INDUSTRIES[3])

    def test_all_cli_routes_initialize_and_export_without_medical_csv(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory)
            with patch.object(db,'ROOT',base),patch.object(db,'DB_FILE',db.DB_FILE),patch('db.csv_records',side_effect=AssertionError('Implicit medical dependency')):
                for number in range(1,9):
                    with self.subTest(number=number):
                        folder=base/str(number);database=folder/'data.db';output=folder/'sample.csv'
                        class Checker:
                            def check_unsaved(self,company):
                                return dict(checked_url=company['site'],final_url=company['site'],availability='available',
                                    ownership='unknown',brand_status='unknown',branch_status='unknown',checked_at=db.now(),
                                    http_status=200,error=None,duration_ms=0,evidence=[],attempt=1,
                                    ownership_details={'industry_check':{'matched':True}},
                                    quick_screen=dict(status='unknown',checked_at=db.now(),duration_ms=0,
                                        features=dict.fromkeys(db.SCREEN_FEATURES,'unknown'),errors=['fixture'],details={}))
                        def enrich(_,config,*,target_count,collection):
                            url='https://medadvisor.ru/test/clinic/1' if number==1 else 'https://orgpage.ru/test/company-1.html'
                            fetch=lambda _:('Телефон: +79991234567\nСайт: https://fixture.test',dict(name='Fixture organization'))
                            return engine.process_cards(iter([url]),config,fetch,Checker(),target_count=target_count,collection=collection)
                        with patch('engine.enrich_and_check',side_effect=enrich),contextlib.redirect_stdout(io.StringIO()):
                            parser_run.main(['--industry',str(number),'--target','1','--database',str(database),'--output',str(output)])
                        with output.open(encoding='utf-8-sig',newline='') as handle:rows=list(csv.DictReader(handle,delimiter=';'))
                        self.assertEqual(len(rows),1)
                        with db.get_connection() as conn:
                            self.assertEqual([r[0] for r in conn.execute('SELECT DISTINCT industry FROM companies')],[INDUSTRIES[number]['name']])
                self.assertFalse((base/'medadvisor.csv').exists())

    def test_menu_can_launch_each_ready_industry(self):
        for number in range(1,9):
            with self.subTest(number=number),patch('db.init_db'),patch('main.clear_screen'),patch('builtins.input',side_effect=[str(number),'1','','q']),patch('engine.run_parser') as run,contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit):main.main()
                self.assertEqual(run.call_count,1)
                config,target=run.call_args.args
                self.assertEqual((config['name'],target),(INDUSTRIES[number]['name'],1))
                self.assertIsNone(config['geography'])

    def test_wrong_catalog_title_is_rejected_before_cards(self):
        page=Mock();page.url=INDUSTRIES[3]['search_url'];page.inner_text.return_value='Каталог организаций'
        page.goto.return_value=SimpleNamespace(status=200);page.locator.return_value.all.return_value=[]
        page.title.return_value='Салоны красоты в России'
        context=Mock();context.new_page.return_value=page;browser=Mock();browser.new_context.return_value=context
        collection=Collection(1,10);collection.stats=dict(source_pages=0)
        with patch('target_collection.time.sleep'),self.assertRaises(SourceStopped) as error:
            list(_orgpage_urls(browser,source_config(INDUSTRIES[3]),collection))
        self.assertEqual(error.exception.reason,'source_error')
        page.eval_on_selector_all.assert_not_called()

    def test_explicit_database_cannot_mix_industries(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);database=base/'medical.db';output=base/'wrong.csv'
            with patch.object(db,'ROOT',base),patch.object(db,'DB_FILE',str(database)):
                db.init_db()
                db.add_or_update_company('https://medadvisor.ru/test/clinic/1',INDUSTRIES[1]['name'],'Fixture',None,None,None,None)
                with patch('engine.run_parser') as run,contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
                    parser_run.main(['--industry','3','--target','1','--database',str(database),'--output',str(output)])
                run.assert_not_called();self.assertFalse(output.exists())
                with db.get_connection() as conn:self.assertEqual(conn.execute('SELECT industry FROM companies').fetchone()[0],INDUSTRIES[1]['name'])


if __name__=='__main__':unittest.main()
