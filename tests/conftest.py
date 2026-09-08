"""Select pure functions without executing module-level production setup."""
import ast
from pathlib import Path
import re
import socket
import sqlite3
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
ALLOWED = {
    "bot.py": {"extract_info", "parse_user_names", "esc"},
    "notifier.py": {"parse_expiring", "notification_stage"},
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
def pure_functions():
    def load(filename):
        tree = ast.parse((ROOT / "src" / filename).read_text(encoding="utf-8"))
        nodes = [node for node in tree.body
                 if isinstance(node, ast.FunctionDef) and node.name in ALLOWED[filename]]
        assert {node.name for node in nodes} == ALLOWED[filename]
        # No imports, assignments, decorators or default expressions are executed.
        for node in nodes:
            assert not node.decorator_list
            assert not node.args.defaults and not any(node.args.kw_defaults)
        namespace = {"re": re}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), filename, "exec"), namespace)
        return namespace
    return load
