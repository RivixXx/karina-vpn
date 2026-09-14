from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.bot_menu import COMMANDS, configure_menu


def run(coroutine):
    try:
        coroutine.send(None)
    except StopIteration:
        return
    finally:
        coroutine.close()
    raise AssertionError('Unexpected I/O')


def test_menu_has_start_and_support_for_default_and_russian_private_chats():
    bot = SimpleNamespace(set_my_commands=AsyncMock(), set_chat_menu_button=AsyncMock())
    run(configure_menu(bot))
    assert bot.set_my_commands.await_count == 4
    assert {call.kwargs['language_code'] for call in bot.set_my_commands.await_args_list} == {'', 'ru'}
    assert {call.kwargs['scope'].type for call in bot.set_my_commands.await_args_list} == {'default', 'all_private_chats'}
    for call in bot.set_my_commands.await_args_list:
        assert [c.command for c in call.args[0]] == [name for name, _ in COMMANDS]
        assert call.args[0][0].command == 'start'
    assert bot.set_chat_menu_button.await_args.kwargs['menu_button'].type == 'commands'


def test_menu_failure_does_not_stop_bot_startup():
    bot = SimpleNamespace(set_my_commands=AsyncMock(side_effect=RuntimeError('offline')),
                          set_chat_menu_button=AsyncMock(side_effect=RuntimeError('offline')))
    run(configure_menu(bot))
    assert bot.set_my_commands.await_count == 4
