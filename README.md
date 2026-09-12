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
MOBILE_INBOUND_IDS=5,10
MOBILE_TRAFFIC_GB=50
CONNECT_DIR=/var/www/karina/connect
NOTIFIER_TIMEZONE=Europe/Moscow
REQUIRED_TG_CHAT_ID=-1000000000000
REQUIRED_TG_CHAT_URL=https://t.me/your_group
REQUIRED_MEMBERSHIP_MODE=new_users
MENU_ANIMATION_FILE_ID=
CONNECT_VIDEO_FILE_ID=
NEWS_CHANNEL_URL=https://t.me/your_channel
SUPPORT_URL=https://t.me/your_support
HAPP_ANDROID_URL=
HAPP_IOS_URL=
HAPP_WINDOWS_URL=
HAPP_MACOS_URL=
TELEGRAPH_ANDROID_URL=
TELEGRAPH_IOS_URL=
TELEGRAPH_WINDOWS_URL=
TELEGRAPH_MACOS_URL=
```

`MOBILE_INBOUND_IDS` is the canonical list for the anti-blocking WebSocket/TLS
and XHTTP/CDN inbounds. `MOBILE_INBOUND_ID` remains a single-inbound fallback
for existing installations. `INBOUND_IDS` remains supported for compatibility. Do not store `config.env`,
`.env`, database files, tokens, or credentials in Git.

Media and help links are optional. To obtain a reusable Telegram `file_id`, an
operator sends the animation or video to the bot in a private test chat and
reads the `file_id` from a temporary diagnostic handler or a trusted Telegram
update inspection tool. Store only that identifier in `config.env`; never put
the bot token in a command, screenshot, log, README, or Git. A missing or stale
media identifier falls back to the text menu and platform choices. Happ and
Telegraph buttons appear only when their HTTPS URL is configured.

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

`REQUIRED_MEMBERSHIP_MODE` accepts `new_users`, `all_users`, or `disabled`.
The default policy checks new and unbound users while preserving cabinet access
for existing linked users. The bot should be an administrator of the configured
group so Telegram can answer authoritative `getChatMember` checks. Validate the
operator-supplied signed Bot API chat ID without printing the bot token:

```bash
python -m src.bot_diagnostics membership-chat
```

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

## Customer Telegram UX

The private-chat `/start` router sends administrators to the admin menu, healthy
bound customers to their cabinet, stale bindings to recovery, and new customers
through the configured channel membership check to the tariff catalog. The
acquisition path has no administrator dead end: a member can select a tariff and
create a persistent request immediately.

The canonical tariff catalog is defined once in `src/models/billing.py`: 30 days
for 199 RUB, 90 days for 499 RUB, 180 days for 899 RUB, and 365 days for 1499
RUB. Telegram callbacks contain only a tariff code; duration and price are
resolved again on the server. Until a payment provider is connected, an
authorized admin confirms or rejects pending requests. Provisioning and renewal
run through `CustomerOrderService`, the same boundary intended for a future
verified payment webhook. Approval is recorded only after the bundle operation
and Telegram binding succeed. Duplicate approval and rejection callbacks are
idempotent; incomplete provisioning remains pending for reconciliation.

Customer presentation lives in `src/ui/`. The cabinet exposes connection,
renewal, device count/reset, installation help, sharing, news, and support while
hiding UUIDs, subscription IDs, raw HWIDs, inbound identifiers, and the internal
mobile credential. Connection and QR actions use the existing authoritative
issuer and never rotate credentials.

Optional customer configuration keys are:

```text
MENU_ANIMATION_FILE_ID=
CONNECT_VIDEO_FILE_ID=
NEWS_CHANNEL_URL=
SUPPORT_URL=
HAPP_ANDROID_URL=
HAPP_IOS_URL=
HAPP_WINDOWS_URL=
HAPP_MACOS_URL=
TELEGRAPH_ANDROID_URL=
TELEGRAPH_IOS_URL=
TELEGRAPH_WINDOWS_URL=
TELEGRAPH_MACOS_URL=
```

URLs must use HTTPS. Missing media or help URLs simply hide the corresponding
button. Upload each animation/video once in a private operator chat, inspect the
received Telegram update using an operator-controlled diagnostic tool, and copy
only its `file_id` into configuration. Never paste or print the bot token while
obtaining a file ID. Deployment installs code and services only; it does not
upload media, create orders, provision customers, or regenerate connection
artifacts.

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

## Referrals

Registered customers receive a stable, random URL-safe referral code lazily when
they open the referral screen. A referral attribution is stored once for a new
Telegram user and cannot be replaced or self-attributed. Following a link or
creating a pending order does not earn a reward.

The referrer receives three subscription days only after the referred user's
first payment is confirmed and subscription provisioning completes. Qualification,
the qualifying order, the absolute target expiry, and reward application are
persisted in SQLite so approval retries and process restarts cannot apply the
reward twice. The normal bundle reconciliation updates primary and mobile expiry.
Existing customers receive a code on demand; no bulk migration is required.

Traffic rewards are represented by the reward ledger design but are not issued:
the current mobile quota is a fixed plan limit, rather than an additive traffic
balance that can safely carry bonus entitlements.

## Mobile migration

Migration is always manual and processes one email. A dry-run may read XUI but
performs no mutation. `--apply` creates/repairs the mobile credential and detaches
the mobile inbound only after the external subscription has been verified.
Neither deployment nor service startup invokes this command.

### One-time primary/mobile cohort migration

The reviewed one-time cohort command is restricted in code to `Vlad__K`,
`Nikolay_p`, `Olga_K`, and `Sergey_B`. It always reports `Mikhail`, `Home`, and
`Anastasia_A` as `EXCLUDED / untouched` and never looks them up or mutates them.
The command requires `PRIMARY_INBOUND_IDS=2,3,4` and
`MOBILE_INBOUND_IDS=5,10`; a mismatch blocks apply.

Run the read-only report first:

```bash
karina-user migrate-bundle-cohort --dry-run
```

For every target it prints current primary/mobile inbound IDs, primary expiry,
Telegram binding, primary HWID usage, planned changes, blocking reconciliation
errors, and whether a completion message would be sent. Dry-run performs no XUI
or SQLite writes and sends no Telegram messages.

After separate review, apply requires an explicit guard phrase:

```bash
karina-user migrate-bundle-cohort --apply --confirm APPLY-PRIMARY-MOBILE
```

Apply reuses the normal mobile migration and reconciliation service, keeps the
primary expiry and Telegram binding, sets the primary HWID limit to 4, and keeps
the mobile credential at 50 GiB with unlimited HWID (`limitHwid=0`). Each client
is verified before its user is notified. Successful notifications receive a
persistent migration event marker, so retries do not resend them; a failed send
is not marked and remains retryable.

### Telegram single-screen navigation and seasonal avatars

Customer callbacks reuse one current bot UI message. Text screens are edited in
place; transitions to QR/photo/video delete the previous bot UI message and send
one replacement, whose message ID becomes current. Returning from media performs
the inverse replacement. Telegram's `Message is not modified` response is treated
as a successful no-op. Ordinary user messages are never deleted by this layer.

The registered-user cabinet is one compound logical screen containing the first
sticker resolved from `getStickerSet("KarinaVPN")` and one HTML menu message. Both
message IDs are tracked and cleared together when leaving the cabinet. Returning
recreates the pair; refresh edits only the menu and retains the sticker. The first
resolved sticker file ID is cached for the bot process. Sticker lookup, send, and
cleanup failures are isolated, so the cabinet text always remains available.

The first unregistered `/start` displays a compact welcome screen. Pending manual
payment requests show their tariff, amount, status and creation time, with actions
to change the tariff or cancel after confirmation. Replacing a tariff cancels the
old pending request before creating its replacement, so only one remains active.

Avatar selection uses `Europe/Moscow`. Seasonal defaults are spring (March-May),
summer (June-August), autumn (September-November), and winter
(December-February). Holiday overrides are February 14 and 23, March 8, April 12,
May 9, June 12, September 1, October 31, plus New Year from December 20 through
January 8. `TG_day.png` is available but disabled because no project date is
defined for it. Missing holiday images fall back to the season, then `main.png`.

Configuration:

```env
AVATAR_CHAT_ID=<telegram-chat-id>
AVATAR_DIR=/opt/karina-vpn/avatar
AVATAR_STATE_FILE=/opt/karina-vpn/var/avatar-state.json
CONNECT_VIDEO_PATH=/opt/karina-vpn/avatar/video_1.mp4
```

`AVATAR_CHAT_ID` is optional. The bot checks once at startup and then daily near
00:05 Moscow time. Bot and chat outcomes are stored separately, preventing repeat
uploads and allowing one failed target to retry without affecting polling. The
current python-telegram-bot 20.8 package has no public `set_my_profile_photo`
wrapper, so the scheduler uses its standard `_post` transport for the exact Bot
API `setMyProfilePhoto` method. `set_chat_photo` remains the public PTB method.
Both failures are logged and isolated.

Inspect selection and persisted status without Telegram mutation:

```bash
karina-user avatar-status
```
