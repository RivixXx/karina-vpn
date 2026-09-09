import sys
from types import ModuleType, SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest


class Button:
    def __init__(self, text, **kwargs):
        self.text = text
        self.__dict__.update(kwargs)
        self.callback_data = kwargs.get("callback_data")


class Markup:
    def __init__(self, rows):
        self.inline_keyboard = rows


telegram = ModuleType("telegram")
telegram.InlineKeyboardButton = Button
telegram.InlineKeyboardMarkup = Markup
telegram.Update = type("Update", (), {"ALL_TYPES": ()})
constants = ModuleType("telegram.constants")
constants.ChatType = NS(PRIVATE="private")
constants.ParseMode = NS(MARKDOWN_V2="MarkdownV2")
extension = ModuleType("telegram.ext")
extension.Application = type("Application", (), {})
extension.CallbackQueryHandler = type("CallbackQueryHandler", (), {})
extension.CommandHandler = type("CommandHandler", (), {})
extension.MessageHandler = type("MessageHandler", (), {})
extension.ContextTypes = NS(DEFAULT_TYPE=object)
extension.filters = NS(TEXT=object(), COMMAND=object())
sys.modules.setdefault("telegram", telegram)
sys.modules.setdefault("telegram.constants", constants)
sys.modules.setdefault("telegram.ext", extension)

from src import bot
from src.models import ClientBundle, ClientInfo, CreateClientBundleResult, TrafficInfo
from src.services import ClientServiceError, ReconciliationRequiredError, ValidationError


def run(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    finally:
        coroutine.close()
    raise AssertionError("unexpected asynchronous I/O")


def client(email="demo", mobile=False):
    return ClientInfo(
        email=email, status="active", enabled=True, expiry_time_ms=2_000_000_000_000,
        expiry_text="18.05.2033 06:33", device_count=1, device_limit=2,
        total_traffic_bytes=50 * 1024 ** 3 if mobile else 0,
        used_traffic_bytes=1024 ** 2 if mobile else 0, inbound_ids=(5,) if mobile else (2, 3, 4),
        sub_id="mobile_id123" if mobile else "primary_id123",
        connect_url="https://connect.example.test/primary_id123.html",
    )


def update(data="", *, user=1, chat="private", text="demo"):
    return NS(
        effective_user=NS(id=user), effective_chat=NS(id=10, type=chat),
        message=NS(text=text, reply_text=AsyncMock()),
        callback_query=NS(data=data, answer=AsyncMock(), edit_message_text=AsyncMock()),
    )


@pytest.fixture
def admin(monkeypatch):
    primary, mobile = client(), client("demo__mobile", True)
    result = CreateClientBundleResult(
        ClientBundle(primary, mobile), "https://connect.example.test/primary_id123.html",
    )
    service = NS(
        validate_email=Mock(side_effect=lambda value: value), get_client=Mock(return_value=None),
        create_client_bundle=Mock(return_value=result),
        get_client_bundle=Mock(return_value=result.bundle),
        get_mobile_traffic=Mock(return_value=TrafficInfo(1024 ** 2, 50 * 1024 ** 3,
                                                         50 * 1024 ** 3 - 1024 ** 2,
                                                         1024 ** 2 / (50 * 1024 ** 3) * 100)),
        plan_mobile_migration=Mock(), migrate_client_to_mobile_bundle=Mock(),
        extend_client=Mock(return_value=primary), set_hwid_limit=Mock(return_value=primary),
        disable_client=Mock(return_value=primary), enable_client=Mock(return_value=primary),
        reset_bundle_devices=Mock(), remove_bundle_device=Mock(),
        connect_dir=NS(is_dir=lambda: True, exists=lambda: True),
    )
    monkeypatch.setattr(bot, "ADMIN_TG_ID", 1)
    monkeypatch.setattr(bot, "build_client_service", lambda: service)
    monkeypatch.setattr(bot, "get_or_create_client_ref", lambda email: 7)
    monkeypatch.setattr(bot, "get_link_by_email", lambda email: None)
    return service


def advance_create(admin):
    context = NS(user_data={})
    start = update("admin_create_help")
    run(bot.admin_create_callback(start, context, "admin_create_help"))
    run(bot.admin_create_text(update(text="demo"), context))
    run(bot.admin_create_callback(update("ac:t:90"), context, "ac:t:90"))
    run(bot.admin_create_callback(update("ac:h:3"), context, "ac:h:3"))
    return context


def test_create_wizard_bundle_called_once_and_is_idempotent(admin):
    context = advance_create(admin)
    confirmation = update("ac:ok")
    run(bot.admin_create_callback(confirmation, context, "ac:ok"))
    admin.create_client_bundle.assert_called_once_with("demo", days=90, hwid_limit=3)
    text = confirmation.callback_query.edit_message_text.call_args.args[0]
    markup = confirmation.callback_query.edit_message_text.call_args.kwargs["reply_markup"]
    assert "Пользователь создан" in text and "__mobile" not in text and "SUB_ID" not in text
    assert any(getattr(button, "url", "") for row in markup.inline_keyboard for button in row)
    run(bot.admin_create_callback(update("ac:ok"), context, "ac:ok"))
    assert admin.create_client_bundle.call_count == 1


@pytest.mark.parametrize("days", ["30", "90", "180", "365", "unlimited"])
def test_create_term_choices(admin, days):
    context = NS(user_data={})
    run(bot.admin_create_callback(update("admin_create_help"), context, "admin_create_help"))
    run(bot.admin_create_text(update(text="demo"), context))
    run(bot.admin_create_callback(update(f"ac:t:{days}"), context, f"ac:t:{days}"))
    assert context.user_data["admin_create"]["days"] == (None if days == "unlimited" else int(days))


@pytest.mark.parametrize("name", ["x", "bad@email", "x" * 57, "demo__mobile"])
def test_create_username_validation(admin, name):
    context = NS(user_data={})
    run(bot.admin_create_callback(update("admin_create_help"), context, "admin_create_help"))
    admin.validate_email.side_effect = ValidationError("invalid") if name != "demo__mobile" else None
    run(bot.admin_create_text(update(text=name), context))
    assert context.user_data["admin_create"]["step"] == "username"
    admin.create_client_bundle.assert_not_called()


def test_create_admin_private_only_and_cancel(admin):
    for kwargs in ({"user": 2}, {"chat": "group"}):
        context = NS(user_data={})
        run(bot.admin_create_callback(update("admin_create_help", **kwargs), context, "admin_create_help"))
        assert not context.user_data
    context = advance_create(admin)
    run(bot.admin_create_callback(update("ac:cancel"), context, "ac:cancel"))
    assert "admin_create" not in context.user_data


def test_create_duplicate_and_stale_state_do_not_mutate(admin, monkeypatch):
    context = NS(user_data={})
    run(bot.admin_create_callback(update("admin_create_help"), context, "admin_create_help"))
    admin.get_client.return_value = client()
    run(bot.admin_create_text(update(text="demo"), context))
    assert context.user_data["admin_create"]["step"] == "username"
    admin.create_client_bundle.assert_not_called()

    admin.get_client.return_value = None
    context.user_data["admin_create"]["updated_at"] = 0
    monkeypatch.setattr(bot.time, "time", lambda: bot.CREATE_TIMEOUT_SECONDS + 1)
    run(bot.admin_create_text(update(text="demo"), context))
    assert "admin_create" not in context.user_data
    admin.create_client_bundle.assert_not_called()


@pytest.mark.parametrize("failure, expected", [
    (ClientServiceError("synthetic"), "Не удалось"),
    (ReconciliationRequiredError("synthetic"), "Требуется проверка"),
])
def test_create_failure_never_shows_connection(admin, failure, expected):
    context = advance_create(admin)
    admin.create_client_bundle.side_effect = failure
    upd = update("ac:ok")
    run(bot.admin_create_callback(upd, context, "ac:ok"))
    text = upd.callback_query.edit_message_text.call_args.args[0]
    assert expected in text and "https://" not in text


def test_profile_is_bundle_level_and_callback_safe(admin):
    upd = update("u:7")
    run(bot.admin_bundle_user(upd, "demo"))
    text = upd.callback_query.edit_message_text.call_args.args[0]
    markup = upd.callback_query.edit_message_text.call_args.kwargs["reply_markup"]
    assert "Основной VPN" in text and "50.0 ГБ" in text and "__mobile" not in text
    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row
                 if getattr(button, "callback_data", None)]
    assert callbacks and all("demo" not in value and len(value.encode()) <= 64 for value in callbacks)


def test_profile_legacy_mobile_state(admin):
    bundle = ClientBundle(client(), None)
    admin.get_client_bundle.return_value = bundle
    admin.get_mobile_traffic.return_value = None
    upd = update("u:7")
    run(bot.admin_bundle_user(upd, "demo"))
    text = upd.callback_query.edit_message_text.call_args.args[0]
    assert "ещё не активирован" in text and "__mobile" not in text


def test_extend_hwid_toggle_and_device_actions_use_bundle_service(admin, monkeypatch):
    monkeypatch.setattr(bot, "get_client_email_by_ref", lambda ref: "demo")
    context = NS(user_data={})
    for data in ("ue30:7", "uh5:7", "uda:7", "ur9:7"):
        run(bot.admin_bundle_action_callback(update(data), context, data))
    admin.extend_client.assert_called_once_with("demo", 30)
    admin.set_hwid_limit.assert_called_once_with("demo", 5)
    admin.reset_bundle_devices.assert_called_once_with("demo")
    admin.remove_bundle_device.assert_called_once_with("demo", 9)

    admin.get_client.return_value = client()
    run(bot.admin_bundle_action_callback(update("ut:7"), context, "ut:7"))
    admin.disable_client.assert_called_once_with("demo")


def test_migration_requires_preview_and_second_confirmation(admin, monkeypatch):
    monkeypatch.setattr(bot, "get_client_email_by_ref", lambda ref: "demo")
    plan = NS(
        already_migrated=False, blocking_errors=(), warnings=("synthetic warning",),
        needs_mobile_create=True, needs_external_link_update=True, needs_primary_detach=True,
    )
    admin.plan_mobile_migration.return_value = plan
    context = NS(user_data={})
    run(bot.admin_bundle_action_callback(update("umy:7"), context, "umy:7"))
    admin.migrate_client_to_mobile_bundle.assert_not_called()
    preview = update("um:7")
    run(bot.admin_bundle_action_callback(preview, context, "um:7"))
    assert "CREATE mobile" in preview.callback_query.edit_message_text.call_args.args[0]
    admin.migrate_client_to_mobile_bundle.assert_not_called()
    run(bot.admin_bundle_action_callback(update("umy:7"), context, "umy:7"))
    admin.migrate_client_to_mobile_bundle.assert_called_once_with("demo")


def test_admin_service_screen_is_read_only_and_private(admin):
    upd = update("admin_service")
    run(bot.callbacks(upd, NS(user_data={})))
    text = upd.callback_query.edit_message_text.call_args.args[0]
    assert "XUI API: reachable" in text and "provider absent" in text
    assert "password" not in text.lower() and "token" not in text.lower()


def test_admin_service_screen_is_read_only_and_does_not_require_client_link(admin, monkeypatch):
    monkeypatch.setattr(bot, "get_link_by_tg", Mock(side_effect=AssertionError("client lookup")))
    upd = update("admin_service")
    run(bot.callbacks(upd, NS(user_data={})))
    text = upd.callback_query.edit_message_text.call_args.args[0]
    assert "XUI API: reachable" in text
    assert "provider absent" in text
