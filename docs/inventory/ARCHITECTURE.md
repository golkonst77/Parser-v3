# Модули и поток данных

Актуально 28.10.2026, код 5e8782b. Это инвентаризация публикуемого репозитория,
а не повторная инвентаризация всех локальных данных.

| Модуль/файл | Назначение |
|---|---|
| parser_run.py | CLI, диалог, отдельные пути, лимиты, resume, защита от БД другой отрасли |
| main.py | Старое меню, общая БД, дополнительные экспорты и рейтинговые операции |
| industries.py | Восемь отраслей, источники, реальные URL, заголовки рубрик и правила строительства |
| target_collection.py | Маршрутизация, поток ссылок, пригодность, resume, накопительный CSV |
| engine.py | Карточки, контакты, проверка источника, сохранение решений и координация |
| medadvisor_catalog.py | Проверяемые номера страниц MedAdvisor и следующий переход |
| db.py | SQLite, миграции, канонизация, транзакционное сохранение и прежние экспорты |
| geography.py | Россия по умолчанию и явные фильтры |
| site_checker.py | Ограниченные браузерные проверки сайтов, доступность и защита |
| organization_identity.py | Доказательства бренда/филиала |
| quick_screen.py | Краткое наблюдение DOM/сети, yes/no/unknown |
| console_progress.py | Прогресс по решениям текущего run_id |
| lead_scoring.py / lead_cli.py | Сохранённые баллы, очереди и прежние операции; не порог приёма |
| lead_scoring_config.json | Веса и coverage без изменения в этой ревизии |
| requirements.txt | Единственная прямая зависимость: playwright==1.63.0 |

Всего 14 runtime-модулей и 7 test_*.py в Git. Отдельных .ps1/.bat-скриптов
запуска в публикуемом наборе нет: проверяемые команды находятся в RUNBOOK.
Внешние Python-зависимости Playwright ставятся pip; Chromium — отдельный ресурс.

Поток: CLI/меню → engine.run_parser → source_config → run_target →
resume_state (при продолжении) → db.init_db(refresh_existing=False) →
enrich_and_check → card_stream → process_cards → Collection →
save_new_usable_company → export_results.

source_config назначает MedAdvisor только имени «Медицинские клиники»; каталог
по умолчанию живой, с первой страницы. Остальные семь маршрутов — Orgpage.
Низкоуровневое чтение явного медицинского CSV сохранено для старых вызовов.
Orgpage проверяет заголовок рубрики и останавливает неверную маршрутизацию.
Подтверждённая защита каталога не обходится.

SQLite WAL: companies/card_keys/migration_originals; runs/errors/collection_decisions;
site_checks/site_check_state; quick_screens/quick_screen_features;
lead_scores/lead_processing. Отказы журналируются; только пригодные новые записи
попадают в companies целевого потока. Рейтинг сохраняется, но не ограничивает N.
Экспорт повторно применяет правила пригодности к сохранённым проверкам отрасли,
исключает доменные дубли и атомарно заменяет output.

Ограничения resume и экспортов — [RUNBOOK](RUNBOOK.md), доказательства и проблемы —
[STATUS](STATUS.md) и [AUDIT_REPORT](AUDIT_REPORT.md). Исторические FILE_MANIFEST,
GITHUB_PLAN и GITIGNORE_PROPOSAL описывают исходную инвентаризацию 26.10.2026;
они не являются текущими инструкциями публикации/запуска.
