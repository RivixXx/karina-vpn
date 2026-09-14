# Production hardening (Ubuntu 24.04)

These files prepare a repeatable migration from the current root-run bot. They do not change VPN protocols or database contents. Run every command from `/opt/karina-vpn` in an existing root shell and keep a second SSH session open throughout.

## Preflight and Nginx preparation

Run `sudo bash scripts/production-hardening.sh preflight`, then create a verified application/data backup with `sudo bash scripts/backup-production.sh`; record the printed backup path. Run `sudo bash scripts/production-hardening.sh prepare-nginx` and record the rollback-state path it prints. Add `include /etc/nginx/snippets/karina-access-log.conf;` once inside the public `server` block in `/etc/nginx/sites-available/vpn.parsekk.ru`. Do not remove its existing `location` blocks. The logging policy omits query strings and Referer and suppresses `/connect/...`. A later webhook unit and location can add its own explicit logging policy independently.

Run `sudo nginx -t` after the include. If it fails, run the rollback command with the recorded state path. Confirm the second SSH session still works.

## Apply and validate

Run `sudo bash scripts/production-hardening.sh apply /var/lib/karina-hardening/<UTC timestamp>`. It creates the locked `karina-bot` account when absent, fixes the three sensitive files to `0600`, installs the unit, validates Nginx, reloads Nginx, restarts only the bot, and validates both services. On an apply error it automatically restores the recorded configuration and file metadata. Existing connections through Nginx are preserved by reload.

Run `sudo bash scripts/production-hardening.sh validate` again from the second SSH session. Confirm the Telegram start/menu/connection flow manually. Keep the rollback-state path printed by `apply`.

## Firewall (separate explicit change)

Determine the port used by both live SSH sessions from their `SSH_CONNECTION`. For a direct `sshd` listener, also confirm it appears in `sudo sshd -T`; for socket activation, confirm `ssh.socket` is active and enabled and that its `ListenStream` matches the live-session port. Run `sudo bash scripts/firewall-plan.sh check --ssh-port <port>`. Review current UFW status and the planned 22/80/443 rules (or the verified non-default SSH port).

Only after review, run `sudo KARINA_APPLY_FIREWALL=YES bash scripts/firewall-plan.sh apply --ssh-port <port>`. The script refuses an active policy or pre-existing rules and requires the port used by the current SSH session. A direct-listener check requires both an `sshd`-owned listener and the port in `sshd -T`. A socket-activation check instead requires an active and enabled `ssh.socket`, a matching `ListenStream`, and a listening TCP socket on that live-session port. It adds SSH/HTTP/HTTPS before enabling UFW, then displays final status. To revert this script's empty-policy setup, run `sudo bash scripts/firewall-plan.sh rollback --ssh-port <port>`. If either SSH session disconnects, use the provider console and disable UFW before changing anything else.

## Rollback

Run `sudo bash scripts/production-hardening.sh rollback /var/lib/karina-hardening/<UTC timestamp>`. It restores the prior unit, Nginx files, owners and modes, validates Nginx, reloads it, and restarts the bot. Validate again and use the application/data backup only if a separate data restore is actually required; this procedure never edits database contents.
