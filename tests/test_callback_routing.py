from contextlib import closing
import json
import sqlite3
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest


def run(coroutine):
    """Mocks complete synchronously; never create Windows asyncio sockets."""
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    finally:
        coroutine.close()
    pytest.fail("Unexpected asynchronous I/O in isolated handler test")


def button(text, **kwargs):
    return NS(text=text, **kwargs)


@pytest.fixture
def bot(source_functions, local_db):
    names = {"get_or_create_client_ref", "get_client_email_by_ref", "delete_client_ref",
             "delete_client_local_state", "client_callback", "is_private_chat", "is_admin",
             "admin_ref_callback", "admin_user", "admin_users", "extract_info", "esc",
             "get_link_by_email", "parse_user_names", "callbacks", "start", "client_keyboard"}
    # Production row_factory is reproduced without accessing a real database.
    def connect():
        db = local_db()
        db.row_factory = sqlite3.Row
        return db
    return source_functions(
        "bot.py", names, db_connect=connect, closing=closing, sqlite3=sqlite3,
        time=NS(time=lambda: 1000), json=json, ChatType=NS(PRIVATE="private"),
        ADMIN_TG_ID=1, InlineKeyboardButton=button, InlineKeyboardMarkup=lambda rows: rows,
        ParseMode=NS(MARKDOWN_V2="MarkdownV2"), SUPPORT_URL="https://example.test/support",
    )


def update(data, chat="private", user=1):
    return NS(effective_chat=NS(type=chat), effective_user=NS(id=user),
              message=NS(reply_text=AsyncMock()),
              callback_query=NS(data=data, answer=AsyncMock(), edit_message_text=AsyncMock()))


def invoke(bot, data, context=None, **kwargs):
    upd = update(data, **kwargs)
    run(bot["callbacks"](upd, context or NS(user_data={})))
    return upd


def state(connect):
    with closing(connect()) as db:
        return {table: db.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
                for table in ("telegram_links", "bind_tokens", "client_refs", "orders", "notifications_sent")}


def test_refs_stable_and_not_reused(bot):
    ref = bot["get_or_create_client_ref"]("demo_target")
    assert bot["get_or_create_client_ref"]("demo_target") == ref
    assert bot["get_client_email_by_ref"](ref) == "demo_target"
    bot["delete_client_ref"]("demo_target")
    assert bot["get_client_email_by_ref"](ref) is None
    assert bot["get_or_create_client_ref"]("demo_target") > ref


@pytest.mark.parametrize("ref", [0, -1, 999, 2**64, "1", None, True])
def test_stale_and_invalid_ref(bot, ref):
    assert bot["get_client_email_by_ref"](ref) is None


def test_callbacks_for_long_name(bot):
    email = "n" * 64
    bot["run_karina"] = Mock(return_value=(0, email + "  🟢 Активен\n"))
    upd = update("admin_users")
    run(bot["admin_users"](upd))
    run(bot["admin_user"](upd, email))
    ref = bot["get_or_create_client_ref"](email)
    run(bot["admin_ref_callback"](upd, NS(user_data={}), f"udel:{ref}"))
    values = [getattr(b, "callback_data", "")
              for call in upd.callback_query.edit_message_text.call_args_list
              for row in call.kwargs["reply_markup"] for b in row]
    assert values
    for value in values:
        assert email not in value and len(value.encode()) <= 64
    for action in ("u", "ub", "ud", "uc", "udel", "uy"):
        assert bot["client_callback"](action, email) == f"{action}:{ref}"
    assert not any(getattr(b, "callback_data", "").startswith("udel")
                   for row in bot["client_keyboard"](email) for b in row)


@pytest.mark.parametrize("warning", [False, True])
def test_delete_success_and_double_callback(bot, local_db, warning):
    ref = bot["get_or_create_client_ref"]("demo_target")
    other_ref = bot["get_or_create_client_ref"]("demo_other")
    before = state(local_db)
    bot["run_karina"] = Mock(side_effect=lambda args: (0, json.dumps({"vpn_deleted": True,
        "warnings": ["cleanup skipped"] if warning else []})) if args[0] == "delete-confirmed" else (0, "info"))
    ctx = NS(user_data={})
    invoke(bot, f"udel:{ref}", ctx)
    assert all(call.args[0][0] != "delete-confirmed" for call in bot["run_karina"].call_args_list)
    invoke(bot, f"uy:{ref}", ctx)
    after = state(local_db)
    assert len(after["telegram_links"]) == 1
    assert after["telegram_links"][0][1] == "demo_other"
    assert len(after["bind_tokens"]) == 2
    assert all(row[1] == "demo_other" for row in after["bind_tokens"])
    assert after["orders"] == before["orders"]
    assert after["notifications_sent"] == before["notifications_sent"]
    assert bot["get_client_email_by_ref"](ref) is None
    assert bot["get_client_email_by_ref"](other_ref) == "demo_other"
    count = bot["run_karina"].call_count
    upd = invoke(bot, f"uy:{ref}", ctx)
    assert bot["run_karina"].call_count == count
    assert "больше не существует" in upd.callback_query.edit_message_text.call_args.args[0]


def test_backend_failure_preserves_local_access(bot, local_db):
    ref = bot["get_or_create_client_ref"]("demo_target")
    before = state(local_db)
    bot["run_karina"] = Mock(side_effect=lambda args: (1, "failure") if args[0] == "delete-confirmed" else (0, "info"))
    ctx = NS(user_data={})
    invoke(bot, f"udel:{ref}", ctx)
    invoke(bot, f"uy:{ref}", ctx)
    assert state(local_db) == before


def test_local_failure_rolls_back_and_retries_without_backend(bot, local_db):
    ref = bot["get_or_create_client_ref"]("demo_target")
    before = state(local_db)
    with closing(local_db()) as db, db:
        db.execute("CREATE TRIGGER fail_ref BEFORE DELETE ON client_refs BEGIN SELECT RAISE(ABORT, 'test'); END")
    bot["run_karina"] = Mock(return_value=(0, '{"vpn_deleted": true, "warnings": []}'))
    ctx = NS(user_data={"delete_confirmation": ref})
    invoke(bot, f"uy:{ref}", ctx)
    assert state(local_db) == before
    with closing(local_db()) as db, db:
        db.execute("DROP TRIGGER fail_ref")
    bot["run_karina"] = Mock(side_effect=AssertionError("Must retry only local cleanup"))
    invoke(bot, f"uy:{ref}", ctx)
    assert bot["get_client_email_by_ref"](ref) is None


@pytest.mark.parametrize("data", ["u:0", "u:-1", "u:999", "uy:abc", "u:999999999999999999999999", "admin_user:demo_target"])
def test_invalid_callback_is_safe(bot, data):
    bot["run_karina"] = Mock(side_effect=AssertionError("No backend"))
    invoke(bot, data)


def test_xui_missing_is_stale(bot):
    ref = bot["get_or_create_client_ref"]("demo_target")
    bot["run_karina"] = Mock(return_value=(1, "Ошибка: клиент demo_target не найден"))
    upd = invoke(bot, f"u:{ref}")
    assert "больше не существует" in upd.callback_query.edit_message_text.call_args.args[0]


@pytest.mark.parametrize("chat", ["group", "supergroup", "channel"])
def test_group_start_and_callback_reveal_nothing(bot, chat):
    bot["run_karina"] = Mock(side_effect=AssertionError("No backend"))
    upd = invoke(bot, "uy:1", chat=chat)
    assert not upd.callback_query.edit_message_text.called
    assert "личном чате" in upd.callback_query.answer.call_args.args[0]
    run(bot["start"](upd, NS(args=["bind_synthetic"])))
    assert "личном чате" in upd.message.reply_text.call_args.args[0]


def test_non_admin_cannot_delete(bot, local_db):
    ref = bot["get_or_create_client_ref"]("demo_target")
    before = state(local_db)
    bot["run_karina"] = Mock(side_effect=AssertionError("No backend"))
    invoke(bot, f"uy:{ref}", user=2)
    assert state(local_db) == before


def test_confirm_without_prompt_does_not_delete(bot):
    ref = bot["get_or_create_client_ref"]("demo_target")
    bot["run_karina"] = Mock(return_value=(0, "info"))
    invoke(bot, f"uy:{ref}")
    assert bot["run_karina"].call_args_list[0].args[0] == ["info", "demo_target"]
    assert bot["run_karina"].call_count == 1


def test_cancel_invalidates_confirmation(bot):
    ref = bot["get_or_create_client_ref"]("demo_target")
    bot["run_karina"] = Mock(return_value=(0, "info"))
    ctx = NS(user_data={})
    invoke(bot, f"udel:{ref}", ctx)
    invoke(bot, f"u:{ref}", ctx)
    invoke(bot, f"uy:{ref}", ctx)
    assert all(call.args[0][0] != "delete-confirmed" for call in bot["run_karina"].call_args_list)


def test_private_client_mode_unchanged(bot):
    bot["get_link_by_tg"] = Mock(return_value={"email": "demo_target"})
    bot["render_client_home"] = AsyncMock()
    upd = update("client_home", user=2)
    run(bot["start"](upd, NS(args=[])))
    bot["render_client_home"].assert_awaited_once_with(upd, "demo_target")
    invoke(bot, "client_home", user=2)
    assert bot["render_client_home"].await_count == 2


def test_ref_migration_preserves_existing_data(bot, local_db, source_functions):
    before = state(local_db)
    with closing(local_db()) as db, db:
        db.execute("DROP TABLE client_refs")
    init = source_functions("bot.py", {"init_db"}, db_connect=local_db)["init_db"]
    init()
    ref = bot["get_or_create_client_ref"]("demo_target")
    init()
    assert bot["get_client_email_by_ref"](ref) == "demo_target"
    after = state(local_db)
    for table in ("telegram_links", "bind_tokens", "orders", "notifications_sent"):
        assert before[table] == after[table]


def test_backend_check_error_does_not_delete(bot, local_db):
    ref = bot["get_or_create_client_ref"]("demo_target")
    before = state(local_db)
    bot["run_karina"] = Mock(return_value=(1, "HTTP 503"))
    invoke(bot, f"uy:{ref}", NS(user_data={"delete_confirmation": ref}))
    assert bot["run_karina"].call_count == 1
    assert state(local_db) == before


def test_unknown_backend_result_does_not_revoke_access(bot, local_db):
    ref = bot["get_or_create_client_ref"]("demo_target")
    before = state(local_db)
    bot["run_karina"] = Mock(return_value=(0, "unexpected"))
    invoke(bot, f"uy:{ref}", NS(user_data={"delete_confirmation": ref}))
    assert state(local_db) == before
