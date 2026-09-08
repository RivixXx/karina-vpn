import pytest


def test_extract_info_fields(pure_functions, fixture_text):
    result = pure_functions("bot.py")["extract_info"](fixture_text("cli_info.txt"))
    assert result == {
        "email": "demo_client", "status": "🟢 Активен",
        "expiry": "15.01.2030 12:00", "devices": "1 / 2",
        "used": "12.5 ГБ", "limit": "100.0 ГБ", "servers": "[2, 3, 4, 5]",
    }


def test_example_url_not_recognized(pure_functions):
    # Existing hardcoded-domain limitation; never add production URLs to fixtures.
    assert pure_functions("bot.py")["extract_info"](
        "https://vpn.example.test/connect/example.html"
    ) == {}


def test_prefix_aliases_and_unknown_lines(pure_functions):
    assert pure_functions("bot.py")["extract_info"](
        "\n noise\n Срок: Без срока\n Трафик: ∞\n"
    ) == {"expiry": "Без срока", "limit": "∞"}


def test_parse_list_current_behavior(pure_functions, fixture_text):
    assert pure_functions("bot.py")["parse_user_names"](fixture_text("cli_list.txt")) == [
        "demo", "client_1234567890", "client_12345678901",
        "n" * 32, "n" * 64, "disabled_demo", "expired_demo",
    ]


def test_exactly_18_character_name_is_visible(pure_functions, fixture_text):
    name = "client_12345678901"
    assert len(name) == 18
    assert name in pure_functions("bot.py")["parse_user_names"](fixture_text("cli_list.txt"))


@pytest.mark.parametrize("text", ["", "\n", "Пользователей нет.", "Всего: 5"])
def test_empty_or_summary_lines(pure_functions, text):
    assert pure_functions("bot.py")["parse_user_names"](text) == []


def test_markdown_escape(pure_functions):
    assert pure_functions("bot.py")["esc"]("demo_client. (1)!") == r"demo\_client\. \(1\)\!"
