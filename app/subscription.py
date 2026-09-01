"""
КАРЬЕРНЫЙ ГАЙД БОТ — проверка обязательной подписки на канал.

Как работает проверка:
  • Пользователь подписан (member / administrator / creator) → доступ открыт.
  • Пользователь подал заявку на вступление (join request, ждёт одобрения
    админа) → тоже считаем, что условие выполнено:
      - «restricted» в getChatMember — заявка ожидает одобрения;
      - заявки дополнительно трекаются через chat_join_request-обновления
        (поле join_requested в состоянии пользователя).
  • Проверка отключена, если CHANNEL_ID не задан.
  • При ошибке проверки (например, неверный ID канала) доступ НЕ блокируем,
    но пишем ошибку в лог — чтобы не потерять пользователей из-за конфига.
"""

import logging

from aiogram import Bot
from aiogram.enums import ChatMemberStatus

from config import CHANNEL_ID
from app import states

logger = logging.getLogger(__name__)

# Статусы, которые считаем «подписан».
SUBSCRIBED_STATUSES = {
    ChatMemberStatus.CREATOR,
    ChatMemberStatus.ADMINISTRATOR,
    ChatMemberStatus.MEMBER,
}


async def is_subscribed(bot: Bot, user_id: int) -> bool:
    """
    True, если пользователь выполнил условие: подписан ИЛИ подал заявку.
    """
    # 1) Заявка на вступление уже была подана (трекается из chat_join_request).
    if states.get(user_id).get("join_requested"):
        return True

    # 2) Проверка выключена (CHANNEL_ID не задан) — доступ открыт.
    if not CHANNEL_ID:
        return True

    # 3) Проверяем статус в канале.
    try:
        member = await bot.get_chat_member(CHANNEL_ID, user_id)
        status = member.status
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Ошибка проверки подписки (канал %s, user %s): %s", CHANNEL_ID, user_id, exc
        )
        # Не блокируем пользователя из-за ошибки проверки.
        return True

    if status in SUBSCRIBED_STATUSES:
        return True

    # «restricted» — заявка на вступление ожидает одобрения админа.
    if status == ChatMemberStatus.RESTRICTED:
        return True

    # «left» — не вступил и не подавал заявку; «kicked» — заблокирован.
    return False
