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

The harness reads source text and compiles explicitly selected functions
through AST. It never imports legacy modules: their top-level code reads
production configuration or executes commands. Tests block socket connections,
subprocesses and SQLite connections. Lifecycle tests alone use a connection
factory restricted to a synthetic database in pytest's `tmp_path`.

The suite checks text contracts and local SQLite lifecycle, not live integration
behavior. The list formatter runs with in-memory dependencies; the real CLI
is not imported or launched. Replace AST loading
with normal imports only once imports become safe in a future task.

Focused fixes:

- CLI list columns now have explicit two-space separators, including for
  names of 18, 32 and 64 characters. The bot parser needs no regex changes.
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
- `extract_info` recognizes only its hardcoded production URL prefix; the
  anonymized `example.test` fixture intentionally yields no `url` field.
- Other security and billing findings from the audit remain unresolved.
