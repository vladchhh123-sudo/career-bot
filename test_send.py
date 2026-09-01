"""
КАРЬЕРНЫЙ ГАЙД БОТ — тестовая рассылка всех альбомов в ваш чат.

Помогает проверить, что все фотографии/видео отправляются корректно.

Запуск:
    python test_send.py <ваш_chat_id>

Где <ваш_chat_id> — ваш числовой ID в Telegram (узнать у @userinfobot).

Скрипт по очереди отправит вам: старт, «узнать подробнее» и все этапы
прогрева — и выведет в консоль, что именно было отправлено и с какими
ошибками, если они были.
"""

import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from config import BOT_TOKEN
from app import messaging, texts


async def main(chat_id: int) -> None:
    if not BOT_TOKEN:
        print("ОШИБКА: BOT_TOKEN не задан. Заполните .env и повторите.")
        sys.exit(1)

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    print("Отправляю стартовое сообщение (first)...")
    ids = await messaging.send_start(bot, chat_id, "Тест")
    print(f"  → отправлено сообщений: {len(ids)}")
    await asyncio.sleep(2)

    print("Отправляю «Узнать подробнее» (infop)...")
    ids = await messaging.send_infop(bot, chat_id, "Тест")
    print(f"  → отправлено сообщений: {len(ids)}")
    await asyncio.sleep(2)

    for i, (folder, _, _btn) in enumerate(texts.WARMUP_STEPS):
        label = str(folder) if folder else "текст"
        print(f"Отправляю этап {i} ({label})...")
        ids = await messaging.send_warmup_step(bot, chat_id, i, "Тест")
        print(f"  → отправлено сообщений: {len(ids)}")
        await asyncio.sleep(2)

    await bot.session.close()
    print("\nГотово. Проверьте чат и логи выше.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Укажите ваш chat_id: python test_send.py 123456789")
        sys.exit(1)
    try:
        chat_id = int(sys.argv[1])
    except ValueError:
        print("chat_id должен быть числом.")
        sys.exit(1)
    asyncio.run(main(chat_id))
