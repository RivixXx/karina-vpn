#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_ROOT=/var/lib/karina-hardening
BOT_USER=karina-bot
BOT_GROUP=karina-bot
BOT_DB=/opt/karina-bot/karina.db
BOT_DB_DIR=/opt/karina-bot
XUI_DB=/etc/x-ui/x-ui.db
LEGAL=/etc/karina-vpn/legal.json
NGINX_SITE=/etc/nginx/sites-available/vpn.parsekk.ru
STAMP="${KARINA_HARDENING_STAMP:-$(date -u +%Y%m%dT%H%M%SZ)}"
STATE="$STATE_ROOT/$STAMP"
MANAGED_PATHS=(
  /etc/systemd/system/karina-bot.service
  /etc/nginx/conf.d/karina-log-policy.conf
  /etc/nginx/snippets/karina-access-log.conf
  "$NGINX_SITE"
)

usage() {
  echo "usage: $0 preflight | prepare-nginx | apply <state-directory> | validate | rollback <state-directory>" >&2
  exit 2
}

require_root() {
  [[ ${EUID:-$(id -u)} -eq 0 ]] || { echo "Run as root" >&2; exit 1; }
}

preflight() {
  [[ "$(uname -s)" == Linux ]] || { echo "Linux is required" >&2; exit 1; }
  [[ -r /etc/os-release ]] || { echo "Missing /etc/os-release" >&2; exit 1; }
  . /etc/os-release
  [[ "${ID:-}" == ubuntu && "${VERSION_ID:-}" == 24.04* ]] || {
    echo "Ubuntu 24.04 is required; found ${PRETTY_NAME:-unknown}" >&2; exit 1;
  }
  for command_name in systemctl systemd-analyze nginx getent install stat groupadd groupdel useradd userdel; do
    command -v "$command_name" >/dev/null || { echo "Missing command: $command_name" >&2; exit 1; }
  done
  for path in "$BOT_DB" "$XUI_DB" "$LEGAL" /etc/karina-vpn/config.env "$NGINX_SITE"; do
    [[ -f "$path" ]] || { echo "Missing required file: $path" >&2; exit 1; }
  done
  [[ -x /opt/karina-vpn/.venv/bin/python ]] || { echo "Missing project Python" >&2; exit 1; }
  systemctl is-active --quiet karina-bot.service || { echo "karina-bot.service is not active" >&2; exit 1; }
  systemd-analyze verify "$ROOT/deploy/systemd/karina-bot-hardened.service"
  nginx -t
  echo "Preflight passed; no changes made"
}

snapshot() {
  umask 077
  install -d -m 700 "$STATE"
  for path in "${MANAGED_PATHS[@]}"; do
    if [[ -e "$path" ]]; then
      install -D -m 600 "$path" "$STATE$path"
    else
      install -D -m 600 /dev/null "$STATE$path.absent"
    fi
  done
  stat -c '%a %U %G %n' "$BOT_DB_DIR" "$BOT_DB" "$XUI_DB" "$LEGAL" > "$STATE/file-modes.before"
  getent passwd "$BOT_USER" >/dev/null || install -m 600 /dev/null "$STATE/bot-user.absent"
  getent group "$BOT_GROUP" >/dev/null || install -m 600 /dev/null "$STATE/bot-group.absent"
  printf '%s\n' "$STATE" > "$STATE_ROOT/latest"
}

prepare_nginx() {
  require_root
  preflight
  snapshot
  trap 'rc=$?; trap - ERR; rollback "$STATE"; exit "$rc"' ERR
  install -m 0644 "$ROOT/deploy/nginx/conf.d/karina-log-policy.conf" /etc/nginx/conf.d/karina-log-policy.conf
  install -m 0644 "$ROOT/deploy/nginx/snippets/karina-access-log.conf" /etc/nginx/snippets/karina-access-log.conf
  trap - ERR
  echo "Nginx policy staged. Add the snippet include to $NGINX_SITE and run apply with: $STATE"
}

apply() {
  require_root
  local source=${1:-}
  [[ -n "$source" && -d "$source" && "$source" == "$STATE_ROOT"/* ]] || { echo "Invalid prepared state" >&2; exit 2; }
  [[ -f "$source/file-modes.before" ]] || { echo "Incomplete prepared state" >&2; exit 1; }
  grep -Fq 'include /etc/nginx/snippets/karina-access-log.conf;' "$NGINX_SITE" || {
    echo "Missing access-log snippet include in $NGINX_SITE" >&2; exit 1;
  }
  nginx -t
  STATE="$source"
  trap 'rc=$?; trap - ERR; rollback "$STATE"; exit "$rc"' ERR
  getent group "$BOT_GROUP" >/dev/null || groupadd --system "$BOT_GROUP"
  getent passwd "$BOT_USER" >/dev/null || useradd --system --gid "$BOT_GROUP" --home-dir /nonexistent --shell /usr/sbin/nologin "$BOT_USER"
  chown root:"$BOT_GROUP" "$BOT_DB_DIR"
  chmod 0770 "$BOT_DB_DIR"
  chown "$BOT_USER:$BOT_GROUP" "$BOT_DB" "$LEGAL"
  chmod 0600 "$BOT_DB" "$XUI_DB" "$LEGAL"
  touch "$BOT_DB.operations.lock"
  chown "$BOT_USER:$BOT_GROUP" "$BOT_DB.operations.lock"
  chmod 0660 "$BOT_DB.operations.lock"
  install -m 0644 "$ROOT/deploy/systemd/karina-bot-hardened.service" /etc/systemd/system/karina-bot.service
  systemctl daemon-reload
  nginx -t
  systemctl reload nginx
  systemctl restart karina-bot.service
  validate
  trap - ERR
  echo "Hardening applied; rollback state: $STATE"
}

validate() {
  require_root
  systemd-analyze verify /etc/systemd/system/karina-bot.service
  nginx -t
  [[ "$(stat -c %a "$BOT_DB")" == 600 ]]
  [[ "$(stat -c %a "$XUI_DB")" == 600 ]]
  [[ "$(stat -c %a "$LEGAL")" == 600 ]]
  [[ "$(stat -c %U:%G "$BOT_DB")" == "$BOT_USER:$BOT_GROUP" ]]
  systemctl is-active --quiet karina-bot.service
  systemctl is-active --quiet nginx
  echo "Hardening validation passed"
}

rollback() {
  require_root
  local source=${1:-}
  [[ -n "$source" && -d "$source" && "$source" == "$STATE_ROOT"/* ]] || { echo "Invalid rollback state" >&2; exit 2; }
  for path in "${MANAGED_PATHS[@]}"; do
    if [[ -f "$source$path.absent" ]]; then
      rm -f -- "$path"
    elif [[ -f "$source$path" ]]; then
      install -D -m 0644 "$source$path" "$path"
    fi
  done
  while read -r mode owner group path; do
    chmod "$mode" "$path"
    chown "$owner:$group" "$path"
  done < "$source/file-modes.before"
  systemctl daemon-reload
  nginx -t
  systemctl reload nginx
  systemctl restart karina-bot.service
  if [[ -f "$source/bot-user.absent" ]] && getent passwd "$BOT_USER" >/dev/null; then
    userdel "$BOT_USER"
  fi
  if [[ -f "$source/bot-group.absent" ]] && getent group "$BOT_GROUP" >/dev/null; then
    groupdel "$BOT_GROUP"
  fi
  echo "Rollback completed from $source"
}

case "${1:-}" in
  preflight) preflight ;;
  prepare-nginx) prepare_nginx ;;
  apply) [[ $# -eq 2 ]] || usage; apply "$2" ;;
  validate) validate ;;
  rollback) [[ $# -eq 2 ]] || usage; rollback "$2" ;;
  *) usage ;;
esac
