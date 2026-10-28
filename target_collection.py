"""Collect N usable organizations; scoring remains descriptive, never an admission gate."""
import csv
import json
import re
import time
from collections import Counter
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
import db
from lead_scoring import excel_text, site_domain


class SourceStopped(RuntimeError):
    def __init__(self,reason,detail=''):
        self.reason,self.detail=reason,detail
        super().__init__(detail or reason)


def source_config(config):
    config=dict(config)
    medical=config['name'].strip().casefold()=='медицинские клиники'
    config['source']='medadvisor' if medical else 'orgpage'
    config['check_sites']=True
    if medical:
        config['link_re']=r'https?://(?:www\.)?medadvisor\.ru/[a-z0-9-]+/clinic/\d+'
        config.setdefault('input_csv',str(db.ROOT/'medadvisor.csv'))
        config.setdefault('start_page',1)
    else:
        config.pop('input_csv',None)
        config['link_re']=r'https?://(?:www\.)?orgpage\.ru/[a-z0-9-]+/[a-z0-9-]+-\d+\.html'
        if 'orgpage.ru' not in (urlsplit(config.get('search_url','')).hostname or ''):
            config['search_url']='https://www.orgpage.ru/search.html?'+urlencode({'q':config['name'],'loc':'Россия','forReplies':'false'})
        parts=urlsplit(config['search_url'])
        if not parts.path.startswith('/rossiya/'):
            query=dict(parse_qsl(parts.query));query['loc']='Россия'
            config['search_url']=urlunsplit(parts._replace(query=urlencode(query)))
    return config


class Collection:
    def __init__(self,target,max_cards,*,require_same_site_domain=False,require_site_topic=False):
        if type(target) is not int or target<=0 or type(max_cards) is not int or max_cards<=0:
            raise ValueError('N и лимит карточек должны быть положительными целыми числами')
        self.target,self.max_cards=target,max_cards
        self.require_same_site_domain=require_same_site_domain
        self.require_site_topic=require_site_topic
        self.accepted=[];self.domains=set();self.identities=set()
        self.stats={};self.run_id=None
        self.catalog_metadata={}
        self.industry=None

    def begin(self,stats,run_id):
        self.stats,self.run_id=stats,run_id
        stats.update(target=self.target,initial_buffer=min(200,2*self.target,self.max_cards),max_cards=self.max_cards,
                     cards_reviewed=0,accepted=0,rejected=0,rejected_by_reason={},all_rejection_reasons={},
                     source_batches=0,source_links_buffered=0,source_pages=0,target_achieved=False,stop_reason=None)
        stats.update(getattr(self,'resume_stats',{}))
        with db.get_connection() as c:
            c.execute('''CREATE TABLE IF NOT EXISTS collection_decisions (
                id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES runs(id),
                company_url TEXT NOT NULL, decided_at TEXT NOT NULL, accepted INTEGER NOT NULL,
                reasons_json TEXT NOT NULL, details_json TEXT NOT NULL)''')

    def precheck(self,row,fetch_status,geography_ok):
        reasons=[row['_rejection_reason']] if row.get('_rejection_reason') else []
        if fetch_status!='ok':reasons.append('card_error')
        if not geography_ok:reasons.append('outside_geography')
        if not db.clean(row.get('Название')) or str(row.get('Название','')).strip().isdigit():reasons.append('name_missing')
        if not site_domain(row.get('Сайт')):reasons.append('site_missing_or_invalid')
        if not (db.clean(row.get('Телефон')) or db.clean(row.get('Email'))):reasons.append('contacts_missing')
        if (db.canonical_url(row['URL агрегатора']) in self.identities or
                site_domain(row.get('Сайт')) in self.domains):reasons.append('duplicate_organization')
        return reasons

    def rejection_reasons(self,row,fetch_status,geography_ok):
        reasons=self.precheck(row,fetch_status,geography_ok)
        check=row.get('Проверка сайта') or {}
        protected = (check.get('availability') in ('captcha','blocked') and
                     (check.get('ownership_details') or {}).get('protection_page_confirmed') is True)
        aliases={d for d in (site_domain(row.get('Сайт')),site_domain(check.get('final_url'))) if d}
        if not reasons:
            if check.get('availability')!='available' and not protected:reasons.append('site_unavailable_or_unchecked')
            elif (check.get('brand_status') or check.get('ownership'))=='mismatch':reasons.append('site_identity_mismatch')
            elif self.require_same_site_domain and site_domain(check.get('final_url'))!=site_domain(row.get('Сайт')):
                reasons.append('site_redirected_to_other_domain')
            elif self.require_site_topic and not protected and not (check.get('ownership_details') or {}).get('industry_check',{}).get('matched'):
                reasons.append('industry_not_confirmed_on_site')
            elif aliases & self.domains:reasons.append('duplicate_organization')
        return reasons

    def consider(self,row,fetch_status,geography_ok):
        reasons=self.rejection_reasons(row,fetch_status,geography_ok)
        check=row.get('Проверка сайта') or {}
        aliases={d for d in (site_domain(row.get('Сайт')),site_domain(check.get('final_url'))) if d}
        self.stats['cards_reviewed']+=1
        accepted=not reasons
        if accepted:
            self.accepted.append(row)
            self.domains.update(aliases)
            self.identities.add(db.canonical_url(row['URL агрегатора']))
            self.stats['accepted']+=1
        else:
            self.stats['rejected']+=1
            primary=Counter(self.stats['rejected_by_reason']);primary[reasons[0]]+=1
            all_reasons=Counter(self.stats['all_rejection_reasons']);all_reasons.update(reasons)
            self.stats['rejected_by_reason']=dict(primary);self.stats['all_rejection_reasons']=dict(all_reasons)
        source_query=self.catalog_metadata.get(db.canonical_url(row['URL агрегатора']),{}).get('_source_query')
        if source_query is not None:
            query_stats=self.stats['queries'][source_query]
            query_stats['reviewed']+=1
            query_stats['accepted' if accepted else 'rejected']+=1
        details={k:row.get(k) for k in ('Название','Сайт','Телефон','Email','Город','Регион','Адрес')}
        if source_query is not None:details.update(source_query=source_query)
        details.update(availability=check.get('availability'),brand_status=check.get('brand_status') or check.get('ownership'),error=check.get('error'),fetch_status=fetch_status)
        with db.get_connection() as c:
            c.execute('INSERT INTO collection_decisions(run_id,company_url,decided_at,accepted,reasons_json,details_json) VALUES (?,?,?,?,?,?)',
                      (self.run_id,row['URL агрегатора'],db.now(),int(accepted),json.dumps(reasons,ensure_ascii=False),json.dumps(details,ensure_ascii=False)))
        return accepted

    def finish(self,reason=None):
        self.stats['target_achieved']=len(self.accepted)>=self.target
        self.stats['stop_reason']='target_reached' if self.stats['target_achieved'] else reason or self.stats.get('stop_reason') or 'source_exhausted'


def _medadvisor_urls(config):
    from geography import match,priority
    records=db.csv_records(config.get('input_csv',db.ROOT/'medadvisor.csv'))
    eligible=[url for url in records if re.fullmatch(config['link_re'],url) and match(records[url],config.get('geography'))[0]]
    yield from sorted(eligible,key=lambda url:priority(records[url],config.get('geography')))


def _orgpage_urls(browser,config,collection,source_context=None):
    from engine import is_company_link,orgpage_context,orgpage_access
    from industries import MAX_PAGES,NEXT_SELECTORS,REQUEST_DELAY
    geo=config.get('geography')
    locations=geo.get('cities',[])+geo.get('regions',[]) if geo is not None else ['Россия']
    category=urlsplit(config['search_url']).path.startswith('/rossiya/')
    if category:locations=['Россия']  # Card geography still applies; category itself is nationwide.
    seen=set();signatures=set()
    context=source_context or orgpage_context(browser)
    page=context.new_page();last_status=[None]
    ajax={'http_status':None,'url':None,'error':None}
    stage='catalog_navigation';page_number=1
    def is_catalog_request(url):
        return site_domain(url)=='orgpage.ru' and urlsplit(url).path.rstrip('/')=='/Search/GetSearchCompany'
    def request_failed(request):
        if is_catalog_request(request.url):ajax.update(url=request.url,error=request.failure)
    def response_received(response):
        if response.request.is_navigation_request() and response.frame==page.main_frame:last_status[0]=response.status
        if is_catalog_request(response.url):ajax.update(http_status=response.status,url=response.url)
    page.on('response',response_received)
    page.on('requestfailed',request_failed)
    try:
        for location in locations:
            page_number=1;stage='catalog_navigation'
            parts=urlsplit(config['search_url']);query=dict(parse_qsl(parts.query))
            if not category:query['loc']=location
            url=urlunsplit(parts._replace(query=urlencode(query)))
            if collection.stats['source_pages']>=config.get('max_pages',MAX_PAGES):raise SourceStopped('safety_page_limit')
            response=page.goto(url,timeout=30000,wait_until='domcontentloaded')
            last_status[0]=response.status if response else None
            if last_status[0] in (401,403,429,444):orgpage_access(page,last_status[0],'catalog')
            time.sleep(3)
            while True:
                collection.stats['source_pages']+=1
                stage='catalog_read'
                diagnostic=orgpage_access(page,last_status[0],'catalog')
                if config.get('catalog_title') and config['catalog_title'].casefold() not in page.title().casefold():
                    raise SourceStopped('source_error','Каталог не соответствует отраслевой рубрике: '+page.title())
                diagnostic.update(page_number=page_number,ajax_http_status=ajax['http_status'],ajax_url=ajax['url'])
                if config.get('_source_query') is not None:
                    diagnostic.update(search_query=config['_source_query'],direction=config['_source_direction'])
                    collection.stats['queries'][config['_source_query']]['pages']+=1
                collection.stats.setdefault('source_diagnostics',[]).append(diagnostic)
                links=page.eval_on_selector_all('a','elements => elements.map(e => e.href)')
                candidates=[db.canonical_url(u) for u in links if site_domain(u)=='orgpage.ru' and is_company_link(u,config['link_re'])]
                diagnostic['cards_found']=len(candidates)
                print('Orgpage catalog: '+json.dumps(diagnostic,ensure_ascii=False),flush=True)
                if not candidates:
                    next_element=next((element for selector in NEXT_SELECTORS+[f'a:text-is("{page_number+1}")'] if (element:=page.query_selector(selector)) is not None and element.is_visible()),None)
                    if next_element is not None:raise SourceStopped('pagination_stalled','Пустая страница с переходом далее')
                    break
                signature=frozenset(candidates)
                if signature in signatures or all(url in seen for url in candidates):raise SourceStopped('pagination_stalled')
                signatures.add(signature)
                for url in candidates:
                    if url not in seen:
                        seen.add(url);yield url
                next_element=next((element for selector in NEXT_SELECTORS+[f'a:text-is("{page_number+1}")'] if (element:=page.query_selector(selector)) is not None and element.is_visible()),None)
                if next_element is None:break
                if collection.stats['source_pages']>=config.get('max_pages',MAX_PAGES):raise SourceStopped('safety_page_limit')
                stage='pagination_navigation'
                page_number+=1
                ajax.update(http_status=None,url=None,error=None)
                href=next_element.get_attribute('href')
                time.sleep(REQUEST_DELAY)
                if href and not href.startswith(('#','javascript:')):
                    target=urljoin(page.url,href)
                    if site_domain(target)!='orgpage.ru':raise SourceStopped('unsafe_pagination_link')
                    response=page.goto(target,timeout=30000,wait_until='domcontentloaded')
                    last_status[0]=response.status if response else None
                else:
                    # Angular pagination updates cards via XHR, without a document response.
                    next_element.click(timeout=10000)
                stage='pagination_wait'
                if last_status[0] in (401,403,429,444):orgpage_access(page,last_status[0],stage)
                time.sleep(2)
                from playwright.sync_api import TimeoutError as PlaywrightTimeout
                try:
                    page.wait_for_function(r"""previous => {
                        const current = [...document.querySelectorAll('a')].map(a => {
                            try { const u = new URL(a.href);
                                return u.hostname.replace(/^www\./, '') === 'orgpage.ru' &&
                                    /\/[a-z0-9-]+\/[a-z0-9-]+-\d+\.html$/i.test(u.pathname)
                                    ? 'https://orgpage.ru' + u.pathname : null;
                            } catch (_) { return null; }
                        }).filter(Boolean);
                        return current.length > 0 && JSON.stringify(current) !== JSON.stringify(previous);
                    }""",arg=candidates,timeout=20000)
                except PlaywrightTimeout:
                    if ajax['http_status'] in (401,403,429,444):orgpage_access(page,ajax['http_status'],stage)
                    raise
                if ajax['http_status'] is not None:
                    orgpage_access(page,ajax['http_status'],stage)
                if ajax['error']:
                    raise RuntimeError('Catalog AJAX failed: '+str(ajax['error']))
        return
    except Exception as exc:
        detail=dict(stage=stage,url=page.url,page_number=page_number,http_status=last_status[0],
                    ajax_http_status=ajax['http_status'],ajax_url=ajax['url'],ajax_error=ajax['error'],
                    exception=type(exc).__name__,error=str(exc))
        if config.get('_source_query') is not None:detail.update(search_query=config['_source_query'],direction=config['_source_direction'])
        if isinstance(exc,SourceStopped):
            detail['cause']=exc.detail
            reason=exc.reason
        else:
            reason='source_error'
        serialized=json.dumps(detail,ensure_ascii=False)
        print('Orgpage stopped: '+serialized,flush=True)
        raise SourceStopped(reason,serialized) from exc
    finally:
        if source_context is None:context.close()
        else:page.close()


def _orgpage_queries(browser,config,collection,source_context=None):
    """Use the same Orgpage generator, sharing context and one global target."""
    from engine import orgpage_context
    context=source_context or orgpage_context(browser)
    iterators=[];seen=set()
    batch_size=config.get('query_batch_size',10)
    if type(batch_size) is not int or batch_size<1:raise ValueError('query_batch_size must be positive')
    collection.stats['queries']={item['query']:dict(direction=item['name'],pages=0,discovered=0,
        duplicate_links=0,reviewed=0,accepted=0,rejected=0,exhausted=False) for item in config['search_queries']}
    try:
        for item in config['search_queries']:
            local=dict(config)
            parts=urlsplit(config['search_url']);query=dict(parse_qsl(parts.query));query['q']=item['query']
            local.update(search_url=urlunsplit(parts._replace(query=urlencode(query))),
                         _source_query=item['query'],_source_direction=item['name'])
            iterators.append((item['query'],_orgpage_urls(browser,local,collection,context)))
        while iterators:
            remaining=[]
            for query,iterator in iterators:
                exhausted=False
                for _ in range(batch_size):
                    try:url=next(iterator)
                    except StopIteration:
                        exhausted=True;collection.stats['queries'][query]['exhausted']=True;break
                    if url in seen:
                        collection.stats['queries'][query]['duplicate_links']+=1
                        continue
                    seen.add(url)
                    collection.stats['queries'][query]['discovered']+=1
                    collection.catalog_metadata.setdefault(url,{})['_source_query']=query
                    yield url
                if not exhausted:remaining.append((query,iterator))
            iterators=remaining
    finally:
        for _,iterator in iterators:iterator.close()
        if source_context is None:context.close()


def card_stream(config,target,browser,collection,source_context=None):
    """Keep the cursor and unused page links; refill only after the current 2N buffer."""
    live_med=config['source']=='medadvisor' and config.get('start_page') is not None
    if live_med:
        from medadvisor_catalog import urls
        iterator=urls(browser,config,collection)
        collection.stats.update(source_scope='catalog_search_pages',medadvisor_start_page=config['start_page'])
    else:
        iterator=_medadvisor_urls(config) if config['source']=='medadvisor' else (_orgpage_queries(browser,config,collection,source_context) if config.get('search_queries') else _orgpage_urls(browser,config,collection,source_context))
        collection.stats['source_scope']='input_csv' if config['source']=='medadvisor' else 'catalog_search_pages'
        if config['source']=='medadvisor':collection.stats['source_input']=str(config.get('input_csv',db.ROOT/'medadvisor.csv'))
    size=min(200,2*target,collection.max_cards)
    reviewed=config.get('_reviewed_urls',set())
    try:
        while True:
            buffer=[];stopped=None
            for _ in range(size):
                try:
                    url=next(iterator)
                    while db.canonical_url(url) in reviewed:url=next(iterator)
                    buffer.append(url)
                except StopIteration:stopped=SourceStopped('source_exhausted');break
                except SourceStopped as exc:stopped=exc;break
                except Exception as exc:stopped=SourceStopped('source_error',str(exc));break
            if stopped and stopped.reason=='access_restricted':raise stopped
            if buffer:
                collection.stats['source_batches']+=1
                collection.stats['source_links_buffered']+=len(buffer)
                yield from buffer
            if stopped:raise stopped
    finally:
        iterator.close()


CSV_FIELDS=('Организация','Официальный сайт','Телефон','Email','Город','Регион','Адрес','Исходная карточка',
            'Доступность сайта','Бренд','Филиал','Статус скрининга','Рейтинг','Полнота проверки','Дата проверки')


RATING_CRITERIA=(
    ('analytics_before_consent','Аналитика до согласия'),
    ('cookie_banner_absent','Cookie-баннер не обнаружен'),
    ('policy_link_absent','Ссылка на политику ПДн не обнаружена'),
    ('form_without_consent','Форма без видимого согласия'),
    ('ga_measurement','GA/GTM с измерительным запросом'),
    ('brand_confirmed','Официальный сайт бренда подтверждён'))
RATING_FIELDS=tuple(label for _,label in RATING_CRITERIA)+(
    'Расшифровка рейтинга','Причины неопределённости','Ошибки скрининга')


def saved_export_scores():
    """Read persisted scores and the exact screening attempt; never recalculate."""
    import sqlite3
    from contextlib import closing
    with closing(sqlite3.connect(Path(db.DB_FILE).resolve().as_uri()+'?mode=ro',uri=True)) as conn:
        rows=conn.execute('''SELECT l.company_url,l.details_json,q.errors_json,s.error
            FROM lead_scores l
            LEFT JOIN site_checks s ON s.id=l.site_check_id AND s.company_url=l.company_url
            LEFT JOIN quick_screens q ON q.site_check_id=s.id AND q.company_url=l.company_url''').fetchall()
    result={}
    for url,details,errors,error in rows:
        score=json.loads(details)
        score['_screen_errors']=json.loads(errors or '[]')
        if error and error not in score['_screen_errors']:
            score['_screen_errors'].append(error)
        result[db.canonical_url(url)]=score
    return result


def rating_export_values(score):
    criteria=score.get('criteria') or {}
    result={label:criteria.get(rule,'unknown') if criteria.get(rule) in ('yes','no','unknown') else 'unknown'
            for rule,label in RATING_CRITERIA}
    result['Расшифровка рейтинга']=' | '.join(
        f"+{reason['points']}: {reason['reason']}" for reason in score.get('reasons',[]))
    result['Причины неопределённости']=json.dumps(score.get('unknown_reasons',{}),ensure_ascii=False)
    result['Ошибки скрининга']=json.dumps(score.get('_screen_errors',[]),ensure_ascii=False)
    return {key:excel_text(value) for key,value in result.items()}


def extend_saved_csv(filename):
    """Append saved diagnostics while preserving every existing cell and row."""
    filename=Path(filename)
    with filename.open(encoding='utf-8-sig',newline='') as handle:
        reader=csv.DictReader(handle,delimiter=';')
        fields=list(reader.fieldnames or [])
        rows=list(reader)
    scores=saved_export_scores()
    for row in rows:
        score=scores.get(db.canonical_url(row['Исходная карточка']))
        if score is None:
            raise ValueError('Нет сохранённого рейтинга: '+row['Исходная карточка'])
        if str(score.get('priority_score','')) != row['Рейтинг']:
            raise ValueError('Сохранённый рейтинг отличается от CSV: '+row['Исходная карточка'])
        additions=rating_export_values(score)
        row.update({key:value for key,value in additions.items() if key not in fields})
    temporary=filename.with_suffix(filename.suffix+'.tmp')
    with temporary.open('w',encoding='utf-8-sig',newline='') as handle:
        writer=csv.DictWriter(handle,fields+[key for key in RATING_FIELDS if key not in fields],delimiter=';')
        writer.writeheader();writer.writerows(rows)
    temporary.replace(filename)
    return len(rows)


def accumulated_records(collection):
    """Read all usable saved rows of this industry, without checks or score updates."""
    import sqlite3
    from contextlib import closing
    from lead_scoring import latest_check
    if not collection.industry:raise ValueError('Industry is required for accumulated export')
    rules=Collection(1,1,require_same_site_domain=collection.require_same_site_domain,
                     require_site_topic=collection.require_site_topic)
    records=[]
    with closing(sqlite3.connect(Path(db.DB_FILE).resolve().as_uri()+'?mode=ro',uri=True)) as conn:
        conn.row_factory=sqlite3.Row
        companies=conn.execute('SELECT * FROM companies WHERE industry=? ORDER BY rowid',(collection.industry,)).fetchall()
        for saved in companies:
            company=dict(saved)
            check=latest_check(conn,company)
            if not check:continue
            check['ownership_details']=json.loads(check.get('ownership_details_json') or '{}')
            row={'Название':company['name'],'Сайт':company['site'],'Телефон':company['phone'],
                 'Email':company['email'],'Город':company.get('city'),'Регион':company.get('region'),
                 'Адрес':company.get('address'),'URL агрегатора':company['url'],'Проверка сайта':check}
            if rules.rejection_reasons(row,'ok',True):continue
            records.append(row)
            rules.domains.update(host for value in (company['site'],check.get('final_url')) if (host:=site_domain(value)))
            rules.identities.add(db.canonical_url(company['url']))
    return records


def export_results(collection,filename):
    filename=Path(filename);filename.parent.mkdir(parents=True,exist_ok=True)
    scores=saved_export_scores()
    records=accumulated_records(collection)
    collection.stats['exported_total']=len(records)
    temporary=filename.with_suffix(filename.suffix+'.tmp')
    with temporary.open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,CSV_FIELDS+RATING_FIELDS,delimiter=';');writer.writeheader()
        for row in records:
            check=row['Проверка сайта'];score=scores.get(db.canonical_url(row['URL агрегатора']),{})
            values=(row['Название'],row['Сайт'],row['Телефон'],row['Email'],row.get('Город'),row.get('Регион'),row.get('Адрес'),row['URL агрегатора'],
                    check['availability'],check.get('brand_status') or check.get('ownership'),check.get('branch_status','unknown'),
                    (check.get('quick_screen') or {}).get('status','unknown'),score.get('priority_score'),score.get('evidence_coverage'),check.get('checked_at'))
            output={k:excel_text(v) for k,v in zip(CSV_FIELDS,values)}
            output.update(rating_export_values(score))
            writer.writerow(output)
    temporary.replace(filename)
    summary=filename.with_suffix('.summary.json')
    summary.write_text(json.dumps(collection.stats,ensure_ascii=False,indent=2),encoding='utf-8')
    return str(filename)


def resume_state(config):
    """Read an isolated industry's durable decisions; never recheck saved cards."""
    import sqlite3
    from contextlib import closing
    if config.get('geography') or config.get('search_queries'):
        raise ValueError('Resume поддерживает отдельный общероссийский каталог одной отрасли')
    with closing(sqlite3.connect(Path(db.DB_FILE).resolve().as_uri()+'?mode=ro',uri=True)) as conn:
        industries={row[0] for row in conn.execute('SELECT DISTINCT industry FROM companies')}
        if industries!={config['name']}:
            raise ValueError('Resume требует БД только выбранной отрасли; смешивать отрасли нельзя')
        reviewed={db.canonical_url(row[0]) for row in conn.execute('SELECT company_url FROM collection_decisions')}
        histories=[json.loads(row[0]) for row in conn.execute('SELECT stats_json FROM runs WHERE stats_json IS NOT NULL ORDER BY id')]
    rules=Collection(1,1,require_same_site_domain=config.get('require_same_site_domain',False),require_site_topic=bool(config.get('site_topic_terms')))
    rules.industry=config['name']
    initial=len(accumulated_records(rules))
    start=None
    if config['source']=='medadvisor':
        proofs={proof['requested_page']:proof for history in histories for proof in history.get('catalog_pages',[]) if proof.get('verified')}
        if not proofs:raise ValueError('Нет проверенного курсора MedAdvisor для безопасного resume')
        start=min(proofs)
        while start in proofs:
            proof=proofs[start]
            # Buffered pages may never have reached process_cards: replay the first incomplete page.
            if any(db.canonical_url(url) not in reviewed for url in proof['cards']) or not proof.get('next_url'):break
            start+=1
    return dict(initial=initial,reviewed=reviewed,start_page=start)


def run_target(config,target):
    import engine
    config=source_config(config)
    config.setdefault('max_pages',200)
    if type(config['max_pages']) is not int or not 1<=config['max_pages']<=1000:
        raise ValueError('max_pages должен быть от 1 до 1000')
    resume=resume_state(config) if config.get('resume') else None
    goal=target
    if resume:
        target=max(0,goal-resume['initial'])
        config['_reviewed_urls']=resume['reviewed']
        if resume['start_page'] is not None:config['start_page']=resume['start_page']
        print(f"Resume: сохранено пригодных {resume['initial']}; итоговая цель {goal}; добрать {target}; ранее просмотрено {len(resume['reviewed'])}")
        if resume['start_page'] is not None:print('Продолжение MedAdvisor со страницы:',resume['start_page'])
    collection=Collection(max(1,target),config.get('max_cards',max(200,10*target)),require_same_site_domain=config.get('require_same_site_domain',False),require_site_topic=bool(config.get('site_topic_terms')))
    if resume:
        collection.resume_stats=dict(goal_total=goal,resume_initial=resume['initial'],resume_reviewed=len(resume['reviewed']))
        if target==0:
            collection.industry=config['name']
            collection.stats=dict(collection.resume_stats,target=0,accepted=0,cards_reviewed=0,rejected=0,
                                  rejected_by_reason={},target_achieved=True,stop_reason='target_already_reached')
            export_results(collection,config['output'])
            print('Итоговая цель уже достигнута; сетевые запросы не выполнялись. CSV:',config['output'])
            return [],collection.stats
    db.init_db(refresh_existing=False)
    try:
        engine.enrich_and_check(None,config,target_count=target,collection=collection)
    finally:
        if collection.stats:
            export_results(collection,config['output'])
            print(f"Карточек просмотрено: {collection.stats['cards_reviewed']}; принято: {collection.stats['accepted']} / {target}; отбраковано: {collection.stats['rejected']}")
            print('Отбраковка по основной причине:',json.dumps(collection.stats['rejected_by_reason'],ensure_ascii=False))
            print('Цель достигнута:', 'да' if collection.stats['target_achieved'] else 'нет')
            print('Причина завершения:',collection.stats['stop_reason'])
            if collection.stats.get('stop_detail'):print('Подробности остановки:',collection.stats['stop_detail'])
            print('Всего пригодных в отраслевом CSV:',collection.stats.get('exported_total',0))
            print('CSV:',config['output'])
    return collection.accepted,collection.stats
