"""Real MedAdvisor search pagination; no offsets into the legacy CSV."""
import json
import re
import time
from pathlib import Path
from urllib.parse import parse_qsl,urlencode,urlsplit,urlunsplit
import db
from lead_scoring import site_domain

SNAPSHOT=r'''() => ({
 title:document.title,text:(document.body?.innerText||'').slice(0,4000),
 active_page:(()=>{let a=document.querySelector('nav a[aria-current="page"],nav a.btn--secondary');return a&&/^\d+$/.test(a.textContent.trim())?Number(a.textContent.trim()):null})(),
 cards:Array.from(document.querySelectorAll('a[href*="/clinic/"]')).map(a=>({url:a.href,name:a.querySelector('.clinic-card__title')?.textContent.trim()||null,city:a.querySelector('.clinic-card__city')?.textContent.trim()||null})),
 pagination:Array.from(document.querySelectorAll('nav a[href]')).map(a=>({url:a.href,text:a.textContent.trim(),rel:a.rel}))})'''


def page_url(base,number):
    if type(number) is not int or number<1:raise ValueError('Начальная страница должна быть целым числом >= 1')
    p=urlsplit(base)
    if site_domain(base)!='medadvisor.ru' or not p.path.endswith('/search'):
        raise ValueError('Ожидается URL каталога поиска MedAdvisor, не отдельной карточки')
    query=dict(parse_qsl(p.query));query.update(page=str(number),tab='clinics')
    return urlunsplit(p._replace(query=urlencode(query),fragment=''))


def _save_proof(config,collection,proof):
    collection.stats.setdefault('catalog_pages',[]).append(proof)
    if config.get('output'):
        path=Path(config['output']).with_suffix('.catalog_pages.json')
        path.parent.mkdir(parents=True,exist_ok=True)
        temp=path.with_suffix(path.suffix+'.tmp')
        temp.write_text(json.dumps(collection.stats['catalog_pages'],ensure_ascii=False,indent=2),encoding='utf-8')
        temp.replace(path)


def urls(browser,config,collection):
    from target_collection import SourceStopped
    from industries import MAX_PAGES,REQUEST_DELAY
    number=config.get('start_page',1)
    base=config.get('search_url','https://medadvisor.ru/russia/search?query=клиника')
    target=page_url(base,number)
    context=browser.new_context(locale='ru-RU')
    page=context.new_page();seen=set();signatures=set()
    try:
        while True:
            if collection.stats['source_pages']>=config.get('max_pages',MAX_PAGES):raise SourceStopped('safety_page_limit')
            response=page.goto(target,timeout=20000,wait_until='domcontentloaded')
            collection.stats['source_pages']+=1
            snapshot=page.evaluate(SNAPSHOT)
            proof=dict(requested_page=number,actual_page=snapshot['active_page'],requested_url=target,final_url=page.url,
                       http_status=response.status if response else None,checked_at=db.now(),verified=False,
                       cards=[r['url'] for r in snapshot['cards']],next_url=None)
            code=proof['http_status']
            text=snapshot['title']+' '+snapshot['text']
            if code in (401,403,429,444) or re.search(r'captcha|verify you are human|я не робот|подтвердите, что вы человек|access denied|доступ ограничен|доступ запрещен',text,re.I):
                _save_proof(config,collection,proof);raise SourceStopped('access_restricted','Каталог MedAdvisor ограничил доступ; обход не выполнялся')
            if code is None or code>=400:
                _save_proof(config,collection,proof);raise SourceStopped('source_error',f'MedAdvisor HTTP {code}')
            actual_query=dict(parse_qsl(urlsplit(page.url).query))
            if site_domain(page.url)!='medadvisor.ru' or urlsplit(page.url).path!=urlsplit(base).path or actual_query.get('page')!=str(number):
                _save_proof(config,collection,proof);raise SourceStopped('catalog_page_mismatch',f'Запрошена страница {number}, получен URL {page.url}')
            if not snapshot['cards'] and not snapshot['pagination']:
                _save_proof(config,collection,proof);return
            if snapshot['active_page']!=number:
                _save_proof(config,collection,proof);raise SourceStopped('catalog_page_unverified',f'Запрошена страница {number}; активная страница: {snapshot["active_page"]}')
            candidates=[]
            for row in snapshot['cards']:
                if site_domain(row['url'])=='medadvisor.ru' and re.fullmatch(config['link_re'],row['url']):
                    url=db.canonical_url(row['url'])
                    collection.catalog_metadata[url]=dict(name=row['name'],city=row['city'],catalog_page=number,catalog_url=page.url)
                    if url not in seen:seen.add(url);candidates.append(url)
            signature=frozenset(db.canonical_url(r['url']) for r in snapshot['cards'])
            if signature in signatures or not candidates:
                _save_proof(config,collection,proof);raise SourceStopped('pagination_stalled')
            signatures.add(signature)
            next_link=next((a for a in snapshot['pagination'] if a['text']=='Далее' or a.get('rel')=='next'),None)
            if next_link:
                next_url=next_link['url'];p=urlsplit(next_url);query=dict(parse_qsl(p.query))
                if site_domain(next_url)!='medadvisor.ru' or p.path!=urlsplit(base).path or query.get('page')!=str(number+1):
                    _save_proof(config,collection,proof);raise SourceStopped('invalid_next_page','Ожидалась следующая страница каталога '+str(number+1))
                proof['next_url']=next_url
            proof['verified']=True
            _save_proof(config,collection,proof)
            print(f"MedAdvisor: страница каталога {number}; карточек: {len(candidates)}; URL: {page.url}",flush=True)
            yield from candidates
            if not next_link:return
            number+=1;target=proof['next_url']
            time.sleep(REQUEST_DELAY)
    finally:
        context.close()
