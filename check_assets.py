"""
КАРЬЕРНЫЙ ГАЙД БОТ — проверка папок с медиа перед запуском.

Запуск:
    python check_assets.py

Скрипт проверяет, что все папки из сценария существуют и содержат нужные
файлы, а также показывает, какие файлы будут отправлены в каждом альбоме.
Если что-то не так — бот всё равно запустится, но пропустит недостающий альбом.
"""

import sys
from pathlib import Path

from config import ASSETS_DIR
from app.media import list_media_files

# Сценарий: папка -> ожидаемые файлы (в порядке сортировки).
# Порядок в списке — только для информации; бот сортирует сам.
EXPECTED = {
    "first": [
        "first1.MP4",
    ],
    "infop": [
        "infop1.png", "infop2.png", "infop3.png",
        "infop4.png", "infop5.png", "infop6.png",
    ],
    "push/reviews": [
        "reviews1.png", "reviews2.png", "reviews3.png",
        "reviews4.png", "reviews5.png", "reviews6.png",
        "reviews7.png", "reviews8.png", "reviews9.png",
    ],
    "push/reject": ["reject1.png"],
    "push/system_will_work": ["system_will_work1.png"],
    "push/hard_find": ["hard_find1.png"],
    "push/script_will_work": ["script_will_work1.png", "script_will_work2.png"],
    "push/costly_mistake": ["costly_mistake1.png"],
}


def main() -> int:
    print("=" * 60)
    print("ПРОВЕРКА МЕДИАФАЙЛОВ — КАРЬЕРНЫЙ ГАЙД БОТ")
    print("=" * 60)

    ok_all = True
    for folder, expected in EXPECTED.items():
        full = ASSETS_DIR / folder
        files = list_media_files(folder)
        names = [p.name for p in files]
        expected_set = set(expected)

        status = "OK "
        if not full.is_dir():
            status = "ERR"
            ok_all = False
        elif expected_set - set(names):
            status = "WARN"  # папка есть, но не хватает ожидаемых файлов
            ok_all = False

        print(f"\n[{status}] assets/{folder}")
        print(f"      Ожидается ({len(expected)}): {', '.join(expected)}")
        if files:
            print(f"      Найдено  ({len(names)}): {', '.join(names)}")
        else:
            print("      Найдено: НЕТ ФАЙЛОВ")

    print("\n" + "-" * 60)
    if ok_all:
        print("Всё в порядке — можно запускать бота:  python bot.py")
    else:
        print("ВНИМАНИЕ: есть расхождения (см. выше).")
        print("ERR — папка не создана/пуста. WARN — не хватает файлов.")
        print("Бот будет пропускать альбомы, файлы которых не найдены.")
    print("-" * 60)
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
