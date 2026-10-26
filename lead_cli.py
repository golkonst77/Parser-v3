"""Commercial lead actions for the existing text menu."""
import json
import lead_scoring as scoring


def handle(choice):
    print('Коммерческий приоритет для ручной проверки; рейтинг не устанавливает нарушения.')
    if choice == '98':
        print('Пересчитано организаций:', scoring.recalculate())
        return
    if choice == '97':
        mode = input('Очередь: 1 — основная (Enter); 2 — только уточнение; 3 — обе с обозначением: ').strip()
        queue = {'':'main','1':'main','2':'review','3':'all'}.get(mode)
        if queue is None:raise ValueError('Неизвестная очередь')
        from geography import choose
        geo=choose()
        filename = input('Файл CSV (Enter = lead_candidates.csv): ').strip() or 'lead_candidates.csv'
        count, filename = scoring.export_csv(filename, queue=queue, geography=geo)
        print(f'Экспортировано контактов: {count}; файл: {filename}')
        return
    leads = scoring.get_leads(include_review=True)
    if choice == '95':
        for title, queue in [('Основная очередь', True), ('Требуется уточнение / недостаточно данных', False)]:
            print(title)
            for lead in [r for r in leads if r['main_queue'] == queue][:20]:
                print(f"{lead['priority_score']:3} | {lead['evidence_coverage']:.0%} | {lead['company']['name']} | {lead['contact_key']} | филиалов: {len(lead['branches'])} | {lead['review_status']}")
        return
    query = input('Название или URL карточки: ').strip().casefold()
    if not query:
        return
    # Show the selected branch's own evidence, rather than a different representative.
    import db
    with db.get_connection() as conn:
        rows = conn.execute('SELECT c.*, s.details_json FROM companies c JOIN lead_scores s ON c.url=s.company_url ORDER BY c.url').fetchall()
    matches = [r for r in rows if query in (r['name'] or '').casefold() or query == r['url'].casefold()]
    for row in matches:
        detail = json.loads(row['details_json'])
        print(row['name'], row['url'])
        print(json.dumps(detail, ensure_ascii=False, indent=2))
    if len(matches) == 1:
        lead = next(r for r in leads if any(b['company']['url'] == matches[0]['url'] for b in r['branches']))
        print('Статус обработки:', lead['processing_status'])
        print('Допустимые статусы:', ', '.join(scoring.PROCESSING))
        status = input('Новый статус (Enter = оставить): ').strip()
        if status:
            scoring.set_processing_status(lead['contact_key'], status)
