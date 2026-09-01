"""
КАРЬЕРНЫЙ ГАЙД БОТ — хранение состояния пользователей.

Состояние одного пользователя:
  {
    "id": int,                # user_id в Telegram
    "name": str,              # полное имя (для {name} берём первое слово)
    "bought": bool,           # купил ли продукт
    "last_action": float,     # unix-время последнего действия в боте (справочно)
    "warmup_step": int,       # индекс СЛЕДУЮЩЕГО этапа прогрева (0..N-1)
    "warmup_sent": [int, ...] # индексы уже отправленных этапов
    "next_warmup_at": float|None  # когда отправлять следующий этап (unix-время)
    "muted": bool,            # прогревание отключено (например, после блокировки бота)
    "passed_gate": bool,      # прошёл ли проверку подписки на канал
    "join_requested": bool,   # подавал ли заявку на вступление в канал
  }

ВАЖНО про логику прогрева:
  Цепочка двигается только ВПЕРЁД и НИКОГДА не сбрасывается кликами:
  • после отправки этапа N следующий придёт через интервал (next_warmup_at);
  • нажатие кнопки (покупка) НЕ сбрасывает ни позицию, ни таймер — цепочка
    продолжается с того места, где человек нажал кнопку (т.е. после него).

Хранение — в памяти (словарь). При перезапуске бота прогресс восстанавливается
из резервного JSON-файла (data/state.json), который периодически сохраняется.
"""

import json
import logging
import threading
import time
from pathlib import Path

from config import DATA_DIR, warmup_interval_seconds, ADMIN_IDS

logger = logging.getLogger(__name__)

STATE_FILE = DATA_DIR / "state.json"
_SAVE_INTERVAL_SECONDS = 60  # как часто сбрасываем состояние на диск

_lock = threading.RLock()
_users: dict[int, dict] = {}
_admins: set[int] = set()     # user_id админов (вошли по паролю /adminkas)
_notify_start: bool = True    # слать ли админам уведомления о входах в бот
_dirty = False


def _now() -> float:
    return time.time()


# ---------------------------------------------------------------------------
# Чтение / запись на диск
# ---------------------------------------------------------------------------
def load_state() -> None:
    """Загружает состояние из JSON (если файл есть и валиден)."""
    global _users, _admins, _notify_start
    if not STATE_FILE.exists():
        logger.info("Файл состояния не найден — стартуем с чистого листа.")
        return
    try:
        raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        with _lock:
            _users = {}
            _admins = set()
            _notify_start = True

            # Формат: {"users": {...}, "admins": [...], "notify_start": bool}
            # (старый формат {uid: {...}} тоже читаем)
            if isinstance(raw, dict) and "users" in raw:
                users_raw = raw.get("users", {})
                _admins = {
                    int(x) for x in (raw.get("admins") or [])
                    if str(x).lstrip("-").isdigit()
                }
                _notify_start = bool(raw.get("notify_start", True))
            else:
                users_raw = raw

            for uid, data in users_raw.items():
                try:
                    uid_int = int(uid)
                    if isinstance(data, dict):
                        data.setdefault("bought", False)
                        data.setdefault("warmup_step", 0)
                        data.setdefault("warmup_sent", [])
                        data.setdefault("muted", False)
                        data.setdefault("next_warmup_at", None)
                        data.setdefault("last_action", _now())
                        # Пользователи, созданные ДО ввода проверки подписки,
                        # считаются прошедшими (чтобы их догрев не остановился).
                        data.setdefault("passed_gate", True)
                        data.setdefault("join_requested", False)
                        data.setdefault("username", "")
                        data.setdefault("awaiting_support", False)
                        _users[uid_int] = data
                except (TypeError, ValueError):
                    continue
        logger.info("Состояние загружено: %d пользователей.", len(_users))
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Не удалось загрузить state.json (%s). Начинаем с чистого листа.", exc)


def save_state() -> None:
    """Сохраняет состояние в JSON (атомарно — через временный файл)."""
    global _dirty
    with _lock:
        if not _dirty:
            return
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            tmp = STATE_FILE.with_suffix(".json.tmp")
            payload = {
                "users": {str(k): v for k, v in _users.items()},
                "admins": sorted(_admins),
                "notify_start": _notify_start,
            }
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            tmp.replace(STATE_FILE)
            _dirty = False
        except OSError as exc:
            logger.error("Не удалось сохранить состояние: %s", exc)


def mark_dirty() -> None:
    global _dirty
    _dirty = True


# ---------------------------------------------------------------------------
# API доступа
# ---------------------------------------------------------------------------
def get(user_id: int) -> dict:
    """Возвращает состояние пользователя (создаёт при первом обращении)."""
    with _lock:
        user = _users.get(user_id)
        if user is None:
            now = _now()
            user = {
                "id": user_id,
                "name": "",
                "username": "",
                "bought": False,
                "last_action": now,
                "warmup_step": 0,
                "warmup_sent": [],
                # Расписание догрева запускается ТОЛЬКО после прохождения
                # проверки подписки (см. mark_gate_passed).
                "next_warmup_at": None,
                "muted": False,
                "passed_gate": False,
                "join_requested": False,
                "awaiting_support": False,
            }
            _users[user_id] = user
            mark_dirty()
        return user


def update(user_id: int, **fields) -> dict:
    """Обновляет поля состояния и возвращает новое состояние."""
    with _lock:
        user = get(user_id)
        user.update(fields)
        mark_dirty()
        return user


def set_name(user_id: int, full_name: str) -> None:
    with _lock:
        user = get(user_id)
        if not user.get("name") and full_name:
            user["name"] = full_name
            mark_dirty()


def set_username(user_id: int, username: str) -> None:
    """Запоминает username пользователя (если его ещё нет)."""
    with _lock:
        user = get(user_id)
        if username and not user.get("username"):
            user["username"] = username
            mark_dirty()


def set_awaiting_support(user_id: int, value: bool = True) -> None:
    """Пользователь ждёт/не ждёт отправки вопроса в поддержку."""
    with _lock:
        user = get(user_id)
        user["awaiting_support"] = bool(value)
        mark_dirty()


def find_user_id_by_username(username: str) -> int | None:
    """Находит user_id по username (без учёта регистра и символа @)."""
    uname = (username or "").lstrip("@").lower()
    if not uname:
        return None
    with _lock:
        for uid, u in _users.items():
            if (u.get("username") or "").lower() == uname:
                return uid
    return None


def touch(user_id: int) -> None:
    """Обновляет last_action (справочно). НЕ трогает расписание прогрева."""
    with _lock:
        user = get(user_id)
        user["last_action"] = _now()
        mark_dirty()


def on_buy_click(user_id: int, step_index: int | None) -> None:
    """
    Пользователь нажал кнопку покупки.

    step_index — номер этапа прогрева, в сообщении которого стояла кнопка
    (None для кнопок в стартовом сообщении и в id001).

    Цепочка продолжается С ЭТОГО МЕСТА (после сообщения с кнопкой) и НЕ
    сбрасывается: если текущая позиция уже дальше — оставляем её;
    если пользователь нажал кнопку в сообщении позади текущей позиции —
    ничего не меняем. Таймер следующего сообщения НЕ перезапускается.
    """
    with _lock:
        user = get(user_id)
        user["last_action"] = _now()
        if step_index is not None and step_index >= 0:
            current = int(user.get("warmup_step", 0))
            if current < step_index + 1:
                # Продолжаем с сообщения ПОСЛЕ того, где нажали кнопку.
                next_step = step_index + 1
                interval = warmup_interval_seconds(next_step)
                user["warmup_step"] = next_step
                user["next_warmup_at"] = _now() + (interval if interval else 0)
        mark_dirty()


def set_join_requested(user_id: int, requested: bool = True) -> None:
    """Отметить, что пользователь подал заявку на вступление в канал."""
    with _lock:
        user = get(user_id)
        user["join_requested"] = bool(requested)
        mark_dirty()


def mark_gate_passed(user_id: int) -> None:
    """
    Отметить, что пользователь прошёл проверку подписки, и запустить
    расписание догрева (первый этап — через обычный интервал).

    Идемпотентно: если уже проходил — расписание НЕ сбрасывается.
    """
    with _lock:
        user = get(user_id)
        if user.get("passed_gate"):
            return
        now = _now()
        first_interval = warmup_interval_seconds(0)
        user["passed_gate"] = True
        user["warmup_step"] = 0
        user["warmup_sent"] = []
        user["next_warmup_at"] = now + (first_interval if first_interval else 0)
        user["last_action"] = now
        mark_dirty()


def mark_bought(user_id: int) -> None:
    with _lock:
        user = get(user_id)
        user["bought"] = True
        user["last_action"] = _now()
        mark_dirty()


def set_muted(user_id: int, muted: bool = True) -> None:
    with _lock:
        user = get(user_id)
        user["muted"] = muted
        mark_dirty()


def all_users() -> dict[int, dict]:
    """Снимок всех пользователей (для планировщика)."""
    with _lock:
        return dict(_users)


# ---------------------------------------------------------------------------
# Админ-панель: сессии админов и уведомления
# ---------------------------------------------------------------------------
def add_admin(user_id: int) -> None:
    with _lock:
        _admins.add(int(user_id))
        mark_dirty()


def remove_admin(user_id: int) -> None:
    with _lock:
        _admins.discard(int(user_id))
        mark_dirty()


def is_admin(user_id: int) -> bool:
    """Админ = вошёл по паролю ИЛИ в списке ADMIN_IDS из конфига."""
    with _lock:
        return int(user_id) in _admins or int(user_id) in ADMIN_IDS


def all_admin_ids() -> list[int]:
    with _lock:
        return sorted(_admins)


def set_notify_start(on: bool) -> None:
    global _notify_start
    with _lock:
        _notify_start = bool(on)
        mark_dirty()


def get_notify_start() -> bool:
    with _lock:
        return _notify_start