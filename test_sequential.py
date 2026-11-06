"""Offline two-run check using the real catalog stream and SQLite."""
import csv
from pathlib import Path
from unittest.mock import Mock, patch

import db
import engine
from target_collection import card_stream, run_target
from test_target_count import TargetTests
import unittest


class SequentialTests(unittest.TestCase):
    setUp=TargetTests.setUp
    tearDown=TargetTests.tearDown
    def test_two_runs_resume_and_export_only_new_organizations(self):
        config=dict(self.cfg,sequential=True,start_page=1,
                    catalog_pages_path=str(Path(self.temp.name)/'catalog_pages.json'))
        cards=['https://medadvisor.ru/moskva/clinic/'+str(i) for i in range(1,5)]
        fetched=[]
        page=Mock()
        def goto(url,**kwargs):
            page.url=url
            return Mock(status=200)
        page.goto.side_effect=goto
        page.evaluate.return_value=dict(title='Клиники',text='',active_page=1,
            cards=[dict(url=url,name='Клиника',city='Москва') for url in cards],pagination=[])
        browser=Mock();browser.new_context.return_value.new_page.return_value=page
        def fetch(url):
            fetched.append(url)
            # The second card repeats the first organization's domain.
            domain='one' if url in cards[:2] else 'new'+url.rsplit('/',1)[1]
            return 'Телефон: +79991234567\nСайт: https://'+domain+'.test',dict(name='Клиника')
        def enrich(_,cfg,*,target_count,collection):
            stream=card_stream(cfg,target_count,browser,collection)
            try:
                return engine.process_cards(stream,cfg,fetch,self.checker,
                                            target_count=target_count,collection=collection)
            finally:stream.close()
        outputs=[]
        with patch('engine.enrich_and_check',side_effect=enrich),patch('engine.time.sleep'):
            for n in (1,2):
                config['output']=str(Path(self.temp.name)/f'run{n}.csv')
                _,stats=run_target(config,1)
                self.assertEqual(stats['accepted'],1)
                if n==1:
                    # Simulate missing final run stats: the sidecar must retain the cursor.
                    with db.get_connection() as conn:conn.execute('UPDATE runs SET stats_json=NULL')
                with Path(config['output']).open(encoding='utf-8-sig',newline='') as f:
                    outputs.append(list(csv.DictReader(f,delimiter=';')))
        self.assertEqual(fetched,cards[:3])
        self.assertEqual([len(rows) for rows in outputs],[1,1])
        self.assertNotEqual(outputs[0][0]['Исходная карточка'],outputs[1][0]['Исходная карточка'])
        with db.get_connection() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM companies').fetchone()[0],2)
            self.assertEqual(conn.execute('SELECT count(*) FROM collection_decisions').fetchone()[0],3)
        self.assertTrue(Path(config['catalog_pages_path']).is_file())

    def test_cli_defaults(self):
        from test_start_page import InteractivePageTests
        _,run=InteractivePageTests().invoke(['--industry','1','--target','2','--sequential',
            '--database',str(Path(self.temp.name)/'persistent.db')])
        cfg,target=run.call_args.args
        self.assertTrue(cfg['sequential'])
        self.assertEqual(target,2)
        self.assertEqual(Path(cfg['catalog_pages_path']).name,'catalog_pages.json')
