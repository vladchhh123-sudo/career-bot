"""
КАРЬЕРНЫЙ ГАЙД БОТ — работа с медиафайлами.

Правила отправки (обновлено):
  • Все файлы из папки отправляются ОДНОЙ медиагруппой (одним сообщением),
    БЕЗ подписи и БЕЗ кнопок — чтобы Telegram гарантированно принял альбом.
  • Текст и кнопки идут ОТДЕЛЬНЫМ сообщением сразу после альбома (см. messaging.py).
  • Если в папке ровно один файл — отправляется обычным медиасообщением
    (Telegram требует для альбома минимум 2 файла).
  • Используются только файлы, реально лежащие в папке проекта.
"""

import logging
from pathlib import Path
from typing import List, Optional

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    BufferedInputFile,
    InputMediaPhoto,
    InputMediaVideo,
    InputMediaAnimation,
)

from config import ASSETS_DIR

logger = logging.getLogger(__name__)

# Расширения файлов по типу медиа
VIDEO_EXTS = (".mp4", ".mov", ".avi", ".mkv", ".webm", ".mpeg", ".mpg", ".3gp")
ANIMATION_EXTS = (".gif",)
PHOTO_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff")

# Telegram принимает альбом от 2 до 10 файлов за один вызов.
TELEGRAM_MEDIA_GROUP_MIN = 2
TELEGRAM_MEDIA_GROUP_MAX = 10

# Лимиты Telegram на размер файлов (байты).
MAX_PHOTO_BYTES = 10 * 1024 * 1024   # фото — до 10 МБ
MAX_VIDEO_BYTES = 50 * 1024 * 1024   # видео/анимация — до 50 МБ


def _is_media_name(filename: str) -> bool:
    """Только файлы с известными медиа-расширениями (отсекает .DS_Store и мусор)."""
    lower = filename.lower()
    return lower.endswith(PHOTO_EXTS + VIDEO_EXTS + ANIMATION_EXTS)


def _sorted_files(folder: Path) -> List[Path]:
    """Отсортированные медиафайлы папки (числовая сортировка: 1, 2, ... 9, 10).

    Файлы без медиа-расширения (.DS_Store, Thumbs.db и т.п.) игнорируются.
    """
    if not folder.is_dir():
        return []

    def sort_key(p: Path) -> tuple:
        digits = "".join(ch for ch in p.stem if ch.isdigit())
        return (int(digits) if digits else 10**9, p.stem.lower())

    files = [p for p in folder.iterdir() if p.is_file() and _is_media_name(p.name)]
    return sorted(files, key=sort_key)


def list_media_files(folder_name: str) -> List[Path]:
    """Список медиафайлов подпапки внутри ASSETS_DIR (например, "first" или "push/reviews")."""
    return _sorted_files(ASSETS_DIR / folder_name)


def has_media(folder_name: str) -> bool:
    """True, если в папке есть хотя бы один файл."""
    return bool(list_media_files(folder_name))


def folder_has_video(folder_name: str) -> bool:
    """True, если в папке есть видео или анимация (gif)."""
    return any(
        p.suffix.lower() in VIDEO_EXTS + ANIMATION_EXTS
        for p in list_media_files(folder_name)
    )


def _kind(path: Path) -> str:
    """Тип медиа: "photo" | "video" | "animation" | None."""
    lower = path.name.lower()
    if lower.endswith(ANIMATION_EXTS):
        return "animation"
    if lower.endswith(VIDEO_EXTS):
        return "video"
    if lower.endswith(PHOTO_EXTS):
        return "photo"
    return None


def _fits_telegram_limits(path: Path) -> bool:
    """
    True, если файл проходит лимиты Telegram по размеру.
    Слишком большие файлы пропускаем с предупреждением — иначе Telegram
    отклонит ВЕСЬ альбом целиком из-за одного файла.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return True

    kind = _kind(path)
    if kind == "photo" and size > MAX_PHOTO_BYTES:
        logger.warning(
            "Файл %s слишком большой для фото (%.1f МБ > 10 МБ) — пропущен.",
            path.name, size / 1e6,
        )
        return False
    if kind in ("video", "animation") and size > MAX_VIDEO_BYTES:
        logger.warning(
            "Файл %s слишком большой для видео (%.1f МБ > 50 МБ) — пропущен.",
            path.name, size / 1e6,
        )
        return False
    return True


def _buffered(path: Path) -> Optional[BufferedInputFile]:
    try:
        return BufferedInputFile(path.read_bytes(), filename=path.name)
    except OSError as exc:
        logger.error("Не удалось прочитать файл %s: %s", path, exc)
        return None


def _input_media(path: Path) -> Optional[InputMediaPhoto | InputMediaVideo | InputMediaAnimation]:
    """Собирает InputMedia для одного файла (без подписи)."""
    if not _fits_telegram_limits(path):
        return None

    data = _buffered(path)
    if data is None:
        return None

    kind = _kind(path)
    if kind == "animation":
        return InputMediaAnimation(media=data)
    if kind == "video":
        return InputMediaVideo(media=data)
    if kind == "photo":
        return InputMediaPhoto(media=data)

    logger.warning("Пропущен файл с неизвестным расширением: %s", path.name)
    return None


async def _send_single(bot: Bot, chat_id: int, path: Path) -> Optional[int]:
    """Отправка одного файла обычным медиасообщением (без подписи)."""
    if not _fits_telegram_limits(path):
        return None

    data = _buffered(path)
    if data is None:
        return None

    kind = _kind(path)
    try:
        if kind == "animation":
            msg = await bot.send_animation(chat_id=chat_id, animation=data)
        elif kind == "video":
            msg = await bot.send_video(chat_id=chat_id, video=data)
        else:
            msg = await bot.send_photo(chat_id=chat_id, photo=data)
        return msg.message_id
    except TelegramBadRequest as exc:
        logger.warning("BadRequest при отправке файла %s: %s", path.name, exc)
        return None
    except Exception as exc:  # noqa: BLE001
        logger.exception("Ошибка отправки файла %s: %s", path.name, exc)
        return None


async def send_media_group(bot: Bot, chat_id: int, folder_name: str) -> List[int]:
    """
    Отправляет ВСЕ медиафайлы из folder_name ОДНИМ альбомом (без подписи).

    Возвращает список message_id отправленных сообщений.
    Текст и кнопки добавляются отдельным сообщением в messaging.py.
    """
    files = list_media_files(folder_name)
    if not files:
        logger.warning("В папке %s нет файлов — медиа не отправлено.", folder_name)
        return []

    # Один файл → обычное сообщение (альбом требует минимум 2 файла)
    if len(files) == 1:
        msg_id = await _send_single(bot, chat_id, files[0])
        return [msg_id] if msg_id is not None else []

    # Режем на чанки по 10 файлов
    chunks = [
        files[i : i + TELEGRAM_MEDIA_GROUP_MAX]
        for i in range(0, len(files), TELEGRAM_MEDIA_GROUP_MAX)
    ]

    sent_ids: List[int] = []
    for chunk in chunks:
        # Собираем валидные файлы (с учётом лимитов по размеру)
        pairs: List[tuple] = []
        for path in chunk:
            item = _input_media(path)
            if item is not None:
                pairs.append((path, item))

        if len(pairs) < TELEGRAM_MEDIA_GROUP_MIN:
            # Слишком мало валидных файлов для альбома — шлём по одному
            for path, _ in pairs:
                mid = await _send_single(bot, chat_id, path)
                if mid is not None:
                    sent_ids.append(mid)
            continue

        try:
            msgs = await bot.send_media_group(
                chat_id=chat_id, media=[item for _, item in pairs]
            )
            sent_ids.extend(m.message_id for m in msgs)
        except Exception as exc:  # noqa: BLE001 — не роняем бота из-за медиа
            logger.exception(
                "Альбом из %s не отправился целиком (%s). Пробую файлы по одному.",
                folder_name, exc,
            )
            # Один битый файл валит весь альбом — шлём по одному.
            for path, _ in pairs:
                mid = await _send_single(bot, chat_id, path)
                if mid is not None:
                    sent_ids.append(mid)

    return sent_ids
