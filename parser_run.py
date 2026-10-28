"""Existing industry routing with optional non-interactive launch arguments."""
import argparse
import importlib.util
from pathlib import Path
import os
import site
import sys


def dependencies():
    if importlib.util.find_spec('playwright') is None:
        user=Path.home()/'AppData/Roaming/Python'/('Python'+str(sys.version_info.major)+str(sys.version_info.minor))/'site-packages'
        if user.is_dir():site.addsitedir(str(user))
    browsers=Path.home()/'AppData/Local/ms-playwright'
    if browsers.is_dir():os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH',str(browsers))


def positive_input(prompt,default=None):
    while True:
        raw=input(prompt).strip()
        if not raw and default is not None:return default
        if raw.isdigit() and int(raw)>0:return int(raw)
        print('Введите целое число больше нуля.')


def main(argv=None):
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    if hasattr(sys.stderr,'reconfigure'):sys.stderr.reconfigure(encoding='utf-8')
    from industries import INDUSTRIES
    parser=argparse.ArgumentParser(description='N = новые пригодные уникальные сайты; география по умолчанию — Россия')
    parser.add_argument('--industry',type=int,choices=list(INDUSTRIES))
    parser.add_argument('--target',type=int)
    parser.add_argument('--start-page',type=int,help='Настоящая начальная страница каталога MedAdvisor (по умолчанию 1)')
    parser.add_argument('--database',type=Path)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--max-cards',type=int)
    parser.add_argument('--max-pages',type=int,default=200,help='Общий предел страниц каталога за запуск: 1–1000 (по умолчанию 200)')
    parser.add_argument('--resume',action='store_true',help='Продолжить отдельную БД; target означает итоговое число пригодных записей отрасли')
    parser.add_argument('--city',action='append',default=[])
    parser.add_argument('--region',action='append',default=[])
    raw=list(sys.argv[1:] if argv is None else argv)
    args=parser.parse_args(raw)
    interactive=not raw
    if interactive:
        for number,config in INDUSTRIES.items():
            print(f"[{number}] {config['name']}"+(' (в разработке)' if config['status']!='ready' else ''))
        while True:
            args.industry=positive_input('Номер отрасли: ')
            if args.industry in INDUSTRIES and INDUSTRIES[args.industry]['status']=='ready':break
            print('Выберите доступную отрасль.')
        args.target=positive_input('Целевое количество новых пригодных сайтов N: ')
    elif args.industry is None or args.target is None:
        parser.error('Укажите --industry и --target либо запустите без аргументов')
    if args.target<=0 or (args.max_cards is not None and args.max_cards<=0):parser.error('N и max-cards должны быть > 0')
    if args.start_page is not None and args.start_page<1:parser.error('--start-page должен быть >= 1')
    if not 1<=args.max_pages<=1000:parser.error('--max-pages должен быть от 1 до 1000')
    if args.resume and (not args.database or not args.database.is_file() or not args.output):
        parser.error('--resume требует существующую --database и явный --output')
    if args.resume and (args.start_page is not None or args.city or args.region):
        parser.error('--resume восстанавливает исходный обход; не задавайте start-page/city/region')
    config=dict(INDUSTRIES[args.industry])
    if config['status']!='ready':parser.error('Эта отрасль пока в разработке')
    from target_collection import source_config
    config=source_config(config)
    if config['source']=='medadvisor':
        config['start_page']=(positive_input('С какой страницы MedAdvisor начать? [1]: ',1) if interactive else args.start_page or 1)
    elif args.start_page is not None:parser.error('--start-page применяется только к MedAdvisor')
    dependencies()
    import db
    from engine import run_parser
    from geography import selection
    if args.database:
        if args.database.is_file():
            import sqlite3
            from contextlib import closing
            with closing(sqlite3.connect(args.database.resolve().as_uri()+'?mode=ro',uri=True)) as conn:
                if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='companies'").fetchone():
                    saved={row[0] for row in conn.execute('SELECT DISTINCT industry FROM companies')}
                    if saved-{config['name']}:
                        parser.error('Указанная --database содержит другую отрасль; используйте отдельную БД')
        args.database.parent.mkdir(parents=True,exist_ok=True)
        db.DB_FILE=str(args.database.resolve())
    if args.output:config['output']=str(args.output)
    if args.max_cards is not None:config['max_cards']=args.max_cards
    config.update(max_pages=args.max_pages,resume=args.resume)
    config['geography']=selection(args.city,args.region) if args.city or args.region else None
    run_parser(config,args.target)


if __name__=='__main__':main()
