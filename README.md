# Karina VPN

## Current state

- Legacy working implementation; migration in progress.
- The six canonical source files are in `src/`. The initial baseline preserved
  them byte for byte; subsequent focused fixes are described below.
- CLI filenames use underscores and `.py` inside `src/` only. Internal
  deployment paths remain unchanged; this is not a new deployment
  procedure. Legacy `/usr/local/bin` command paths still apply.
- Verified duplicate sources and archives were removed. Ignored local data
  and environments in `current-server/` and `karina-bot/` were retained.

## Development

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
pytest -q
python -m compileall src
```

## Safety

Tests must not call production XUI, Happ or Telegram.

The harness imports the configuration, XUI integration, client service and CLI
safely. A few focused notifier and CLI contract tests still compile selected
functions through AST. Tests block socket connections, subprocesses and SQLite
connections. Lifecycle tests alone use a connection factory restricted to a
synthetic database in pytest's `tmp_path`.

The suite checks text contracts and local SQLite lifecycle, not live integration
behavior. Client service and list formatter tests use in-memory dependencies;
the CLI is imported but never launched against production services.

Focused fixes:

- `app_config.py` loads and validates typed configuration only on explicit
  request. Importing `src.karina_user` does not read production configuration.
- `integrations/xui` owns the existing urllib cookie, CSRF and endpoint flow.
  It raises typed errors without printing or exiting; the CLI converts expected
  config and XUI failures to the existing `Ошибка: ...` output.
- `models/client.py` defines typed client, device, traffic, creation, deletion
  and expiry results. `services/client_service.py` owns client business
  operations and receives an already configured XUI client through dependency
  injection. XUI login remains in the lazy CLI application factory.
- `karina_user.py` is now a CLI adapter over `ClientService`; command names and
  stdout formats remain stable for human and administrative use.
- `application.py` builds an authenticated `ClientService` lazily for both CLI
  and Telegram actions. Telegram uses typed results directly and no longer
  starts `karina-user` or parses its Russian stdout.
- Telegram, notifier and CLI each call `ClientService`, which owns XUI client
  operations. `ClientService` calls the Happ integration for subscription
  issuance. CLI text is no longer an internal application protocol.
- SQLite retains Telegram bindings, notification delivery state and billing
  state. Notifier deduplication writes the numeric expiry timestamp while also
  recognizing legacy localized expiry keys.
- `models/billing.py`, `services/billing_service.py` and
  `repositories/billing_repository.py` define the canonical plans, typed order
  lifecycle and SQLite persistence. Telegram can create and cancel pending
  orders through `BillingService`; no payment provider is connected yet.
- Client bundles use a primary credential and an internal `__mobile` credential.
  Primary inbound IDs and the mobile inbound/quota are typed configuration; the
  default mobile quota is exactly 50 GiB. Explicit migration verifies the
  aggregated mobile subscription before detaching the mobile inbound from the
  primary credential and is never run automatically.
- `integrations/subscription.py` is the canonical direct HTTPS subscription
  issuer. It atomically writes a QR code and connection page without calling an
  external crypto service. `karina_issue.py` is its thin command-line adapter;
  `integrations/happ` remains only as a compatibility import.
- CLI list columns now have explicit two-space separators, including for
  names of 18, 32 and 64 characters. This remains the notifier-facing CLI
  contract; Telegram no longer parses the table.
- `client_refs` maps persistent integer IDs to client names. Startup creates
  the table if missing, preserving existing data. Admin callbacks contain only
  an action and ref ID; old email-based buttons require reopening the list.
- Admin deletion requires confirmation in a private chat. The bot verifies the
  client, calls `delete-confirmed`, then atomically removes the Telegram binding,
  bind tokens and ref. Notifications and orders are retained. XUI failure leaves
  local access intact. Cancellation invalidates the confirmation.
- `delete` retains the YES prompt. Trusted internal `delete-confirmed` uses the
  same deletion function, without stdin. Its stdout is JSON with `vpn_deleted`
  and `warnings`. File cleanup warnings do not turn successful VPN deletion
  into failure. Bot and CLI must be deployed together for this contract.
- File cleanup validates subId and resolved paths before unlinking. Invalid
  IDs and paths outside the connect directory are skipped with warnings.
- `/start` and callbacks reject non-private chats before looking up client data.

Known limitations:

- XUI and SQLite cannot share a transaction. If SQLite cleanup fails after VPN
  deletion, the UI offers a local-only retry in the current bot process. A bot
  restart or lost backend response in this interval requires reconciliation;
  deletion progress is not persisted. Direct CLI deletion still has no access
  to Telegram DB and must not be used as a substitute for the admin flow.
- Other security and billing findings from the audit remain unresolved.
- Notification delivery uses send-then-record ordering. A process crash after
  Telegram accepts a message but before the SQLite commit can cause a retry.
- Applying a paid order extends XUI before marking the SQLite order completed.
  A crash between those operations can require manual reconciliation.

## Primary/mobile rollout procedure

Do not bulk migrate users. Perform these steps later during an approved deployment:

1. Back up `/opt/karina-bot`, `/usr/local/bin/karina-user`,
   `/etc/karina-vpn/config.env`, and `/etc/x-ui/x-ui.db`.
2. Update the application files.
3. Add `PRIMARY_INBOUND_IDS=2,3,4`, `MOBILE_INBOUND_ID=5`, and
   `MOBILE_TRAFFIC_GB=50` to `/etc/karina-vpn/config.env`.
4. Run compile and import smoke checks.
5. Restart the bot and notifier if the deployment requires it.
6. Run `karina-user migrate-mobile Mikhail --dry-run`.
7. Review the redacted plan and resolve every blocking error.
8. Run `karina-user migrate-mobile Mikhail --apply`.
9. Run the dry-run again and require `ALREADY MIGRATED`.
10. Manually verify that the existing Happ subscription contains Germany,
    Germany 2, Germany 3, and the mobile anti-blocking route.

Dry-run reads client state and performs no XUI mutation. `--apply` is the only
mode that changes one explicitly named user. Historical traffic attributed to
the primary credential is not transferred to the new mobile counter.

## Production deployment

Production uses one Git checkout at `/opt/karina-vpn`. Application source stays
inside that checkout. Secrets and databases remain outside Git:

- `/etc/karina-vpn/config.env` contains XUI and subscription configuration.
- `/opt/karina-bot/.env` and `/opt/karina-bot/karina.db` remain at their legacy
  paths until a separately reviewed data-path migration.
- `/etc/x-ui/x-ui.db` remains owned by 3x-ui.

The administrator selects and pulls the desired Git revision separately.
`scripts/deploy.sh` never pulls or pushes Git state. It creates `.venv`, installs
`requirements.txt`, performs compile/import checks, installs the CLI wrapper and
systemd units, restarts the bot, enables the notifier timer, and runs read-only
smoke checks. Run it as root from the clean `/opt/karina-vpn` checkout.

The implemented production configuration keys are:

```dotenv
PRIMARY_INBOUND_IDS=2,3,4
MOBILE_INBOUND_ID=5
MOBILE_TRAFFIC_GB=50
CONNECT_DIR=/var/www/karina/connect
NOTIFIER_TIMEZONE=Europe/Moscow
```

`INBOUND_IDS` remains supported for compatibility. Do not store `config.env`,
`.env`, database files, tokens, or credentials in Git.

The recurring notifier scans logical primary users and sends one current expiry
milestone (7, 3, 1 or expired) plus the highest newly reached mobile quota
milestone (80%, 95% or 100%). Successful sends are persisted in SQLite. Quota
cycles advance when a lower authoritative `usedTraffic` value or a changed quota
is observed. If a complete reset and subsequent traffic growth happen between
two scans, 3x-ui exposes no reliable reset identifier through the current client
contract, so that reset cannot be distinguished from the preceding cycle.

Validate a rollout without Telegram sends or database writes:

```bash
python -m src.notifier --dry-run
python -m src.notifier --dry-run --user SomeUser
```

Internal `__mobile` credentials cannot be targeted directly. A non-blocking
process lock prevents overlapping scheduled scans; persistent event keys remain
the correctness guard across restarts and reboots.

### First Git Deployment

1. Run `bash scripts/backup-production.sh` from the reviewed checkout.
2. Clone the private repository into `/opt/karina-vpn` and select the approved
   commit.
3. Preserve `/opt/karina-bot/.env` and `/opt/karina-bot/karina.db`.
4. Add the three primary/mobile keys above to `/etc/karina-vpn/config.env`.
5. Run `bash scripts/preflight.sh` and review every warning.
6. Run `bash scripts/deploy.sh` as root.
7. Check `karina-bot.service` and `karina-notifier.timer` status.
8. Run `karina-user migrate-mobile Mikhail --dry-run` and send the redacted
   output to the tech lead.
9. **DO NOT RUN `migrate-mobile --apply` until the dry-run has been reviewed and
   separately approved.**
10. After approval, apply that one user, repeat dry-run, require
    `ALREADY MIGRATED`, and manually verify all four Happ routes.

Deployment never migrates users, changes nginx/routing, or edits either SQLite
database directly.

## Canonical onboarding

Normal onboarding follows one authoritative flow:

```text
XUI create
→ XUI read-back
→ actual authoritative SUB_ID
→ direct HTTPS subscription
→ atomic QR/HTML generation
→ generated artifact verification
→ Telegram connection URL
```

The value returned by XUI read-back is used even if it differs from the value
requested during creation. The QR code and button contain the direct HTTPS
subscription URL from `SUB_BASE`; no network request is made while producing
the local PNG and HTML files. Output goes to `CONNECT_DIR`, and Telegram receives
the page URL derived from `CONNECT_BASE`.

Crypt5 is not part of normal onboarding. Existing `.crypt5` files are legacy
artifacts only. Deployment does not issue subscriptions, regenerate existing
users, call `crypto.happ.su`, or modify files in `/var/www/karina/connect` in
bulk. Mobile migration remains a separate, explicit per-user operation and is
never started by deployment.

## Telegram administration

Telegram is the recommended interface for routine administration. The admin
menu creates and manages a primary/mobile bundle without exposing internal
credential names, UUIDs, subscription IDs, raw XUI data, or credentials.

Operator workflow:

1. **Create user:** choose `Создать`, enter a primary name, select the term and
   HWID policy, review the confirmation, and create exactly one bundle. New
   bundles receive the configured primary inbounds with unlimited traffic and
   a mobile credential with the configured 50 GiB quota.
2. **Bind Telegram:** open the profile and create a one-time binding link.
   Rebinding invalidates older unused tokens.
3. **Open connection:** use `Подключение`. The service reads the current primary
   credential from XUI, uses its authoritative `SUB_ID`, repairs and verifies
   the direct QR/HTML artifacts, and only then returns the connection page.
4. **Extend:** select 30, 90, 180, or 365 days. Primary and mobile receive the
   same absolute expiry timestamp.
5. **Change HWID:** select 1, 2, 3, 5, or unlimited. One logical HWID policy is
   applied to both credentials.
6. **Manage devices:** view a merged bundle-level list, remove one logical
   device, or reset devices across both credentials.
7. **Disable or enable:** the toggle applies to the complete bundle.
8. **Delete:** confirm the operation separately. Mobile and primary VPN access,
   generated artifacts, Telegram binding, bind tokens, and callback reference
   are removed. Orders and notification history remain.
9. **Migrate legacy mobile:** review the per-user dry-run plan, then use a second
   confirmation to apply it. There is no bulk or automatic migration action.

Partial bundle mutations return a reconciliation warning and are not retried
automatically. Creation sessions are private, administrator-scoped, expire
after ten minutes, and are consumed before the final mutation. Numeric client
references keep names and internal identifiers out of callback data.

The 3x-ui panel and `karina-user` CLI remain maintenance and emergency tools.
Admin manual extension is independent of billing orders and does not change the
billing plan prices or order state machine.

## Backup

Run `bash scripts/backup-production.sh` as root before the first deployment and
before risky maintenance. It creates a mode-700 timestamped directory below
`/root/karina-backups`, copies config, XUI DB, legacy bot config/DB, relevant
systemd units and the site-specific nginx file when present, then restricts files
to mode 600. It does not print file contents or include Let's Encrypt keys.

## Rollback

`bash scripts/rollback-code.sh <previous-commit>` requires a clean checkout,
resolves the supplied commit, checks it out detached, and invokes the normal
deploy script. It never restores or modifies SQLite data.

**Code rollback is not data rollback.** Restore data only through a separate,
explicitly reviewed recovery procedure using a verified backup.

## Mobile migration

Migration is always manual and processes one email. A dry-run may read XUI but
performs no mutation. `--apply` creates/repairs the mobile credential and detaches
the mobile inbound only after the external subscription has been verified.
Neither deployment nor service startup invokes this command.
