from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

from src.bot_diagnostics import membership_chat


def run(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    finally:
        coroutine.close()


def test_membership_diagnostic_is_safe_and_uses_configured_chat():
    bot = NS(
        get_me=AsyncMock(return_value=NS(id=77, username="fixture_bot")),
        get_chat=AsyncMock(return_value=NS(id=-100123, title="Karina VPN")),
        get_chat_member=AsyncMock(return_value=NS(status="administrator")),
    )
    output = []
    assert run(membership_chat(bot, NS(required_tg_chat_id=-100123), output.append)) == 0
    assert output == [
        "Bot: @fixture_bot", "Chat: Karina VPN", "Chat ID: -100123",
        "Bot membership: administrator", "Membership check capability: OK",
    ]
    assert all("token" not in line.lower() for line in output)
    bot.get_chat.assert_awaited_once_with(-100123)
    bot.get_chat_member.assert_awaited_once_with(-100123, 77)
