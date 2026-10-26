import contextlib
import io
import unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
from urllib.parse import parse_qs,urlsplit
import db
import parser_run
from target_collection import Collection,SourceStopped,card_stream,source_config
from medadvisor_catalog import page_url
import test_target_count as previous


class InteractivePageTests(unittest.TestCase):
    def invoke(self,argv,answers=()):
        with patch('builtins.input',side_effect=answers) as prompt,patch('parser_run.dependencies'),patch('engine.run_parser') as run,contextlib.redirect_stdout(io.StringIO()):
            parser_run.main(argv)
        return prompt,run

    def test_interactive_medical_default_page(self):
        prompt,run=self.invoke([],['1','2',''])
        self.assertEqual(prompt.call_count,3)
        self.assertEqual(prompt.call_args.args[0],'С какой страницы MedAdvisor начать? [1]: ')
        self.assertEqual(run.call_args.args[0]['start_page'],1)
        self.assertEqual(run.call_args.args[1],2)

    def test_interactive_page_five(self):
        _,run=self.invoke([],['1','200','5'])
        self.assertEqual(run.call_args.args[0]['start_page'],5)

    def test_orgpage_does_not_ask_medical_page(self):
        prompt,run=self.invoke([],['2','100'])
        self.assertEqual(prompt.call_count,2)
        self.assertNotIn('start_page',run.call_args.args[0])

    def test_noninteractive_page_five(self):
        prompt,run=self.invoke(['--industry','1','--target','200','--start-page','5'])
        prompt.assert_not_called()
        self.assertEqual(run.call_args.args[0]['start_page'],5)
        self.assertEqual(run.call_args.args[1],200)

    def test_invalid_page_and_wrong_aggregator(self):
        for args in (['--industry','1','--target','2','--start-page','0'],['--industry','2','--target','2','--start-page','5']):
            with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):self.invoke(args)


class CatalogPageTests(unittest.TestCase):
    def browser(self,active=(5,6),next_number=6):
        page=Mock();visited=[]
        def goto(url,**kwargs):
            page.url=url;visited.append(url)
            return SimpleNamespace(status=200)
        def snapshot(*args):
            number=int(parse_qs(urlsplit(page.url).query)['page'][0])
            return dict(title='Результаты поиска',text='Клиники',active_page=active[0] if number==5 else active[1],
                cards=[dict(url=f'https://medadvisor.ru/moskva/clinic/{number}001',name=f'Клиника страницы {number}',city='Москва')],
                pagination=[dict(text='Далее',url=page_url('https://medadvisor.ru/russia/search?query=клиника',next_number),rel='')] if number==5 else [])
        page.goto.side_effect=goto;page.evaluate.side_effect=snapshot
        context=Mock();context.new_page.return_value=page
        browser=Mock();browser.new_context.return_value=context
        return browser,context,visited

    def collection(self):
        collection=Collection(3,20)
        collection.stats=dict(source_pages=0,source_batches=0,source_links_buffered=0)
        return collection

    def config(self):
        return source_config(dict(name='Медицинские клиники',start_page=5,search_url='https://medadvisor.ru/russia/search?query=клиника'))

    def test_real_cursor_starts_five_then_six_without_csv(self):
        browser,context,visited=self.browser();collection=self.collection()
        with patch('db.csv_records',side_effect=AssertionError('CSV must not supply catalog cards')),patch('medadvisor_catalog.time.sleep'):
            values=[];iterator=card_stream(self.config(),3,browser,collection)
            with self.assertRaises(SourceStopped) as stopped:
                while True:values.append(next(iterator))
        self.assertEqual(stopped.exception.reason,'source_exhausted')
        self.assertEqual([parse_qs(urlsplit(u).query)['page'][0] for u in visited],['5','6'])
        self.assertEqual(values,['https://medadvisor.ru/moskva/clinic/5001','https://medadvisor.ru/moskva/clinic/6001'])
        self.assertEqual(collection.catalog_metadata[values[0]]['catalog_page'],5)
        self.assertEqual(collection.stats['source_scope'],'catalog_search_pages')
        self.assertTrue(all(p['verified'] for p in collection.stats['catalog_pages']))
        context.close.assert_called_once()

    def test_page_one_returned_for_five_is_not_accepted(self):
        browser,_,_=self.browser(active=(1,6));collection=self.collection()
        with self.assertRaises(SourceStopped) as error:next(card_stream(self.config(),3,browser,collection))
        self.assertEqual(error.exception.reason,'catalog_page_unverified')
        self.assertFalse(collection.stats['catalog_pages'][0]['verified'])

    def test_skipped_next_page_rejected(self):
        browser,_,_=self.browser(next_number=7)
        with self.assertRaises(SourceStopped) as error:next(card_stream(self.config(),3,browser,self.collection()))
        self.assertEqual(error.exception.reason,'invalid_next_page')

    def test_page_number_not_card_id(self):
        with self.assertRaises(ValueError):page_url('https://medadvisor.ru/moskva/clinic/5',5)
        self.assertEqual(parse_qs(urlsplit(page_url('https://medadvisor.ru/russia/search?query=клиника',5)).query)['page'],['5'])


class ProgressTests(unittest.TestCase):
    setUp=previous.TargetTests.setUp
    tearDown=previous.TargetTests.tearDown

    def row(self,accepted):
        return {'URL агрегатора':'https://medadvisor.ru/moskva/clinic/'+('1' if accepted else '2'),'Название':'Клиника',
                'Сайт':'https://example.test' if accepted else None,'Телефон':'+79991234567',
                'Проверка сайта':dict(availability='available',final_url='https://example.test') if accepted else None}

    def test_rejected_card_counts_and_accepted_progress(self):
        from console_progress import prefix
        run_id=db.start_run();collection=Collection(100,1000);collection.begin({},run_id)
        collection.consider(self.row(False),'ok',True)
        self.assertEqual(prefix(run_id,100),'[001] [Готово: 0/100]')
        collection.consider(self.row(True),'ok',True)
        self.assertEqual(prefix(run_id,100),'[002] [Готово: 1/100]')

    def test_saved_run_progress_survives_lost_in_memory_counters(self):
        from console_progress import prefix
        run_id=db.start_run();collection=Collection(100,1000);collection.begin({},run_id)
        collection.consider(self.row(True),'ok',True);collection.consider(self.row(False),'ok',True)
        collection.stats.clear();collection.accepted.clear()
        self.assertEqual(prefix(run_id,100),'[002] [Готово: 1/100]')
        new_id=db.start_run();fresh=Collection(100,1000);fresh.begin({},new_id)
        fresh.consider(self.row(False),'ok',True)
        self.assertEqual(prefix(new_id,100),'[001] [Готово: 0/100]')


if __name__=='__main__':unittest.main()
