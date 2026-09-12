from contextlib import closing
import sqlite3
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest
from src.telegram_format import markdown_v2_escape


def run(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    finally:
        coroutine.close()
    pytest.fail("Unexpected asynchronous I/O in isolated handler test")


def button(text, **kwargs):
    return NS(text=text, **kwargs)


def client(email="demo_target", warning=None):
    return NS(
        email=email, status="active", enabled=True, expiry_time_ms=2_000_000_000_000,
        expiry_text="18.05.2033 06:33", device_count=0, device_limit=2,
        total_traffic_bytes=0, used_traffic_bytes=0, inbound_ids=(1,),
        sub_id="safe_id123", connect_url="https://example.test/connect/safe_id123.html",
        file_cleanup_warning=warning, removed=True,
    )


@pytest.fixture
def bot(source_functions, local_db):
    names = {
        "get_or_create_client_ref", "get_client_email_by_ref", "delete_client_ref",
        "delete_client_local_state", "client_callback", "is_private_chat", "is_admin",
        "admin_ref_callback", "admin_user", "admin_users", "markdown_v2_escape", "get_link_by_email", "get_link_by_tg",
        "callbacks", "start", "client_keyboard", "status_text", "safe_user_error",
        "format_devices", "format_admin_stats", "format_expiring",
        "admin_bundle_user", "format_bundle_profile", "bytes_to_human",
        "membership_required", "check_required_membership", "membership_keyboard",
        "render_membership_gate", "render_membership_error", "format_user_cabinet",
        "render_tariffs", "send_menu_animation",
    }

    def connect():
        db = local_db()
        db.row_factory = sqlite3.Row
        return db

    service = NS(
        get_client=Mock(side_effect=lambda email: client(email)),
        get_client_bundle=Mock(side_effect=lambda email: NS(primary=client(email), mobile=None)),
        get_mobile_traffic=Mock(return_value=None),
        get_primary_inbound_names=Mock(return_value=("Germany",)),
        list_clients=Mock(return_value=[client()]),
        get_devices=Mock(return_value=[]),
        get_bundle_devices=Mock(return_value=[]),
        ensure_connection=Mock(return_value="https://example.test/connect/fresh.html"),
        reissue_bundle_connection=Mock(return_value="https://example.test/connect/fresh.html"),
        get_mobile_subscription_url=Mock(return_value="https://sub.example.test/mobile"),
        get_expiring=Mock(return_value=[]),
        delete_client=Mock(return_value=client()),
        delete_client_bundle=Mock(return_value=client()),
        reset_bundle_devices=Mock(),
    )
    functions = source_functions(
        "bot.py", names, db_connect=connect, closing=closing, sqlite3=sqlite3,
        time=NS(time=lambda: 1000), ChatType=NS(PRIVATE="private"), ADMIN_TG_ID=1,
        ACTION_TIMEOUT_SECONDS=600, ADMIN_USERS_PAGE_SIZE=20,
        is_mobile_email=lambda email: email.endswith("__mobile"),
        InlineKeyboardButton=button, InlineKeyboardMarkup=lambda rows: rows,
        ParseMode=NS(MARKDOWN_V2="MarkdownV2"), SUPPORT_URL="https://example.test/support",
        build_client_service=lambda: service, EXPECTED_SERVICE_ERRORS=(RuntimeError,),
        LOGGER=NS(warning=Mock()),
        _markdown_v2_escape=markdown_v2_escape,
        REQUIRED_MEMBERSHIP_MODE="new_users", REQUIRED_TG_CHAT_ID=-100123,
        REQUIRED_TG_CHAT_URL="https://t.me/fixture_group",
        MembershipCheckError=type("MembershipCheckError", (Exception,), {}),
        CUSTOMER_CONFIG=NS(menu_animation_file_id=None),
        tariff_list_view=lambda **kwargs: ("tariffs", []),
        connection_view=lambda page, mobile=None: ("connection", []),
    )
    functions["_service"] = service
    return functions


def update(data, chat="private", user=1):
    return NS(
        effective_chat=NS(type=chat), effective_user=NS(id=user),
        message=NS(reply_text=AsyncMock()),
        callback_query=NS(data=data, answer=AsyncMock(), edit_message_text=AsyncMock()),
    )


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


def test_admin_list_uses_client_service_and_long_ref(bot):
    email = "n" * 64
    bot["_service"].list_clients.return_value = [client(email)]
    upd = update("admin_users")
    run(bot["admin_users"](upd))
    bot["_service"].list_clients.assert_called_once_with()
    rows = upd.callback_query.edit_message_text.call_args.kwargs["reply_markup"]
    values = [getattr(item, "callback_data", "") for row in rows for item in row]
    assert values and all(email not in value and len(value.encode()) <= 64 for value in values)


def test_admin_connection_repairs_before_showing_url(bot):
    ref = bot["get_or_create_client_ref"]("demo_target")
    upd = invoke(bot, f"uc:{ref}")
    bot["_service"].reissue_bundle_connection.assert_called_once_with("demo_target")
    rows = upd.callback_query.edit_message_text.call_args.kwargs["reply_markup"]
    assert any(getattr(item, "url", "") == "https://example.test/connect/fresh.html"
               for row in rows for item in row)


@pytest.mark.parametrize("warning", [None, "cleanup skipped"])
def test_delete_success_and_double_callback(bot, local_db, warning):
    ref = bot["get_or_create_client_ref"]("demo_target")
    other_ref = bot["get_or_create_client_ref"]("demo_other")
    before = state(local_db)
    bot["_service"].delete_client_bundle.return_value = client(warning=warning)
    context = NS(user_data={})
    invoke(bot, f"udel:{ref}", context)
    bot["_service"].delete_client_bundle.assert_not_called()
    invoke(bot, f"udc:{ref}", context)
    invoke(bot, f"uy:{ref}", context)
    after = state(local_db)
    bot["_service"].delete_client_bundle.assert_called_once_with("demo_target")
    assert len(after["telegram_links"]) == 1 and after["telegram_links"][0][1] == "demo_other"
    assert all(row[1] == "demo_other" for row in after["bind_tokens"])
    assert after["orders"] == before["orders"]
    assert after["notifications_sent"] == before["notifications_sent"]
    assert bot["get_client_email_by_ref"](ref) is None
    assert bot["get_client_email_by_ref"](other_ref) == "demo_other"
    invoke(bot, f"uy:{ref}", context)
    assert bot["_service"].delete_client_bundle.call_count == 1


def test_service_delete_failure_preserves_local_access(bot, local_db):
    ref = bot["get_or_create_client_ref"]("demo_target")
    before = state(local_db)
    bot["_service"].delete_client_bundle.side_effect = RuntimeError("synthetic")
    context = NS(user_data={})
    invoke(bot, f"udel:{ref}", context)
    invoke(bot, f"udc:{ref}", context)
    invoke(bot, f"uy:{ref}", context)
    assert state(local_db) == before


def test_local_failure_rolls_back_and_retries_without_service_delete(bot, local_db):
    ref = bot["get_or_create_client_ref"]("demo_target")
    before = state(local_db)
    with closing(local_db()) as db, db:
        db.execute("CREATE TRIGGER fail_ref BEFORE DELETE ON client_refs BEGIN SELECT RAISE(ABORT, 'test'); END")
    context = NS(user_data={
        "delete_confirmation": {"ref": ref, "step": 2, "updated_at": 1000},
    })
    invoke(bot, f"uy:{ref}", context)
    assert state(local_db) == before
    with closing(local_db()) as db, db:
        db.execute("DROP TRIGGER fail_ref")
    invoke(bot, f"uy:{ref}", context)
    assert bot["_service"].delete_client_bundle.call_count == 1
    assert bot["get_client_email_by_ref"](ref) is None


def test_stale_service_client_is_safe(bot):
    ref = bot["get_or_create_client_ref"]("demo_target")
    bot["_service"].get_client_bundle.side_effect = None
    bot["_service"].get_client_bundle.return_value = None
    upd = invoke(bot, f"u:{ref}")
    assert "больше не существует" in upd.callback_query.edit_message_text.call_args.args[0]


@pytest.mark.parametrize("chat", ["group", "supergroup"])
def test_group_start_and_callback_reveal_nothing(bot, chat):
    upd = invoke(bot, "uy:1", chat=chat)
    assert not upd.callback_query.edit_message_text.called
    run(bot["start"](upd, NS(args=["bind_synthetic"])))
    assert "личном чате" in upd.message.reply_text.call_args.args[0]
    bot["_service"].get_client.assert_not_called()


def test_channel_update_is_ignored(bot):
    upd = invoke(bot, "uy:1", chat="channel")
    run(bot["start"](upd, NS(args=["bind_synthetic"])))
    assert not upd.callback_query.answer.called
    assert not upd.callback_query.edit_message_text.called
    assert not upd.message.reply_text.called


def test_non_admin_cannot_delete(bot, local_db):
    ref = bot["get_or_create_client_ref"]("demo_target")
    before = state(local_db)
    invoke(bot, f"uy:{ref}", user=2)
    assert state(local_db) == before
    bot["_service"].delete_client_bundle.assert_not_called()


def test_confirm_without_prompt_does_not_delete(bot):
    ref = bot["get_or_create_client_ref"]("demo_target")
    invoke(bot, f"uy:{ref}")
    bot["_service"].get_client.assert_called_with("demo_target")
    bot["_service"].delete_client.assert_not_called()


def test_private_client_mode_unchanged(bot):
    bot["get_link_by_tg"] = Mock(return_value={"email": "demo_target"})
    bot["render_client_home"] = AsyncMock()
    upd = update("client_home", user=2)
    run(bot["start"](upd, NS(args=[])))
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


def test_bot_source_has_no_cli_or_subprocess_dependency():
    source = __import__("pathlib").Path("src/bot.py").read_text(encoding="utf-8")
    assert "subprocess" not in source
    assert "run_karina" not in source
    assert "karina_user" not in source
    assert "extract_info" not in source
    assert "parse_user_names" not in source


@pytest.mark.parametrize(("status", "is_member", "allowed"), [
    ("member", None, True), ("administrator", None, True), ("creator", None, True),
    ("restricted", True, True), ("restricted", False, False),
    ("left", None, False), ("kicked", None, False), ("banned", None, False),
])
def test_membership_authoritative_statuses(bot, status, is_member, allowed):
    api = NS(get_chat_member=AsyncMock(return_value=NS(status=status, is_member=is_member)))
    assert run(bot["check_required_membership"](api, 44)) is allowed
    api.get_chat_member.assert_awaited_once_with(-100123, 44)


def test_unbound_start_rechecks_membership_and_uses_configured_url(bot):
    bot["get_link_by_tg"] = Mock(return_value=None)
    api = NS(get_chat_member=AsyncMock(return_value=NS(status="left")))
    upd = update("unused", user=44)
    run(bot["start"](upd, NS(args=[], bot=api)))
    api.get_chat_member.assert_awaited_once()
    keyboard = upd.callback_query.edit_message_text.call_args.kwargs["reply_markup"]
    assert keyboard[0][0].url == "https://t.me/fixture_group"
    assert keyboard[1][0].callback_data == "membership_check"


def test_unbound_member_continues_existing_onboarding(bot):
    bot["get_link_by_tg"] = Mock(return_value=None)
    api = NS(get_chat_member=AsyncMock(return_value=NS(status="member")))
    upd = update("unused", user=44)
    run(bot["start"](upd, NS(args=[], bot=api)))
    assert upd.message.reply_text.call_args.args[0] == "tariffs"


def test_all_users_mode_rechecks_linked_user(bot):
    bot["REQUIRED_MEMBERSHIP_MODE"] = "all_users"
    api = NS(get_chat_member=AsyncMock(return_value=NS(status="left")))
    upd = update("unused", user=2)
    run(bot["start"](upd, NS(args=[], bot=api)))
    api.get_chat_member.assert_awaited_once_with(-100123, 2)
    assert upd.callback_query.edit_message_text.called


def test_membership_check_callback_calls_api_again_and_stays_blocked(bot):
    bot["get_link_by_tg"] = Mock(return_value=None)
    api = NS(get_chat_member=AsyncMock(return_value=NS(status="left")))
    invoke(bot, "membership_check", user=44, context=NS(user_data={}, bot=api))
    invoke(bot, "membership_check", user=44, context=NS(user_data={}, bot=api))
    assert api.get_chat_member.await_count == 2


def test_membership_api_failure_is_infrastructure_error(bot):
    bot["get_link_by_tg"] = Mock(return_value=None)
    api = NS(get_chat_member=AsyncMock(side_effect=RuntimeError("invalid chat")))
    upd = update("unused", user=44)
    run(bot["start"](upd, NS(args=[], bot=api)))
    assert "проверить подписку" in upd.callback_query.edit_message_text.call_args.args[0]
    assert "среди участников" not in upd.callback_query.edit_message_text.call_args.args[0]


def test_membership_disabled_and_linked_new_user_policy_bypass(bot):
    assert bot["membership_required"](True) is False
    bot["REQUIRED_MEMBERSHIP_MODE"] = "disabled"
    assert bot["membership_required"](False) is False
    bot["REQUIRED_MEMBERSHIP_MODE"] = "all_users"
    assert bot["membership_required"](True) is True


def test_client_connect_and_reset_are_bound_to_own_email(bot):
    context = NS(user_data={})
    invoke(bot, "client_connect", context=context, user=2)
    bot["_service"].reissue_bundle_connection.assert_called_once_with("demo_other")
    invoke(bot, "client_reset_devices", context=context, user=2)
    bot["_service"].reset_bundle_devices.assert_not_called()
    invoke(bot, "client_reset_confirm", context=context, user=2)
    bot["_service"].reset_bundle_devices.assert_called_once_with("demo_other")
