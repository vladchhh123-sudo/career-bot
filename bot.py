"""
КАРЬЕРНЫЙ ГАЙД БОТ — точка входа.

Запуск:
    python bot.py

Бот работает НЕПРЕРЫВНО (long polling в бесконечном цикле):
  • при обрыве сети / ошибке Telegram — автоматический перезапуск через 5 сек;
  • фоновый планировщик прогрева крутится параллельно (asyncio-таска);
  • состояние пользователей периодически сохраняется на диск и переживает рестарт.
"""

import asyncio
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

# Загружаем .env до импорта config (там читаются переменные окружения)
load_dotenv(Path(__file__).resolve().parent / ".env")

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from config import BOT_TOKEN
from app import states
from app.handlers import router
from app.admin import router as admin_router
from app.scheduler import scheduler_loop


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        stream=sys.stdout,
    )
    # Меньше шума от библиотек
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


async def set_default_commands(bot: Bot) -> None:
    """Кнопка «Меню» (⋮) в чате с ботом: список доступных команд."""
    commands = [
        BotCommand(command="start", description="Начать"),
        BotCommand(command="support", description="Написать в поддержку"),
    ]
    await bot.set_my_commands(commands)


async def main() -> None:
    setup_logging()
    logger = logging.getLogger("bot")

    if not BOT_TOKEN:
        logger.error(
            "BOT_TOKEN не задан. Создайте файл .env рядом с bot.py со строкой "
            "BOT_TOKEN=123456:ABC... и перезапустите бота."
        )
        sys.exit(1)

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    # Быстрая проверка токена — падаем сразу, если он невалиден.
    try:
        me = await bot.get_me()
        logger.info("Бот запущен: @%s (id=%d)", me.username, me.id)
    except Exception as exc:  # noqa: BLE001
        logger.error("Не удалось подключиться к Telegram: %s. Проверьте токен и интернет.", exc)
        await bot.session.close()
        sys.exit(1)

    # Регистрируем команды для кнопки «Меню» в чате.
    await set_default_commands(bot)

    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    dp.include_router(admin_router)

    # Восстанавливаем состояние и запускаем планировщик прогрева.
    states.load_state()
    scheduler_task = asyncio.create_task(scheduler_loop(bot))

    try:
        # Непрерывная работа: при любом сбое polling перезапускается.
        while True:
            try:
                await dp.start_polling(
                    bot,
                    allowed_updates=["message", "callback_query", "chat_join_request"],
                    skip_updates=True,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.exception("Сбой polling: %s. Перезапуск через 5 секунд...", exc)
                await asyncio.sleep(5)
    finally:
        logger.info("Останавливаем бота: сохраняем состояние...")
        scheduler_task.cancel()
        try:
            await scheduler_task
        except asyncio.CancelledError:
            pass
        states.save_state()
        await bot.session.close()
        logger.info("Бот остановлен.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Работа бота завершена пользователем.")