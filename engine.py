"""
ДВИЖОК ПАРСИНГА — Парсер-Комплекс v2.1
Единая логика для всех отраслей.
"""
import csv
import re
import os
import time
from contextlib import ExitStack
from site_checker import SyncSiteChecker
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
import db  # Модуль базы данных

# =====================================================
# ОБЩИЕ КОНСТАНТЫ
# =====================================================
VERSION = "3.0"
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
HEADERS = {'User-Agent': UA}

PUBLIC_EMAIL_DOMAINS = {
    'gmail.com', 'mail.ru', 'yandex.ru', 'rambler.ru', 'inbox.ru',
    'list.ru', 'bk.ru', 'ya.ru', 'hotmail.com', 'outlook.com',
    'yahoo.com', 'icloud.com', 'protonmail.com', 'yandex.com', 'mail.com'
}

SOCIAL_MEDIA_DOMAINS = {
    'instagram.com', 'vk.com', 'facebook.com', 'ok.ru',
    'twitter.com', 'x.com', 'tiktok.com', 'youtube.com', 't.me'
}

# Фильтр госучреждений (только для медицины)
STATE_NAME_KEYWORDS = [
    'городская больница', 'областная больница', 'клиническая больница',
    'больница №', 'больница им', 'гкб ', 'дгкб', 'родильный дом', 'роддом',
    'гбуз', 'гбу ', 'фгбу', 'поликлиника №', 'гп №',
    'станция скорой', 'диспансер', 'нии ', 'нмиц',
    'научно-исследовательский', 'федеральный центр',
    'минздрав', 'фмба', 'военно-медицинская',
    'университетская клиника', 'им. ', 'имени ',
]
STATE_DOMAIN_KEYWORDS = [
    'zdrav.mos.ru', 'zdrav.spb.ru', 'mosgorzdrav', 'gov.ru', 'minzdrav',
]

# =====================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =====================================================

def is_state_institution(row):
    """Проверяет, является ли запись госучреждением"""
    name = (row.get('Название') or '').lower()
    site = (row.get('Сайт') or '').lower()
    email = (row.get('Email') or '').lower()
    for kw in STATE_NAME_KEYWORDS:
        if kw in name:
            return kw.strip()
    for kw in STATE_DOMAIN_KEYWORDS:
        if kw in site or kw in email:
            return kw.strip()
    return None

def check_website_status(company, checker):
    """Browser result; availability and ownership are independent."""
    return checker.check(company)

def is_company_link(link, link_re):
    """Проверяет, является ли ссылка целевой"""
    if not link:
        return False
    if re.fullmatch(link_re, link):
        return True
    # Для orgpage — дополнительная проверка
    if 'orgpage.ru' in link and link.endswith('.html'):
        exclude = ['/otzivy/', '/rossiya/', 'search.html', 'landing.html',
                   '/msk-metro/', '/spb-metro/', '/Account/', '/Cabinet/']
        if any(kw in link for kw in exclude):
            return False
        if re.search(r'/[a-z0-9\-]+/[a-z0-9\-]+\-\d+\.html', link, re.IGNORECASE):
            return True
    return False

def extract_contacts_from_text(text, source):
    """Извлекает телефон, email, сайт из текста страницы"""
    phone = 'Не найден'
    email = 'Не найден'
    website = 'Не найден'

    # Exclude recommendation cards/footer before extracting this card's contacts.
    if source=='medadvisor':
        text=re.split(r'Похожие клиники|Другие клиники',text,maxsplit=1,flags=re.I)[0]

    # Телефон
    ph_match = re.search(r'(?:Телефон|Тел\.?)\s*[:\-]?\s*([+\d\s\(\)-]{10,25})', text, re.IGNORECASE)
    if ph_match:
        phone = ph_match.group(1).strip()
    else:
        phones = re.findall(r'\+7\s?\(?\d{3}\)?\s?\d{3}[-.]?\d{2}[-.]?\d{2}|8\s?\(?\d{3}\)?\s?\d{3}[-.]?\d{2}[-.]?\d{2}', text)
        if phones:
            phone = sorted(set(phones))[0]

    # Email
    exclude_domain = 'medadvisor.ru' if source == 'medadvisor' else 'orgpage.ru'
    emails = [e for e in re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', text) if exclude_domain not in e]
    if emails:
        email = ', '.join(sorted(set(emails)))

    # Directory labels may include the organization type before the actual URL.
    # Require a site label; never manufacture a website from an email domain.
    site_match=re.search(r'(?:официальный\s+сайт|веб[- ]?сайт|сайт|website|site)\s*(?:(?:клиники|организации|компании|центра|учреждения)\s*)?[:—–-]?\s*((?:https?://|www\.)?[a-zа-яё0-9](?:[a-zа-яё0-9-]*[a-zа-яё0-9])?(?:\.[a-zа-яё0-9](?:[a-zа-яё0-9-]*[a-zа-яё0-9])?)+(?:/[^\s<>\"\x27,]*)?)',text,re.I)
    if site_match:
        site=site_match[1].rstrip('.,;)')
        if db.domain(site) not in (exclude_domain,*SOCIAL_MEDIA_DOMAINS):
            website=site if site.lower().startswith(('http://','https://')) else 'https://'+site

    return phone, email, website

def check_captcha(page):
    """Проверяет наличие CAPTCHA"""
    try:
        text = page.evaluate('() => document.body.innerText').lower()
        return any(ind in text for ind in ['captcha', 'я не робот', 'подтвердите, что вы человек'])
    except Exception:
        return False

# =====================================================
# ФАЗА 1: СБОР ССЫЛОК
# =====================================================

def _collect_links_orgpage_one(config, target_count):
    """Сбор ссылок с orgpage.ru через Playwright"""
    from industries import NEXT_SELECTORS, MAX_PAGES, MAX_RETRIES
    
    collected = set()
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent=UA, locale='ru-RU', timezone_id='Europe/Moscow'
        )
        page = context.new_page()
        
        print(f"\n Открываем: {config['search_url']}")
        response=page.goto(config['search_url'], timeout=30000, wait_until='domcontentloaded')
        if response is None or response.status in (401,403,429):
            browser.close()
            raise RuntimeError('Catalog access restricted; collection stopped')
        time.sleep(3)
        
        page_num = 1
        while len(collected) < target_count and page_num <= MAX_PAGES:
            print(f"   📄 Страница #{page_num}")
            
            if check_captcha(page):
                browser.close()
                raise RuntimeError("CAPTCHA: collection stopped without bypass")
            
            all_links = page.eval_on_selector_all('a', 'elements => elements.map(e => e.href)')
            new_count = 0
            for link in all_links:
                if is_company_link(link, config['link_re']) and link not in collected:
                    collected.add(link)
                    new_count += 1
                    if len(collected) >= target_count:
                        break
            
            print(f"   ➕ Найдено: {new_count}, всего: {len(collected)} / {target_count}")
            if new_count == 0 and page_num > 1:
                print("   ⚠️  Новых нет, останавливаем сбор.")
                break
            
            # Пагинация
            next_clicked = False
            for selector in NEXT_SELECTORS + [f'a:has-text("{page_num + 1}")']:
                try:
                    el = page.query_selector(selector)
                    if el:
                        el.click()
                        next_clicked = True
                        break
                except Exception:
                    pass
            
            if not next_clicked:
                print("   ⚠️  Кнопка пагинации не найдена.")
                break
            
            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    page.wait_for_load_state('domcontentloaded', timeout=20000)
                    time.sleep(2)
                    break
                except PlaywrightTimeout:
                    if attempt < MAX_RETRIES:
                        page.reload(wait_until='domcontentloaded', timeout=20000)
                        time.sleep(2)
            
            page_num += 1
        
        browser.close()
    
    return list(collected)[:target_count]

def collect_links_orgpage(config, target_count):
    from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
    geo=config.get('geography')
    if geo is None:return _collect_links_orgpage_one(config,target_count)
    locations=geo.get('cities',[])+geo.get('regions',[])
    collected=[]
    for loc in locations:
        if len(collected)>=target_count:break
        local=dict(config)
        parts=urlsplit(config['search_url'])
        query=dict(parse_qsl(parts.query));query['loc']=loc
        local['search_url']=urlunsplit(parts._replace(query=urlencode(query)))
        collected.extend(url for url in _collect_links_orgpage_one(local,target_count-len(collected)) if url not in collected)
    return collected[:target_count]


def collect_links_medadvisor(config, target_count):
    records = db.csv_records(config.get('input_csv', db.ROOT / 'medadvisor.csv'))
    from geography import match, priority
    geo=config.get('geography')
    eligible=[url for url in records if re.fullmatch(config['link_re'],url) and match(records[url],geo)[0]]
    return sorted(eligible,key=lambda url:priority(records[url],geo))[:target_count]

# =====================================================
# ФАЗА 2: ОБОГАЩЕНИЕ + ПРОВЕРКА + ЗАПИСЬ В БД
# =====================================================

def card_address(text):
    """Literal catalog address only; never infer a city from a URL or clinic name."""
    found=re.search(r'актуальный адрес\s*[-—–:]\s*([^\n]+)',text or '',re.I)
    return found[1].strip() if found else None


def card_metadata(page):
    """Read organization identity from the card, never from its URL."""
    return page.evaluate("""() => {
        let candidates = [];
        function visit(x) {
            if (!x || typeof x !== 'object') return;
            if (Array.isArray(x)) { x.forEach(visit); return; }
            let types = [].concat(x['@type'] || []);
            if (types.some(t => /Organization|LocalBusiness|Medical|Dentist|Clinic|BeautySalon|Store/.test(t)) && x.name) candidates.push(x);
            if (x['@graph']) visit(x['@graph']);
        }
        document.querySelectorAll('script[type="application/ld+json"]').forEach(e => {
            try { visit(JSON.parse(e.textContent)); } catch (_) {}
        });
        let org = candidates[0] || {};
        let address = org.address || {};
        let h1 = document.querySelector('h1');
        return {name: org.name || (h1 && h1.textContent.trim()) || null,
                city: address.addressLocality || null, region: address.addressRegion || null, address: address.streetAddress || null};
    }""")


def process_cards(urls, config, fetch, site_checker=None, *, target_count=None, collection=None):
    """Commit each card before advancing. fetch returns (text, metadata)."""
    from industries import REQUEST_DELAY
    references = db.csv_records(config.get('input_csv', db.ROOT / 'medadvisor.csv')) if config['source'] == 'medadvisor' and config.get('start_page') is None else {}
    results = []
    stats = dict(new=0, updated=0, unchanged=0, skipped=0, errors=0)
    run_id = db.start_run()
    status = 'completed'
    from target_collection import Collection,SourceStopped
    from console_progress import prefix as progress_prefix
    viewed=0
    if target_count is not None:
        collection=collection or Collection(target_count,config.get('max_cards',max(200,10*target_count)))
        collection.industry=config['name']
        collection.begin(stats,run_id)
    try:
        candidates=urls if collection else dict.fromkeys(db.canonical_url(u) for u in urls)
        for url in candidates:
            url=db.canonical_url(url)
            viewed+=1
            if collection and stats['cards_reviewed']>=collection.max_cards:
                stats['stop_reason']='safety_card_limit'
                status='stopped'
                break
            metadata = dict(references.get(url, {}))
            if collection:metadata.update(collection.catalog_metadata.get(url,{}))
            from geography import match, location
            if metadata and not match(metadata,config.get('geography'))[0]:
                stats['skipped'] += 1
                if collection:
                    collection.consider({'Название':metadata.get('name'),'URL агрегатора':url,'Город':metadata.get('city')},'ok',False)
                    print(f"{progress_prefix(run_id,collection.target)} Skipped {url}; outside_geography; fetch=not_requested")
                    if stats['cards_reviewed']>=collection.max_cards:
                        stats['stop_reason']='safety_card_limit';status='stopped';break
                continue
            phone = email = website = None
            fetch_status, error = 'ok', None
            checked=None;access_stop=None
            try:
                text, card = fetch(url)
                if config['source'] != 'medadvisor':
                    metadata.update(card)
                else:
                    metadata.update({k:v for k,v in card.items() if k in ('address','brand','region','city') and v})
                if collection and not db.clean(metadata.get('name')) and card.get('name'):
                    metadata['name']=card['name']
                if not metadata.get('address'):
                    metadata['address']=card_address(text)
                phone, email, website = map(db.clean, extract_contacts_from_text(text, config['source']))
            except Exception as exc:
                fetch_status, error = 'error', str(exc)
                if isinstance(exc,SourceStopped):access_stop=exc
                stats['errors'] += 1
                db.log_error(run_id, url, error)
            metadata.update(location(metadata))
            geography_ok, geography_reason = match(metadata,config.get('geography'))
            site_status = None
            if fetch_status != 'ok':
                site_status = None
            elif not website:
                site_status = 'Нет сайта'
            row = {'Название': metadata.get('name'), 'URL агрегатора': url,
                   'Телефон': phone, 'Email': email, 'Сайт': website, 'Статус сайта': site_status,
                   'Город':metadata.get('city'),'Регион':metadata.get('region'),'Адрес':metadata.get('address')}
            result=None
            if collection:
                from lead_scoring import site_domain
                host=site_domain(website)
                if host in collection.domains:
                    row['_rejection_reason']='duplicate_organization'
                elif host and host in db.existing_site_domains():
                    row['_rejection_reason']='existing_domain'
                elif db.existing_card(url):
                    row['_rejection_reason']='existing_organization'
            else:
                result = db.add_or_update_company(url, config['name'], metadata.get('name'), phone, email,
                    website, site_status, config.get('search_url'), city=metadata.get('city'),
                    region=metadata.get('region'), address=metadata.get('address'),brand=metadata.get('brand'), source=config['source'], fetch_status=fetch_status,
                    error=error, data_status='complete' if metadata.get('name') else 'incomplete',
                    contact_status='error' if fetch_status != 'ok' else 'found' if any((phone,email,website)) else 'absent')
                stats[result] += 1
            # Target mode checks new candidates before committing them.
            pre_reasons=collection.precheck(row,fetch_status,geography_ok) if collection else []
            if geography_ok and fetch_status == 'ok' and not pre_reasons and config.get('check_sites') and site_checker:
                if collection:
                    company=dict(url=url,industry=config['name'],name=metadata.get('name'),site=website,
                                 phone=phone,email=email,address=metadata.get('address'),city=metadata.get('city'),
                                 region=metadata.get('region'),brand=metadata.get('brand'),source=config['source'],
                                 source_url=config.get('search_url'),site_status=None)
                else:
                    with db.get_connection() as connection:
                        company=dict(connection.execute('SELECT * FROM companies WHERE canonical_url=? ORDER BY rowid LIMIT 1',
                                                        (db.canonical_url(url),)).fetchone())
                if config.get('site_topic_terms'):company['site_topic_terms']=config['site_topic_terms']
                if db.clean(company['site']):
                    try:
                        checked = (site_checker.check_unsaved(company) if collection else check_website_status(company, site_checker))
                        site_status = 'Работает' if checked['availability'] == 'available' else company['site_status']
                        if not collection and checked['availability'] == 'available':
                            with db.get_connection() as connection:
                                connection.execute('UPDATE companies SET site_status=? WHERE url=? AND site=?',
                                                   (site_status,company['url'],company['site']))
                        row['Статус сайта'] = site_status
                        row['Проверка сайта'] = checked
                    except Exception as exc:
                        stats['errors'] += 1
                        db.log_error(run_id,url,'Website check: ' + str(exc))
            if collection and not collection.rejection_reasons(row,fetch_status,geography_ok):
                result=db.save_new_usable_company(company,checked)
                if result=='new':stats['new']+=1
                else:row['_rejection_reason']=result
            row['Географический отбор'] = geography_reason
            selected = (collection.consider(row,fetch_status,geography_ok) if collection else
                        geography_ok and not (config.get('filter_state') and is_state_institution(row)) and not (config.get('working_only') and site_status != 'Работает'))
            if selected:
                results.append(row)
            else:
                stats['skipped'] += 1
            progress=progress_prefix(run_id,collection.target) if collection else f"[{viewed:03d}]"
            action='Skipped' if collection and not selected else 'Saved'
            detail=result or (collection.rejection_reasons(row,fetch_status,geography_ok)[0] if collection else 'not_saved')
            print(f"{progress} {action} {url}; {detail}; fetch={fetch_status}",flush=True)
            if collection and access_stop:
                stats['stop_reason']=access_stop.reason
                stats['stop_detail']=access_stop.detail
                status='stopped'
                break
            if collection and len(collection.accepted)>=collection.target:break
            if collection and stats['cards_reviewed']>=collection.max_cards:
                stats['stop_reason']='safety_card_limit';status='stopped';break
            time.sleep(REQUEST_DELAY)
    except SourceStopped as exc:
        if not collection:raise
        stats['stop_reason']=exc.reason
        stats['stop_detail']=exc.detail
        status='stopped' if exc.reason!='source_exhausted' else 'completed'
    except BaseException:
        status = 'interrupted'
        if collection:stats['stop_reason']='interrupted'
        raise
    finally:
        if collection:collection.finish()
        db.finish_run(run_id, status, stats)
    return results, stats


def orgpage_context(browser):
    # Normal browser settings and timings from beauty_parser_v5.0.py.
    return browser.new_context(viewport={'width':1920,'height':1080}, user_agent=UA,
                               locale='ru-RU', timezone_id='Europe/Moscow')


def orgpage_access(page, status, stage):
    """Only visible challenges or explicit denial prove an access restriction."""
    from target_collection import SourceStopped
    text=page.inner_text('body')
    markers=[phrase for phrase in ('access denied','доступ ограничен','доступ запрещен',
        'you have been blocked','я не робот','подтвердите, что вы человек',
        'verify you are human','проверка безопасности соединения',
        'введите код с картинки','complete the captcha','подтвердите, что вы не робот') if phrase in text.casefold()]
    challenge=page.locator('iframe[src*="captcha"], iframe[src*="challenges.cloudflare.com"], '
                           '.g-recaptcha, .h-captcha, #captcha, input[name="captcha"]').all()
    if any(element.is_visible() for element in challenge):markers.append('visible_captcha_element')
    # A CAPTCHA mentioned in a footer/privacy notice is not a challenge.
    diagnostic=dict(stage=stage,url=page.url,http_status=status,markers=markers,body_length=len(text))
    import json
    if status in (401,403,429,444) or markers:
        print('Orgpage access: '+json.dumps(diagnostic,ensure_ascii=False),flush=True)
        raise SourceStopped('access_restricted',json.dumps(diagnostic,ensure_ascii=False))
    if status is None or status>=400:
        raise SourceStopped('source_error',json.dumps(diagnostic,ensure_ascii=False))
    return diagnostic


def enrich_and_check(urls, config, *, target_count=None, collection=None):
    with sync_playwright() as p:
        orgpage=config['source']=='orgpage'
        browser = p.chromium.launch(headless=not orgpage, args=['--no-sandbox'])
        try:
            context = orgpage_context(browser) if orgpage else browser.new_context(viewport={'width':1920,'height':1080}, user_agent=UA)
            page = context.new_page()
            def fetch(url):
                response = page.goto(url, timeout=15000, wait_until='domcontentloaded')
                if orgpage:
                    if response and response.status in (401,403,429,444):
                        orgpage_access(page,response.status,'card')
                    time.sleep(1)
                    orgpage_access(page,response.status if response else None,'card')
                if target_count is not None and response and response.status in (401,403,429,444):
                    from target_collection import SourceStopped
                    raise SourceStopped('access_restricted',f'Card HTTP {response.status}')
                if response is None or response.status >= 400:
                    raise RuntimeError(f'Card HTTP status: {response.status if response else "no response"}')
                if not orgpage and check_captcha(page):
                    if target_count is not None:
                        from target_collection import SourceStopped
                        raise SourceStopped('access_restricted','CAPTCHA: card data unavailable')
                    raise RuntimeError('CAPTCHA: card data unavailable')
                return page.evaluate('() => document.body.innerText'), card_metadata(page)
            with ExitStack() as stack:
                checker = stack.enter_context(SyncSiteChecker(concurrency=1)) if config.get('check_sites') else None
                if urls is None:
                    from target_collection import card_stream
                    urls=card_stream(config,target_count,browser,collection,source_context=context if orgpage else None)
                try:
                    return process_cards(urls, config, fetch, site_checker=checker,target_count=target_count,collection=collection)
                finally:
                    if hasattr(urls,'close'):urls.close()
        finally:
            browser.close()

# =====================================================
# ТОЧКА ВХОДА
# =====================================================

def run_parser(config, target_count):
    """N means accepted unique organizations, independent of scoring readiness."""
    from target_collection import run_target,source_config
    chosen=source_config(config)
    print(f"{chosen.get('emoji','')} {chosen['name']} | источник: {chosen['source']}")
    print(f"Цель: {target_count} новых пригодных уникальных сайтов")
    print('География:',chosen.get('geography') or 'вся Россия')
    return run_target(chosen,target_count)
