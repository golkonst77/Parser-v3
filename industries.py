"""
КОНФИГУРАЦИЯ ОТРАСЛЕЙ — Парсер-Комплекс v2.1
Добавляйте новые отрасли просто дописав блок в INDUSTRIES.
"""

INDUSTRIES = {
    1: {
        'name': 'Медицинские клиники',
        'emoji': '🏥',
        'source': 'medadvisor',
        'search_url': 'https://medadvisor.ru/russia/search?query=клиника',
        'link_re': r'https?://medadvisor\.ru/[a-z0-9-]+/clinic/\d+',
        'input_csv': 'medadvisor.csv',       # Исходный CSV со ссылками
        'output': 'medclinics_WORKING_ONLY.csv',
        'filter_state': True,                # Отсеивать госучреждения
        'check_sites': True,                 # Проверять сайты на работоспособность
        'working_only': False,               # Оставлять только с рабочим сайтом
        'default_count': 100,
        'status': 'ready',
    },
    2: {
        'name': 'Салоны красоты и косметология',
        'emoji': '💅',
        'source': 'orgpage',
        'search_url': 'https://www.orgpage.ru/search.html?q=салон+красоты&loc=Россия&forReplies=false',
        'link_re': r'https://www\.orgpage\.ru/[a-z\-]+/[a-z0-9\-]+\-\d+\.html$',
        'output': 'beauty_WORKING_ONLY.csv',
        'filter_state': False,
        'check_sites': True,
        'working_only': True,                # Только рабочие сайты
        'default_count': 100,
        'status': 'ready',
    },
    3: {
        'name': 'Строительство и ремонт',
        'emoji': '🔨',
        'source': 'orgpage',
        'search_url': 'https://www.orgpage.ru/search.html?q=строительство+домов+коттеджей&loc=Россия&forReplies=false',
        'search_queries': [
            {'name':'Строительство домов и коттеджей','query':'строительство домов коттеджей'},
            {'name':'Ремонт и отделка квартир','query':'ремонт отделка квартир'},
            {'name':'Строительные и подрядные организации','query':'строительные подрядные организации'},
            {'name':'Производство и продажа строительных материалов','query':'строительные материалы'},
            {'name':'Производство и монтаж окон и дверей','query':'окна двери производство монтаж'},
            {'name':'Кровельные и фасадные работы','query':'кровельные фасадные работы'},
            {'name':'Металлоконструкции и сэндвич-панели','query':'металлоконструкции сэндвич панели'},
        ],
        # Consecutive batches keep all query cursors and their real AJAX pagination.
        'query_batch_size': 10,
        'require_same_site_domain': True,
        'site_topic_terms': ['строител','строим','домостро','ремонт','отделоч','кровел','кровл',
            'фасад','окна','окон','двер','металлоконструк','сэндвич','бетон','кирпич',
            'стройматериал','пиломатериал','профнастил','инженерн','монтажн','сантехнич',
            'штукатур','гипсокартон','керамогранит'],
        'link_re': r'https://www\.orgpage\.ru/[a-z\-]+/[a-z0-9\-]+\-\d+\.html$',
        'output': 'construction_WORKING_ONLY.csv',
        'filter_state': False,
        'check_sites': True,
        'working_only': True,
        'default_count': 100,
        'status': 'ready',
    },
    4: {
        'name': 'Ветеринарные клиники',
        'emoji': '🐾',
        'source': 'orgpage',
        'search_url': 'https://www.orgpage.ru/search.html?q=ветеринарная+клиника&loc=Москва&forReplies=false',
        'link_re': r'https://www\.orgpage\.ru/[a-z\-]+/[a-z0-9\-]+\-\d+\.html$',
        'output': 'vet_WORKING_ONLY.csv',
        'filter_state': False,
        'check_sites': True,
        'working_only': True,
        'default_count': 100,
        'status': 'soon',
    },
    5: {
        'name': 'Автосервисы и детейлинг',
        'emoji': '🚗',
        'source': 'orgpage',
        'search_url': 'https://www.orgpage.ru/search.html?q=детейлинг+центр&loc=Москва&forReplies=false',
        'link_re': r'https://www\.orgpage\.ru/[a-z\-]+/[a-z0-9\-]+\-\d+\.html$',
        'output': 'auto_WORKING_ONLY.csv',
        'filter_state': False,
        'check_sites': True,
        'working_only': True,
        'default_count': 100,
        'status': 'soon',
    },
    6: {
        'name': 'Стоматологические клиники',
        'emoji': '🦷',
        'source': 'medadvisor',
        'search_url': 'https://medadvisor.ru/russia/search?query=стоматология',
        'link_re': r'https?://medadvisor\.ru/[a-z0-9-]+/clinic/\d+',
        'input_csv': 'medadvisor.csv',
        'output': 'stomat_WORKING_ONLY.csv',
        'filter_state': True,
        'check_sites': True,
        'working_only': False,
        'default_count': 100,
        'status': 'soon',
    },
    7: {
        'name': 'Юридические услуги',
        'emoji': '⚖️',
        'source': 'orgpage',
        'search_url': 'https://www.orgpage.ru/search.html?q=юридические+услуги&loc=Москва&forReplies=false',
        'link_re': r'https://www\.orgpage\.ru/[a-z\-]+/[a-z0-9\-]+\-\d+\.html$',
        'output': 'law_WORKING_ONLY.csv',
        'filter_state': False,
        'check_sites': True,
        'working_only': True,
        'default_count': 100,
        'status': 'soon',
    },
    8: {
        'name': 'Фитнес-клубы и студии йоги',
        'emoji': '🏋️',
        'source': 'orgpage',
        'search_url': 'https://www.orgpage.ru/search.html?q=фитнес+клуб&loc=Москва&forReplies=false',
        'link_re': r'https://www\.orgpage\.ru/[a-z\-]+/[a-z0-9\-]+\-\d+\.html$',
        'output': 'fitness_WORKING_ONLY.csv',
        'filter_state': False,
        'check_sites': True,
        'working_only': True,
        'default_count': 100,
        'status': 'soon',
    },
}

# Селекторы пагинации для orgpage
NEXT_SELECTORS = [
    'a:has-text("Следующая")',
    'a:has-text(">")',
    'a[rel="next"]',
]

# Общие настройки
MAX_PAGES = 15
MAX_RETRIES = 2
SAVE_EVERY = 10
REQUEST_DELAY = 0.8