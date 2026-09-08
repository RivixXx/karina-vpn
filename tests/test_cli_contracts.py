"""Text fixtures only: importing the CLI reads production configuration.

No CLI process or module is executed. Update fixtures deliberately if the
formatter's prefixes or column widths change in a later migration.
"""
from urllib.parse import urlparse


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
    assert any(line.startswith(name17 + " 🟢") for line in lines)
    assert any(line.startswith(name18 + "🟢") for line in lines)
    assert any(line.startswith("disabled_demo") and "🔴 Отключён" in line for line in lines)
    assert any(line.startswith("expired_demo") and "🟠 Истёк" in line for line in lines)


def test_expiring_cli_suffix(fixture_text):
    rows = [line for line in fixture_text("cli_expiring.txt").splitlines()
            if line.startswith("demo_")]
    assert len(rows) == 4
    assert all(line.endswith(" дн.") and "осталось " in line for line in rows)
