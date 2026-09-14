#!/usr/bin/env bash
set -Eeuo pipefail

usage() { echo "usage: $0 check|apply|rollback --ssh-port <port>" >&2; exit 2; }
[[ $# -eq 3 && "$2" == --ssh-port && "$3" =~ ^[0-9]+$ ]] || usage
MODE=$1
SSH_PORT=$3
(( SSH_PORT >= 1 && SSH_PORT <= 65535 )) || usage
[[ ${EUID:-$(id -u)} -eq 0 ]] || { echo "Run as root" >&2; exit 1; }
command -v ufw >/dev/null || { echo "ufw is required" >&2; exit 1; }
command -v sshd >/dev/null || { echo "sshd is required" >&2; exit 1; }
command -v ss >/dev/null || { echo "ss is required" >&2; exit 1; }
[[ -n "${SSH_CONNECTION:-}" ]] || { echo "Run from a live SSH session; SSH_CONNECTION is missing" >&2; exit 1; }
read -r _client_ip _client_port _server_ip LIVE_SSH_PORT <<< "$SSH_CONNECTION"
[[ "$LIVE_SSH_PORT" == "$SSH_PORT" ]] || { echo "Requested port $SSH_PORT differs from this SSH session port $LIVE_SSH_PORT" >&2; exit 1; }
LISTEN_PATTERN="[[:space:]]([^[:space:]]*:)?$SSH_PORT[[:space:]]"
ss -H -ltn | grep -E "$LISTEN_PATTERN" >/dev/null || { echo "Nothing listens on TCP $SSH_PORT" >&2; exit 1; }
if ss -H -ltnp | grep -E "$LISTEN_PATTERN.*users:\(\(\"sshd\"" >/dev/null; then
  sshd -T | awk '$1 == "port" {print $2}' | grep -Fxq "$SSH_PORT" || { echo "sshd does not declare port $SSH_PORT" >&2; exit 1; }
  echo "Verified sshd-owned listener on TCP $SSH_PORT"
else
  systemctl is-active --quiet ssh.socket || { echo "Listener is not owned by sshd and ssh.socket is inactive" >&2; exit 1; }
  systemctl is-enabled --quiet ssh.socket || { echo "Listener is not owned by sshd and ssh.socket is not enabled" >&2; exit 1; }
  systemctl show ssh.socket --property=Listen --value \
    | tr ' ' '\n' \
    | grep -Eq "(^|:)$SSH_PORT$" || { echo "ssh.socket ListenStream does not include TCP $SSH_PORT" >&2; exit 1; }
  echo "Verified active, enabled ssh.socket listener on TCP $SSH_PORT"
fi
printf 'Planned inbound rules: tcp/%s (SSH), tcp/80, tcp/443\n' "$SSH_PORT"
[[ "$MODE" == check ]] && { ufw status verbose; echo "Firewall check passed; no changes made"; exit 0; }
MARKER="/var/lib/karina-hardening/firewall-$SSH_PORT.applied"
rollback_firewall() {
  ufw --force disable || true
  ufw --force delete allow "$SSH_PORT/tcp" || true
  ufw --force delete allow 80/tcp || true
  ufw --force delete allow 443/tcp || true
  rm -f -- "$MARKER"
}
if [[ "$MODE" == rollback ]]; then
  [[ -f "$MARKER" ]] || { echo "No matching firewall apply marker" >&2; exit 1; }
  rollback_firewall
  echo "Firewall rules added by this script were removed"
  exit 0
fi
[[ "$MODE" == apply ]] || usage
[[ "${KARINA_APPLY_FIREWALL:-}" == YES ]] || { echo "Set KARINA_APPLY_FIREWALL=YES after reviewing check output" >&2; exit 1; }
ufw status | grep -Fxq 'Status: inactive' || { echo "Refusing to replace an active firewall policy" >&2; exit 1; }
[[ "$(ufw status numbered | grep -c '^\[' || true)" == 0 ]] || { echo "Refusing to alter pre-existing UFW rules" >&2; exit 1; }
install -d -m 700 /var/lib/karina-hardening
install -m 600 /dev/null "$MARKER"
trap 'rc=$?; trap - ERR; rollback_firewall; exit "$rc"' ERR
ufw allow "$SSH_PORT/tcp" comment 'SSH'
ufw allow 80/tcp comment 'HTTP'
ufw allow 443/tcp comment 'HTTPS'
ufw --force enable
ufw status verbose
trap - ERR
