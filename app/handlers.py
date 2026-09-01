"""
КАРЬЕРНЫЙ ГАЙД БОТ — обработчики сообщений и нажатий кнопок.
"""

import logging

from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, ChatJoinRequest

from config import PAYMENT_URL
from app import states
from app import messaging
from app import subscription
from app import admin
from app import texts
from app.keyboards import (
    parse_callback,
    ACT_GET_SYSTEM,
    ACT_GET_INFO,
    ACT_PAY,
    ACT_CHECK_SUB,
)

logger = logging.getLogger(__name__)

router = Router()


def _payment_url() -> str | None:
    """Ссылка на оплату (из .env / config.py). Пустая строка → None."""
    url = (PAYMENT_URL or "").strip()
    return url or None


def _user_name(message: Message) -> str:
    return message.from_user.full_name or ""


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    """
    Команда /start.
    Если пользователь не подписан на канал — просим подписаться.
    Если подписан (или подал заявку) — открываем стартовое сообщение.
    """
    user_id = message.from_user.id
    states.set_name(user_id, _user_name(message))
    states.set_username(user_id, message.from_user.username or "")

    # 🔔 Уведомляем админов о новом входе в бота (можно выключить в панели).
    await admin.notify_admins_new_entry(message.bot, message.from_user)

    if await subscription.is_subscribed(message.bot, user_id):
        states.mark_gate_passed(user_id)
        await messaging.send_start(message.bot, message.chat.id, _user_name(message))
    else:
        states.touch(user_id)
        await messaging.send_subscribe_prompt(
            message.bot, message.chat.id, _user_name(message)
        )


@router.chat_join_request()
async def on_join_request(event: ChatJoinRequest) -> None:
    """
    Пользователь подал заявку на вступление в канал — запоминаем,
    чтобы кнопка «Я ПОДПИСАЛСЯ(АСЬ) ✅» пропустила его, даже если
    заявка ещё ждёт одобрения админа.
    """
    states.set_join_requested(event.from_user.id, True)
    logger.info("Заявка на вступление в канал от пользователя %s", event.from_user.id)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    """Справка (служебная, не из сценария)."""
    await message.answer(
        "Это бот с гайдом по поиску удалённой работы.\n\n"
        "Напиши /start, чтобы начать.",
    )


@router.message(Command("support"))
async def cmd_support(message: Message) -> None:
    """
    Обычный пользователь пишет /support — просим задать вопрос.
    Следующее его текстовое сообщение уйдёт админам как обращение.
    """
    user_id = message.from_user.id
    states.set_name(user_id, _user_name(message))
    states.set_username(user_id, message.from_user.username or "")
    states.set_awaiting_support(user_id, True)
    await message.answer(f"{texts.first_name(_user_name(message))}, напиши свой вопрос.")


@router.message(F.text & ~F.text.startswith("/"))
async def on_support_question(message: Message) -> None:
    """
    Текст от пользователя, который ждёт вопрос в поддержку, —
    отправляем админам в заданном формате.
    """
    user_id = message.from_user.id
    if not states.get(user_id).get("awaiting_support"):
        return

    states.set_awaiting_support(user_id, False)
    await admin.notify_admins_support(message.bot, message.from_user, message.text or "")
    await message.answer("📨 Твой вопрос отправлен. Ответ придёт сюда, в этот чат.")


@router.message(Command("paid"))
async def cmd_paid(message: Message) -> None:
    """
    Ручная отметка оплаты (запасной вариант, если вебхук Tribute ещё не настроен).

    Использование:
      /paid          — ответом на сообщение пользователя
      /paid 123456   — по user_id

    Доступно только администраторам (вошедшим по /adminkas или из ADMIN_IDS).
    """
    sender_id = message.from_user.id

    if not states.is_admin(sender_id):
        await message.answer("⛔️ Эта команда доступна только администратору.")
        return

    target_id: int | None = None
    if message.reply_to_message and message.reply_to_message.from_user:
        target_id = message.reply_to_message.from_user.id
    else:
        parts = (message.text or "").split()
        if len(parts) > 1 and parts[1].lstrip("-").isdigit():
            target_id = int(parts[1])

    if target_id is None:
        await message.answer(
            "Укажите, кого отметить как оплатившего:\n"
            "• ответьте на сообщение пользователя командой /paid\n"
            "• или напишите /paid <user_id>"
        )
        return

    states.mark_bought(target_id)
    await messaging.send_purchase_confirmation(message.bot, target_id)
    await message.answer(f"✅ Пользователь {target_id} отмечен как оплативший.")


# ---------------------------------------------------------------------------
# Нажатия inline-кнопок
# ---------------------------------------------------------------------------
@router.callback_query(~F.data.startswith("adm:"))
async def on_callback(query: CallbackQuery) -> None:
    parsed = parse_callback(query.data)
    if parsed is None:
        # Чужой callback — игнорируем, но отвечаем, чтобы убрать "часики"
        await query.answer()
        return

    action, step = parsed
    user_id = query.from_user.id
    user = states.get(user_id)

    if action == ACT_GET_SYSTEM:
        # Кнопки «ПОЛУЧИТЬ СИСТЕМУ» / «ЗАБРАТЬ СИСТЕМУ» / «ПОЛУЧИТЬ ДОСТУП».
        # Показываем сообщение с оплатой.
        await query.answer()
        await messaging.send_payment(
            query.bot,
            query.message.chat.id,
            bought=bool(user.get("bought")),
            payment_url=_payment_url(),
        )
        # Цепочка прогрева продолжается с сообщения ПОСЛЕ того, где нажали
        # кнопку (не сбрасывается и не перезапускается).
        states.on_buy_click(user_id, step)

    elif action == ACT_GET_INFO:
        # Кнопка «УЗНАТЬ ПОДРОБНЕЕ 🔎» → сообщение id001
        await query.answer()
        await messaging.send_infop(query.bot, query.message.chat.id, query.from_user.full_name or "")

    elif action == ACT_CHECK_SUB:
        # Кнопка «Я ПОДПИСАЛСЯ(АСЬ) ✅» — проверяем подписку заново.
        if await subscription.is_subscribed(query.bot, user_id):
            await query.answer("Отлично! Доступ открыт 🎉")
            states.mark_gate_passed(user_id)
            await messaging.send_start(
                query.bot, query.message.chat.id, query.from_user.full_name or ""
            )
        else:
            await query.answer(
                "Ты ещё не подписался(ась) на канал 😔\n"
                "Подпишись и нажми кнопку ещё раз.",
                show_alert=True,
            )

    elif action == ACT_PAY:
        # Кнопка «ОПЛАТИТЬ» без ссылки — подсказываем, что платёж настраивается
        await query.answer("Оплата подключается: пришлите ссылку на оплату — и кнопка заработает.", show_alert=True)

    else:
        await query.answer()
