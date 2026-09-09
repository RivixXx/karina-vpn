#!/usr/bin/env bash
set -Eeuo pipefail

[[ "$(uname -s)" == Linux ]] || { echo "Linux is required" >&2; exit 1; }
[[ ${EUID:-$(id -u)} -eq 0 ]] || { echo "Run as root" >&2; exit 1; }
DEST="/root/karina-backups/$(date +%Y%m%d-%H%M%S)"
install -d -m 700 "$DEST"

copy_if_present() {
  local source=$1 name=$2
  if [[ -e "$source" ]]; then
    cp -aL -- "$source" "$DEST/$name"
    chmod -R go-rwx "$DEST/$name"
  fi
}

copy_if_present /etc/karina-vpn/config.env karina-config.env
copy_if_present /etc/x-ui/x-ui.db x-ui.db
copy_if_present /opt/karina-bot/.env legacy-bot.env
copy_if_present /opt/karina-bot/karina.db karina.db
copy_if_present /etc/systemd/system/karina-bot.service karina-bot.service
copy_if_present /etc/systemd/system/karina-notifier.service karina-notifier.service
copy_if_present /etc/systemd/system/karina-notifier.timer karina-notifier.timer
copy_if_present /etc/nginx/sites-available/vpn.parsekk.ru nginx-vpn.parsekk.ru
copy_if_present /etc/nginx/sites-enabled/vpn.parsekk.ru nginx-vpn.parsekk.ru.enabled
find "$DEST" -type f -exec chmod 600 {} +
echo "Backup created: $DEST"
