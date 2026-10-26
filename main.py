"""
ГЛАВНОЕ МЕНЮ — Парсер-Комплекс v3.0 (с базой данных)
"""
import sys
import os

def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')

def show_menu():
    from industries import INDUSTRIES
    
    print("=" * 70)
    print("🚀 ПАРСЕР-КОМПЛЕКС v3.0 — С базой данных и репарсингом")
    print("=" * 70)
    print("\n📋 ОТРАСЛИ ДЛЯ ПАРСИНГА:\n")
    
    for num, cfg in INDUSTRIES.items():
        mark = '✅' if cfg['status'] == 'ready' else ''
        suffix = '' if cfg['status'] == 'ready' else '  (в разработке)'
        print(f"   {mark} [{num}] {cfg['emoji']} {cfg['name']}{suffix}")
    
    print("\n" + "-" * 70)
    print("📊 РАБОТА С БАЗОЙ ДАННЫХ:\n")
    print("   📈 [91] Статистика всей базы")
    print("   📤 [92] Экспорт отрасли в Excel (CSV)")
    print("   📦 [93] Экспорт ВСЕЙ базы в Excel")
    print("   🔍 [94] Поиск записей по названию")
    print("   [95] Лучшие кандидаты и очередь уточнения")
    print("   [96] Причины рейтинга организации")
    print("   [97] Экспорт кандидатов CSV")
    print("   [98] Пересчитать рейтинг")
    print("\n" + "=" * 70)

def get_target_count(default):
    while True:
        raw = input(f"\n🎯 Сколько пригодных организаций собрать? (Enter = {default} по умолчанию): ").strip()
        if not raw:
            return default
        if raw.isdigit() and int(raw) > 0:
            return int(raw)
        print("   ⚠️ Введите положительное число или нажмите Enter.")

def show_stats():
    """Показывает статистику по всей базе"""
    import db
    rows, total = db.get_stats()
    
    print("\n" + "=" * 70)
    print("📊 СТАТИСТИКА БАЗЫ ДАННЫХ")
    print("=" * 70)
    print(f"\n{'Отрасль':<40} {'Всего':>6} {'Сайт':>6} {'Тел':>6} {'Email':>6}")
    print("-" * 70)
    
    for r in rows:
        print(f"{r['industry']:<40} {r['total']:>6} {r['working_sites']:>6} {r['with_phone']:>6} {r['with_email']:>6}")
    
    print("-" * 70)
    print(f"{'ИТОГО':<40} {total:>6}")
    print(f"\n💾 Файл базы: {db.DB_FILE} ({os.path.getsize(db.DB_FILE) // 1024} КБ)")
    print("=" * 70)

def export_industry():
    """Экспорт одной отрасли в CSV"""
    import db
    from industries import INDUSTRIES
    
    print("\n📤 ЭКСПОРТ ОТРАСЛИ В EXCEL\n")
    for num, cfg in INDUSTRIES.items():
        print(f"   [{num}] {cfg['emoji']} {cfg['name']}")
    
    raw = input("\nНомер отрасли: ").strip()
    if not raw.isdigit() or int(raw) not in INDUSTRIES:
        print("️ Неверный номер.")
        return
    
    cfg = INDUSTRIES[int(raw)]
    count, filename = db.export_industry_to_csv(cfg['name'])
    print(f"\n✅ Экспортировано {count} записей в файл: {filename}")
    print("💡 Откройте в Excel — колонки уже на месте!")

def export_all():
    """Экспорт всей базы"""
    import db
    count, filename = db.export_all_to_csv()
    print(f"\n✅ Экспортировано {count} записей в файл: {filename}")

def search_records():
    """Поиск по названию"""
    import db
    query = input("\n🔍 Введите часть названия для поиска: ").strip()
    if not query:
        return
    
    conn = db.get_connection()
    rows = conn.execute("""
        SELECT industry, name, phone, site, site_status 
        FROM companies 
        WHERE name LIKE ?
        ORDER BY name
    """, (f'%{query}%',)).fetchall()
    conn.close()
    
    print(f"\n Найдено {len(rows)} записей:\n")
    for r in rows:
        status_icon = '✅' if r['site_status'] == 'Работает' else '❌'
        print(f"   {status_icon} [{r['industry']}] {r['name']}")
        print(f"      📞 {r['phone']}  |  🌐 {r['site']}")
        print()

def main():
    import db
    db.init_db()
    from industries import INDUSTRIES
    from engine import run_parser
    
    while True:
        clear_screen()
        show_menu()
        
        choice = input("\n👉 Ваш выбор: ").strip().lower()
        
        if choice == 'q':
            print("\n До свидания!")
            sys.exit(0)
        
        # Работа с базой
        if choice == '91':
            show_stats()
            input("\n🔙 Нажмите Enter...")
            continue
        elif choice == '92':
            export_industry()
            input("\n🔙 Нажмите Enter...")
            continue
        elif choice == '93':
            export_all()
            input("\n🔙 Нажмите Enter...")
            continue
        elif choice == '94':
            search_records()
            input("\n🔙 Нажмите Enter...")
            continue
        
        if choice in ('95', '96', '97', '98'):
            from lead_cli import handle
            try:
                handle(choice)
            except (ValueError, OSError) as exc:
                print(f"Ошибка рейтинга: {exc}")
            input("Нажмите Enter...")
            continue

        # Парсинг отраслей
        if not choice.isdigit() or int(choice) not in INDUSTRIES:
            print(f"\n️ Нет такого пункта.")
            input("🔙 Нажмите Enter...")
            continue
        
        cfg = dict(INDUSTRIES[int(choice)])
        
        if cfg['status'] != 'ready':
            print(f"\n🔒 Отрасль «{cfg['name']}» пока в разработке.")
            input(" Нажмите Enter...")
            continue
        
        print(f"\n{cfg['emoji']} Выбрано: {cfg['name']}")
        cfg['geography'] = None  # Вся Россия; фильтр доступен через parser_run.py
        target = get_target_count(cfg['default_count'])
        
        try:
            run_parser(cfg, target)
        except ImportError as e:
            print(f"\n❌ Ошибка импорта: {e}")
        except Exception as e:
            print(f"\n❌ Ошибка: {e}")
        
        input("\n Нажмите Enter, чтобы вернуться в меню...")

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n👋 Программа остановлена.")
        sys.exit(0)