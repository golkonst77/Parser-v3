"""Bounded Chromium checks with conservative ownership evidence and no bypasses."""
import asyncio
import re
import threading
import time
from urllib.parse import urlsplit, urlunsplit

from playwright.async_api import async_playwright
import db
from quick_screen import QuickScreen
from organization_identity import assess_identity

TRANSIENT = {'timeout', 'dns_error', 'network_error', 'unavailable'}

def homepage(url):
    value = url if '://' in url else 'https://' + url
    p = urlsplit(value)
    if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password:
        raise ValueError('Expected an HTTP(S) website URL without credentials')
    return urlunsplit((p.scheme, p.netloc, '/', '', ''))

def classify_error(error):
    value = str(error).lower()
    if 'timeout' in value or isinstance(error, (TimeoutError, asyncio.TimeoutError)):
        return 'timeout'
    if any(s in value for s in ('err_cert', 'err_ssl', 'certificate', 'ssl_error')):
        return 'ssl_error'
    if any(s in value for s in ('err_name_not_resolved', 'dns')):
        return 'dns_error'
    if any(s in value for s in ('err_connection_refused', 'err_address_unreachable')):
        return 'unavailable'
    return 'network_error'

def ownership(company, final_url, content):
    assessment=assess_identity(company,final_url,content)
    return assessment['brand_status'], [e['kind'] for e in assessment['evidence']]


SNAPSHOT = """() => ({title: document.title || '',
    text: (document.body?.innerText || '').slice(0,150000),
    challenge: !!document.querySelector('iframe[src*="recaptcha"],iframe[src*="hcaptcha"],#challenge-form,.cf-turnstile')})"""

class BrowserSiteChecker:
    def __init__(self, *, concurrency=2, timeout=20, navigation_timeout=8, retries=1,
                 browser=None, page_hook=None, launch_options=None,
                 quick_screen=True, screen_budget=3.0, screen_observation=2.0):
        if not 1 <= concurrency <= 8 or timeout <= 0 or navigation_timeout <= 0 or not 0 <= retries <= 2:
            raise ValueError('Invalid checker limits')
        self.limit = asyncio.Semaphore(concurrency)
        self.concurrency = concurrency
        self.timeout = timeout
        self.navigation_timeout = navigation_timeout
        self.retries = retries
        self.browser = browser
        self.page_hook = page_hook
        self.launch_options = launch_options or {}
        self.owns_browser = browser is None
        self.playwright = None
        self.quick_screen = quick_screen
        self.screen_budget = screen_budget
        self.screen_observation = screen_observation
        if screen_budget <= 0 or screen_observation < 0:
            raise ValueError('Invalid screen limits')

    async def __aenter__(self):
        if self.owns_browser:
            self.playwright = await async_playwright().start()
            try:
                self.browser = await self.playwright.chromium.launch(headless=True, **self.launch_options)
            except BaseException:
                await self.playwright.stop()
                raise
        return self

    async def __aexit__(self, *args):
        try:
            if self.owns_browser and self.browser:
                await self.browser.close()
        finally:
            if self.playwright:
                await self.playwright.stop()

    async def _navigate(self, context, target, company, result, screen=None):
        if screen:
            await screen.attach(context)
        page = await context.new_page()
        page.set_default_timeout(int(self.navigation_timeout * 1000))
        if screen:
            screen.begin_navigation()
        response = await page.goto(target, wait_until='domcontentloaded',
                                   timeout=int(self.navigation_timeout * 1000))
        result['final_url'] = page.url
        result['http_status'] = response.status if response else None
        content = await page.evaluate(SNAPSHOT)
        text = (content['title'] + ' ' + content['text']).casefold()
        challenge_title = bool(re.search(r'\bcaptcha\b|проверка (?:браузера|безопасности)', content['title'], re.I))
        protection_page = challenge_title or (content.get('challenge') and len(content['text']) < 1500) or any(s in text for s in ('verify you are human','подтвердите, что вы человек','я не робот','captcha challenge'))
        access_page = any(s in text for s in ('access denied','доступ запрещен','доступ ограничен','you have been blocked'))
        if protection_page:
            result['availability'] = 'captcha'
            result['error'] = 'Human verification required; availability inconclusive'
        elif result['http_status'] in (401,403,429) or access_page:
            result['availability'] = 'blocked'
            result['error'] = 'Access restricted; no bypass attempted'
        elif response is None:
            result['availability'] = 'network_error'
            result['error'] = 'No main-document response'
        elif response.status >= 500:
            result['availability'] = 'unavailable'
            result['error'] = f'HTTP {response.status}'
        elif response.status >= 400:
            result['availability'] = 'http_error'
            result['error'] = f'HTTP {response.status}'
        else:
            result['availability'] = 'available'
            identity=assess_identity(company,page.url,content)
            result.update(ownership=identity['brand_status'],brand_status=identity['brand_status'],branch_status=identity['branch_status'],ownership_details=identity,
                evidence=[e['kind'] for e in identity['evidence']])
        if result['availability'] in ('captcha','blocked'):
            result.setdefault('ownership_details',{})['protection_page_confirmed'] = bool(response is not None and (protection_page or access_page))
        if result['availability']=='available' and company.get('site_topic_terms'):
            # Industry selection uses this already loaded snapshot, without extra navigation.
            pattern=r'\b(?:'+'|'.join(re.escape(term) for term in company['site_topic_terms'])+r')\w*'
            matches=list(re.finditer(pattern,content['title']+' '+content['text'],re.I))
            result.setdefault('ownership_details',{})['industry_check']=dict(
                matched=bool(matches),title=content['title'],
                terms=sorted({match[0].casefold() for match in matches}),
                excerpt=(content['title']+' '+content['text'])[max(0,matches[0].start()-60):matches[0].end()+160] if matches else '')
        return page

    async def check(self, company, url=None, on_attempt=None):
        original = url or company.get('site') or ''
        async with self.limit:
            deadline = time.monotonic() + self.timeout
            last = None
            for attempt in range(1, self.retries + 2):
                started = time.monotonic()
                result = dict(checked_url=original, final_url=None, availability='invalid_url',
                              ownership='unknown',brand_status='unknown',branch_status='unknown',ownership_details={}, checked_at=db.now(),http_status=None,
                              error=None,duration_ms=0,evidence=[],attempt=attempt)
                context = None
                screen = QuickScreen(original,budget=self.screen_budget,observation=self.screen_observation) if self.quick_screen else None
                if screen:
                    result['quick_screen'] = screen.result
                try:
                    target = homepage(original)
                    async def navigate():
                        nonlocal context
                        context = await self.browser.new_context(ignore_https_errors=False)
                        return await self._navigate(context,target,company,result,screen)
                    page = await asyncio.wait_for(navigate(), max(.001,deadline-time.monotonic()))
                    if screen:
                        if result['availability'] == 'available':
                            # Additional bounded budget; screening failures never change availability.
                            await screen.collect(page)
                        else:
                            screen.mark_unknown('site availability: ' + result['availability'])
                    if screen:
                        await screen.detach()
                    if self.page_hook and result['availability'] == 'available':
                        try:
                            await asyncio.wait_for(self.page_hook(page,result),self.navigation_timeout)
                        except Exception as exc:
                            result['hook_error'] = str(exc) or type(exc).__name__
                except ValueError as exc:
                    result['error'] = str(exc)
                    if screen:
                        screen.mark_unknown('navigation: ' + str(exc))
                except Exception as exc:
                    result['availability'] = classify_error(exc)
                    result['ownership'] = 'unknown'
                    result['error'] = str(exc) or type(exc).__name__
                    if screen:
                        screen.mark_unknown('navigation: ' + result['error'])
                finally:
                    if screen:
                        await screen.detach()
                    if context:
                        try:
                            await asyncio.wait_for(context.close(), 2)
                        except Exception as exc:
                            result['error'] = (result['error'] or '') + '; context cleanup: ' + str(exc)
                result['duration_ms'] = round((time.monotonic()-started)*1000)
                if on_attempt:
                    on_attempt(result)
                last = result
                if result['availability'] not in TRANSIENT or time.monotonic() >= deadline:
                    break
                if attempt <= self.retries:
                    await asyncio.sleep(min(.2,max(0,deadline-time.monotonic())))
            return last

    async def check_many(self, companies, on_attempt=None):
        # Fixed worker count avoids unbounded page/task creation on large inputs.
        iterator = iter(enumerate(companies))
        results = [None] * len(companies)
        async def worker():
            for i, company in iterator:
                results[i] = await self.check(company, on_attempt=(lambda r: on_attempt(company,r)) if on_attempt else None)
        await asyncio.gather(*(worker() for _ in range(self.concurrency)))
        return results

class SyncSiteChecker:
    """Bridge for the existing synchronous parser; one async browser for the run."""
    def __init__(self, **options):
        self.options = options

    def __enter__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever,daemon=True)
        self.thread.start()
        self.checker = BrowserSiteChecker(**self.options)
        try:
            self._run(self.checker.__aenter__())
        except BaseException:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join()
            self.loop.close()
            raise
        return self

    def _run(self, coro):
        future = asyncio.run_coroutine_threadsafe(coro,self.loop)
        try:
            return future.result()
        except BaseException:
            future.cancel()
            raise

    def check_unsaved(self, company):
        """Check a candidate without inserting it or its attempts into SQLite."""
        return self._run(self.checker.check(company))

    def check(self, company):
        return self._run(self.checker.check(company,on_attempt=lambda r: db.save_site_check(company['url'],r)))

    def __exit__(self, *args):
        try:
            self._run(self.checker.__aexit__(*args))
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join()
            self.loop.close()
