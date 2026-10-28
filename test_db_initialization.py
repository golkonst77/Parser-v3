import contextlib
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import db
import engine
import parser_run


class InitializationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.base=Path(self.temp.name)
        self.root_patch=patch.object(db,'ROOT',self.base);self.root_patch.start()
        self.db_patch=patch.object(db,'DB_FILE',str(self.base/'test.db'));self.db_patch.start()

    def tearDown(self):
        self.db_patch.stop();self.root_patch.stop();self.temp.cleanup()

    def test_schema_initializes_without_default_medical_csv(self):
        self.assertFalse((self.base/'medadvisor.csv').exists())
        with patch('db.csv_records',side_effect=AssertionError('Medical CSV must not be read')):
            db.init_db()
            db.init_db(refresh_existing=False)
        with db.get_connection() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM companies').fetchone()[0],0)

    def test_no_refresh_never_loads_implicit_csv_and_preserves_rows(self):
        db.init_db()
        db.add_or_update_company('https://orgpage.ru/city/builder-1.html','Строительство и ремонт',
                                 'Builder','+79991234567',None,'https://builder.test','Работает')
        with db.get_connection() as c:before=[tuple(r) for r in c.execute('SELECT * FROM companies')]
        with patch('pathlib.Path.is_file',return_value=True),patch('db.csv_records',side_effect=AssertionError('No refresh')):
            db.init_db(refresh_existing=False)
        with db.get_connection() as c:self.assertEqual([tuple(r) for r in c.execute('SELECT * FROM companies')],before)

    def test_explicit_missing_csv_and_medical_reader_remain_strict(self):
        missing=self.base/'missing-reference.csv'
        with self.assertRaises(FileNotFoundError):db.init_db(missing)
        with self.assertRaises(FileNotFoundError):db.csv_records()

    def test_explicit_medical_reference_still_repairs_legacy_name(self):
        db.init_db()
        url='https://medadvisor.ru/moskva/clinic/123'
        db.add_or_update_company(url,'Медицинские клиники','123',None,None,None,None)
        reference=self.base/'reference.csv'
        with reference.open('w',encoding='utf-8',newline='') as f:
            writer=csv.writer(f);writer.writerow(['link href','clinic-card__title','clinic-card__city'])
            writer.writerow([url,'Fixture clinic','Москва'])
        db.init_db(reference)
        with db.get_connection() as c:
            row=c.execute('SELECT name,city FROM companies WHERE url=?',(url,)).fetchone()
        self.assertEqual(tuple(row),('Fixture clinic','Москва'))

    def test_construction_cli_separate_database_and_csv_without_medical_file(self):
        database=self.base/'new'/'construction.db';output=self.base/'new'/'construction.csv'
        class Checker:
            def check_unsaved(self,company):
                return dict(checked_url=company['site'],final_url=company['site'],availability='available',
                    ownership='unknown',brand_status='unknown',branch_status='unknown',checked_at=db.now(),
                    http_status=200,error=None,duration_ms=0,evidence=[],attempt=1,
                    ownership_details={'industry_check':{'matched':True}},
                    quick_screen=dict(status='unknown',checked_at=db.now(),duration_ms=0,
                        features=dict.fromkeys(db.SCREEN_FEATURES,'unknown'),errors=['offline fixture'],details={}))
        def enrich(_,config,*,target_count,collection):
            self.assertEqual(config['source'],'orgpage')
            def fetch(url):
                return 'Телефон: +79991234567\nСайт: https://builder.test',dict(name='Fixture builder')
            return engine.process_cards(iter(['https://orgpage.ru/city/builder-1.html']),config,fetch,
                                        Checker(),target_count=target_count,collection=collection)
        with patch('engine.enrich_and_check',side_effect=enrich),patch('db.csv_records',side_effect=AssertionError('Orgpage cannot depend on medical CSV')),contextlib.redirect_stdout(io.StringIO()):
            parser_run.main(['--industry','3','--target','1','--database',str(database),'--output',str(output)])
        self.assertTrue(database.exists())
        with output.open(encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f,delimiter=';'))
        self.assertEqual(len(rows),1)
        self.assertTrue(json.loads(output.with_suffix('.summary.json').read_text(encoding='utf-8'))['target_achieved'])
        self.assertFalse((self.base/'medadvisor.csv').exists())


if __name__=='__main__':unittest.main()
