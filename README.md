# Parser v3

Сбор пригодных организаций по России: медицинские клиники — MedAdvisor,
остальные семь отраслей — собственные рубрики Orgpage. Результат: контакты,
проверки сайтов и ограниченный первичный скрининг в SQLite и CSV.
Сохранённый рейтинг описывает проверку и не служит условием приёма.

Проверено локально на Windows, Python 3.14.2, Playwright 1.63.0 и Chromium.
Для нового окружения (в настроенном повторять установку не требуется):

```powershell
python -m pip install -r requirements.txt
python -m playwright install chromium
```

Малый отдельный сбор:

```powershell
python -B -u .\parser_run.py --industry 3 --target 5 --max-cards 30 --max-pages 3 --database .\local_run\construction_demo\data.db --output .\local_run\construction_demo\sample.csv
```

Для нового сбора выбирайте новые пути. Без `--resume` target означает число
новых пригодных организаций, с `--resume` — итог отрасли в существующей отдельной
БД. CSV накопительный по отрасли из этой БД и заменяет указанный файл.
Не используйте один output для разных баз/отраслей; прежний снимок CSV при
необходимости скопируйте. Подробнее — в RUNBOOK.

Все восемь отраслей прошли реальные малые проверки 28.10.2026: по одной пригодной
организации. Большой сбор всех отраслей не проверялся. Доступные тесты репозитория:

```powershell
python -B -m unittest discover -v
python -B .\parser_run.py --help
```

Тесты создают собственные временные БД/CSV и не требуют внешних каталогов.
`python -B parser_run.py` открывает диалог. Старое `main.py` также запускает все
отрасли, но использует общую parser_v2.db и при старте выполняет миграцию.
Для изолированных сборов используйте CLI с явными путями.

- [Запуск, продолжение, ошибки, восстановление](docs/inventory/RUNBOOK.md)
- [Восемь отраслей и реальные URL](docs/inventory/INDUSTRIES.md)
- [Подтверждённый статус и ограничения](docs/inventory/STATUS.md)
- [Модули и поток данных](docs/inventory/ARCHITECTURE.md)
- [Ревизия и оставшиеся проблемы](docs/inventory/AUDIT_REPORT.md)
- [Проверки второго этапа](docs/ALL_INDUSTRIES_20261028.md)

Код опубликован в публичном [golkonst77/Parser-v3](https://github.com/golkonst77/Parser-v3).
CSV/SQLite, логи, архивы и секреты исключены из Git. Контрольная точка 27.10.2026:
1000 медицинских клиник, 107 организаций красоты, код 30eb0bc; данные отдельно
в приватном Parser-v3-Data. Уровень подтверждения — в STATUS.
