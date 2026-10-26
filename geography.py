"""Explicit city/region selection; unknown locations never imply membership."""
import re

DEFAULT = {'cities': ['Калуга', 'Обнинск', 'Москва'], 'regions': ['Московская область']}
# Limited explicit reference map; no guesses from URL slugs or substrings.
CITY_REGIONS = {'калуга':'Калужская область','обнинск':'Калужская область',
                'ступино':'Московская область','мытищи':'Московская область',
                'химки':'Московская область','андреевка':'Московская область'}


def key(value):
    return re.sub(r'\s+',' ',(value or '').strip().casefold().replace('ё','е')).removeprefix('г. ').removeprefix('город ')


def is_region(value):
    return bool(re.search(r'область|обл\.?$|край|республика|автономн|округ',key(value)))


def location(company):
    city, region = company.get('city'), company.get('region')
    if is_region(city):
        region = region or city
        city = None
    if city and not region:
        region = CITY_REGIONS.get(key(city))
    return dict(city=city or None,region=region or None)


def selection(cities=(), regions=()):
    cities=[v.strip() for v in cities if v.strip()]
    regions=[v.strip() for v in regions if v.strip()]
    if any(is_region(v) for v in cities):
        raise ValueError('Название области укажите в регионах, а не в городах')
    if not cities and not regions:
        raise ValueError('Выберите хотя бы один город или регион; все регионы задаются отдельно')
    return dict(cities=list(dict.fromkeys(cities)),regions=list(dict.fromkeys(regions)))


def region_key(value):
    return re.sub(r'\bобл\.?$', 'область', key(value))


def match(company, selected):
    if selected is None:
        return True, 'all_geographies_explicitly_selected'
    loc=location(company)
    if key(loc['city']) in {key(v) for v in selected.get('cities',[])}:
        return True,'selected_city'
    if region_key(loc['region']) in {region_key(v) for v in selected.get('regions',[])}:
        return True,'selected_region'
    return False, 'geography_unknown' if not any(loc.values()) else 'outside_selected_geography'


def priority(company, selected):
    if selected is None:return 0
    loc=location(company)
    ordered=[('city',v) for v in selected.get('cities',[])]+[('region',v) for v in selected.get('regions',[])]
    return next((i for i,(field,v) in enumerate(ordered) if (key(loc[field])==key(v) if field=='city' else region_key(loc[field])==region_key(v))),len(ordered))


def choose():
    raw=input('География: 1 — Калуга, Обнинск, Москва, Московская область (Enter); 2 — свой выбор; 3 — все: ').strip()
    if raw in ('','1'):return selection(**DEFAULT)
    if raw=='3':return None
    if raw!='2':raise ValueError('Неизвестный выбор географии')
    return selection(input('Города через ;: ').split(';'),input('Регионы через ;: ').split(';'))
