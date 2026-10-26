"""Reproducible commercial prioritization of observations, never a legal verdict."""
import csv
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

CONFIG_PATH = Path(__file__).with_name('lead_scoring_config.json')
RULES = {
    'analytics_before_consent': 'Наблюдалась попытка измерительного запроса до действий согласия',
    'cookie_banner_absent': 'Cookie-баннер не обнаружен в проверенной области',
    'policy_link_absent': 'Рабочая ссылка на политику ПДн не обнаружена в проверенной области',
    'form_without_consent': 'Найдена форма без видимого механизма согласия',
    'ga_measurement': 'GA/GTM обнаружены вместе с измерительным запросом Google Analytics',
    'brand_confirmed': 'Официальный сайт бренда подтверждён',
}
READY = 'готова к ручной проверке'
CLARIFY = 'требуется уточнение'
INSUFFICIENT = 'недостаточно данных'
PROCESSING = ('не обработан', 'в работе', 'проверен', 'отложен', 'не подходит')


def load_config(path=None):
    config = json.loads(Path(path or CONFIG_PATH).read_text(encoding='utf-8'))
    weights = config.get('weights', {})
    if set(weights) != set(RULES) or any(type(v) is not int or not 0 <= v <= 100 for v in weights.values()):
        raise ValueError('Веса должны содержать ровно шесть правил с целыми баллами 0–100')
    if sum(weights.values()) > 100:
        raise ValueError('Сумма весов не должна превышать 100')
    coverage = config.get('coverage', {})
    minimum, ready = coverage.get('minimum'), coverage.get('ready')
    if any(type(v) not in (int, float) for v in (minimum, ready)) or not 0 <= minimum <= ready <= 1:
        raise ValueError('Пороги полноты должны удовлетворять 0 <= minimum <= ready <= 1')
    if not isinstance(config.get('model_version'), str) or not config['model_version'].strip():
        raise ValueError('Укажите model_version')
    return config


def config_hash(config):
    return hashlib.sha256(json.dumps(config, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def site_domain(value):
    """Exact IDNA hostname, with www and trailing dot normalized; no guessed eTLD."""
    if not value or str(value).strip() in ('Не найден', 'Нет сайта'):
        return None
    try:
        p = urlsplit(value if '://' in value else 'https://' + value)
        if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password:
            return None
        return p.hostname.rstrip('.').lower().removeprefix('www.').encode('idna').decode('ascii')
    except (ValueError, UnicodeError):
        return None


def score_lead(company, check=None, config=None):
    """Score one latest attempt. Never read last-known/effective historical features."""
    config = config if config is not None else load_config()
    check = check or {}
    screen = check.get('quick_screen') or {}
    features = screen.get('features') or {}
    details = screen.get('details') or {}
    observations = details.get('observations') or {}
    available = check.get('availability') == 'available'
    current_domain = site_domain(company.get('site'))
    applicable = bool(current_domain and current_domain == site_domain(check.get('checked_url')))
    if not applicable:
        screen, features, details, observations = {}, {}, {}, {}
        available = False
    brand = (check.get('brand_status') or check.get('ownership') or 'unknown') if applicable else 'unknown'
    branch = (check.get('branch_status') or 'unknown') if applicable else 'unknown'
    values, unknown = {}, {}

    def state(field):
        value = features.get(field, 'unknown')
        return value if value in ('yes', 'no') else 'unknown'

    def covered(group, fields):
        if not available or screen.get('status') not in ('complete', 'partial'):
            return False
        samples = [s for s in details.get('samples', []) if s.get('group') == group]
        if samples:
            last = samples[-1]
            if (last.get('limits') or last.get('limited') or last.get('legacy_limited') or
                    last.get('unknown_reasons') or last.get('uninspected_frames') or
                    any(f.get('rendered') for f in last.get('frames', []))):
                return False
        relevant = [l for l in details.get('limitations', []) if l.get('group') == group]
        if relevant and (not samples or relevant[-1].get('elapsed_ms', 0) >= samples[-1].get('elapsed_ms', 0)):
            return False
        if observations:
            return all(observations.get(f, {}).get('state') == state(f) and
                       state(f) != 'unknown' and not observations[f].get('reason_codes') for f in fields)
        # Legacy complete snapshots had no per-feature reason codes.
        return screen.get('status') == 'complete' and not screen.get('errors')

    def decide(rule, value, fields=(), reason=None):
        values[rule] = value
        if value == 'unknown':
            reasons = [code for f in fields for code in observations.get(f, {}).get('reason_codes', [])]
            unknown[rule] = sorted(set(reasons + ([reason] if reason else []))) or ['observation_unavailable']

    requests = details.get('requests', [])
    fresh = details.get('consent_context') == 'fresh_context_no_interaction'
    measured = [r for r in requests if r.get('kind') == 'measurement' and r.get('phase') == 'no_consent_interaction']
    if fresh and measured and state('analytics_requests') == 'yes':
        decide('analytics_before_consent', 'yes')
    elif available and fresh and state('analytics_requests') == 'no':
        decide('analytics_before_consent', 'no')
    else:
        decide('analytics_before_consent', 'unknown', ('analytics_requests',), 'measurement_or_fresh_context_not_proven')
    for rule, field, group in (('cookie_banner_absent', 'cookie_banner', 'banner'),
                                ('policy_link_absent', 'policy_link', 'policy')):
        if state(field) == 'yes':
            decide(rule, 'no')
        elif state(field) == 'no' and covered(group, (field,)):
            decide(rule, 'yes')
        else:
            decide(rule, 'unknown', (field,), 'section_coverage_insufficient')
    forms = details.get('groups', {}).get('forms', {}).get('forms', [])
    form_proof = any(f.get('consent') == 'no' and f.get('fields') for f in forms)
    if (state('forms') == 'yes' and state('forms_without_consent') == 'yes' and form_proof and
            covered('forms', ('forms', 'forms_without_consent'))):
        decide('form_without_consent', 'yes')
    elif state('forms') == 'no' and covered('forms', ('forms',)):
        decide('form_without_consent', 'no')
    elif state('forms_without_consent') == 'no' and covered('forms', ('forms', 'forms_without_consent')):
        decide('form_without_consent', 'no')
    else:
        decide('form_without_consent', 'unknown', ('forms', 'forms_without_consent'), 'form_scope_or_consent_not_proven')
    if (fresh and state('ga_gtm') == 'yes' and state('ga_gtm_requests') == 'yes' and
            any(r.get('provider') == 'ga_gtm' for r in measured)):
        decide('ga_measurement', 'yes')
    elif available and fresh and state('ga_gtm_requests') == 'no':
        decide('ga_measurement', 'no')
    else:
        decide('ga_measurement', 'unknown', ('ga_gtm', 'ga_gtm_requests'), 'google_measurement_not_proven')
    decide('brand_confirmed', 'yes' if brand == 'confirmed' else 'no' if brand == 'mismatch' else 'unknown',
           reason='brand_requires_manual_confirmation' if brand not in ('confirmed', 'mismatch') else None)
    reasons = [dict(rule=k, points=config['weights'][k], reason=RULES[k])
               for k in RULES if values[k] == 'yes' and config['weights'][k]]
    coverage = sum(v != 'unknown' for v in values.values()) / len(RULES)
    if not current_domain or not screen or not available or coverage < config['coverage']['minimum']:
        review = INSUFFICIENT
    elif brand != 'confirmed' or coverage < config['coverage']['ready']:
        review = CLARIFY
    else:
        review = READY
    excluded = []
    if not current_domain: excluded.append('official_site_missing_or_invalid')
    if not applicable: excluded.append('current_site_not_checked')
    if not available: excluded.append('site_not_available_in_latest_attempt')
    if brand != 'confirmed': excluded.append('brand_not_confirmed')
    if coverage < config['coverage']['ready']: excluded.append('evidence_coverage_below_ready_threshold')
    metrika_before = ('yes' if fresh and state('metrika_requests') == 'yes' and
                      any(r.get('provider') == 'metrika' for r in measured) else
                      'no' if available and fresh and state('metrika_requests') == 'no' else 'unknown')
    return dict(priority_score=sum(r['points'] for r in reasons), evidence_coverage=coverage,
                review_status=review, main_queue=review == READY, reasons=reasons, unknown_reasons=unknown,
                criteria=values, exclusions=excluded, brand_status=brand, branch_status=branch,
                features={k: state(k) for k in ('cookie_banner', 'metrika_requests', 'ga_gtm', 'ga_gtm_requests',
                                             'forms', 'forms_without_consent', 'policy_link')},
                metrika_before_consent=metrika_before,
                checked_at=check.get('checked_at') if applicable else None,
                checked_url=check.get('checked_url') if applicable else None,
                final_url=check.get('final_url') if applicable else None,
                screen_status=screen.get('status', 'unknown'), availability=check.get('availability') if applicable else None,
                config_hash=config_hash(config), model_version=config['model_version'], config=config)


def ensure_schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS lead_scores (
        company_url TEXT PRIMARY KEY REFERENCES companies(url), site_check_id INTEGER REFERENCES site_checks(id),
        priority_score INTEGER NOT NULL CHECK(priority_score BETWEEN 0 AND 100),
        evidence_coverage REAL NOT NULL CHECK(evidence_coverage BETWEEN 0 AND 1),
        review_status TEXT NOT NULL, main_queue INTEGER NOT NULL CHECK(main_queue IN (0,1)),
        config_hash TEXT NOT NULL, calculated_at TEXT NOT NULL, details_json TEXT NOT NULL)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS lead_processing (
        contact_key TEXT PRIMARY KEY, status TEXT NOT NULL, updated_at TEXT NOT NULL)''')


def latest_check(conn, company):
    domain = site_domain(company.get('site'))
    if not domain:
        return None
    # One attempt supplies all the evidence; do not mix dates or old domains.
    for row in conn.execute('SELECT * FROM site_checks WHERE company_url=? ORDER BY id DESC', (company['url'],)):
        if site_domain(row['checked_url']) != domain:
            continue
        result = dict(row)
        screen = conn.execute('SELECT * FROM quick_screens WHERE site_check_id=?', (row['id'],)).fetchone()
        if screen:
            import db
            screen = dict(screen)
            result['quick_screen'] = dict(status=screen['status'], features={f: screen[f] for f in db.SCREEN_FEATURES},
                                         details=json.loads(screen['details_json']), errors=json.loads(screen['errors_json']))
        return result
    return None


def persist_company(conn, company_url, config=None):
    import db
    company = dict(conn.execute('SELECT * FROM companies WHERE url=?', (company_url,)).fetchone())
    check = latest_check(conn, company)
    try:
        config = config if config is not None else load_config()
        result = score_lead(company, check, config)
    except (OSError, ValueError, TypeError) as exc:
        # Configuration problems must never discard a collected site check.
        result = dict(priority_score=0, evidence_coverage=0, review_status=INSUFFICIENT, main_queue=False,
                      reasons=[], unknown_reasons={'configuration': [str(exc)]}, exclusions=['scoring_configuration_error'],
                      config_hash='invalid', features={}, brand_status='unknown', branch_status='unknown',
                      checked_at=None, checked_url=None, final_url=None)
    conn.execute('''INSERT INTO lead_scores VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(company_url) DO UPDATE SET
        site_check_id=excluded.site_check_id, priority_score=excluded.priority_score,
        evidence_coverage=excluded.evidence_coverage, review_status=excluded.review_status,
        main_queue=excluded.main_queue, config_hash=excluded.config_hash,
        calculated_at=excluded.calculated_at, details_json=excluded.details_json''',
        (company_url, check['id'] if check else None, result['priority_score'], result['evidence_coverage'],
         result['review_status'], int(result['main_queue']), result['config_hash'], db.now(),
         json.dumps(result, ensure_ascii=False)))
    return result


def recalculate(config_path=None):
    """Atomic refresh of derived rows only; validate config before changing anything."""
    import db
    config = load_config(config_path)
    with db.get_connection() as conn:
        ensure_schema(conn)
        companies = [r[0] for r in conn.execute('SELECT url FROM companies ORDER BY url')]
        for company_url in companies:
            persist_company(conn, company_url, config)
    return len(companies)


def rank_key(lead):
    # Confidence-adjusted commercial score, with deterministic tie breakers.
    return (-lead['priority_score'] * lead['evidence_coverage'], -lead['priority_score'],
            -lead['evidence_coverage'], lead['company']['url'])


def get_leads(*, include_review=False, config_path=None, queue=None, geography=None):
    """One contact per exact site/observed redirect group; retain every branch."""
    import db
    if queue not in (None, 'main', 'review', 'all'):
        raise ValueError('Неизвестная очередь')
    from geography import match
    recalculate(config_path)
    with db.get_connection() as conn:
        items = []
        for row in conn.execute('SELECT c.*,s.details_json FROM companies c JOIN lead_scores s ON s.company_url=c.url ORDER BY c.url'):
            company = dict(row)
            score = json.loads(company.pop('details_json'))
            if match(company, geography)[0]:
                items.append(dict(company=company, **score))
        processing = {r['contact_key']: r['status'] for r in conn.execute('SELECT * FROM lead_processing')}
    parents = {}

    def find(domain):
        parents.setdefault(domain, domain)
        if parents[domain] != domain:
            parents[domain] = find(parents[domain])
        return parents[domain]

    for item in items:
        source = site_domain(item['company'].get('site'))
        final = site_domain(item.get('final_url')) if item.get('availability') == 'available' else None
        if source:
            find(source)
            if final:
                a, b = find(source), find(final)
                parents[max(a, b)] = min(a, b)
    groups = {}
    for item in items:
        domain = site_domain(item['company'].get('site'))
        key = find(domain) if domain else 'company:' + item['company']['url']
        groups.setdefault(key, []).append(item)
    leads = []
    for key, members in groups.items():
        members.sort(key=lambda r: (not r['main_queue'], r['brand_status'] != 'confirmed', *rank_key(r)))
        representative = dict(members[0])
        aliases = sorted(d for d in parents if find(d) == key) if not key.startswith('company:') else []
        representative.update(contact_key=key, domain_aliases=aliases,
            processing_status=processing.get(key) or next((processing[d] for d in aliases if d in processing), PROCESSING[0]),
            branches=[dict(company=m['company'], priority_score=m['priority_score'], evidence_coverage=m['evidence_coverage'],
                           review_status=m['review_status'], brand_status=m['brand_status'], branch_status=m['branch_status'],
                           checked_at=m.get('checked_at'), main_queue=m['main_queue'], exclusions=m['exclusions']) for m in members])
        mode = queue or ('all' if include_review else 'main')
        if mode == 'all' or (mode == 'main' and representative['main_queue']) or (mode == 'review' and not representative['main_queue']):
            leads.append(representative)
    return sorted(leads, key=rank_key)


def set_processing_status(contact_key, status):
    import db
    if status not in PROCESSING:
        raise ValueError('Неизвестный статус ручной обработки')
    leads = get_leads(include_review=True)
    lead = next((r for r in leads if r['contact_key'] == contact_key), None)
    if lead is None:
        raise ValueError('Кандидат не найден')
    with db.get_connection() as conn:
        # Save aliases too, so a subsequently discovered redirect retains the workflow state.
        for key in set([contact_key] + lead['domain_aliases']):
            conn.execute('INSERT INTO lead_processing VALUES (?,?,?) ON CONFLICT(contact_key) DO UPDATE SET status=excluded.status,updated_at=excluded.updated_at',
                         (key, status, db.now()))


CSV_FIELDS = ('Организация', 'Город', 'Регион', 'Отрасль', 'Официальный сайт', 'Телефон', 'Email',
              'Статус бренда', 'Статус филиала', 'Cookie-баннер', 'Метрика до согласия', 'GA/GTM',
              'Измерительные запросы GA', 'Формы', 'Формы без видимого согласия', 'Политика ПДн',
              'Рейтинг', 'Полнота проверки', 'Причины начисления баллов', 'Причины unknown',
              'Дата проверки', 'Готовность к проверке', 'Статус ручной обработки', 'Основная очередь',
              'Причины исключения из основной очереди', 'Домен контакта', 'Филиалы', 'Модель', 'Хеш конфигурации')


def excel_text(value):
    value = '' if value is None else str(value)
    # External catalog data must remain text when opened in Excel, including +7 phones.
    return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) else value


def export_csv(filename='lead_candidates.csv', *, include_review=False, config_path=None, queue=None, geography=None):
    leads = get_leads(include_review=include_review, config_path=config_path, queue=queue, geography=geography)
    with Path(filename).open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, CSV_FIELDS, delimiter=';')
        writer.writeheader()
        for lead in leads:
            c, f = lead['company'], lead['features']
            data = dict(zip(CSV_FIELDS, (
                c.get('name'), c.get('city'), c.get('region'), c.get('industry'), c.get('site'), c.get('phone'), c.get('email'),
                lead['brand_status'], lead['branch_status'], f.get('cookie_banner', 'unknown'), lead.get('metrika_before_consent', 'unknown'),
                f.get('ga_gtm', 'unknown'), f.get('ga_gtm_requests', 'unknown'), f.get('forms', 'unknown'),
                f.get('forms_without_consent', 'unknown'), f.get('policy_link', 'unknown'),
                lead['priority_score'], f"{lead['evidence_coverage'] * 100:.1f}%".replace('.', ','),
                ' | '.join(f"+{r['points']}: {r['reason']}" for r in lead['reasons']),
                json.dumps(lead['unknown_reasons'], ensure_ascii=False), lead.get('checked_at'), lead['review_status'],
                lead['processing_status'], 'да' if lead['main_queue'] else 'нет', ' | '.join(lead['exclusions']),
                lead['contact_key'], json.dumps(lead['branches'], ensure_ascii=False), lead.get('model_version'), lead['config_hash'])))
            writer.writerow({k: excel_text(v) for k, v in data.items()})
    return len(leads), str(filename)
