"""Select pure functions without executing module-level production setup."""
import ast
from pathlib import Path
import re
import socket
import sqlite3
import subprocess
from contextlib import closing

import pytest

ROOT = Path(__file__).resolve().parents[1]
SQLITE_CONNECT = sqlite3.connect
ALLOWED = {
    "bot.py": {"esc"},
}


@pytest.fixture(autouse=True)
def block_external_io(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("External I/O is forbidden in baseline tests")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setattr(subprocess, "Popen", blocked)
    monkeypatch.setattr(sqlite3, "connect", blocked)


@pytest.fixture
def fixture_text():
    def read(name):
        return (ROOT / "tests" / "fixtures" / name).read_text(encoding="utf-8")
    return read


@pytest.fixture
def source_functions():
    def load(filename, names, **dependencies):
        tree = ast.parse((ROOT / "src" / filename).read_text(encoding="utf-8"))
        nodes = [node for node in tree.body
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
        assert {node.name for node in nodes} == set(names)
        # No imports, assignments, decorators or default expressions are executed.
        for node in nodes:
            assert not node.decorator_list
            assert all(isinstance(value, ast.Constant) for value in node.args.defaults)
            assert not any(node.args.kw_defaults)
            # Annotations are documentation only; do not import Telegram types.
            node.returns = None
            for arg in node.args.args + node.args.kwonlyargs:
                arg.annotation = None
        namespace = {"re": re, **dependencies}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), filename, "exec"), namespace)
        return namespace
    return load


@pytest.fixture
def pure_functions(source_functions):
    return lambda filename: source_functions(filename, ALLOWED[filename])


@pytest.fixture
def local_db(tmp_path, source_functions):
    # Only this factory bypasses the global SQLite guard, at this exact tmp_path.
    database = tmp_path / "synthetic.sqlite"

    def connect():
        return SQLITE_CONNECT(database)

    for filename, name, dependency in (
        ("bot.py", "init_db", "db_connect"),
        ("notifier.py", "init_db", "db_connect"),
    ):
        source_functions(filename, {name}, **{dependency: connect})[name]()

    from src.repositories import BillingRepository
    BillingRepository(connect=connect).init_schema()

    with closing(connect()) as db, db:
        for email, tg_id in (("demo_target", 1), ("demo_other", 2)):
            db.execute("INSERT INTO telegram_links VALUES (?, ?, '', '', 0)", (tg_id, email))
            db.executemany("INSERT INTO bind_tokens VALUES (?, ?, 0, 4102444800, 0)",
                           [(email + "_token_a", email), (email + "_token_b", email)])
            db.execute("INSERT INTO notifications_sent "
                       "(email, expiry_key, notification_day, sent_at) VALUES (?, 'fixture', 7, 0)",
                       (email,))
            db.execute("INSERT INTO orders "
                       "(order_id, tg_id, email, plan_id, plan_title, days, amount, created_at) "
                       "VALUES (?, ?, ?, 'fixture', 'Synthetic plan', 30, 1, 0)",
                       (email + "_order", tg_id, email))
    return connect
