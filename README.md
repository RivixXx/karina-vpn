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
- `delete_client_local_state(email)` atomically removes Telegram bindings and
  bind tokens while retaining notifications and orders. Telegram has no delete
  flow yet, so this helper is not called automatically. CLI deletion still
  leaves local access records; integration is a separate task.

Known limitations:

- Admin callbacks still embed client names and can exceed Telegram's 64-byte
  callback limit for long names. The formatter/parser regression tests do not
  cover delivery of Telegram keyboards; callback identifiers need a separate fix.
- `extract_info` recognizes only its hardcoded production URL prefix; the
  anonymized `example.test` fixture intentionally yields no `url` field.
- Other security and billing findings from the audit remain unresolved.
