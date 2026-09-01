"""
КАРЬЕРНЫЙ ГАЙД БОТ — клавиатуры (inline-кнопки).

Все callback_data закодированы через `:` и только здесь, чтобы в одном месте
было видно, какие кнопки есть в проекте.

Формат: "cb:<action>:<step>"
  action — действие (get_system / get_info / pay)
  step   — номер этапа прогрева, в сообщении которого стоит кнопка
           (-1 для кнопок в стартовом сообщении и в id001).
"""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import CHANNEL_LINK
from app import texts

# Префикс всех callback-данных бота
CB = "cb"

# Действия
ACT_GET_SYSTEM = "get_system"       # ведёт к сообщению с оплатой
ACT_GET_INFO = "get_info"           # ведёт к сообщению «УЗНАТЬ ПОДРОБНЕЕ» (id001)
ACT_PAY = "pay"                     # кнопка «ОПЛАТИТЬ»
ACT_CHECK_SUB = "check_sub"         # кнопка «Я ПОДПИСАЛСЯ(АСЬ) ✅»


def _mk(action: str, step: int | None = None) -> str:
    """Кодирует callback: "cb:<action>:<step>". step=None → -1."""
    return f"{CB}:{action}:{-1 if step is None else step}"


def start_keyboard() -> InlineKeyboardMarkup:
    """Кнопки стартового сообщения."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=texts.START_KEYBOARD[0], callback_data=_mk(ACT_GET_SYSTEM)),
    )
    builder.row(
        InlineKeyboardButton(text=texts.START_KEYBOARD[1], callback_data=_mk(ACT_GET_INFO)),
    )
    return builder.as_markup()


def infop_keyboard(step: int | None = None) -> InlineKeyboardMarkup:
    """
    Кнопка под сообщением «УЗНАТЬ ПОДРОБНЕЕ» (id001).

    step — если сообщение пришло как этап прогрева (id001_resend),
    передаётся номер этого этапа, чтобы цепочка продолжилась после него.
    """
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=texts.INFOP_KEYBOARD[0], callback_data=_mk(ACT_GET_SYSTEM, step)
        ),
    )
    return builder.as_markup()


def payment_keyboard(bought: bool = False, payment_url: str | None = None) -> InlineKeyboardMarkup:
    """
    Клавиатура сообщения с оплатой.
      • bought=False → кнопка «ОПЛАТИТЬ» (ссылка на оплату, если передана).
      • bought=True  → без кнопок (оплата уже получена).
    """
    builder = InlineKeyboardBuilder()
    if not bought:
        if payment_url:
            builder.row(InlineKeyboardButton(text=texts.PAYMENT_KEYBOARD[0], url=payment_url))
        else:
            # Пока ссылка не задана — кнопка молча "глотает" нажатие.
            builder.row(
                InlineKeyboardButton(text=texts.PAYMENT_KEYBOARD[0], callback_data=_mk(ACT_PAY))
            )
    return builder.as_markup()


def subscribe_keyboard() -> InlineKeyboardMarkup:
    """
    Клавиатура сообщения с просьбой подписаться:
      • «Подписаться на канал» — ссылка-приглашение;
      • «Я ПОДПИСАЛСЯ(АСЬ) ✅» — проверка подписки.
    """
    builder = InlineKeyboardBuilder()
    if CHANNEL_LINK:
        builder.row(
            InlineKeyboardButton(text=texts.SUBSCRIBE_BUTTONS[0], url=CHANNEL_LINK)
        )
    builder.row(
        InlineKeyboardButton(text=texts.SUBSCRIBE_BUTTONS[1], callback_data=_mk(ACT_CHECK_SUB))
    )
    return builder.as_markup()


def warmup_keyboard(button_text: str, step_index: int) -> InlineKeyboardMarkup:
    """
    Клавиатура сообщений прогрева — одна кнопка покупки.

    step_index — номер этапа прогрева. Нужен, чтобы при нажатии кнопки
    цепочка продолжилась с сообщения ПОСЛЕ этого этапа.
    """
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=button_text, callback_data=_mk(ACT_GET_SYSTEM, step_index)
        ),
    )
    return builder.as_markup()


def parse_callback(data: str | None) -> tuple[str, int | None] | None:
    """
    Разбирает callback_data вида "cb:<action>:<step>".
    Возвращает (action, step) или None, если данные не от бота.
    step = -1 → None (кнопка не из цепочки прогрева).
    """
    if not data:
        return None
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != CB:
        return None
    action = parts[1]
    try:
        raw_step = int(parts[2])
    except ValueError:
        raw_step = -1
    step = None if raw_step < 0 else raw_step
    return action, step
