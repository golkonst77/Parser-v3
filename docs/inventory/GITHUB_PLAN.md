# План приватного репозитория

> Архивный план 26.10.2026, не действующая инструкция. Пользователь впоследствии
> разрешил PUBLIC-публикацию Parser-v3; код опубликован. Данные находятся отдельно
> в приватном Parser-v3-Data. Не выполнять старые шаги повторно.
> Актуальные сведения: [STATUS](STATUS.md) и [RUNBOOK](RUNBOOK.md).

Цель после отдельного согласования: **golkonst77/Parser-v3**, private. Наличие репозитория и права доступа не проверялись; запросов к GitHub не было. git init/commit/remote add/push и gh repo create не выполнялись. Новая резервная копия не создана.

## Подготовка

Использовать отдельную чистую копию, не инициализировать текущий рабочий каталог. Отдельно согласовать и проверить восстановление резервной копии данных вне Git. Для живой SQLite с WAL/SHM простой перенос одного .db не гарантирует согласованности.

В чистую копию включать только allowlist: 14 runtime-модулей из FILE_MANIFEST, lead_scoring_config.json, отобранные test_*.py, синтетические tests/fixtures и шесть docs/inventory-файлов. README и requirements/pyproject с проверенными версиями подготовить отдельно; сейчас они не создавались. Исторические отчёты включать после проверки данных и устаревших утверждений. При исключении отчётов адаптировать ссылки inventory либо перенести очищенные версии.

Предлагаемая структура сохраняет текущие плоские импорты:

```text
Parser-v3/
  .gitignore                 # после согласования предложения
  README.md                  # будущий документ
  requirements.txt           # будущая фиксация версий
  main.py, parser_run.py, ... # runtime-модули
  lead_scoring_config.json
  test_*.py                  # отобранные тесты
  tests/fixtures/            # только синтетические данные
  docs/inventory/
```

Не включать базы/WAL/SHM, реальные CSV/XLSX/JSON/HTML/PNG, medadvisor.csv, результаты/логи/снимки, backups, *_results, orgpage_recovery/orgpage_pagination_fix, *.bak/*.before, окружения, кэши, .env, cookies/storage_state, ключи и локальные конфиги. Stage/control/replay/repair/verify-скрипты пересмотреть по одному: они могут менять данные и обращаться к сети.

Не запрещать все JSON: lead_scoring_config.json нужен приложению. Тесты частично зависят от исключаемых *_results и real_site_check_20261023; простой перенос не гарантирует полного прохождения. Отдельно согласовать переносимость и синтетические seed-файлы. Исключая medadvisor.csv, нужно решить вопрос CSV-пути меню, а не включать реальные данные ради удобства.

## Проверка перед публикацией

Применить [GITIGNORE_PROPOSAL.txt](GITIGNORE_PROPOSAL.txt) как .gitignore только после подтверждения в будущей чистой копии. Просмотреть каждый добавляемый файл, проверить данные/секреты, воспроизводимость зависимостей/браузера, тесты и ссылки. .gitignore не удаляет tracked-файлы. Приватность ограничивает доступ, но не заменяет очистку. Не использовать git add . или git add -A.

## Команды только после отдельного согласования

Шаблон для действительно подготовленной новой копии; заменить путь на согласованный. Если репозиторий существует, остановиться и отдельно согласовать дальнейшую работу.

```powershell
Set-Location -LiteralPath 'D:\DATA\Parser-v3-clean'
git init -b main
# Сначала подготовить отсутствующие README/requirements и согласованный .gitignore.
git add -- .gitignore README.md requirements.txt main.py parser_run.py engine.py industries.py geography.py db.py target_collection.py medadvisor_catalog.py site_checker.py organization_identity.py quick_screen.py console_progress.py lead_scoring.py lead_cli.py lead_scoring_config.json docs/inventory
# Проверенные тесты/fixtures добавить отдельно явными путями.
git status --short
git diff --cached --stat
git diff --cached --check
git ls-files
# После просмотра staged-содержимого и проверок:
git commit -m 'Initial reviewed Parser v3 source'
gh repo create golkonst77/Parser-v3 --private --source . --remote origin
# Убедиться в PRIVATE до отправки содержимого:
gh repo view golkonst77/Parser-v3 --json nameWithOwner,visibility
git remote -v
git push -u origin main
```

Это команды для review, они не выполнялись. Копия исходников на GitHub не заменяет отдельный backup баз и результатов. Код, зависимости и действующий .gitignore текущего проекта не менялись.
