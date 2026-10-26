import unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
from engine import orgpage_access,orgpage_context,UA
from target_collection import Collection,SourceStopped,source_config,_orgpage_urls


class OrgpageRegressionTests(unittest.TestCase):
    def page(self,text='Каталог салонов. This site is protected by reCAPTCHA.'):
        page=Mock();page.url='https://www.orgpage.ru/search.html?q=beauty'
        page.inner_text.return_value=text
        page.locator.return_value.all.return_value=[]
        return page

    def test_normal_catalog_with_captcha_notice_is_allowed(self):
        page=self.page('Каталог салонов. '+('Описание компании. '*40)+'This site is protected by reCAPTCHA.')
        result=orgpage_access(page,200,'catalog')
        self.assertEqual(result['markers'],[])
        page.inner_text.return_value='Каталог салонов. Protected by reCAPTCHA'
        self.assertEqual(orgpage_access(page,200,'catalog')['markers'],[])

    def test_403_has_exact_diagnosis(self):
        page=self.page('403 Forbidden')
        with self.assertRaises(SourceStopped) as caught:orgpage_access(page,403,'catalog')
        self.assertEqual(caught.exception.reason,'access_restricted')
        self.assertIn('403',caught.exception.detail)
        self.assertIn(page.url,caught.exception.detail)
        self.assertIn('catalog',caught.exception.detail)

    def test_visible_challenge_stops(self):
        page=self.page('Подтвердите, что вы человек')
        element=Mock();element.is_visible.return_value=True
        page.locator.return_value.all.return_value=[element]
        with self.assertRaises(SourceStopped) as caught:orgpage_access(page,200,'card')
        self.assertIn('visible_captcha_element',caught.exception.detail)

    def test_context_uses_reference_normal_settings(self):
        browser=Mock();orgpage_context(browser)
        browser.new_context.assert_called_once_with(viewport={'width':1920,'height':1080},
            user_agent=UA,locale='ru-RU',timezone_id='Europe/Moscow')
        browser.new_context.return_value.add_init_script.assert_not_called()

    def test_numeric_next_page_and_context_reuse(self):
        page=self.page('Каталог салонов')
        links=[['https://www.orgpage.ru/moskva/first-1.html'],['https://www.orgpage.ru/moskva/second-2.html']]
        index=[0]
        def goto(url,**kwargs):
            index[0]=1 if 'page=2' in url else 0;page.url=url
            return SimpleNamespace(status=200)
        page.goto.side_effect=goto
        page.eval_on_selector_all.side_effect=lambda *args:links[index[0]]
        element=Mock();element.get_attribute.return_value='?q=beauty&page=2'
        page.query_selector.side_effect=lambda selector:element if selector=='a:text-is("2")' and index[0]==0 else None
        context=Mock();context.new_page.return_value=page;browser=Mock()
        collection=Collection(2,40);collection.stats={'source_pages':0}
        config=source_config(dict(name='Салоны красоты и косметология',search_url=page.url))
        with patch('target_collection.time.sleep') as sleep:
            urls=list(_orgpage_urls(browser,config,collection,context))
        self.assertEqual(urls,['https://orgpage.ru/moskva/first-1.html','https://orgpage.ru/moskva/second-2.html'])
        self.assertEqual(collection.stats['source_pages'],2)
        self.assertEqual(len(collection.stats['source_diagnostics']),2)
        sleep.assert_any_call(3);sleep.assert_any_call(2)
        browser.new_context.assert_not_called();context.close.assert_not_called();page.close.assert_called_once()

    def test_access_denied_does_not_retry_or_extract(self):
        page=self.page('403 Forbidden');page.goto.return_value=SimpleNamespace(status=403)
        context=Mock();context.new_page.return_value=page;browser=Mock()
        collection=Collection(5,40);collection.stats={'source_pages':0}
        config=source_config(dict(name='Салоны красоты и косметология',search_url=page.url))
        with self.assertRaises(SourceStopped):list(_orgpage_urls(browser,config,collection,context))
        page.goto.assert_called_once();page.eval_on_selector_all.assert_not_called()

    def test_navigation_failure_is_diagnostic(self):
        page=self.page();page.goto.side_effect=RuntimeError('net::ERR_NAME_NOT_RESOLVED')
        context=Mock();context.new_page.return_value=page
        collection=Collection(5,40);collection.stats={'source_pages':0}
        config=source_config(dict(name='Салоны красоты и косметология',search_url=page.url))
        with self.assertRaises(SourceStopped) as caught:list(_orgpage_urls(Mock(),config,collection,context))
        self.assertEqual(caught.exception.reason,'source_error')
        self.assertIn('ERR_NAME_NOT_RESOLVED',caught.exception.detail)
        self.assertIn(page.url,caught.exception.detail)

    def ajax_fixture(self,status=200):
        from target_collection import card_stream
        page=self.page('Каталог салонов')
        phase=[0];handlers={}
        page.on.side_effect=lambda name,handler:handlers.update({name:handler})
        links=[['https://www.orgpage.ru/moskva/salon-'+str(i)+'.html' for i in range(1,31)],
               ['https://www.orgpage.ru/moskva/salon-'+str(i)+'.html' for i in range(31,61)]]
        page.goto.return_value=SimpleNamespace(status=200)
        page.eval_on_selector_all.side_effect=lambda *args:links[phase[0]]
        element=Mock();element.is_visible.return_value=True;element.get_attribute.return_value=''
        page.query_selector.side_effect=lambda selector:element if phase[0]==0 else None
        def click(**kwargs):
            phase[0]=1
            response=Mock();response.url='https://www.orgpage.ru/Search/GetSearchCompany/'
            response.status=status;response.request.is_navigation_request.return_value=False
            handlers['response'](response)
        element.click.side_effect=click
        context=Mock();context.new_page.return_value=page;browser=Mock()
        collection=Collection(20,200)
        collection.stats=dict(source_pages=0,source_batches=0,source_links_buffered=0)
        config=source_config(dict(name='Салоны красоты и косметология',search_url=page.url))
        return page,element,collection,card_stream(config,20,browser,collection,source_context=context)

    def test_ajax_continues_after_first_30_cards_without_document_navigation(self):
        page,element,collection,stream=self.ajax_fixture()
        with patch('target_collection.time.sleep'):
            urls=[next(stream) for _ in range(60)]
            with self.assertRaises(SourceStopped) as exhausted:next(stream)
            self.assertEqual(exhausted.exception.reason,'source_exhausted')
        self.assertEqual(len(urls),60)
        self.assertEqual(urls[30],'https://orgpage.ru/moskva/salon-31.html')
        self.assertEqual(len(set(urls)),60)
        self.assertEqual(collection.stats['source_pages'],2)
        self.assertEqual(collection.stats['source_batches'],2)
        self.assertEqual(collection.stats['source_links_buffered'],60)
        self.assertEqual(collection.stats['source_diagnostics'][1]['ajax_http_status'],200)
        self.assertEqual(collection.stats['source_diagnostics'][1]['page_number'],2)
        page.goto.assert_called_once();element.click.assert_called_once()
        page.wait_for_function.assert_called_once()

    def test_ajax_timeout_preserves_page_url_stage_and_exception(self):
        import json
        from playwright.sync_api import TimeoutError as PlaywrightTimeout
        page,element,collection,stream=self.ajax_fixture()
        page.wait_for_function.side_effect=PlaywrightTimeout('card list did not change')
        with patch('target_collection.time.sleep'),self.assertRaises(SourceStopped) as caught:
            list(stream)
        self.assertEqual(caught.exception.reason,'source_error')
        detail=json.loads(caught.exception.detail)
        self.assertEqual(detail['stage'],'pagination_wait')
        self.assertEqual(detail['page_number'],2)
        self.assertEqual(detail['url'],page.url)
        self.assertEqual(detail['http_status'],200)
        self.assertIn('card list did not change',detail['error'])
        self.assertEqual(detail['exception'],'TimeoutError')

    def test_ajax_restriction_is_not_hidden_by_document_200(self):
        import json
        from playwright.sync_api import TimeoutError as PlaywrightTimeout
        page,element,collection,stream=self.ajax_fixture(status=429)
        page.wait_for_function.side_effect=PlaywrightTimeout('blocked AJAX')
        with patch('target_collection.time.sleep'),self.assertRaises(SourceStopped) as caught:
            list(stream)
        self.assertEqual(caught.exception.reason,'access_restricted')
        self.assertEqual(json.loads(caught.exception.detail)['ajax_http_status'],429)
        element.click.assert_called_once();page.reload.assert_not_called()

    def test_hidden_next_button_confirms_end_without_click(self):
        page,element,collection,stream=self.ajax_fixture()
        element.is_visible.return_value=False
        with patch('target_collection.time.sleep'):
            self.assertEqual(len([next(stream) for _ in range(30)]),30)
            with self.assertRaises(SourceStopped) as exhausted:next(stream)
            self.assertEqual(exhausted.exception.reason,'source_exhausted')
        element.click.assert_not_called()
