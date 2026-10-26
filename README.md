# Parser v3

Windows / Python 3.14.2. Исходники подготовлены отдельно от рабочих данных.

```powershell
python -m pip install -r requirements.txt
python -m playwright install chromium
python -B parser_run.py --industry 2 --target 20 --max-cards 100 --database local_run/sample.db --output local_run/sample.csv
```

Установка и браузер требуют доступа к сети. CLI без аргументов задаёт вопросы. Отрасли: 1 медицина, 2 красота, 3 строительство; остальные недоступны. N — новые пригодные сайты, CSV накопительный по отрасли. Недобор возможен при блокировке/исчерпании/лимитах.

MedAdvisor: `python -B parser_run.py --industry 1 --target 2 --start-page 1 --database local_run/medical.db --output local_run/medical.csv`. Только parser_run.py принимает --start-page; main.py использует локальный medadvisor.csv, который намеренно не включён. Предпочтителен CLI.

Реальные БД/CSV/логи/снимки, ключи и сеансы не включены. docs/inventory — историческая инвентаризация исходного каталога; ссылки на не включённые отчёты/данные могут отсутствовать. Рейтинг сохранён, но не ограничивает приём. Полный набор тестов не переносился.

Безопасные тесты:
```powershell
python -B -m unittest test_orgpage_recovery test_target_count.CursorTests test_start_page.InteractivePageTests test_start_page.CatalogPageTests
```

Не запускать весь test_target_count: его файловые сценарии требуют отдельного тестового каталога. Реальный сбор создаёт данные и обращается к внешним сайтам. Выбирайте новые пути output/database; автоматического 15-минутного ограничения в самом CLI нет.

Проверка 26.10.2026: импорт 14 runtime-модулей успешен; 22 перечисленных mock-теста прошли. Staged-набор из 27 файлов проверен по allowlist и шаблонам секретов, совпадений не найдено. Git diff --check обнаруживает только унаследованные пробелы в исходниках; косметические исправления не выполнялись.

Диагностика 26.10.2026: в защитной среде Codex sync_playwright().__enter__() не завершился за 20 секунд. В разрешённой среде драйвер, установленный Chromium и Orgpage работают (HTTP 200). Один сбор N=5/max_cards=30 завершён за 77.91 с: просмотрено 17, принято/экспортировано 5; отказы: 8 site_unavailable_or_unchecked, 4 site_missing_or_invalid. Исходный код парсера не исправлялся: блокирующий дефект вне защитной среды не воспроизведён. Данные теста остаются только в исходном каталоге local_run/diagnostic_5_20261026 и в Git не добавляются. Временный diagnose_launch.py в исходном каталоге печатает DIAG BEGIN/END для imports, sync_driver_start, browser_launch, context_create, page_create, navigation и card_fetch; операции ограничены 20–25 с, запуск — 90 с.

Git credential helper manager содержит учётные данные, но GitHub API для golkonst77/Parser-v3 вернул 404: репозиторий отсутствует либо недоступен текущей учётной записи. Владелец и private не подтверждены, remote отсутствует, push не выполнялся. Для подготовки remote через https://github.com/new выбрать владельца golkonst77, имя Parser-v3, Private и не добавлять README/.gitignore/license. Если репозиторий уже существует, предоставить доступ текущей учётной записи. Публикация возможна только после проверки владельца и private; gh не нужен.
