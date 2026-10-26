# Запуск и эксплуатация

Команды запуска ниже — справка для будущего использования. Парсер при инвентаризации не запускался. Проверки: [STATUS.md](STATUS.md).

## Предпосылки

Локально найдены Python 3.14.2 и импортируемый Playwright. Версия зависимости и наличие работоспособного Chromium не проверены; requirements/lock отсутствуют. parser_run.dependencies ищет пользовательские site-packages и `%LOCALAPPDATA%/ms-playwright`, при наличии задаёт PLAYWRIGHT_BROWSERS_PATH. Ничего не устанавливалось.

## CLI

```powershell
Set-Location -LiteralPath 'D:\DATA\Parser V2_0'
python -B .\parser_run.py
```

Интерактивно: доступная отрасль → N → начальная страница MedAdvisor (Enter=1). Доступны 1 медицина, 2 красота, 3 строительство; 4–8 имеют soon.

Малый будущий сбор в отдельные файлы (создаёт данные и обращается к сети):

```powershell
python -B .\parser_run.py --industry 1 --target 2 --start-page 1 --max-cards 12 --database .\local_run\sample.db --output .\local_run\sample.csv
```

Для Orgpage выбрать --industry 2/3 и убрать --start-page. Необязательные --city и --region можно повторять. Без них — Россия как область поиска. N/max-cards >0; start-page >=1, только MedAdvisor.

`python -B .\main.py` открывает старое меню и вызывает db.init_db() до выбора, поэтому способен мигрировать/обновлять БД. Медицинские ссылки в этом пути берутся из medadvisor.csv. Пункты 92/93 экспортируют обычные строки БД, 95–98 работают с рейтинговыми очередями/пересчётом; это другие выборки, чем target-CSV.

## Настройки и ожидаемый результат

industries.py содержит output, search_url, search_queries/query_batch_size, require_same_site_domain/site_topic_terms, ready/soon. Общие константы MAX_PAGES=15, MAX_RETRIES=2, SAVE_EVERY=10, REQUEST_DELAY=0.8; не все исторические константы управляют новым конвейером. max_cards по умолчанию max(200,10N). Проверяющий сайтов вызывается с concurrency=1; его defaults: timeout=20, navigation_timeout=8, retries=1; скрининг budget=3/observation=2 секунды. lead_scoring_config.json задаёт веса, не порог пригодности.

Все отрасли по умолчанию пишут в parser_v2.db; --database меняет путь, --output — CSV. Инициализация новой БД без явного seed также читает существующий medadvisor.csv: отдельная БД не обязательно пустая. Существующие изолированные тесты передают собственный seed.

N — новые пригодные сайты текущего запуска; известные домены всей БД не считаются повторно. Прогресс: `[просмотрено] [Готово: принято/N]`. CSV накопительный по отрасли, может превышать N и включать прежние строки вне геофильтра запуска.

CSV: 24 колонки, UTF-8 BOM, `;`, контакты/география/доступность/скрининг/сохранённый рейтинг/неопределённость. summary.json относится к текущему запуску, а не ко всем строкам CSV; catalog_pages.json содержит доказательства пагинации MedAdvisor. Эти файлы и БД хранят реальные данные, их нужно исключать из Git.

Проверять target_achieved и stop_reason: source_exhausted, safety_card_limit, safety_page_limit, access_restricted, source_error, pagination_stalled и прочие причины допускают недобор. При CAPTCHA/блокировке источник останавливается без обхода. finally может сохранить фактическую часть при прерывании. Следующий запуск получает новый run_id, полноценного CLI-resume нет.

## Проверки без записи данных

Синтаксис без .pyc:

```powershell
python -B -c "import ast,pathlib; p=list(pathlib.Path('.').glob('*.py')); [ast.parse(f.read_bytes(),filename=str(f)) for f in p]; print(len(p))"
```

Чистые mock-тесты:

```powershell
python -B -m unittest test_orgpage_recovery test_target_count.CursorTests test_start_page.InteractivePageTests test_start_page.CatalogPageTests test_stage3_1.IdentityTests test_stage3_1.EvidenceTests test_stage5.GeographyTests test_csv_rating_export.SavedCsvTests.test_saved_points_and_unknown_are_not_recalculated
```

Итоговые 52 теста включали также ConstructionTests, загруженный через unittest.TestLoader с исключением метода test_target_and_query_attribution_are_global. В текущем прогоне дополнительно действовал Python audit hook с запретом файловых мутаций, subprocess и socket-событий. Полный test_construction не является чистым: исключённый метод создаёт БД/файлы.

Не использовать полный discovery/stage*_run_tests.py как read-only проверку: есть временные БД, Chromium, localhost-серверы и перезапись timing/example-файлов. Полные проверки требуют отдельного разрешённого изолированного окружения.
