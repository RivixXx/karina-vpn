#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG=/etc/karina-vpn/config.env
BOT_DATA=/opt/karina-bot
WITH_XUI=false
[[ "${1:-}" == "--with-xui" ]] && WITH_XUI=true
[[ $# -le 1 ]] || { echo "usage: $0 [--with-xui]" >&2; exit 2; }

cd "$ROOT"
[[ "$(uname -s)" == Linux ]] || { echo "Linux is required" >&2; exit 1; }
command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }
command -v git >/dev/null || { echo "git is required" >&2; exit 1; }
command -v systemctl >/dev/null || { echo "systemd is required" >&2; exit 1; }
[[ -z "$(git status --porcelain)" ]] || { echo "Git working tree is not clean" >&2; exit 1; }

bad_paths="$(git ls-files | grep -E '(^|/)(\.env|config\.env)$|\.(db|sqlite[^/]*)$' || true)"
[[ -z "$bad_paths" ]] || { echo "Forbidden secret/data paths are tracked" >&2; exit 1; }
for path in requirements.txt src/bot.py src/notifier.py src/karina_issue.py src/karina_user.py src/integrations/subscription.py; do
  [[ -f "$path" ]] || { echo "Missing required file: $path" >&2; exit 1; }
done
[[ -f "$CONFIG" ]] || { echo "Missing $CONFIG" >&2; exit 1; }
grep -Eq '^XUI_BASE=.+$' "$CONFIG" || { echo "XUI_BASE is missing or empty" >&2; exit 1; }
[[ -d "$BOT_DATA" ]] || echo "Recommendation: create $BOT_DATA and preserve legacy .env/DB paths"
[[ -w "$BOT_DATA" ]] || echo "Warning: legacy DB parent is not writable: $BOT_DATA"

if $WITH_XUI; then
  "$ROOT/.venv/bin/python" -c 'from src.application import build_client_service; build_client_service()'
fi
echo "Preflight checks passed"
