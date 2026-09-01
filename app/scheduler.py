"""
КАРЬЕРНЫЙ ГАЙД БОТ — планировщик цепочки прогрева.

Логика (обновлено):
  • Каждому пользователю, который ещё НЕ купил и не заблокировал бота,
    отправляются этапы прогрева по расписанию.
  • Расписание хранится в поле next_warmup_at (unix-время следующего этапа).
    После отправки этапа N следующий придёт через интервал (см. config).
  • Нажатия кнопок НЕ сбрасывают цепочку: ни позиция (warmup_step),
    ни таймер (next_warmup_at) не перезапускаются. Цепочка продолжается
    с того места, где человек нажал кнопку (после этого сообщения).
  • Если пользователь купил — прогревание прекращается.
  • Если пользователь заблокировал бота (403) — прогревание отключается (muted).

Работает в фоне (asyncio-таска), запускается из bot.py при старте.
"""

import asyncio
import logging
import time
from typing import List

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError

from app import texts
from app import states
from app.messaging import send_warmup_step
from config import warmup_interval_seconds

logger = logging.getLogger(__name__)

# Как часто (в секундах) планировщик проверяет очередь.
TICK_SECONDS = 30

# Пауза перед повторной попыткой, если этап не отправился (нет файлов и т.п.)
RETRY_SECONDS = 60


async def _send_step(bot: Bot, user_id: int, step_index: int, user_name: str) -> List[int]:
    try:
        sent = await send_warmup_step(bot, user_id, step_index, user_name)
        logger.info("Прогрев %d → пользователю %d", step_index, user_id)
        return sent
    except TelegramForbiddenError:
        # Пользователь заблокировал бота — больше не пишем ему.
        logger.info("Пользователь %d заблокировал бота — прогревание отключено.", user_id)
        states.set_muted(user_id, True)
        return []
    except Exception as exc:  # noqa: BLE001
        logger.exception("Ошибка отправки этапа %d пользователю %d: %s", step_index, user_id, exc)
        return []


async def warmup_tick(bot: Bot) -> None:
    """Один проход по всем пользователям: отправляем всё, что уже «созрело»."""
    steps = texts.WARMUP_STEPS
    now = time.time()

    for user_id, user in states.all_users().items():
        # Пропускаем купивших, заблокировавших и завершивших цепочку
        if user.get("bought") or user.get("muted"):
            continue

        # Догрев шлём только тем, кто прошёл проверку подписки на канал.
        if not user.get("passed_gate"):
            continue

        step_index = int(user.get("warmup_step", 0))
        if step_index >= len(steps):
            continue

        # Когда должен прийти следующий этап.
        due = user.get("next_warmup_at")
        if due is None:
            # Миграция старых состояний: считаем от последнего действия.
            interval = warmup_interval_seconds(step_index)
            if interval is None:
                continue
            due = float(user.get("last_action") or now) + interval
            states.update(user_id, next_warmup_at=due)

        if now < float(due):
            continue  # ещё не время

        sent = await _send_step(bot, user_id, step_index, user.get("name", ""))

        if not sent:
            # Сообщение не доставлено (например, в папке ещё нет файлов) —
            # НЕ двигаем этап: повторим чуть позже.
            logger.warning(
                "Этап %d пользователю %d не отправлен — повторим через %d сек.",
                step_index, user_id, RETRY_SECONDS,
            )
            states.update(user_id, next_warmup_at=now + RETRY_SECONDS)
            continue

        # Обновляем прогресс: следующий этап + его время прихода.
        next_step = step_index + 1
        interval = warmup_interval_seconds(next_step)
        sent_before = list(user.get("warmup_sent", []))
        states.update(
            user_id,
            warmup_step=next_step,
            warmup_sent=sent_before + [step_index],
            next_warmup_at=(now + interval) if interval is not None else None,
            last_action=now,
        )


async def scheduler_loop(bot: Bot) -> None:
    """Бесконечный цикл планировщика (запускается как фоновая таска)."""
    logger.info("Планировщик прогрева запущен (тик %d сек).", TICK_SECONDS)
    while True:
        try:
            await warmup_tick(bot)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Ошибка в цикле прогрева: %s", exc)

        # Периодически сбрасываем состояние на диск.
        states.save_state()

        await asyncio.sleep(TICK_SECONDS)
