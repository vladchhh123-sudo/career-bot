"""
КАРЬЕРНЫЙ ГАЙД БОТ — админ-панель.

Вход: /adminkas <пароль>. Админом становится ЛЮБОЙ, кто ввёл верный пароль
(не привязано к конкретному ID). Пароль задаётся в config (.env → ADMIN_PASSWORD).

Команды админа:
  /adminkas <пароль>       — вход в админ-панель
  /admin                   — показать панель (кнопки) и список команд
  /stats                   — общая статистика
  /user <ID|@username>     — карточка пользователя (шаг, история шагов)
  /send <получатели> | <текст>  — рассылка (all = всем; несколько через запятую;
                                   фото — отправьте фото и ответьте на него /send)
  /support_answer <ID|@username> | <текст> — ответ на обращение
  /adminlogout             — выход из админ-панели

Форматирование в /send и /support_answer — HTML:
  <b>bold</b>, <i>italic</i>, <blockquote>цитата</blockquote>,
  <a href="https://...">ссылка</a>
"""

import logging
import time
from html import escape as _esc

from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import ADMIN_PASSWORD
from app import states
from app import texts

logger = logging.getLogger(__name__)

router = Router()

# Человекочитаемые названия шагов цепочки прогрева (порядок = WARMUP_STEPS)
STEP_LABELS = [
    "Отзывы (30 мин)",
    "Отказы (6 ч)",
    "Подойдёт ли мне система (15,3 ч)",
    "Почему сложно найти (24 ч)",
    "Топ-15 компаний (28 ч)",
    "Скрипты (48 ч)",
    "5 свежих вакансий (72 ч)",
    "Повтор «Узнать подробнее» (82 ч)",
    "Ошибка, которая дорого обходится (120 ч)",
]


def _rel(ts) -> str:
    """Время в прошлом человекочитаемо (сколько назад)."""
    if not ts:
        return "—"
    d = time.time() - float(ts)
    if d < 60:
        return "только что"
    if d < 3600:
        return f"{int(d // 60)} мин назад"
    if d < 86400:
        return f"{int(d // 3600)} ч назад"
    return f"{int(d // 86400)} дн назад"


def _in(ts) -> str:
    """Время в будущем человекочитаемо (через сколько)."""
    if not ts:
        return "—"
    d = float(ts) - time.time()
    if d <= 0:
        return "уже пора"
    if d < 3600:
        return f"через {int(d // 60)} мин"
    if d < 86400:
        return f"через {int(d // 3600)} ч"
    return f"через {int(d // 86400)} дн"


def _name_of(user: dict) -> str:
    return _esc(user.get("name")) or "—"


def _username_of(user: dict) -> str:
    u = user.get("username")
    return f"@{_esc(u)}" if u else "—"


def _resolve(arg: str) -> int | None:
    """ID или @username → user_id (или None)."""
    arg = (arg or "").strip()
    if not arg:
        return None
    if arg.startswith("@"):
        return states.find_user_id_by_username(arg)
    if arg.lstrip("-").isdigit():
        return int(arg)
    return None


def _collect_targets(recipients_raw: str) -> list[int]:
    """Разбирает получателей рассылки: all | id | @username | список через запятую."""
    recipients_raw = (recipients_raw or "").strip()
    if recipients_raw.lower() == "all":
        return list(states.all_users().keys())

    ids: list[int] = []
    for part in recipients_raw.split(","):
        part = part.strip()
        if not part:
            continue
        if part.startswith("@"):
            uid = states.find_user_id_by_username(part)
            if uid is not None:
                ids.append(uid)
        elif part.lstrip("-").isdigit():
            ids.append(int(part))

    seen: set = set()
    out: list[int] = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


async def _send_text_safe(bot, user_id: int, text: str) -> None:
    """Текст с HTML; если разметка сломана — шлём как есть."""
    try:
        await bot.send_message(user_id, text, parse_mode="HTML")
    except TelegramBadRequest:
        await bot.send_message(user_id, text)


async def _send_media(bot, user_id: int, kind: str, file_id: str, caption: str) -> None:
    """Медиа с подписью (HTML). Если разметка сломана — без неё."""
    senders = {
        "photo": bot.send_photo,
        "video": bot.send_video,
        "document": bot.send_document,
        "animation": bot.send_animation,
    }
    kwargs = {}
    if caption:
        kwargs["caption"] = caption
        kwargs["parse_mode"] = "HTML"
    try:
        await senders[kind](user_id, file_id, **kwargs)
    except TelegramBadRequest:
        kwargs.pop("parse_mode", None)
        await senders[kind](user_id, file_id, **kwargs)


# ---------------------------------------------------------------------------
# Уведомления админам (используются и из handlers.py)
# ---------------------------------------------------------------------------
async def notify_admins_new_entry(bot, user) -> None:
    """🔔 Новый вход в бота — всем админам (если уведомления включены)."""
    if not states.get_notify_start():
        return
    admins = states.all_admin_ids()
    if not admins:
        return

    username = f"@{_esc(user.username)}" if user.username else "—"
    text = (
        "🔔 <b>Новый вход в бота</b>\n"
        "\n"
        f"Имя: {_esc(user.full_name) or '—'}\n"
        f"Username: {username}\n"
        f"ID: <code>{user.id}</code>"
    )
    for aid in admins:
        try:
            await bot.send_message(aid, text, parse_mode="HTML")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Не удалось уведомить админа %s: %s", aid, exc)


async def notify_admins_support(bot, user, question_text: str) -> None:
    """⚠️ Новое обращение в поддержку — всем админам."""
    admins = states.all_admin_ids()
    if not admins:
        return

    username = f"@{_esc(user.username)}" if user.username else "—"
    text = (
        "⚠️ <b>Новое обращение</b>\n"
        "\n"
        f"Имя: {_esc(user.full_name) or '—'}\n"
        f"Username: {username}\n"
        f"ID: <code>{user.id}</code>\n"
        "\n"
        "<b>Текст обращения:</b>\n"
        f"{_esc(question_text or '')}"
    )
    for aid in admins:
        try:
            await bot.send_message(aid, text, parse_mode="HTML")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Не удалось уведомить админа %s: %s", aid, exc)


# ---------------------------------------------------------------------------
# Тексты панели
# ---------------------------------------------------------------------------
def _welcome_text() -> str:
    return (
        "🔐 <b>Доступ открыт!</b>\n"
        "\n"
        "Ты вошёл в админ-панель.\n"
        "\n"
        "<b>Команды:</b>\n"
        "/stats — общая статистика\n"
        "/user ID_или_@username — карточка пользователя\n"
        "/send получатели | текст — рассылка (all = всем)\n"
        "/support_answer ID_или_@username | текст — ответ на обращение\n"
        "/adminlogout — выйти\n"
        "\n"
        "<b>Рассылка с фото:</b> отправь боту фото, затем ответь на него "
        "командой /send с получателями и подписью.\n"
        "\n"
        "<b>Форматирование</b> (HTML): <code>&lt;b&gt;жирный&lt;/b&gt;</code>, "
        "<code>&lt;i&gt;курсив&lt;/i&gt;</code>, <code>&lt;blockquote&gt;цитата&lt;/blockquote&gt;</code>, "
        "<code>&lt;a href=\"URL\"&gt;ссылка&lt;/a&gt;</code>"
    )


def _panel_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📊 Общая статистика", callback_data="adm:stats"))
    label = "🔔 Уведомления о входах: ВКЛ" if states.get_notify_start() else "🔕 Уведомления о входах: ВЫКЛ"
    builder.row(InlineKeyboardButton(text=label, callback_data="adm:toggle_notify"))
    builder.row(InlineKeyboardButton(text="🔒 Выйти", callback_data="adm:logout"))
    return builder.as_markup()


def _stats_text() -> str:
    users = states.all_users()
    total = len(users)
    passed = sum(1 for u in users.values() if u.get("passed_gate"))
    bought = sum(1 for u in users.values() if u.get("bought"))
    muted = sum(1 for u in users.values() if u.get("muted"))
    in_warmup = sum(
        1 for u in users.values()
        if u.get("passed_gate") and not u.get("bought") and not u.get("muted")
    )

    lines = ["📊 <b>Общая статистика</b>", ""]
    lines.append(f"👥 Всего пользователей: <b>{total}</b>")
    lines.append(f"🔓 Прошли подписку: <b>{passed}</b>")
    lines.append(f"💳 Купили: <b>{bought}</b>")
    lines.append(f"⏳ В догреве: <b>{in_warmup}</b>")
    lines.append(f"🔕 Заблокировали бота: <b>{muted}</b>")

    step_count = [0] * len(texts.WARMUP_STEPS)
    finished = 0
    for u in users.values():
        s = int(u.get("warmup_step", 0) or 0)
        if s >= len(texts.WARMUP_STEPS):
            finished += 1
        else:
            step_count[s] += 1

    lines.append("")
    lines.append("📈 <b>На каком шаге пользователи:</b>")
    for i, label in enumerate(STEP_LABELS):
        lines.append(f"{i + 1}. {label} — <b>{step_count[i]}</b>")
    lines.append(f"🏁 Завершили цепочку — <b>{finished}</b>")
    return "\n".join(lines)


def _user_card_text(user: dict) -> str:
    uid = user.get("id")
    bought = bool(user.get("bought"))
    passed = bool(user.get("passed_gate"))
    muted = bool(user.get("muted"))
    step = int(user.get("warmup_step", 0) or 0)
    sent = list(user.get("warmup_sent") or [])
    nxt = user.get("next_warmup_at")
    last = user.get("last_action")

    lines = ["👤 <b>Карточка пользователя</b>", ""]
    lines.append(f"Имя: {_name_of(user)}")
    lines.append(f"Username: {_username_of(user)}")
    lines.append(f"ID: <code>{uid}</code>")
    lines.append("")
    lines.append(f"💳 Купил: {'✅ да' if bought else '❌ нет'}")
    lines.append(f"🔓 Подписка: {'✅ пройдена' if passed else '❌ не пройдена'}")
    if muted:
        lines.append("🔕 Заблокировал бота: да")
    lines.append("")

    if not passed:
        lines.append("📌 Статус: <b>ещё не прошёл проверку подписки</b>")
    elif step >= len(texts.WARMUP_STEPS):
        lines.append("📌 Цепочка догрева: <b>завершена</b>")
    else:
        label = STEP_LABELS[step] if step < len(STEP_LABELS) else "—"
        lines.append(f"📌 Сейчас на шаге: <b>{step + 1}. {label}</b>")
    lines.append(f"⏰ Следующее сообщение: {_in(nxt)}")
    lines.append(f"🕐 Последнее действие: {_rel(last)}")
    lines.append("")

    if sent:
        lines.append("📜 <b>История шагов (уже отправлено):</b>")
        for s in sent:
            if 0 <= s < len(STEP_LABELS):
                lines.append(f"✅ {s + 1}. {STEP_LABELS[s]}")
    else:
        lines.append("📜 <b>История шагов:</b> ещё ничего не отправлено")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Команды
# ---------------------------------------------------------------------------
@router.message(Command("adminkas"))
async def cmd_adminkas(message: Message, command: CommandObject) -> None:
    pwd = (command.args or "").strip()
    if ADMIN_PASSWORD and pwd == ADMIN_PASSWORD:
        states.add_admin(message.from_user.id)
        await message.answer(_welcome_text(), reply_markup=_panel_keyboard())
    else:
        await message.answer("❌ Неверный пароль.")


@router.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    if not states.is_admin(message.from_user.id):
        await message.answer("⛔️ Нет доступа. Вход: /adminkas <пароль>")
        return
    await message.answer(_welcome_text(), reply_markup=_panel_keyboard())


@router.message(Command("adminlogout"))
async def cmd_adminlogout(message: Message) -> None:
    states.remove_admin(message.from_user.id)
    await message.answer("🔒 Ты вышел из админ-панели.")


@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    if not states.is_admin(message.from_user.id):
        await message.answer("⛔️ Нет доступа. Вход: /adminkas <пароль>")
        return
    await message.answer(_stats_text())


@router.message(Command("user"))
async def cmd_user(message: Message, command: CommandObject) -> None:
    if not states.is_admin(message.from_user.id):
        await message.answer("⛔️ Нет доступа. Вход: /adminkas <пароль>")
        return
    arg = (command.args or "").strip()
    uid = _resolve(arg)
    user = states.all_users().get(uid) if uid is not None else None
    if user is None:
        await message.answer(f"❌ Пользователь не найден: {arg or '—'}")
        return
    await message.answer(_user_card_text(user))


@router.message(Command("send"))
async def cmd_send(message: Message, command: CommandObject) -> None:
    if not states.is_admin(message.from_user.id):
        await message.answer("⛔️ Нет доступа. Вход: /adminkas <пароль>")
        return

    raw = command.args or ""
    if "|" not in raw:
        await message.answer(
            "Формат: /send получатели | текст\n"
            "\n"
            "Примеры:\n"
            "/send all | Привет!\n"
            "/send 123456789 | Привет!\n"
            "/send @username | Привет!\n"
            "/send 123456789, @username | Привет!\n"
            "\n"
            "С фото: отправь фото боту, затем ответь на него: /send all | Подпись"
        )
        return

    recipients_raw, text = raw.split("|", 1)
    text = text.strip()

    if not text and not message.reply_to_message:
        await message.answer("Нужен текст сообщения (или фото, на которое ты отвечаешь).")
        return

    targets = _collect_targets(recipients_raw)
    if not targets:
        await message.answer("Не нашёл получателей.")
        return

    reply = message.reply_to_message
    media = None
    if reply is not None:
        if reply.photo:
            media = ("photo", reply.photo[-1].file_id)
        elif reply.video:
            media = ("video", reply.video.file_id)
        elif reply.document:
            media = ("document", reply.document.file_id)
        elif reply.animation:
            media = ("animation", reply.animation.file_id)

    sent = 0
    failed = 0
    for uid in targets:
        try:
            if media is not None:
                await _send_media(message.bot, uid, media[0], media[1], text)
            else:
                await _send_text_safe(message.bot, uid, text)
            sent += 1
        except TelegramForbiddenError:
            failed += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("Рассылка не ушла пользователю %s: %s", uid, exc)
            failed += 1

    await message.answer(f"📨 Рассылка завершена: отправлено <b>{sent}</b>, ошибок <b>{failed}</b>.")


@router.message(Command("support_answer"))
async def cmd_support_answer(message: Message, command: CommandObject) -> None:
    if not states.is_admin(message.from_user.id):
        await message.answer("⛔️ Нет доступа. Вход: /adminkas <пароль>")
        return

    raw = command.args or ""
    if "|" not in raw:
        await message.answer("Формат: /support_answer ID_или_@username | текст")
        return

    recipients_raw, text = raw.split("|", 1)
    text = text.strip()
    if not text:
        await message.answer("Пустой текст ответа.")
        return

    uid = _resolve(recipients_raw)
    if uid is None:
        await message.answer(f"Не найден получатель: {recipients_raw.strip() or '—'}")
        return

    reply_text = f"📩 <b>Ответ поддержки</b>\n\n{text}"
    try:
        await _send_text_safe(message.bot, uid, reply_text)
        states.set_awaiting_support(uid, False)
        await message.answer(f"✅ Ответ отправлен пользователю {uid}.")
    except Exception as exc:  # noqa: BLE001
        logger.exception("Не удалось отправить ответ пользователю %s: %s", uid, exc)
        await message.answer(f"❌ Не удалось отправить ответ: {exc}")


# ---------------------------------------------------------------------------
# Кнопки панели
# ---------------------------------------------------------------------------
@router.callback_query(F.data.startswith("adm:"))
async def on_admin_callback(query: CallbackQuery) -> None:
    if not states.is_admin(query.from_user.id):
        await query.answer("Нет доступа", show_alert=True)
        return

    data = query.data
    if data == "adm:stats":
        await query.answer()
        await query.message.answer(_stats_text())
    elif data == "adm:toggle_notify":
        new_value = not states.get_notify_start()
        states.set_notify_start(new_value)
        await query.message.edit_reply_markup(reply_markup=_panel_keyboard())
        await query.answer(f"Уведомления о входах: {'ВКЛ' if new_value else 'ВЫКЛ'}")
    elif data == "adm:logout":
        states.remove_admin(query.from_user.id)
        await query.answer("Вышел из админ-панели")
        await query.message.edit_text("🔒 Ты вышел из админ-панели.")
    else:
        await query.answer()