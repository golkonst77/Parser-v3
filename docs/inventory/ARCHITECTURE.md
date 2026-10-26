# Архитектура

Ограничения доказательств: [STATUS.md](STATUS.md). Историческая миграция: [STAGE1_REPORT.md](../../STAGE1_REPORT.md); накопление: [UNIQUE_URL_ACCUMULATION_REPORT.md](../../UNIQUE_URL_ACCUMULATION_REPORT.md).

## Текущий поток

`main.py / parser_run.py → industries → engine.run_parser → source_config → run_target → db.init_db(refresh_existing=False) → enrich_and_check → card_stream → process_cards → Collection → save_new_usable_company → export_results`.

1. industries.py задаёт отрасли, URL/regex, output, ready/soon и правила строительства. CLI проверяет N/start_page/max_cards. geography.py обрабатывает явные фильтры; None означает отсутствие географического ограничения.
2. source_config переопределяет источник по точному имени отрасли. MedAdvisor с start_page использует medadvisor_catalog.urls, без него — локальный CSV. Orgpage использует _orgpage_urls, строительство — _orgpage_queries с общим контекстом и исключением повторных ссылок.
3. card_stream сохраняет курсор и порции до 2N ссылок. max_cards по умолчанию max(200,10N), MAX_PAGES=15. Источник останавливается при ограничении доступа. Это пределы поиска, не обещание достижения N.
4. engine получает текст карточки, метаданные, контакты/географию. Известные карточки и домены общей БД пропускаются в целевом режиме. Отказы не занимают домен новой companies, но журналируются как решения/ошибки.
5. site_checker открывает главную страницу Chromium, определяет availability и identity через organization_identity, запускает QuickScreen. Контексты изолированы, время и повторы ограничены. Бренд и филиал оцениваются независимо.
6. Collection принимает запись без порога рейтинга; строительство добавляет доменный и тематический фильтры. save_new_usable_company повторно проверяет уникальность и транзакционно сохраняет организацию, проверку, скрининг и рейтинг. Старый add_or_update_company остаётся доступен другим сценариям и способен обновлять данные.
7. console_progress читает решения текущего run_id. Завершение фиксирует статистику в runs. Прогресс по SQL не означает восстановления курсора между запусками.
8. export_results формирует CSV из всех пригодных сохранённых записей отрасли и сохранённых баллов, без сетевой перепроверки и пересчёта старых рейтингов. Временный CSV заменяет output, summary.json сохраняется рядом. MedAdvisor CLI может писать catalog_pages.json. Отдельной блокировки нескольких процессов, пишущих общий output/.tmp, нет.

## Хранение и побочные действия

Основная БД — db.ROOT/parser_v2.db, SQLite WAL. По коду схемы: companies/card_keys/migration_originals; runs/errors/collection_decisions; site_checks/site_check_state; quick_screens/quick_screen_features; lead_scores/lead_processing. Это анализ схемообразующего кода, не новая проверка содержимого основной базы.

Наличие WAL/SHM важно для будущего backup: перенос одного живого .db не подтверждён как согласованная копия. Инициализация БД и меню способны мигрировать данные; в ходе инвентаризации не вызывались.

lead_scoring_config.json задаёт веса и coverage для коммерческих очередей. lead_cli предоставляет просмотр/экспорт, изменение статуса и пересчёт. Подсистема рейтинга остаётся, хотя не ограничивает N. QuickScreen наблюдает баннеры, формы, документы и аналитику в ограниченном окне; no и unknown имеют разный смысл. Полного аудита сайта и юридических выводов этот поток не обеспечивает.
