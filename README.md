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
- `integrations/happ/subscription.py` exposes the existing Happ, Crypt5, QR and
  connection-page issuance flow as an injectable Python function;
  `karina_issue.py` remains its command-line adapter.
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
