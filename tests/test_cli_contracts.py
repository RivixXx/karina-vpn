"""Importing the CLI reads production configuration, so load functions via AST.

cmd_list runs only against in-memory doubles, never XUI or a subprocess.
"""
import re
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest


def test_info_fixture_contract(fixture_text):
    lines = fixture_text("cli_info.txt").splitlines()
    for prefix in ("Пользователь:", "Статус:", "Истекает:", "Устройства:",
                   "Использовано:", "Лимит:", "Серверы:"):
        assert sum(line.startswith(prefix) for line in lines) == 1
    urls = [line for line in lines if line.startswith("https://")]
    assert len(urls) == 1
    assert urlparse(urls[0]).hostname == "vpn.example.test"
    assert urlparse(urls[0]).path == "/connect/example.html"


def test_list_column_boundary(fixture_text):
    lines = fixture_text("cli_list.txt").splitlines()
    name17, name18 = "client_1234567890", "client_12345678901"
    assert len(name17) == 17 and len(name18) == 18
    assert any(re.match(re.escape(name17) + r"\s{2,}🟢", line) for line in lines)
    assert any(re.match(re.escape(name18) + r"\s{2,}🟢", line) for line in lines)
    assert any(line.startswith("disabled_demo") and "🔴 Отключён" in line for line in lines)
    assert any(line.startswith("expired_demo") and "🟠 Истёк" in line for line in lines)


def test_expiring_cli_suffix(fixture_text):
    rows = [line for line in fixture_text("cli_expiring.txt").splitlines()
            if line.startswith("demo_")]
    assert len(rows) == 4
    assert all(line.endswith(" дн.") and "осталось " in line for line in rows)


@pytest.mark.parametrize("length", [2, 4, 17, 18, 32, 64])
def test_real_list_formatter_round_trips_to_bot(length, source_functions, pure_functions, capsys):
    name = "n" * length
    client = SimpleNamespace(
        email=name, status="active", device_count=1, device_limit=2,
        used_traffic_bytes=0, total_traffic_bytes=0, expiry_time_ms=0,
    )
    functions = source_functions(
        "karina_user.py", {"cmd_list"},
        build_service=lambda: SimpleNamespace(list_clients=lambda: [client]),
        status_text=lambda status: "🟢 Активен",
        bytes_to_human=lambda value: "500 МБ",
        bytes_to_gb=lambda value: "∞",
        fmt_date_short=lambda value: "01.10.2030",
    )
    functions["cmd_list"]([])
    output = capsys.readouterr().out
    row = next(line for line in output.splitlines() if line.startswith(name))
    assert re.split(r"\s{2,}", row.strip()) == [
        name, "🟢 Активен", "1/2", "500 МБ", "∞", "01.10.2030",
    ]
    assert pure_functions("bot.py")["parse_user_names"](output) == [name]
