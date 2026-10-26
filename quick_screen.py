"""Observe main-page signals in a fresh session, without clicks or legal verdicts."""
import asyncio
import copy
import inspect
import re
import time
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from db import SCREEN_FEATURES, now

GROUP_FEATURES = dict(banner=('cookie_banner','cookie_accept','cookie_reject','cookie_settings'),
                      forms=('forms','form_consent','forms_without_consent'),policy=('policy_link',))


def empty_result(url, reason=None):
    return dict(url=url, checked_at=now(), status='unknown', duration_ms=0,
                features=dict.fromkeys(SCREEN_FEATURES, 'unknown'),
                errors=[reason] if reason else [], details=dict(
                    scope='loaded_main_document_and_open_shadow_roots',
                    consent_context='fresh_context_no_interaction',
                    observation='bounded_window_not_full_site_audit',
                    requests=[], counters={}, groups={}, samples=[],observations={},limitations=[]))


def analytics_endpoint(url):
    """Return (provider, script|measurement, counter IDs) for known endpoints."""
    try:
        p = urlsplit(url)
        host, path = (p.hostname or '').lower(), p.path.lower()
        query = parse_qs(p.query)
    except ValueError:
        return None
    ids = []
    if re.fullmatch(r'(?:[a-z0-9-]+\.)?mc\.yandex\.(?:ru|com|com\.tr|by|kz)', host):
        if path.startswith('/metrika/') and path.endswith('.js'):
            return 'metrika', 'script', ids
        match = re.search(r'/(?:watch|webvisor|clmap|collect|track)/(\d+)', path)
        if match:
            return 'metrika', 'measurement', [match[1]]
    if host == 'googletagmanager.com' or host.endswith('.googletagmanager.com'):
        ids = [s for s in query.get('id', []) if re.fullmatch(r'(?:GTM|G|GT|UA)-[A-Z0-9-]+', s, re.I)]
        if path in ('/gtm.js','/gtag/js'):
            return 'ga_gtm', 'script', ids
    if host == 'google-analytics.com' or host.endswith('.google-analytics.com') or host in ('analytics.google.com','stats.g.doubleclick.net'):
        ids = [s for s in query.get('tid', []) if re.fullmatch(r'(?:G|UA)-[A-Z0-9-]+', s, re.I)]
        if path in ('/collect','/g/collect','/j/collect','/mp/collect'):
            return 'ga_gtm', 'measurement', ids
        if path.endswith(('/analytics.js','/ga.js')):
            return 'ga_gtm', 'script', ids
    if host == 'clarity.ms' or host.endswith('.clarity.ms'):
        if path.startswith('/tag/'):
            return 'clarity', 'script', [p.path.rsplit('/',1)[-1]]
        if path == '/collect':
            return 'clarity', 'measurement', ids
    if host.endswith('.hotjar.com') or host.endswith('.hotjar.io'):
        match = re.search(r'hotjar-(\d+)\.js', path)
        if match:
            return 'hotjar', 'script', [match[1]]
        if host.startswith(('in.','vc.','ws.')):
            return 'hotjar', 'measurement', ids
    # Matomo/Piwik can be hosted on the organization's own domain.
    if re.search(r'/(?:matomo|piwik)\.(js|php)$', path):
        return 'matomo', 'script' if path.endswith('.js') else 'measurement', query.get('idsite', [])[:5]
    return None


def safe_url(url):
    """Never persist tracking payloads, API secrets, email or form data."""
    p = urlsplit(url)
    allowed = {}
    for key, values in parse_qs(p.query).items():
        if key in ('id','tid','idsite'):
            allowed[key] = [v for v in values if re.fullmatch(r'[A-Za-z0-9_-]{1,64}', v)]
    return urlunsplit((p.scheme, p.netloc, p.path, urlencode(allowed,doseq=True), ''))


DOM_COMMON = r"""
    const roots = [document];
    let limited = false;
    const limits=[], metrics={};
    for (let i=0; i<roots.length && i<30; i++) {
        const nodes = roots[i].querySelectorAll('*');
        metrics.dom_nodes=Math.max(metrics.dom_nodes||0,nodes.length);
        if (nodes.length > 12000) {limited=true;limits.push('dom_nodes');}
        for (const e of Array.from(nodes).slice(0,12000)) if (e.shadowRoot) {
            if (roots.length<30) roots.push(e.shadowRoot); else {limited=true;limits.push('shadow_roots');}
        }
    }
    const all = s => roots.flatMap(r => Array.from(r.querySelectorAll(s)));
    const visible = (e, viewport=false) => {
        if (!e || !e.getClientRects().length || e.closest('[hidden],[inert]')) return false;
        for (let n=e; n; n=n.parentElement || n.getRootNode()?.host) {
            if (!(n instanceof Element)) break;
            const s = getComputedStyle(n);
            if (s.display==='none' || s.visibility==='hidden' || Number(s.opacity)===0) return false;
        }
        const r=e.getBoundingClientRect();
        return r.width>0 && r.height>0 && (!viewport || (r.bottom>0 && r.right>0 && r.top<innerHeight && r.left<innerWidth));
    };
    const text = e => (e?.textContent || '').replace(/\s+/g,' ').trim();
    const frames = all('iframe,frame').map(e=>({src:e.getAttribute('src')||'',name:e.name||e.id||'',
        rendered:visible(e),viewport:visible(e,true),width:e.getBoundingClientRect().width,height:e.getBoundingClientRect().height}));
    const result = (features, evidence) => ({features,evidence,limited,limits:[...new Set(limits)],metrics,frames,
        uninspected_frames:frames.filter(f=>f.rendered).length,legacy_uninspected_frames:frames.length});
"""

BANNER_JS = '() => {' + DOM_COMMON + r"""
    const candidates=all('[role="dialog"],dialog[open],[aria-modal="true"],[id*="cookie" i],[class*="cookie" i],[id*="consent" i],[class*="consent" i]');
    // Unnamed fixed notices are common. Bound traversal to avoid expensive full-page scans.
    const blocks=all('div,section,aside');
    metrics.banner_blocks=blocks.length;
    if (blocks.length>2000) {limited=true;limits.push('banner_blocks');}
    for (const e of blocks.slice(0,2000)) if (['fixed','sticky'].includes(getComputedStyle(e).position)) candidates.push(e);
    const found=[],offscreen=[];
    for (const e of new Set(candidates)) {
        if (!visible(e)) continue;
        const t=text(e), cookie=/cookies?|куки|cookie-файл/i.test(t);
        if (!cookie || t.length>6000) continue;
        const controls=Array.from(e.querySelectorAll('button,[role="button"],input[type="button"],input[type="submit"],a')).filter(x=>visible(x));
        const dialog=e.matches('[role="dialog"],dialog[open],[aria-modal="true"]');
        const fixed=['fixed','sticky'].includes(getComputedStyle(e).position);
        const ui=/banner|notice|popup|consent|cookie/i.test(e.id+' '+e.className);
        const notice=/использу|согласи|настрой|предпочт|accept|consent|\buse\b|preferences|принять/i.test(t);
        if (notice && (dialog || (fixed && (controls.length || ui)) || (ui && controls.some(c=>c.tagName!=='A')))) {
            if (!visible(e,true)) {offscreen.push({text:t.slice(0,400)});continue;}
            if (found.some(f=>f.element.contains(e))) continue;
            found.push({element:e,text:t.slice(0,600),buttons:controls.filter(c=>visible(c,true)).map(c=>text(c)||c.getAttribute('aria-label')||c.getAttribute('value')||'').filter(Boolean)});
        }
    }
    const buttons=found.flatMap(f=>f.buttons);
    const feature = rx => found.length ? (buttons.some(b=>rx.test(b))?'yes':'no') : 'unknown';
    return result({cookie_banner:found.length?'yes':'no',
        cookie_accept:found.length?(buttons.some(b=>!/отказ|отклон|не соглас|не принима|reject|decline|deny/i.test(b)&&/принять|разрешить|согласен|согласна|accept|allow|agree|^ok$|^хорошо[.!]?$|^понятно[.!]?$/i.test(b))?'yes':'no'):'unknown',
        cookie_reject:feature(/отказ|отклон|не соглас|не принима|только необходим|reject|decline|deny|necessary only/i),
        cookie_settings:feature(/настро|предпочт|выбрать|settings|preferences|customi[sz]e/i)},
        {banner_count:found.length,texts:found.map(f=>f.text).slice(0,10),offscreen:offscreen.slice(0,10),buttons:buttons.slice(0,30).map(s=>s.slice(0,120))});
}"""

FORMS_JS = '() => {' + DOM_COMMON + r"""
    const identify = e => {
        const a=(e.type+' '+e.name+' '+e.id+' '+e.autocomplete+' '+(e.getAttribute('placeholder')||'')+' '+Array.from(e.labels||[]).map(text).join(' ')).toLowerCase();
        if (/password|hidden|submit|checkbox|radio/.test(e.type)) return null;
        if (/\btel\b|phone|mobile|телефон/.test(a)) return 'phone';
        if (/email|e-mail|электрон|почт/.test(a)) return 'email';
        if (/\bname\b|fio|fullname|first.?name|last.?name|given-name|family-name|фио|фамил|отчеств|имя/i.test(a)) return 'name';
        return null;
    };
    const forms=[];
    const containers=new Set(all('form,[role="form"]'));
    for (const e of all('input,textarea').filter(e=>visible(e)&&identify(e))) {
        if (!e.closest('form,[role="form"]')) containers.add(e.closest('[role="dialog"],section')||e.parentElement);
    }
    metrics.form_containers=containers.size;
    if (containers.size>150) {limited=true;limits.push('form_containers');}
    for (const form of Array.from(containers).slice(0,150)) {
        const fields=Array.from(form.querySelectorAll('input,textarea')).filter(e=>visible(e)).map(identify).filter(Boolean);
        if (!fields.length) continue;
        const boxes=Array.from(form.querySelectorAll('input[type="checkbox"],[role="checkbox"]')).filter(e=>{
            const labels=Array.from(e.labels||[]);
            const label=labels.map(text).join(' ') || text(e.closest('label')) || e.getAttribute('aria-label') || '';
            return (visible(e)||labels.some(l=>visible(l))) && /согласи|согласен|согласна|персональн|обработк.*данн|privacy|consent|agree/i.test(label);
        });
        const purpose=(text(form).match(/запис[ьа]|заявк|обратн[а-я ]+(?:связь|звонок)|консультац|appointment|callback|feedback|request/ig)||[]).slice(0,5);
        forms.push({fields:[...new Set(fields)],purpose:purpose.length?purpose:['personal_data_fields'],
            consent:boxes.length?'yes':'no',consent_prechecked:boxes.some(e=>e.checked||e.getAttribute('aria-checked')==='true')});
    }
    return result({forms:forms.length?'yes':'no',form_consent:forms.length?(forms.some(f=>f.consent==='yes')?'yes':'no'):'unknown',
        forms_without_consent:forms.length?(forms.some(f=>f.consent==='no')?'yes':'no'):'unknown'}, {forms});
}"""

POLICY_JS = '() => {' + DOM_COMMON + r"""
    const links=[], documents=[], candidates=all('a[href]');
    metrics.links=candidates.length;
    if(candidates.length>3000) {limited=true;limits.push('links');}
    for(const a of candidates.slice(0,3000)) {
        if(!visible(a)) continue;
        const t=text(a),raw=(a.getAttribute('href')||'').trim(),combined=t+' '+raw;
        if(!/политик|персональн|конфиденц|privacy|policy|cookie|куки|согласи/i.test(combined)) continue;
        let category='other';
        if(/согласи|consent/i.test(t)&&/персональн|данн|privacy/i.test(combined)) category='pd_consent';
        else if(/cookie|куки/i.test(combined)) category='cookie_policy';
        else if((/персональн|конфиденц|privacy|personal.data/i.test(combined)&&/политик|конфиденц|policy|privacy/i.test(combined))||/\/(?:policy|politika)(?:[./_-]|$)/i.test(raw)) category='pd_policy';
        else if(/персональн|personal.data/i.test(combined)) category='ambiguous_pd';
        let url=null,valid=false,reason=null;
        try {
            const u=new URL(raw,document.baseURI),current=new URL(location.href); url=u.href;
            if(!raw||raw.startsWith('#')) reason='placeholder_or_fragment';
            else if(!['http:','https:'].includes(u.protocol)||!u.hostname||u.username||u.password) reason='unsupported_url';
            else if(u.origin===current.origin&&u.pathname===current.pathname&&u.search===current.search&&u.hash) reason='same_document_fragment';
            else valid=true;
        } catch(_) {reason='invalid_url';}
        const item={text:t.slice(0,300),url,raw_href:raw,category,valid_url:valid,rejection_reason:reason,location:a.closest('footer,[role="contentinfo"]')?'footer':'main'};
        documents.push(item);if(category==='pd_policy'&&valid) links.push(item);
    }
    return result({policy_link:links.length?'yes':documents.some(d=>d.category==='ambiguous_pd'&&d.valid_url)?'unknown':'no'},{links:links.slice(0,30),documents:documents.slice(0,60)});
}"""

SCRIPTS_JS = '() => {' + DOM_COMMON + r"""
    const scripts=all('script');
    metrics.scripts=scripts.length;
    if (scripts.length>300) {limited=true;limits.push('scripts');}
    let remaining=200000;
    const sources=[];
    for (const e of scripts.slice(0,300)) {
        const raw=e.getAttribute('src')||e.getAttribute('data-src');
        const value=e.textContent||'';
        if (value.length>remaining) {limited=true;limits.push('script_code');}
        sources.push({src:raw?new URL(raw,document.baseURI).href:null,code:value.slice(0,Math.max(0,remaining)),type:e.type||''});
        remaining=Math.max(0,remaining-value.length);
    }
    return {sources,limited,limits:[...new Set(limits)],metrics};
}"""


class QuickScreen:
    """One observer per navigation attempt; no second page and no interaction."""
    def __init__(self, url, *, budget=3.0, observation=2.0):
        if budget <= 0 or observation < 0:
            raise ValueError('Invalid quick-screen limits')
        self.budget = budget
        self.observation = observation
        self.result = empty_result(url)
        self.started = time.monotonic()
        self.collect_started = None
        self.events = {}
        self.counter_ids = {}
        self.script_providers = set()
        self.capture_ok = False
        self.overflow = False
        self.emitter = None
        self.listeners = []
        self.groups_ok = set()

    def begin_navigation(self):
        self.started = time.monotonic()
        self.result['details']['navigation_started_at'] = now()

    def _observations(self):
        for key,state in self.result['features'].items():
            item=self.result['details']['observations'].setdefault(key,dict(state=state,reason_codes=[],evidence=[],first_seen_ms=None,last_observed_ms=None,scope='bounded_main_document'))
            item['state']=state
            if state=='unknown' and not item.get('reason_codes'):
                item['reason_codes']=['not_applicable_no_banner' if key.startswith('cookie_') and key!='cookie_banner' and self.result['features']['cookie_banner']=='no' else 'not_applicable_no_form' if key in ('form_consent','forms_without_consent') and self.result['features']['forms']=='no' else 'observation_unavailable']
            elif state!='unknown': item['reason_codes']=[]

    def error(self, value):
        if value not in self.result['errors']:
            self.result['errors'].append(value)

    async def attach(self, context):
        self.emitter = context
        try:
            for event, handler in (('request',self._request),('requestfailed',self._failed),('response',self._response)):
                value = context.on(event,handler)
                if inspect.isawaitable(value):
                    await value
                self.listeners.append((event,handler))
            self.capture_ok = True
        except Exception as exc:
            self.error('network_listener: ' + str(exc))

    async def detach(self):
        if self.emitter:
            for event, handler in self.listeners:
                try:
                    value = self.emitter.remove_listener(event,handler)
                    if inspect.isawaitable(value):
                        await value
                except Exception as exc:
                    self.error('listener_cleanup: ' + str(exc))
        self.listeners.clear()

    def _request(self, request):
        try:
            match = analytics_endpoint(request.url)
            if not match:
                return
            provider, kind, ids = match
            self.counter_ids.setdefault(provider,set()).update(ids)
            if kind == 'script':
                self.script_providers.add(provider)
            key = (provider,kind,safe_url(request.url))
            if key not in self.events:
                if len(self.events) >= 250:
                    self.overflow = True
                    return
                self.events[key] = dict(provider=provider,kind=kind,url=key[2],
                    resource_type=request.resource_type,first_ms=round((time.monotonic()-self.started)*1000),
                    count=0,failed=False,http_status=None,phase='no_consent_interaction')
            self.events[key]['count'] += 1
        except Exception as exc:
            self.capture_ok = False
            self.error('network_event: ' + str(exc))

    def _failed(self, request):
        self._outcome(request,failed=True)

    def _response(self, response):
        self._outcome(response.request,http_status=response.status)

    def _outcome(self, request, **values):
        try:
            match = analytics_endpoint(request.url)
            if match:
                key = (match[0],match[1],safe_url(request.url))
                if key in self.events:
                    self.events[key].update(values)
        except Exception as exc:
            self.capture_ok = False
            self.error('network_outcome: ' + str(exc))

    def _scripts(self, data):
        if not isinstance(data,dict) or not isinstance(data.get('sources'),list):
            raise ValueError('Invalid script snapshot')
        for source in data['sources']:
            match = analytics_endpoint(source.get('src') or '')
            if match:
                provider, _, ids = match
                self.script_providers.add(provider)
                self.counter_ids.setdefault(provider,set()).update(ids)
            code = source.get('code') or ''
            yandex = re.findall(r'\bym\s*\(\s*[\'\"]?(\d+)[\'\"]?\s*,\s*[\'\"]init[\'\"]',code)
            google = re.findall(r'\b(?:GTM-[A-Z0-9]+|G-[A-Z0-9]+|UA-\d+-\d+|GT-[A-Z0-9]+)\b',code)
            if yandex:
                self.script_providers.add('metrika')
                self.counter_ids.setdefault('metrika',set()).update(yandex)
            if google and re.search(r'gtag|googletagmanager|dataLayer|GoogleAnalyticsObject',code):
                self.script_providers.add('ga_gtm')
                self.counter_ids.setdefault('ga_gtm',set()).update(google)
        if data.get('limited'):
            self.error('scripts: DOM/code limit reached')
        else:
            self.groups_ok.add('scripts')

    async def _snapshot(self, page, group, script, deadline):
        fields=GROUP_FEATURES.get(group,())
        stamp=lambda: round((time.monotonic()-self.started)*1000)
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('screen budget exhausted')
            data = await asyncio.wait_for(page.evaluate(script),min(.5,remaining))
            at=stamp()
            if group == 'scripts':
                self._scripts(data)
                self.result['details']['samples'].append(dict(group=group,elapsed_ms=at,
                    limits=data.get('limits',[]),metrics=data.get('metrics',{})))
                return
            if not isinstance(data,dict) or not isinstance(data.get('features'),dict):
                raise ValueError('Invalid ' + group + ' snapshot')
            reasons=['dom_limit:'+v for v in data.get('limits',[])]
            if data.get('limited') and not reasons: reasons.append('dom_limit:unspecified')
            if data.get('uninspected_frames'): reasons.append('iframe:visible_uninspected')
            evidence=data.get('evidence',{})
            if group=='banner' and evidence.get('offscreen') and not evidence.get('banner_count'):
                reasons.append('dom:banner_candidate_outside_viewport')
            semantic=('semantic:ambiguous_personal_data_document' if group=='policy' and
                any(d.get('category')=='ambiguous_pd' and d.get('valid_url') for d in evidence.get('documents',[])) else None)
            for key,value in data['features'].items():
                if key not in SCREEN_FEATURES or value not in ('yes','no','unknown'):
                    raise ValueError('Invalid feature state')
                if value == 'no' and reasons:
                    value = 'unknown'
                # A second sample may add positive evidence; never erase an observed signal.
                old=self.result['features'][key]
                if old != 'yes':
                    self.result['features'][key] = value
                item=self.result['details']['observations'].setdefault(key,dict(first_seen_ms=None,evidence=[],scope='bounded_main_document'))
                if old!='yes' or value=='yes':
                    item.update(state=self.result['features'][key],reason_codes=(reasons+([semantic] if semantic else [])) if value=='unknown' else [],last_observed_ms=at)
                    if value!='unknown' and (item['first_seen_ms'] is None or old!=value): item['first_seen_ms']=at
                    item['evidence']=copy.deepcopy(evidence)
            self.result['details']['groups'][group] = evidence
            self.result['details']['samples'].append(dict(group=group,
                elapsed_ms=at,
                features={key:self.result['features'][key] for key in data['features']},
                evidence=evidence,limits=data.get('limits',[]),metrics=data.get('metrics',{}),frames=data.get('frames',[]),
                legacy_limited=bool(data.get('limited')),legacy_uninspected_frames=data.get('legacy_uninspected_frames',data.get('uninspected_frames',0)),unknown_reasons=reasons))
            self.groups_ok.discard(group)
            if not reasons and not semantic: self.groups_ok.add(group)
            for reason in reasons+([semantic] if semantic else []):
                self.result['details']['limitations'].append(dict(group=group,reason=reason,elapsed_ms=at))
        except Exception as exc:
            self.groups_ok.discard(group)
            reason=('time_window:' if isinstance(exc,(TimeoutError,asyncio.TimeoutError)) else 'dom_error:')+(str(exc) or type(exc).__name__)
            for field in fields:
                if self.result['features'][field] != 'yes':
                    self.result['features'][field] = 'unknown'
                    self.result['details']['observations'][field]=dict(state='unknown',reason_codes=[reason],evidence=[],first_seen_ms=None,last_observed_ms=stamp(),scope='bounded_main_document')
            self.result['details']['limitations'].append(dict(group=group,reason=reason,elapsed_ms=stamp()))
            self.error(group + ': ' + reason)

    def summarize_network(self, complete=False):
        features = self.result['features']
        for provider,field in (('metrika','metrika'),('ga_gtm','ga_gtm')):
            if provider in self.script_providers or any(e['provider']==provider for e in self.events.values()):
                features[field] = 'yes'
            elif complete and 'scripts' in self.groups_ok and self.capture_ok and not self.overflow:
                features[field] = 'no'
            fields = [e for e in self.events.values() if e['provider']==provider and e['kind']=='measurement']
            features[field+'_requests'] = 'yes' if fields else 'no' if complete and self.capture_ok and not self.overflow else 'unknown'
        other = any(p not in ('metrika','ga_gtm') for p in self.script_providers) or any(e['provider'] not in ('metrika','ga_gtm') for e in self.events.values())
        features['other_analytics'] = 'yes' if other else 'no' if complete and 'scripts' in self.groups_ok and self.capture_ok and not self.overflow else 'unknown'
        features['analytics_requests'] = 'yes' if any(e['kind']=='measurement' for e in self.events.values()) else 'no' if complete and self.capture_ok and not self.overflow else 'unknown'
        self.result['details']['requests'] = copy.deepcopy(list(self.events.values()))
        self.result['details']['counters'] = {p: sorted(ids)[:50] for p,ids in self.counter_ids.items()}
        at=round((time.monotonic()-self.started)*1000)
        for key in ('metrika','ga_gtm','metrika_requests','ga_gtm_requests','other_analytics','analytics_requests'):
            provider='metrika' if key.startswith('metrika') else 'ga_gtm' if key.startswith('ga_gtm') else None
            events=[e for e in self.events.values() if (not provider or e['provider']==provider) and (not key.endswith('requests') or e['kind']=='measurement')]
            self.result['details']['observations'][key]=dict(state=features[key],reason_codes=[] if features[key]!='unknown' else
                ['network:capture_overflow' if self.overflow else 'network:listener_unavailable' if not self.capture_ok else 'time_window:incomplete'],
                evidence=copy.deepcopy(events) or dict(providers=sorted(self.script_providers),counters=copy.deepcopy(self.result['details']['counters'])),
                first_seen_ms=min((e['first_ms'] for e in events),default=at if features[key]!='unknown' else None),last_observed_ms=at,scope='observed_network_and_loaded_scripts')
        if self.overflow:
            self.error('network capture limit reached')

    async def collect(self, page):
        self.collect_started = time.monotonic()
        self.result['details']['navigation_observed_ms'] = round((self.collect_started-self.started)*1000)
        deadline = self.collect_started + self.budget
        self.result['url'] = page.url
        try:
            schedule=(('banner',BANNER_JS),('forms',FORMS_JS),('policy',POLICY_JS),('scripts',SCRIPTS_JS))
            target=min(self.observation,max(0,self.budget-.2))
            while True:
                for group,script in schedule: await self._snapshot(page,group,script,deadline)
                elapsed=time.monotonic()-self.collect_started
                if elapsed>=target or time.monotonic()>=deadline: break
                await asyncio.sleep(min(.4,target-elapsed,max(0,deadline-time.monotonic())))
            budget_exhausted = time.monotonic() >= deadline
            complete = not budget_exhausted and self.capture_ok and bool(self.groups_ok)
            if not complete:
                self.error('screen budget exhausted or observation unavailable')
                for key,value in self.result['features'].items():
                    if value == 'no':
                        self.result['features'][key] = 'unknown'
            self.summarize_network(complete=complete)
            self._observations()
            unresolved=any(self.result['features'][f]=='unknown' for f in ('cookie_banner','forms','policy_link'))
            self.result['status'] = ('unknown' if budget_exhausted else
                                     'complete' if complete and not self.result['errors'] and not unresolved else 'partial')
        except asyncio.CancelledError:
            self.mark_unknown('screen interrupted')
            raise
        except Exception as exc:
            self.mark_unknown('screen: ' + str(exc))
        finally:
            self.result['duration_ms'] = round((time.monotonic()-self.collect_started)*1000)
        return self.result

    def mark_unknown(self, reason):
        self.error(reason)
        for key,value in self.result['features'].items():
            if value == 'no':
                self.result['features'][key] = 'unknown'
        self.summarize_network(complete=False)
        self._observations()
        for item in self.result['details']['observations'].values():
            if item['state']=='unknown': item['reason_codes']=[reason]
        self.result['status'] = 'partial' if 'yes' in self.result['features'].values() else 'unknown'
        return self.result
