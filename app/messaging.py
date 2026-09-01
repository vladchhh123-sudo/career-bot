"""
КАРЬЕРНЫЙ ГАЙД БОТ — единая точка отправки сообщений.

Порядок отправки (обновлено):
  • Если у сообщения есть медиа: СНАЧАЛА все фото/видео одним альбомом
    (одним сообщением, без подписи и кнопок), СРАЗУ ПОСЛЕ — текст с кнопкой
    отдельным сообщением. Без задержек.
  • Если медиа нет — просто текст с кнопкой.

Хендлеры и планировщик пользуются именно этими функциями,
поэтому логика не дублируется.
"""

import asyncio
import logging
from typing import List, Optional

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest

from app import texts
from app.keyboards import (
    start_keyboard,
    infop_keyboard,
    payment_keyboard,
    warmup_keyboard,
    subscribe_keyboard,
)
from app.media import send_media_group, folder_has_video

logger = logging.getLogger(__name__)

# Пауза после отправки видео перед текстом (секунды).
# Telegram обрабатывает видео на сервере с задержкой, поэтому текст,
# отправленный сразу после видео, может прийти к пользователю РАНЬШЕ видео.
# Небольшая пауза сохраняет порядок «сначала медиа, потом текст».
VIDEO_TEXT_DELAY_SECONDS = 1.0


async def _send_text(
    bot: Bot,
    chat_id: int,
    text: str,
    reply_markup=None,
    parse_mode: str = "HTML",
) -> Optional[int]:
    try:
        msg = await bot.send_message(
            chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode=parse_mode
        )
        return msg.message_id
    except TelegramBadRequest as exc:
        # Если в тексте что-то не так с HTML — пробуем без разметки
        logger.warning("TelegramBadRequest при отправке текста: %s. Отправляем без разметки.", exc)
        try:
            msg = await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
            return msg.message_id
        except Exception:  # noqa: BLE001
            logger.exception("Не удалось отправить текст без разметки.")
            return None
    except Exception as exc:  # noqa: BLE001
        logger.exception("Ошибка отправки текста: %s", exc)
        return None


async def _send_album_and_text(
    bot: Bot,
    chat_id: int,
    folder_name: str,
    text: str,
    reply_markup=None,
    parse_mode: str = "HTML",
) -> List[int]:
    """
    Сначала все медиафайлы одним альбомом, затем текст с кнопкой.
    Оба сообщения уходят подряд, без задержек.
    Возвращает список message_id отправленных сообщений.
    """
    sent = await send_media_group(bot, chat_id, folder_name)

    # Если в альбоме было видео — ждём немного, чтобы текст не «обогнал» его.
    if folder_has_video(folder_name) and VIDEO_TEXT_DELAY_SECONDS > 0:
        await asyncio.sleep(VIDEO_TEXT_DELAY_SECONDS)

    msg_id = await _send_text(bot, chat_id, text, reply_markup=reply_markup, parse_mode=parse_mode)
    if msg_id is not None:
        sent.append(msg_id)
    return sent


async def send_start(bot: Bot, chat_id: int, user_full_name: str) -> List[int]:
    """Стартовое сообщение: альбом из assets/first, затем текст с 2 кнопками."""
    caption = texts.render(texts.START_CAPTION, user_full_name)
    return await _send_album_and_text(
        bot=bot,
        chat_id=chat_id,
        folder_name="first",
        text=caption,
        reply_markup=start_keyboard(),
    )


async def send_infop(
    bot: Bot,
    chat_id: int,
    user_full_name: str,
    step: int | None = None,
) -> List[int]:
    """
    Сообщение id001 «УЗНАТЬ ПОДРОБНЕЕ»: альбом из assets/infop, затем текст с кнопкой.
    step — номер этапа прогрева, если сообщение пришло как этап (id001_resend).
    """
    caption = texts.render(texts.INFOP_CAPTION, user_full_name)
    return await _send_album_and_text(
        bot=bot,
        chat_id=chat_id,
        folder_name="infop",
        text=caption,
        reply_markup=infop_keyboard(step=step),
    )


async def send_subscribe_prompt(bot: Bot, chat_id: int, user_full_name: str) -> Optional[int]:
    """Сообщение с просьбой подписаться на канал (перед стартом)."""
    text = texts.render(texts.SUBSCRIBE_CAPTION, user_full_name)
    return await _send_text(bot, chat_id, text, reply_markup=subscribe_keyboard())


async def send_payment(
    bot: Bot,
    chat_id: int,
    bought: bool = False,
    payment_url: Optional[str] = None,
) -> Optional[int]:
    """Сообщение с оплатой (или подтверждением оплаты, если bought=True)."""
    caption = texts.PURCHASED_CAPTION if bought else texts.PAYMENT_CAPTION
    kb = payment_keyboard(bought=bought, payment_url=payment_url)
    return await _send_text(bot, chat_id, caption, reply_markup=kb)


async def send_purchase_confirmation(bot: Bot, chat_id: int) -> Optional[int]:
    """Сообщение «Оплата получена ✔️». Без кнопок."""
    return await _send_text(bot, chat_id, texts.PURCHASED_CAPTION)


async def send_warmup_step(
    bot: Bot,
    chat_id: int,
    step_index: int,
    user_full_name: str,
) -> List[int]:
    """
    Отправляет этап прогрева по индексу (см. texts.WARMUP_STEPS).
    Возвращает список message_id отправленных сообщений.
    """
    if step_index < 0 or step_index >= len(texts.WARMUP_STEPS):
        logger.error("Неизвестный этап прогрева: %d", step_index)
        return []

    folder, caption_template, button_text = texts.WARMUP_STEPS[step_index]
    caption = texts.render(caption_template, user_full_name)

    # Повтор сообщения id001 (альбом assets/infop + текст с кнопкой)
    if folder == "id001_resend":
        return await send_infop(bot, chat_id, user_full_name, step=step_index)

    # Обычное текстовое сообщение (без медиагруппы)
    if folder is None:
        msg_id = await _send_text(
            bot, chat_id, caption, reply_markup=warmup_keyboard(button_text, step_index)
        )
        return [msg_id] if msg_id is not None else []

    # Альбом из assets/push/<folder> + текст с кнопкой
    return await _send_album_and_text(
        bot=bot,
        chat_id=chat_id,
        folder_name=f"push/{folder}",
        text=caption,
        reply_markup=warmup_keyboard(button_text, step_index),
    )


async def send_error(bot: Bot, chat_id: int) -> None:
    await _send_text(bot, chat_id, texts.ERROR_MESSAGE)
