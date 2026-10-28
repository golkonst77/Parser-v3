"""SQLite storage and additive v3 migration. Original URL remains the identifier."""
import csv
import json
import re
import sqlite3
from contextlib import nullcontext
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent
DB_FILE = str(ROOT / 'parser_v2.db')

SCREEN_FEATURES = ('cookie_banner','cookie_accept','cookie_reject','cookie_settings',
                   'metrika','metrika_requests','ga_gtm','ga_gtm_requests',
                   'other_analytics','analytics_requests','forms','form_consent',
                   'forms_without_consent','policy_link')

def now():
    return datetime.now(timezone.utc).isoformat()

def clean(value):
    if value is None or str(value).strip() in ('', 'Не найден', 'Нет сайта'):
        return None
    return str(value).strip()

def canonical_url(value):
    p = urlsplit(value.strip())
    host = (p.hostname or '').lower().removeprefix('www.')
    # These directories use the path as card identity; queries are tracking/search parameters.
    if host in ('medadvisor.ru', 'orgpage.ru'):
        return urlunsplit(('https', host, p.path.rstrip('/'), '', ''))
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip('/'), p.query, ''))

def domain(site):
    site = clean(site)
    if not site:
        return None
    return (urlsplit(site if '://' in site else 'https://' + site).hostname or '').lower().removeprefix('www.') or None

def csv_records(filename=None):
    filename = Path(filename or ROOT / 'medadvisor.csv')
    records = {}
    with filename.open(encoding='utf-8-sig', newline='') as f:
        for row in csv.DictReader(f):
            url = clean(row.get('link href'))
            if url:
                key = canonical_url(url)
                item = {'name': clean(row.get('clinic-card__title')), 'city': clean(row.get('clinic-card__city'))}
                if key in records and records[key] != item:
                    raise ValueError('Conflicting CSV identity: ' + key)
                records[key] = item
    return records

class ClosingConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()

def get_connection():
    conn = sqlite3.connect(DB_FILE, timeout=30, factory=ClosingConnection)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    conn.execute('PRAGMA journal_mode=WAL')
    return conn

def init_db(csv_path=None, *, refresh_existing=True):
    # The default medical CSV is optional migration data, not a schema dependency.
    # Explicit inputs (and csv_records itself) remain strict about missing files.
    records = csv_records(csv_path) if csv_path is not None or (refresh_existing and (ROOT / 'medadvisor.csv').is_file()) else {}
    with get_connection() as c:
        c.execute('CREATE TABLE IF NOT EXISTS companies (url TEXT PRIMARY KEY, industry TEXT NOT NULL, name TEXT, phone TEXT, email TEXT, site TEXT, site_status TEXT, source_url TEXT, created_at TEXT, updated_at TEXT)')
        c.execute('CREATE TABLE IF NOT EXISTS migration_originals (url TEXT PRIMARY KEY, original_json TEXT NOT NULL)')
        fields = {r['name'] for r in c.execute('PRAGMA table_info(companies)')}
        for field in ('city','region','source','canonical_url','normalized_domain','fetch_status','data_status','last_error','last_attempt_at','contact_status','address','brand'):
            if field not in fields:
                c.execute(f'ALTER TABLE companies ADD COLUMN {field} TEXT')
        c.execute('CREATE TABLE IF NOT EXISTS card_keys (canonical_url TEXT PRIMARY KEY, company_url TEXT NOT NULL)')
        c.execute('CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY, started_at TEXT, finished_at TEXT, status TEXT, stats_json TEXT)')
        c.execute('CREATE TABLE IF NOT EXISTS errors (id INTEGER PRIMARY KEY, run_id INTEGER, url TEXT, occurred_at TEXT, message TEXT)')
        for r in c.execute('SELECT rowid AS original_rowid, * FROM companies').fetchall():
            if not refresh_existing:
                c.execute('INSERT OR IGNORE INTO card_keys VALUES (?,?)',(canonical_url(r['url']),r['url']))
                continue
            c.execute('INSERT OR IGNORE INTO migration_originals VALUES (?,?)', (r['url'], json.dumps(dict(r),ensure_ascii=False)))
            key = canonical_url(r['url'])
            source = urlsplit(key).hostname
            c.execute('INSERT OR IGNORE INTO card_keys VALUES (?,?)',(key,r['url']))
            c.execute('UPDATE companies SET canonical_url=?, normalized_domain=?, source=COALESCE(source,?), data_status=COALESCE(data_status,?) WHERE url=?', (key,domain(r['site']), source, 'incomplete' if source == 'orgpage.ru' or not clean(r['name']) else 'complete',r['url']))
            ref = records.get(key)
            if ref and (not clean(r['name']) or r['name'].strip().isdigit()) and ref['name']:
                c.execute('UPDATE companies SET name=?, city=?, data_status=?, updated_at=? WHERE url=?',(ref['name'],ref['city'],'complete',now(),r['url']))
        c.execute('CREATE INDEX IF NOT EXISTS companies_domain ON companies(normalized_domain)')
        c.execute('''CREATE TABLE IF NOT EXISTS site_checks (
            id INTEGER PRIMARY KEY, company_url TEXT NOT NULL, checked_url TEXT NOT NULL,
            final_url TEXT, availability TEXT NOT NULL, ownership TEXT NOT NULL,
            checked_at TEXT NOT NULL, http_status INTEGER, error TEXT,
            duration_ms INTEGER, evidence_json TEXT, attempt INTEGER NOT NULL)''')
        check_fields={r['name'] for r in c.execute('PRAGMA table_info(site_checks)')}
        for field in ('brand_status','branch_status','ownership_details_json'):
            if field not in check_fields: c.execute(f'ALTER TABLE site_checks ADD COLUMN {field} TEXT')
        c.execute('''CREATE TABLE IF NOT EXISTS site_check_state (
            company_url TEXT NOT NULL, checked_url TEXT NOT NULL,
            latest_check_id INTEGER NOT NULL, last_success_id INTEGER,
            PRIMARY KEY(company_url, checked_url))''')
        c.execute('CREATE INDEX IF NOT EXISTS site_checks_company ON site_checks(company_url, checked_at)')
        feature_columns = ','.join(f"{field} TEXT NOT NULL CHECK({field} IN ('yes','no','unknown'))"
                                   for field in SCREEN_FEATURES)
        c.execute('''CREATE TABLE IF NOT EXISTS quick_screens (
            id INTEGER PRIMARY KEY, site_check_id INTEGER NOT NULL UNIQUE REFERENCES site_checks(id),
            company_url TEXT NOT NULL REFERENCES companies(url), checked_url TEXT NOT NULL,
            final_url TEXT, checked_at TEXT NOT NULL, status TEXT NOT NULL,
            duration_ms INTEGER NOT NULL, errors_json TEXT NOT NULL, details_json TEXT NOT NULL,
            ''' + feature_columns + ')')
        c.execute('''CREATE TABLE IF NOT EXISTS quick_screen_features (
            company_url TEXT NOT NULL, checked_url TEXT NOT NULL, feature TEXT NOT NULL,
            value TEXT NOT NULL CHECK(value IN ('yes','no')),
            screen_id INTEGER NOT NULL REFERENCES quick_screens(id), checked_at TEXT NOT NULL,
            PRIMARY KEY(company_url,checked_url,feature))''')
        c.execute('CREATE INDEX IF NOT EXISTS quick_screens_company ON quick_screens(company_url, checked_url, id)')
        from lead_scoring import ensure_schema
        ensure_schema(c)
        c.execute('PRAGMA user_version=3')

def add_or_update_company(url, industry, name, phone, email, site, site_status, source_url='', *, city=None, region=None, address=None, brand=None, source=None, fetch_status='ok', error=None, data_status=None, contact_status=None, clear_fields=()):
    """Unknown values preserve previous evidence. clear_fields explicitly confirms removal."""
    key = canonical_url(url)
    stamp = now()
    with get_connection() as c:
        c.execute('INSERT OR IGNORE INTO card_keys VALUES (?,?)',(key,url))
        identity = c.execute('SELECT company_url FROM card_keys WHERE canonical_url=?',(key,)).fetchone()[0]
        old = c.execute('SELECT * FROM companies WHERE url=?',(identity,)).fetchone()
        values = dict(industry=industry,name=clean(name),phone=clean(phone),email=clean(email),site=clean(site),site_status=clean(site_status),source_url=clean(source_url),city=clean(city),region=clean(region),address=clean(address),brand=clean(brand),source=source or urlsplit(key).hostname)
        if old:
            for k in values:
                if values[k] is None or (fetch_status != 'ok' and k in ('phone','email','site','site_status')):
                    values[k] = old[k]
            if fetch_status == 'ok' and clean(site) and site != old['site'] and not clean(site_status):
                # A successful check of a previous domain cannot validate a newly found site.
                values['site_status'] = 'Не проверен'
        for k in clear_fields:
            if k not in ('phone','email','site') or fetch_status != 'ok':
                raise ValueError('Removal requires successful retrieval and a contact field')
            values[k] = None
        values['normalized_domain'] = domain(values['site'])
        values['data_status'] = data_status or ('complete' if values['name'] else 'incomplete')
        changed = old is None or any(old[k] != v for k,v in values.items())
        values.update(fetch_status=fetch_status,last_error=error,last_attempt_at=stamp,contact_status=contact_status or ('error' if fetch_status != 'ok' else 'found' if any(values[k] for k in ('phone','email','site')) else 'absent'))
        if old is None:
            values.update(url=identity,canonical_url=key,created_at=stamp,updated_at=stamp)
            c.execute(f"INSERT INTO companies ({','.join(values)}) VALUES ({','.join('?' for _ in values)})",tuple(values.values()))
        else:
            if changed:
                values['updated_at'] = stamp
            c.execute('UPDATE companies SET '+','.join(k+'=?' for k in values)+' WHERE url=?',(*values.values(),identity))
    return 'new' if old is None else 'updated' if changed else 'unchanged'

def existing_site_domains(connection=None):
    """Compare IDNA hostnames globally; retain original site URLs unchanged."""
    from lead_scoring import site_domain
    with (nullcontext(connection) if connection is not None else get_connection()) as c:
        values=c.execute('''SELECT site FROM companies
            UNION SELECT s.checked_url FROM site_checks s JOIN companies c ON c.url=s.company_url
            UNION SELECT s.final_url FROM site_checks s JOIN companies c ON c.url=s.company_url
                WHERE s.availability='available' ''').fetchall()
    return {host for row in values if (host:=site_domain(row[0]))}


def existing_card(url,connection=None):
    with (nullcontext(connection) if connection is not None else get_connection()) as c:
        return c.execute('SELECT 1 FROM card_keys WHERE canonical_url=?',(canonical_url(url),)).fetchone() is not None


def save_new_usable_company(company,check):
    """Insert a new domain and its existing check atomically; never update old rows."""
    from lead_scoring import site_domain
    aliases={host for value in (company['site'],check.get('final_url')) if (host:=site_domain(value))}
    with get_connection() as c:
        c.execute('BEGIN IMMEDIATE')
        if aliases & existing_site_domains(c):return 'existing_domain'
        if existing_card(company['url'],c):return 'existing_organization'
        stamp=now()
        values={field:clean(company.get(field)) for field in
                ('url','industry','name','phone','email','site','source_url','city','region','address','brand','source')}
        values.update(site_status='Работает',canonical_url=canonical_url(company['url']),
                      normalized_domain=site_domain(company['site']),created_at=stamp,updated_at=stamp,
                      fetch_status='ok',data_status='complete',last_error=None,last_attempt_at=stamp,contact_status='found')
        c.execute('INSERT INTO companies ('+','.join(values)+') VALUES ('+','.join('?' for _ in values)+')',tuple(values.values()))
        c.execute('INSERT INTO card_keys VALUES (?,?)',(values['canonical_url'],values['url']))
        save_site_check(values['url'],check,connection=c)
    return 'new'


def possible_duplicates():
    """Evidence for manual review only; never merge branches."""
    with get_connection() as c:
        rows = [dict(r) for r in c.execute('SELECT * FROM companies')]
    pairs=[]
    for i,a in enumerate(rows):
        for b in rows[i+1:]:
            reasons=[]
            for field in ('canonical_url','normalized_domain','phone'):
                av,bv=clean(a[field]),clean(b[field])
                if field == 'phone':
                    av,bv=(re.sub(r'\D','',v) if v else None for v in (av,bv))
                if av and av==bv: reasons.append(field)
            if clean(a['name']) and a['name'].casefold()==(b['name'] or '').casefold() and a['city'] and a['city']==b['city']:
                reasons.append('name_city')
            if reasons: pairs.append({'urls':[a['url'],b['url']], 'reasons':reasons})
    return pairs

def start_run():
    with get_connection() as c:
        return c.execute('INSERT INTO runs(started_at,status) VALUES (?,?)',(now(),'running')).lastrowid

def save_site_check(company_url, result, *, connection=None):
    """Append every attempt; success pointer survives all unsuccessful attempts."""
    with (nullcontext(connection) if connection is not None else get_connection()) as c:
        key = c.execute('SELECT company_url FROM card_keys WHERE canonical_url=?',
                        (canonical_url(company_url),)).fetchone()
        identity = key[0] if key else company_url
        if not c.execute('SELECT 1 FROM companies WHERE url=?', (identity,)).fetchone():
            raise ValueError('Save organization before checking its site')
        fields = ('checked_url','final_url','availability','ownership','checked_at',
                  'http_status','error','duration_ms','evidence_json','attempt','brand_status','branch_status','ownership_details_json')
        values = [json.dumps(result.get('evidence', []), ensure_ascii=False)
                  if f == 'evidence_json' else json.dumps(result.get('ownership_details',{}),ensure_ascii=False)
                  if f == 'ownership_details_json' else result.get(f) for f in fields]
        check_id = c.execute('INSERT INTO site_checks(company_url,' + ','.join(fields) +
                             ') VALUES (' + ','.join('?' for _ in range(len(fields)+1)) + ')',
                             (identity, *values)).lastrowid
        success = check_id if result['availability'] == 'available' else None
        c.execute('''INSERT INTO site_check_state VALUES (?,?,?,?)
            ON CONFLICT(company_url,checked_url) DO UPDATE SET
            latest_check_id=excluded.latest_check_id,
            last_success_id=COALESCE(excluded.last_success_id,site_check_state.last_success_id)''',
            (identity,result['checked_url'],check_id,success))
        if result.get('quick_screen') is not None:
            _save_quick_screen(c, identity, check_id, result)
        from lead_scoring import ensure_schema, persist_company
        ensure_schema(c)
        persist_company(c, identity)
    return check_id

def _save_quick_screen(conn, company_url, check_id, result):
    """Same transaction as site_checks; unknown never erases a known feature."""
    screen = result['quick_screen']
    features = {field: screen.get('features',{}).get(field,'unknown') for field in SCREEN_FEATURES}
    if any(value not in ('yes','no','unknown') for value in features.values()):
        raise ValueError('Invalid quick-screen feature state')
    fields = ('site_check_id','company_url','checked_url','final_url','checked_at','status',
              'duration_ms','errors_json','details_json',*SCREEN_FEATURES)
    values = (check_id,company_url,result['checked_url'],result.get('final_url'),screen['checked_at'],
              screen['status'],screen.get('duration_ms',0),
              json.dumps(screen.get('errors',[]),ensure_ascii=False),
              json.dumps(screen.get('details',{}),ensure_ascii=False),*features.values())
    screen_id = conn.execute('INSERT INTO quick_screens(' + ','.join(fields) + ') VALUES (' +
                             ','.join('?' for _ in fields) + ')',values).lastrowid
    for feature,value in features.items():
        if value != 'unknown':
            conn.execute('''INSERT INTO quick_screen_features VALUES (?,?,?,?,?,?)
                ON CONFLICT(company_url,checked_url,feature) DO UPDATE SET
                value=excluded.value,screen_id=excluded.screen_id,checked_at=excluded.checked_at''',
                (company_url,result['checked_url'],feature,value,screen_id,screen['checked_at']))

def get_quick_screen_state(company_url, checked_url):
    """Return latest observation plus individually dated known features from history."""
    with get_connection() as conn:
        key = conn.execute('SELECT company_url FROM card_keys WHERE canonical_url=?',
                           (canonical_url(company_url),)).fetchone()
        identity = key[0] if key else company_url
        row = conn.execute('''SELECT * FROM quick_screens WHERE company_url=? AND checked_url=?
                              ORDER BY id DESC LIMIT 1''',(identity,checked_url)).fetchone()
        if not row:
            return None
        latest = dict(row)
        latest['errors'] = json.loads(latest.pop('errors_json'))
        latest['details'] = json.loads(latest.pop('details_json'))
        known = {r['feature']: dict(r) for r in conn.execute(
            'SELECT * FROM quick_screen_features WHERE company_url=? AND checked_url=?',
            (identity,checked_url))}
        return dict(latest=latest,effective=known)

def get_site_check_state(company_url, checked_url):
    with get_connection() as c:
        key = c.execute('SELECT company_url FROM card_keys WHERE canonical_url=?',
                        (canonical_url(company_url),)).fetchone()
        identity = key[0] if key else company_url
        state = c.execute('SELECT * FROM site_check_state WHERE company_url=? AND checked_url=?',
                          (identity,checked_url)).fetchone()
        if not state:
            return None
        return {label: dict(c.execute('SELECT * FROM site_checks WHERE id=?', (state[field],)).fetchone())
                if state[field] else None
                for label,field in (('latest','latest_check_id'),('last_success','last_success_id'))}

def finish_run(run_id, status, stats):
    with get_connection() as c:
        c.execute('UPDATE runs SET finished_at=?,status=?,stats_json=? WHERE id=?',(now(),status,json.dumps(stats),run_id))

def log_error(run_id,url,error):
    with get_connection() as c:
        c.execute('INSERT INTO errors(run_id,url,occurred_at,message) VALUES (?,?,?,?)',(run_id,url,now(),str(error)))

def get_stats():
    """Возвращает статистику по всем отраслям"""
    conn = get_connection()
    rows = conn.execute("""
        SELECT industry, 
               COUNT(*) as total,
               SUM(CASE WHEN site_status='Работает' THEN 1 ELSE 0 END) as working_sites,
               SUM(CASE WHEN phone != 'Не найден' AND phone != '' THEN 1 ELSE 0 END) as with_phone,
               SUM(CASE WHEN email != 'Не найден' AND email != '' THEN 1 ELSE 0 END) as with_email
        FROM companies 
        GROUP BY industry
        ORDER BY total DESC
    """).fetchall()
    conn.close()
    
    total = sum(r['total'] for r in rows)
    return rows, total

def export_industry_to_csv(industry, filename=None):
    """Экспортирует отрасль в CSV с разделителем ; для Excel"""
    conn = get_connection()
    rows = conn.execute("""
        SELECT name, url, phone, email, site, site_status, source_url, created_at, updated_at
        FROM companies 
        WHERE industry = ?
        ORDER BY name
    """, (industry,)).fetchall()
    conn.close()
    
    if filename is None:
        safe_name = industry.replace(' ', '_').replace('/', '_')
        filename = f"export_{safe_name}.csv"
    
    with open(filename, mode='w', encoding='utf-8-sig', newline='') as f:
        fieldnames = ['Название', 'URL', 'Телефон', 'Email', 'Сайт', 'Статус сайта', 'Источник', 'Добавлено', 'Обновлено']
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=';')
        writer.writeheader()
        for row in rows:
            writer.writerow({
                'Название': row['name'],
                'URL': row['url'],
                'Телефон': row['phone'],
                'Email': row['email'],
                'Сайт': row['site'],
                'Статус сайта': row['site_status'],
                'Источник': row['source_url'],
                'Добавлено': row['created_at'],
                'Обновлено': row['updated_at'],
            })
    
    return len(rows), filename

def export_all_to_csv(filename='export_ALL.csv'):
    """Экспортирует всю базу в один CSV"""
    conn = get_connection()
    rows = conn.execute("""
        SELECT industry, name, url, phone, email, site, site_status, created_at, updated_at
        FROM companies 
        ORDER BY industry, name
    """).fetchall()
    conn.close()
    
    with open(filename, mode='w', encoding='utf-8-sig', newline='') as f:
        fieldnames = ['Отрасль', 'Название', 'URL', 'Телефон', 'Email', 'Сайт', 'Статус сайта', 'Добавлено', 'Обновлено']
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=';')
        writer.writeheader()
        for row in rows:
            writer.writerow({
                'Отрасль': row['industry'],
                'Название': row['name'],
                'URL': row['url'],
                'Телефон': row['phone'],
                'Email': row['email'],
                'Сайт': row['site'],
                'Статус сайта': row['site_status'],
                'Добавлено': row['created_at'],
                'Обновлено': row['updated_at'],
            })
    
    return len(rows), filename
