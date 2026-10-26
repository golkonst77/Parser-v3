"""Conservative brand evidence and independent branch evidence. No URL exceptions."""
import re
from urllib.parse import urlsplit


def normalize(value):
    return ' '.join(re.findall(r'[a-zа-яё0-9]+', (value or '').casefold().replace('ё','е')))


def assess_identity(company, final_url, content):
    text=content.get('title','')+' '+content.get('text','')
    normalized=normalize(text)
    name=company.get('name') or ''
    quoted=re.findall(r'["«“]([^"»”]+)["»”]',name)
    brand=company.get('brand') or (quoted[0] if quoted else name)
    generic={'ооо','ао','пао','ип','клиника','клиники','медицинский','центр','медицинские','на','в','и','the','clinic','company','medical','center'}
    words=[w for w in normalize(brand).split() if len(w)>2 and w not in generic and not w.isdigit()]
    compact=lambda s: ''.join(normalize(s).split())
    brand_match=bool(words) and (all(w in normalized.split() for w in words) or compact(' '.join(words)) in compact(text))
    emails=lambda s: set(re.findall(r'[\w.+%-]+@[\w.-]+\.[a-z]{2,}',(s or '').casefold()))
    phones=lambda s: {re.sub(r'\D','',p)[-10:] for p in re.findall(r'\+?\d[\d ()-]{8,}\d',s or '') if len(re.sub(r'\D','',p))>=10}
    matched_emails=sorted(emails(company.get('email')) & emails(text))
    matched_phones=sorted(phones(company.get('phone')) & phones(text))
    evidence=[]
    if brand_match: evidence.append(dict(kind='brand_in_page',value=brand))
    if matched_emails: evidence.append(dict(kind='exact_email',values=matched_emails))
    if matched_phones: evidence.append(dict(kind='exact_phone',values=matched_phones))
    hostname=(urlsplit(final_url).hostname or '').lower()
    domain_match=any(w.isascii() and len(w)>=4 and w in hostname.split('.')[0] for w in words)
    if domain_match: evidence.append(dict(kind='brand_in_domain',value=hostname))
    address=company.get('address') or ''
    # Address proof requires street tokens AND house number in one short page segment.
    # Shared switchboard numbers, mailboxes and domains cannot establish a branch.
    address_match=False
    match=re.search(r'(?:ул(?:ица)?\.?|проспект|пр-т|шоссе|переулок)\s+(.+?)[, ]+(?:д(?:ом)?\.?\s*)?(\d+(?:/\d+)?[а-яa-z]?)\b',address,re.I)
    if match:
        street=[w[:5] for w in normalize(match[1]).split() if len(w)>3]
        house=match[2].casefold()
        suffix=re.search(r'(?<![а-я])(корп(?:ус)?|кор|стр(?:оение)?|к)\.?\s*(\d+)',address[match.end():],re.I)
        for line in re.split(r'[\n;]',text):
            if len(line)>600: continue
            # Compact 27к1 is a house plus corpus, never house 27 without qualification.
            candidate=re.sub(r'(?<=\d)(?=к\.?\s*\d)', ' ', line, flags=re.I) if suffix else line
            if street and all(w in normalize(line) for w in street) and re.search(r'(?<!\d)'+re.escape(house)+r'(?![\dа-яa-z/])',candidate,re.I):
                if suffix:
                    kind=r'стр(?:оение)?' if suffix[1].casefold().startswith('стр') else r'корп(?:ус)?|кор|к'
                    if not re.search(r'(?<![а-я])(?:'+kind+r')\.?\s*'+re.escape(suffix[2])+r'(?!\d)',candidate,re.I):continue
                address_match=True
                evidence.append(dict(kind='branch_address',source=address,page_excerpt=line[:600]))
                break
    if any(s in text.casefold() for s in ('this domain is for sale','домен выставлен на продажу','buy this domain')):
        brand_status='mismatch'
        evidence.append(dict(kind='parked_domain_for_sale'))
    elif matched_emails or (brand_match and (matched_phones or address_match)):
        brand_status='confirmed'
    elif brand_match or matched_phones or domain_match:
        brand_status='probable'
    else: brand_status='unknown'
    branch_status='confirmed' if address_match and brand_status=='confirmed' else 'unknown'
    return dict(brand_status=brand_status,branch_status=branch_status,brand=brand,
        evidence=evidence,branch_reason=None if branch_status=='confirmed' else
        'source_address_missing' if not address else 'specific_branch_address_not_confirmed',
        scope='brand_or_network_separate_from_specific_branch')
