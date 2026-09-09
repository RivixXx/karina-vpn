#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$ROOT/.venv/bin/python"
cd "$ROOT"
[[ -x "$PYTHON" ]] || { echo "Missing virtualenv Python" >&2; exit 1; }
"$PYTHON" -m compileall -q src
"$PYTHON" -c 'import qrcode, src.app_config, src.application, src.bot, src.notifier, src.karina_issue, src.karina_user, src.integrations.subscription'
"$PYTHON" -c 'from src.app_config import load_config; load_config("/etc/karina-vpn/config.env")'
"$PYTHON" -m src.karina_user >/dev/null
"$PYTHON" -m src.karina_issue --help >/dev/null
systemctl is-active --quiet karina-bot.service
systemctl is-enabled --quiet karina-notifier.timer
echo "Read-only smoke checks passed"
