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
from src.models import (
    ClientBundle, ClientInfo, CreateClientBundleResult, DeviceInfo,
    TrafficInfo,
)
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
        get_primary_inbound_names=Mock(return_value=("Германия", "Германия 2", "Германия 3")),
        get_mobile_traffic=Mock(return_value=TrafficInfo(1024 ** 2, 50 * 1024 ** 3,
                                                         50 * 1024 ** 3 - 1024 ** 2,
                                                         1024 ** 2 / (50 * 1024 ** 3) * 100)),
        plan_mobile_migration=Mock(), migrate_client_to_mobile_bundle=Mock(),
        extend_client=Mock(return_value=primary), extend_bundle=Mock(return_value=primary),
        set_hwid_limit=Mock(return_value=primary),
        disable_client=Mock(return_value=primary), enable_client=Mock(return_value=primary),
        set_bundle_enabled=Mock(return_value=primary),
        reissue_bundle_connection=Mock(return_value=result.subscription_page),
        reset_bundle_devices=Mock(), remove_bundle_device=Mock(),
        delete_client_bundle=Mock(),
        get_devices=Mock(return_value=[]),
        get_bundle_devices=Mock(return_value=[]),
        get_traffic=Mock(return_value=TrafficInfo(0, 0, None, None)),
        list_clients=Mock(return_value=[primary]), get_expiring=Mock(return_value=[]),
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
    assert "Основные подключения" in text and "50.0 ГБ" in text and "__mobile" not in text
    assert "Германия 3" in text
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
    assert "Не подключена" in text and "__mobile" not in text


def test_mobile_exhausted_and_traffic_failure_card(admin):
    bundle = admin.get_client_bundle.return_value
    exhausted = TrafficInfo(50 * 1024 ** 3, 50 * 1024 ** 3, 0, 100.0)
    text = bot.format_bundle_profile(bundle, exhausted, ("Германия",))
    assert "50.0 ГБ / 50.0 ГБ · лимит исчерпан" in text

    admin.get_mobile_traffic.side_effect = ClientServiceError("synthetic")
    upd = update("u:7")
    run(bot.admin_bundle_user(upd, "demo"))
    assert "Данные временно недоступны" in upd.callback_query.edit_message_text.call_args.args[0]


def test_client_list_filters_mobile_and_paginates(admin):
    clients = [client(f"user{i:02d}") for i in range(21)] + [client("hidden__mobile", True)]
    admin.list_clients.return_value = clients
    upd = update("admin_users")
    run(bot.admin_users(upd, 0))
    rows = upd.callback_query.edit_message_text.call_args.kwargs["reply_markup"].inline_keyboard
    labels = [button.text for row in rows for button in row]
    callbacks = [getattr(button, "callback_data", "") for row in rows for button in row]
    assert not any("__mobile" in label for label in labels)
    assert "admin_users:1" in callbacks


def test_unlimited_extend_and_reset_require_confirmation(admin, monkeypatch):
    monkeypatch.setattr(bot, "get_client_email_by_ref", lambda ref: "demo")
    admin.get_client.return_value = client()
    context = NS(user_data={})
    run(bot.admin_bundle_action_callback(update("ueu:7"), context, "ueu:7"))
    admin.extend_bundle.assert_called_once_with("demo", None)

    run(bot.admin_bundle_action_callback(update("uda:7"), context, "uda:7"))
    admin.reset_bundle_devices.assert_not_called()
    run(bot.admin_bundle_action_callback(update("uday:7"), context, "uday:7"))
    admin.reset_bundle_devices.assert_called_once_with("demo")


def test_mobile_ref_and_expired_delete_confirmation_are_safe(admin, monkeypatch):
    monkeypatch.setattr(bot, "get_client_email_by_ref", lambda ref: "demo__mobile")
    context = NS(user_data={})
    run(bot.admin_bundle_action_callback(update("ut:7"), context, "ut:7"))
    admin.set_bundle_enabled.assert_not_called()

    monkeypatch.setattr(bot, "get_client_email_by_ref", lambda ref: "demo")
    context.user_data["delete_confirmation"] = {"ref": 7, "step": 1, "updated_at": 0}
    monkeypatch.setattr(bot.time, "time", lambda: bot.ACTION_TIMEOUT_SECONDS + 1)
    run(bot.admin_ref_callback(update("udc:7"), context, "udc:7"))
    admin.delete_client_bundle.assert_not_called()


def test_cancel_clears_destructive_state_and_hwid_lowering_warns(admin, monkeypatch):
    monkeypatch.setattr(bot, "get_client_email_by_ref", lambda ref: "demo")
    context = NS(user_data={
        "delete_confirmation": {"ref": 7, "step": 1, "updated_at": 1},
        "device_reset_confirmation": {"ref": 7, "updated_at": 1},
    })
    run(bot.admin_ref_callback(update("u:7"), context, "u:7"))
    assert "delete_confirmation" not in context.user_data
    assert "device_reset_confirmation" not in context.user_data

    crowded = client()
    crowded = crowded.__class__(**{**crowded.__dict__, "device_count": 3})
    admin.get_client.return_value = crowded
    upd = update("uh1:7")
    run(bot.admin_bundle_action_callback(upd, context, "uh1:7"))
    text = upd.callback_query.edit_message_text.call_args.args[0]
    assert "Подключено устройств больше нового лимита" in text


def test_bundle_card_dynamic_values_are_plain_text_safe(admin):
    special = "Test-User_[device]+foo(bar)=x!{value}|"
    primary = client(special)
    admin.get_client_bundle.return_value = ClientBundle(primary, None)
    admin.get_primary_inbound_names.return_value = (special,)
    upd = update("u:7")
    run(bot.admin_bundle_user(upd, special))
    call = upd.callback_query.edit_message_text.call_args
    assert special in call.args[0]
    assert "parse_mode" not in call.kwargs


def test_extend_hwid_toggle_and_device_actions_use_bundle_service(admin, monkeypatch):
    monkeypatch.setattr(bot, "get_client_email_by_ref", lambda ref: "demo")
    context = NS(user_data={})
    admin.get_client.return_value = client()
    for data in ("ue30:7", "uh5:7", "uda:7", "uday:7", "ur9:7"):
        run(bot.admin_bundle_action_callback(update(data), context, data))
    admin.extend_bundle.assert_called_once_with("demo", 30)
    admin.set_hwid_limit.assert_called_once_with("demo", 5)
    admin.reset_bundle_devices.assert_called_once_with("demo")
    admin.remove_bundle_device.assert_called_once_with("demo", 9)

    run(bot.admin_bundle_action_callback(update("ut:7"), context, "ut:7"))
    admin.set_bundle_enabled.assert_called_once_with("demo", False)


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


RESERVED = set(r"_*[]()~`>#+-=|{}.!")


def assert_valid_markdown_v2(text):
    escaped = False
    markup_counts = {"*": 0, "_": 0, "`": 0}
    for char in text:
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char in markup_counts:
            markup_counts[char] += 1
        elif char in RESERVED:
            raise AssertionError(f"unescaped MarkdownV2 character: {char!r}")
    assert not escaped
    assert all(count % 2 == 0 for count in markup_counts.values())


@pytest.mark.parametrize("value", [
    "Test-User", "Test_User", "A+B", "50.0%", "[device]", "foo(bar)",
    "x=y", "test!", r"\#name", "{value}", "pipe|value",
])
def test_markdown_v2_dynamic_escape_covers_production_characters(value):
    escaped = bot.markdown_v2_escape(value)
    assert_valid_markdown_v2(escaped)


def test_render_admin_home_is_valid_markdown_v2():
    upd = update()
    run(bot.render_admin_home(upd))
    call = upd.callback_query.edit_message_text.call_args
    assert call.kwargs["parse_mode"] == bot.ParseMode.MARKDOWN_V2
    assert "HTTPS\\-выдача" in call.args[0]
    assert_valid_markdown_v2(call.args[0])


def test_dynamic_markdown_formatters_escape_values():
    special = "Test-User_[device]+foo(bar)=x!{value}|"
    item = client()
    item = item.__class__(**{**item.__dict__, "expiry_text": special})
    assert_valid_markdown_v2(bot.format_client_profile(item))
    order = NS(id=special, days=30, amount_rub=100)
    plan = NS(title=special)
    assert_valid_markdown_v2(bot.format_billing_order(order, plan))


def test_client_device_and_traffic_renders_escape_dynamic_values(admin):
    special = "Test-User_[device]+foo(bar)=x!{value}|"
    admin.get_client.return_value = client()
    admin.get_devices.return_value = [DeviceInfo(1, special, special, special, special, 0, 0)]
    devices_update = update("client_devices")
    run(bot.client_devices(devices_update, "demo"))
    device_call = devices_update.callback_query.edit_message_text.call_args
    assert device_call.kwargs["parse_mode"] == bot.ParseMode.MARKDOWN_V2
    assert_valid_markdown_v2(device_call.args[0])

    admin.get_traffic.return_value = TrafficInfo(1024, 50 * 1024 ** 3, 50 * 1024 ** 3 - 1024, 0.1)
    traffic_update = update("client_traffic")
    run(bot.client_traffic(traffic_update, "demo"))
    traffic_call = traffic_update.callback_query.edit_message_text.call_args
    assert traffic_call.kwargs["parse_mode"] == bot.ParseMode.MARKDOWN_V2
    assert_valid_markdown_v2(traffic_call.args[0])


def test_application_error_handler_logs_and_replies_without_parse_mode(monkeypatch):
    logger = NS(error=Mock(), warning=Mock())
    monkeypatch.setattr(bot, "LOGGER", logger)
    message = NS(reply_text=AsyncMock())
    upd = NS(effective_message=message)
    error = RuntimeError("technical secret")
    run(bot.telegram_error_handler(upd, NS(error=error)))
    logger.error.assert_called_once()
    reply = message.reply_text.call_args
    assert "technical secret" not in reply.args[0]
    assert "parse_mode" not in reply.kwargs


def test_user_cabinet_traffic_failure_uses_safe_fallback(admin):
    admin.get_mobile_traffic.side_effect = ClientServiceError("synthetic")
    upd = update("client_home")
    run(bot.render_client_home(upd, "demo"))
    call = upd.callback_query.edit_message_text.call_args
    assert "Данные временно недоступны" in call.args[0]
    assert "demo__mobile" not in call.args[0]


def test_admin_service_screen_is_read_only_and_does_not_require_client_link(admin, monkeypatch):
    monkeypatch.setattr(bot, "get_link_by_tg", Mock(side_effect=AssertionError("client lookup")))
    upd = update("admin_service")
    run(bot.callbacks(upd, NS(user_data={})))
    text = upd.callback_query.edit_message_text.call_args.args[0]
    assert "XUI API: reachable" in text
    assert "provider absent" in text
