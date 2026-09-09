#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
[[ "$(uname -s)" == Linux ]] || { echo "Linux is required" >&2; exit 1; }
[[ ${EUID:-$(id -u)} -eq 0 ]] || { echo "Run as root" >&2; exit 1; }
[[ "$ROOT" == /opt/karina-vpn ]] || { echo "Expected checkout at /opt/karina-vpn" >&2; exit 1; }
cd "$ROOT"
bash "$ROOT/scripts/preflight.sh"

if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
fi
.venv/bin/python -m pip install --disable-pip-version-check -r requirements.txt
.venv/bin/python -m compileall -q src
.venv/bin/python -c 'import qrcode, src.app_config, src.application, src.bot, src.notifier, src.karina_issue, src.karina_user'

[[ -f /opt/karina-bot/.env ]] || { echo "Missing legacy bot config /opt/karina-bot/.env" >&2; exit 1; }
[[ -f /opt/karina-bot/karina.db ]] || { echo "Missing legacy bot DB /opt/karina-bot/karina.db" >&2; exit 1; }
[[ -d /var/www/karina/connect ]] || { echo "Missing /var/www/karina/connect" >&2; exit 1; }

wrapper='#!/usr/bin/env bash
set -Eeuo pipefail
exec /opt/karina-vpn/.venv/bin/python -m src.karina_user "$@"'
printf '%s\n' "$wrapper" > /usr/local/bin/karina-user
chmod 755 /usr/local/bin/karina-user
issue_wrapper='#!/usr/bin/env bash
set -Eeuo pipefail
exec /opt/karina-vpn/.venv/bin/python -m src.karina_issue "$@"'
printf '%s\n' "$issue_wrapper" > /usr/local/bin/karina-issue
chmod 755 /usr/local/bin/karina-issue
install -m 644 deploy/systemd/karina-bot.service /etc/systemd/system/karina-bot.service
install -m 644 deploy/systemd/karina-notifier.service /etc/systemd/system/karina-notifier.service
install -m 644 deploy/systemd/karina-notifier.timer /etc/systemd/system/karina-notifier.timer
systemctl daemon-reload
systemctl restart karina-bot.service
systemctl enable --now karina-notifier.timer
bash "$ROOT/scripts/smoke-check.sh"
systemctl --no-pager --full status karina-bot.service
systemctl --no-pager --full status karina-notifier.timer
echo "Deployment completed without user migration"
