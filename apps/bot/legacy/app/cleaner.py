import os

import pandas as pd

from config import DATA_DIR

RAW_FILE = os.path.join(DATA_DIR, "latest_report.csv")
OUTPUT_FILE = os.path.join(DATA_DIR, "cleaned_report.csv")

LOG_DUMP_CATEGORY = "Слив логов"

def _allowed_tags() -> list[str]:
    try:
        from store.locations import tags

        return tags()
    except Exception:
        return [
            "Next",
            "РОКАЛАБ",
            "Юг",
            "АРГАТЕХНН",
            "АРГАТЕХКАЗ",
            "Донор",
            "МскСевер",
            "КалиевАстана",
            "КалиевАлматы",
            "Сигма",
            "Континент",
        ]


# Legacy name — always resolve via getter so admin edits apply.
ALLOWED_TAGS = _allowed_tags()


def is_log_dump_task(task: str) -> bool:
    t = str(task).lower()
    return "log_dump" in t or "disk space by" in t


def run_cleaner():
    if not os.path.exists(RAW_FILE):
        print(f"❌ Ошибка: Файл {RAW_FILE} не найден. Сначала запустите report.py")
        return

    print(f"📥 Загрузка файла: {RAW_FILE}")
    df = pd.read_csv(RAW_FILE, encoding="utf-8-sig")
    if df.empty:
        print("ℹ️  Исходный отчёт пустой — cleaned_report тоже пустой")
        df.to_csv(OUTPUT_FILE, index=False, encoding="utf-8-sig")
        return

    print("\n--- ТЕКУЩЕЕ СОСТОЯНИЕ (До обработки) ---")
    print(df.head())
    print("-" * 30)

    # --- ШАГ 1: ИСПРАВЛЕНИЕ ДАТ ---
    def step_1_fix_dates(df):
        print("⚙️ Применяю: Исправление формата даты...")
        # Naive строки в CSV — UTC; потребители парсят через queue_hours.parse_csv_utc.
        parsed_created = pd.to_datetime(df["Создана"], utc=True)
        df["Создана"] = parsed_created.dt.strftime("%Y-%m-%d %H:%M:%S")
        for col in ("В очереди с", "В очереди до"):
            if col in df.columns:
                parsed = pd.to_datetime(df[col], errors="coerce", utc=True)
                df[col] = parsed.dt.strftime("%Y-%m-%d %H:%M:%S").fillna("")
        return df

    # --- ШАГ 2: ОБРАБОТКА NaN ---
    def step_2_handle_nan(df):
        print("⚙️ Применяю: Замену NaN в Резолюции на 'Открыт'...")
        df['Резолюция'] = df['Резолюция'].fillna("Открыт")
        df['Резолюция'] = df['Резолюция'].replace('fixed', 'Закрыт')
        df['Статус'] = df['Статус'].replace('closed', 'Закрыт')
        df['Статус'] = df['Статус'].replace('delieveryWaiting', 'Ожидание поставки')
        df['Статус'] = df['Статус'].replace('diagnostics', 'В очереди')
        df['Статус'] = df['Статус'].replace('inProgress', 'В очереди')
        df['Статус'] = df['Статус'].replace('moving', 'Перемещение')
        df['Статус'] = df['Статус'].replace('new', 'Перемещение')
        df['Статус'] = df['Статус'].replace('pause', 'В очереди')
        df['Статус'] = df['Статус'].replace('queued', 'В очереди')
        df['Статус'] = df['Статус'].replace('waitingForAnotherTeam', 'Ждем смежников')
        df['Статус'] = df['Статус'].replace('waitingForInspection', 'В очереди')
        df['Статус'] = df['Статус'].replace('treated', 'В очереди')
        df['Статус'] = df['Статус'].replace('check', 'В очереди')
        df["Таймер_простоя"] = df["Статус"] == "open"
        df['Статус'] = df['Статус'].replace('open', 'В очереди')
        return df

    # --- ШАГ 3: ФИЛЬТРАЦИЯ И ВЫДЕЛЕНИЕ ДОНОРА ---
    def process_tags_and_extract_donor(df):
        print("⚙️ Применяю: Выделение статуса 'Донор' в отдельную колонку...")

        def extract_info(tag_string):
            if pd.isna(tag_string) or tag_string == "":
                return "Не Донор", ""

            current_tags = [t.strip() for t in str(tag_string).split(",")]
            allowed = set(_allowed_tags())
            filtered = [t for t in current_tags if t in allowed]

            is_donor = "Донор" if "Донор" in filtered else "Не Донор"
            remaining_tags = [t for t in filtered if t != "Донор"]
            tags_string = ", ".join(remaining_tags)

            return is_donor, tags_string

        res = df['Теги'].apply(lambda x: extract_info(x))
        df['Статус робота'], df['Теги'] = zip(*res)
        return df

    # --- ШАГ 4: ОБРАБОТКА NaN В ПОРТУ ---
    def step_4_handle_port_nan(df):
        print("⚙️ Применяю: Замену NaN в Порту приписки на 'нет разметки'...")
        df['Порт приписки'] = df['Порт приписки'].fillna("нет разметки")
        return df

     # --- ШАГ 3.5: КЛАССИФИКАЦИЯ ЛОКАЦИЙ (НОВАЯ) ---
    def step_3_5_classify_locations(df):
        print("⚙️ Применяю: Классификацию локаций по правилам...")

        def identify_location(row):
            port = str(row['Порт приписки']).strip()
            tags = str(row['Теги']).strip()
            tag_list = [t.strip() for t in tags.split(",") if t.strip()]

            # МскСевер / АрмаМСК — отдельные парки, приоритет над остальными тегами и портом
            if "МскСевер" in tag_list:
                return "Север (Москва)"
            if "АрмаМСК" in tag_list:
                return "АрмаМСК"

            # Порт важнее тега: устаревший тег не меняет локацию, если робот уже в порту.

            # Москва: Next / Сигма / Континент по тегу, остальное → АрмаМСК
            if port == "Moscow Robot":
                if "Next" in tag_list:
                    return "Next"
                if "Сигма" in tag_list:
                    return "Сигма"
                if "Континент" in tag_list:
                    return "Континент"
                return "АрмаМСК"

            # Астана: только с тегом КалиевАстана
            if port == "Astana":
                if "КалиевАстана" in tag_list:
                    return "КалиевАстана"
                return "Прочие локации"

            # Алматы: только с тегом КалиевАлматы
            if port in ("Almaty", "Алматы"):
                if "КалиевАлматы" in tag_list:
                    return "КалиевАлматы"
                return "Прочие локации"

            # Нижний Новгород: только с тегом АРГАТЕХНН (порт без тега → Прочие)
            if port in ("Нижний Новгород", "Nizhny Novgorod"):
                if "АРГАТЕХНН" in tags:
                    return "АРГАТЕХНН"
                return "Прочие локации"

            # Казань: только с тегом АРГАТЕХКАЗ
            if port in ("Казань", "Kazan"):
                if "АРГАТЕХКАЗ" in tags:
                    return "АРГАТЕХКАЗ"
                return "Прочие локации"

            # Санкт-Петербург: по тегу, иначе Мурино
            if port == "Saint Petersburg":
                if "РОКАЛАБ" in tags:
                    return "РОКАЛАБ"
                if "Юг" in tags:
                    return "Юг"
                return "Мурино"

            if port == "Innopolis Robot":
                return "Иннополис"

            # Подхват из тегов только если порт не определил город.
            # КалиевАстана / КалиевАлматы / АрмаМСК — только свой порт + тег (выше).
            if tags != "":
                tag_list = [t.strip() for t in tags.split(",")]
                for tag in tag_list:
                    if tag not in ("Донор", "КалиевАстана", "КалиевАлматы", "АрмаМСК", "МскСевер"):
                        return tag

            return "Прочие локации"

        # Создаем новый столбец
        df['Локация'] = df.apply(identify_location, axis=1)
        return df

    def step_3_6_log_dump_category(df):
        print("⚙️ Применяю: Категорию «Слив логов» для задач log_dump / Disk space by...")
        df["Категория"] = df["Задача"].apply(
            lambda task: LOG_DUMP_CATEGORY if is_log_dump_task(task) else ""
        )
        return df


    # --- ВЫПОЛНЕНИЕ ШАГОВ ---
    df = step_1_fix_dates(df)
    df = step_2_handle_nan(df)
    df = process_tags_and_extract_donor(df)
    df = step_3_5_classify_locations(df)
    df = step_3_6_log_dump_category(df)
    df = step_4_handle_port_nan(df)

    print("\n--- РЕЗУЛЬТАТ ПОСЛЕ ОБРАБОТКИ ---")
    print(df.head())
    print("-" * 30)

    # --- СОХРАНЕНИЕ ---
    print(f"💾 Сохраняю обработанные данные в: {OUTPUT_FILE}")
    df.to_csv(OUTPUT_FILE, index=False, encoding="utf-8-sig")
    print("✅ Готово!")

    # --- АНАЛИЗ ---
    print("\n🔍 Анализ уникальных значений в колонках:")
    print(f"Статусы: {df['Статус'].unique()}")
    print(f"Резолюции: {df['Резолюция'].unique()}")
    print("-" * 30)

if __name__ == "__main__":
    run_cleaner()
