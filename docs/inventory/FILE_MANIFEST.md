# Манифест и дерево

Дата: 26.10.2026. Исходно 1209 файлов, 241524076 байт (около 230.3 MiB). Данные не копировались в документы. Результаты, копии и кэши скрыты в дереве; виртуальные окружения не обнаружены.

## Дерево исходников

```text
Parser V2_0/
  console_progress.py
  CONSTRUCTION_REPORT.md
  db.py
  engine.py
  geography.py
  industries.py
  lead_cli.py
  lead_scoring.py
  main.py
  medadvisor_catalog.py
  MEDADVISOR_START_PAGE_REPORT.md
  organization_identity.py
  ORGPAGE_PAGINATION_REPORT.md
  ORGPAGE_RECOVERY_REPORT.md
  parser_run.py
  quick_screen.py
  real_site_check_control.py
  REAL_SITE_CHECK_REPORT.md
  REPEAT_RUN_AUDIT.md
  site_checker.py
  STAGE1_REPORT.md
  stage2_backup.py
  STAGE2_REPORT.md
  stage2_verify.py
  stage3_1_control.py
  STAGE3_1_REPORT.md
  stage3_1_verify.py
  STAGE3_REPORT.md
  stage3_tools.py
  STAGE4_REPORT.md
  stage4_run_tests.py
  stage4_verify.py
  stage5_acceptance.py
  stage5_control.py
  stage5_repair_addresses.py
  STAGE5_REPORT.md
  stage5_run_tests.py
  start_page_run_tests.py
  start_page_verify.py
  target_collection.py
  target_count_replay.py
  TARGET_COUNT_REPORT.md
  target_count_run_tests.py
  target_count_verify.py
  test_construction.py
  test_csv_rating_export.py
  test_orgpage_recovery.py
  test_stage1.py
  test_stage2.py
  test_stage3.py
  test_stage3_1.py
  test_stage4.py
  test_stage5.py
  test_start_page.py
  test_target_count.py
  test_unique_urls.py
  UNIQUE_URL_ACCUMULATION_REPORT.md
  lead_scoring_config.json
  tests/fixtures/quick_screen/  # 15 HTML-fixtures
  docs/inventory/              # 6 новых документов
  [результаты, выгрузки, БД, backups и кэши скрыты]
```

## Ключевые файлы

| Файл | Роль |
|---|---|
| [main.py](../../main.py) | Меню, инициализация БД, выбор отрасли/N, обычные экспорты. |
| [parser_run.py](../../parser_run.py) | CLI/интерактивный вход, N/география/страница/БД/output. |
| [engine.py](../../engine.py) | Получение карточек, извлечение контактов, проверки, запись решений. |
| [industries.py](../../industries.py) | Отрасли, ready/soon, URL/regex и параметры строительства. |
| [db.py](../../db.py) | SQLite, миграции, глобальные домены, атомарная запись, история. |
| [geography.py](../../geography.py) | Города/регионы, нормализация и явные фильтры. |
| [target_collection.py](../../target_collection.py) | Маршрутизация источника, пригодность, поток ссылок, CSV/summary. |
| [medadvisor_catalog.py](../../medadvisor_catalog.py) | Каталог MedAdvisor, активная страница, переходы и доказательства. |
| [site_checker.py](../../site_checker.py) | Chromium, доступность, время/повторы, вызов identity/скрининга. |
| [organization_identity.py](../../organization_identity.py) | Независимая оценка бренда и филиала. |
| [quick_screen.py](../../quick_screen.py) | Ограниченное DOM/сетевое наблюдение, yes/no/unknown. |
| [console_progress.py](../../console_progress.py) | Прогресс по решениям run_id. |
| [lead_scoring.py](../../lead_scoring.py) | Приоритет, очереди, домены, Excel-экранирование. |
| [lead_cli.py](../../lead_cli.py) | Меню рейтингов, экспорт/пересчёт и статус обработки. |
| [lead_scoring_config.json](../../lead_scoring_config.json) | Несекретные веса шести критериев и coverage; нужен коду. |
| [medadvisor.csv](../../medadvisor.csv) | Реальный источник карточек CSV-пути; не публиковать. |
| [parser_v2.db](../../parser_v2.db) | Основная БД; исключить вместе с WAL/SHM. |
| [parser_v2.db-wal](../../parser_v2.db-wal) | Журнал SQLite; учитывать при согласованном backup. |
| [parser_v2.db-shm](../../parser_v2.db-shm) | Shared memory SQLite; не публиковать. |
| [engine.py.bak](../../engine.py.bak) | Историческая копия, не активный модуль. |

## Тесты и вспомогательные скрипты

12 корневых тестовых модулей: `test_construction.py`, `test_csv_rating_export.py`, `test_orgpage_recovery.py`, `test_stage1.py`, `test_stage2.py`, `test_stage3.py`, `test_stage3_1.py`, `test_stage4.py`, `test_stage5.py`, `test_start_page.py`, `test_target_count.py`, `test_unique_urls.py`. Покрывают миграции, контакты, доступность, скрининг, идентичность, рейтинг, географию, пагинацию и накопление. Текущий поднабор и пропуски — [STATUS.md](STATUS.md).

Stage/control/verify/replay/repair/backup-скрипты в дереве — исторические управляющие утилиты. Они могут обращаться к сети, создавать файлы и менять базы; не запускались и не должны автоматически включаться в безопасные тесты.

## Существующая документация и пробелы

- [STAGE1_REPORT.md](../../STAGE1_REPORT.md): миграция; часть описания отбора предшествует Collection.
- [STAGE2_REPORT.md](../../STAGE2_REPORT.md): проверка доступности.
- [STAGE3_REPORT.md](../../STAGE3_REPORT.md): быстрый скрининг.
- [STAGE3_1_REPORT.md](../../STAGE3_1_REPORT.md): идентификация и уточнения скрининга.
- [STAGE4_REPORT.md](../../STAGE4_REPORT.md): рейтинги.
- [STAGE5_REPORT.md](../../STAGE5_REPORT.md): география.
- [ORGPAGE_RECOVERY_REPORT.md](../../ORGPAGE_RECOVERY_REPORT.md): ограничения доступа.
- [ORGPAGE_PAGINATION_REPORT.md](../../ORGPAGE_PAGINATION_REPORT.md): AJAX-пагинация.
- [MEDADVISOR_START_PAGE_REPORT.md](../../MEDADVISOR_START_PAGE_REPORT.md): актуальная модель страницы CLI.
- [CONSTRUCTION_REPORT.md](../../CONSTRUCTION_REPORT.md): направления строительства.
- [UNIQUE_URL_ACCUMULATION_REPORT.md](../../UNIQUE_URL_ACCUMULATION_REPORT.md): новые сайты и накопительный CSV.
- [TARGET_COUNT_REPORT.md](../../TARGET_COUNT_REPORT.md): история; утверждения о CSV до N и CSV-пути MedAdvisor устарели.
- [REPEAT_RUN_AUDIT.md](../../REPEAT_RUN_AUDIT.md): история; описание повторного upsert устарело.

Нет README, dependency/lock-манифеста и единого переносимого тестового окружения. Исторические отчёты требуют проверки на реальные данные перед публикацией; новые документы ссылаются на них вместо дублирования подробностей.

## Скрытые каталоги

| Каталог | Размер файлов, байт | Назначение |
|---|---:|---|
| `__pycache__/` | 659346 | Кэш Python |
| `backups/` | 111205458 | Копии/результаты/исторические артефакты; исключить из публикации |
| `construction_results/` | 4686566 | Копии/результаты/исторические артефакты; исключить из публикации |
| `orgpage_pagination_fix/` | 2412595 | Копии/результаты/исторические артефакты; исключить из публикации |
| `orgpage_recovery/` | 551864 | Копии/результаты/исторические артефакты; исключить из публикации |
| `real_site_check_20261023/` | 27426096 | Копии/результаты/исторические артефакты; исключить из публикации |
| `stage3_1_results/` | 24088406 | Копии/результаты/исторические артефакты; исключить из публикации |
| `stage4_results/` | 797076 | Копии/результаты/исторические артефакты; исключить из публикации |
| `stage5_results/` | 56193401 | Копии/результаты/исторические артефакты; исключить из публикации |
| `start_page_results/` | 820420 | Копии/результаты/исторические артефакты; исключить из публикации |
| `target_count_results/` | 483421 | Копии/результаты/исторические артефакты; исключить из публикации |
| `unique_url_results/` | 95889 | Копии/результаты/исторические артефакты; исключить из публикации |

Порог большого файла: 10 MiB. Единственный файл выше порога — parser_v2.db, 10989568 байт. Меньшие бинарные файлы/данные также исключать. Наличие backups не подтверждает полноту и восстановимость копии.
