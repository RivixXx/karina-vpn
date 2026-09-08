# Karina VPN

## Current state

- Legacy working implementation; migration in progress.
- The six canonical source files in `src/` are preserved byte for byte.
- CLI filenames use underscores and `.py` inside `src/` only. Internal paths,
  imports and production behavior are unchanged; this is not a new deployment
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

The harness reads source text and compiles explicitly selected pure functions
through AST. It never imports legacy modules: their top-level code reads
production configuration or executes commands. Tests block socket connections,
subprocesses and SQLite connections. Fixtures are synthetic.

The suite characterizes text contracts, not live integration behavior. Running
the real CLI remains blocked by import-time configuration. Replace AST loading
with normal imports only once imports become safe in a future task.

Known limitations preserved by the baseline:

- Names of 18 or more characters disappear from the bot list (strict xfail).
- `extract_info` recognizes only its hardcoded production URL prefix; the
  anonymized `example.test` fixture intentionally yields no `url` field.
- Lifecycle, security and billing findings from the audit remain unresolved.
