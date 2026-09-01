#!/usr/bin/env bash
# Запуск бота (macOS / Linux).
# Работает и с системным python3, и с venv, если он создан (см. README).
set -e
cd "$(dirname "$0")"

if [ -d ".venv" ]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
fi

exec python3 bot.py
