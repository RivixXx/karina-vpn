from types import SimpleNamespace as NS
from unittest.mock import Mock
from unittest.mock import AsyncMock

import pytest

from src.app_config import ConfigError, load_config
from src.models import ClientBundle, ClientInfo, OrderStatus, PLANS, TrafficInfo
from src.repositories import BillingRepository
from src.services import BillingService, CustomerOrderError, CustomerOrderService
from src.ui.connection import connection_view
from src.ui.customer import cabinet_keyboard, format_cabinet, stale_binding_view
from src.ui.help import platform_choice_view, platform_view
from src.ui.tariffs import get_tariff, tariff_detail_view, tariff_list_view
from src import bot


def run(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    finally:
        coroutine.close()


def client(*, expiry=2_000_000_000_000, enabled=True, devices=2, limit=5,
           email="tg_42"):
    return ClientInfo(email, "active", enabled, expiry, "18.05.2033 06:33",
                      devices, limit, 0, 0, (2, 3, 4), "sub", "https://c.test/sub.html")


def bundle(**kwargs):
    return ClientBundle(client(**kwargs), client(email="tg_42__mobile", limit=0))


def callbacks(markup):
    return [button.callback_data for row in markup.inline_keyboard for button in row
            if button.callback_data]


def test_canonical_tariff_catalog_and_server_side_callbacks():
    assert [(p.id, p.days, p.price_rub) for p in PLANS] == [
        ("m1", 30, 199), ("m3", 90, 499), ("m6", 180, 899), ("y1", 365, 1499),
    ]
    text, keyboard = tariff_list_view()
    assert " tranche" not in text
    assert callbacks(keyboard) == ["tariff:m1", "tariff:m3", "tariff:m6", "tariff:y1"]
    assert get_tariff("m6").price_rub == 899
    assert get_tariff("unknown") is None


@pytest.mark.parametrize("code,monthly,saving", [
    ("m1", "199 ₽ в месяц", None), ("m3", "166 ₽ в месяц", "98 ₽"),
    ("m6", "150 ₽ в месяц", "295 ₽"), ("y1", "125 ₽ в месяц", "889 ₽"),
])
def test_tariff_marketing_is_derived(code, monthly, saving):
    text, keyboard = tariff_detail_view(code)
    assert monthly in text
    assert (saving in text) if saving else "Экономия" not in text
    assert callbacks(keyboard)[0] == f"order:{code}"


def test_connection_and_platform_navigation_hide_missing_optional_urls():
    _, connection = connection_view(
        "https://connect.example.test/user.html", "https://sub.example.test/mobile",
    )
    assert callbacks(connection) == ["client_qr", "connect_video", "client_home"]
    assert any(getattr(button, "url", None) == "https://sub.example.test/mobile"
               for row in connection.inline_keyboard for button in row)
    _, choice = platform_choice_view()
    assert set(callbacks(choice)) >= {"platform:android", "platform:ios", "platform:windows", "platform:macos"}
    config = NS(happ_android_url=None, telegraph_android_url=None,
                connect_video_file_id=None)
    text, android = platform_view("android", config)
    assert "Android" in text
    assert all(getattr(button, "url", None) is None
               for row in android.inline_keyboard for button in row)


def test_platform_configured_urls_are_shown():
    config = NS(happ_android_url="https://app.example.test", telegraph_android_url="https://guide.example.test",
                connect_video_file_id="telegram-file")
    _, keyboard = platform_view("android", config)
    urls = [button.url for row in keyboard.inline_keyboard for button in row
            if getattr(button, "url", None)]
    assert urls == ["https://app.example.test", "https://guide.example.test"]
    assert "connect_video" in callbacks(keyboard)


@pytest.mark.parametrize("expiry,enabled,expected", [
    (2_000_000_000_000, True, "🟢 VPN работает"), (0, True, "без ограничений"),
    (1, True, "🔴 Подписка закончилась"), (2_000_000_000_000, False, "⛔ Подписка отключена"),
])
def test_cabinet_subscription_states(expiry, enabled, expected):
    text = format_cabinet(bundle(expiry=expiry, enabled=enabled), None,
                          traffic_unavailable=True, now_ms=1_000)
    assert expected in text
    assert "__mobile" not in text and "subId" not in text and "UUID" not in text


def test_cabinet_mobile_states_and_device_count_only():
    healthy = format_cabinet(bundle(), TrafficInfo(12 * 1024 ** 3, 50 * 1024 ** 3,
                                                    38 * 1024 ** 3, 24), now_ms=1_000)
    exhausted = format_cabinet(bundle(), TrafficInfo(50 * 1024 ** 3, 50 * 1024 ** 3,
                                                      0, 100), now_ms=1_000)
    missing = format_cabinet(ClientBundle(client(), None), None, now_ms=1_000)
    assert "12.0 / 50 ГБ" in healthy and "Устройства: 2 из 5" in healthy
    assert "лимит исчерпан" in exhausted
    assert "Не подключена" in missing


def test_cabinet_optional_external_buttons():
    keyboard = cabinet_keyboard(news_url=None, support_url=None)
    assert all(getattr(button, "url", None) is None
               for row in keyboard.inline_keyboard for button in row)
    keyboard = cabinet_keyboard(news_url="https://news.test", support_url="https://support.test")
    assert len([b for row in keyboard.inline_keyboard for b in row
                if getattr(b, "url", None)]) == 2


def test_stale_binding_is_preserved_and_recoverable():
    text, keyboard = stale_binding_view("https://support.test")
    assert "Привязка сохранена" in text
    assert keyboard.inline_keyboard[0][0].url == "https://support.test"


def config_file(tmp_path, **extra):
    values = {"XUI_BASE": "https://x.test", "XUI_USER": "user", "XUI_PASS": "pass",
              "SUB_BASE": "https://sub.test", "CONNECT_BASE": "https://connect.test",
              "INBOUND_IDS": "2,3,4,5", "REQUIRED_MEMBERSHIP_MODE": "disabled"}
    values.update(extra)
    path = tmp_path / "config.env"
    path.write_text("\n".join(f"{key}={value}" for key, value in values.items()), encoding="utf-8")
    return path


def test_customer_config_empty_is_safe_and_https_is_validated(tmp_path):
    config = load_config(config_file(tmp_path, MENU_ANIMATION_FILE_ID="file",
                                     CONNECT_VIDEO_FILE_ID="video"))
    assert config.menu_animation_file_id == "file" and config.support_url is None
    with pytest.raises(ConfigError, match="SUPPORT_URL"):
        load_config(config_file(tmp_path, SUPPORT_URL="http://unsafe.test"))


def test_menu_media_uses_file_id_and_failure_is_nonfatal(monkeypatch):
    monkeypatch.setattr(bot, "CUSTOMER_CONFIG", NS(menu_animation_file_id="telegram-file"))
    update = NS(effective_chat=NS(id=42))
    api = NS(send_animation=AsyncMock())
    run(bot.send_menu_animation(update, NS(bot=api)))
    api.send_animation.assert_awaited_once_with(chat_id=42, animation="telegram-file")
    api.send_animation.side_effect = RuntimeError("deleted")
    run(bot.send_menu_animation(update, NS(bot=api)))


class FakeClientService:
    def __init__(self, existing=None, fail_create=False):
        self.current = existing
        self.fail_create = fail_create
        self.created = []
        self.extended = []

    def get_client_bundle(self, email):
        return self.current

    def create_client_bundle(self, email, days):
        if self.fail_create:
            raise RuntimeError("provision failed")
        self.created.append((email, days))
        self.current = bundle(email=email)

    def extend_bundle(self, email, days):
        self.extended.append((email, days))


def order_service(tmp_path, local_db, *, link=None, client_service=None):
    repo = BillingRepository(tmp_path / "orders.sqlite", connect=local_db)
    repo.init_schema()
    client_service = client_service or FakeClientService()
    links = {} if link is None else {42: link}
    billing = BillingService(repo, client_service, token_factory=lambda: "OPAQUE", now_provider=lambda: 100)
    service = CustomerOrderService(
        billing, client_service, lambda tg: links.get(tg),
        lambda tg, email: links.__setitem__(tg, {"email": email}), now_provider=lambda: 200,
    )
    return service, repo, client_service, links


def test_pending_order_is_persistent_and_duplicate_safe(tmp_path, local_db):
    service, repo, _, _ = order_service(tmp_path, local_db)
    first, created = service.create_request(42, "m6")
    second, duplicate = service.create_request(42, "m1")
    assert created is True and duplicate is False and first.id == second.id
    assert repo.get_order(first.id).status is OrderStatus.PENDING
    assert first.days == 180 and first.amount_rub == 899


def test_new_user_approval_provisions_once_binds_and_is_idempotent(tmp_path, local_db):
    service, repo, clients, links = order_service(tmp_path, local_db)
    order, _ = service.create_request(42, "m3")
    completed, changed = service.approve(order.id)
    again, duplicate = service.approve(order.id)
    assert changed is True and duplicate is False
    assert clients.created == [("tg_42", 90)] and links[42]["email"] == "tg_42"
    assert completed.status is again.status is OrderStatus.COMPLETED


def test_new_user_retry_after_status_write_failure_does_not_extend_or_recreate(tmp_path, local_db):
    service, repo, clients, links = order_service(tmp_path, local_db)
    order, _ = service.create_request(42, "m3")
    approve = repo.approve_pending
    repo.approve_pending = Mock(return_value=None)
    with pytest.raises(Exception, match="статус"):
        service.approve(order.id)
    assert clients.created == [("tg_42", 90)] and links[42]["email"] == "tg_42"
    repo.approve_pending = approve
    completed, changed = service.approve(order.id)
    assert changed and completed.status is OrderStatus.COMPLETED
    assert clients.created == [("tg_42", 90)] and clients.extended == []


def test_renewal_uses_existing_bundle_and_never_creates_second(tmp_path, local_db):
    clients = FakeClientService(existing=bundle())
    service, _, clients, _ = order_service(tmp_path, local_db, link={"email": "existing"}, client_service=clients)
    order, _ = service.create_request(42, "m1")
    service.approve(order.id)
    assert clients.created == [] and clients.extended == [("existing", 30)]


def test_unlimited_renewal_is_controlled(tmp_path, local_db):
    clients = FakeClientService(existing=bundle(expiry=0))
    service, repo, _, _ = order_service(tmp_path, local_db, link={"email": "existing"}, client_service=clients)
    order, _ = service.create_request(42, "m1")
    with pytest.raises(CustomerOrderError, match="Бессрочная"):
        service.approve(order.id)
    assert repo.get_order(order.id).status is OrderStatus.PENDING


def test_rejection_and_provision_failure_are_safe(tmp_path, local_db):
    service, repo, _, _ = order_service(tmp_path, local_db, client_service=FakeClientService(fail_create=True))
    order, _ = service.create_request(42, "m1")
    with pytest.raises(RuntimeError):
        service.approve(order.id)
    assert repo.get_order(order.id).status is OrderStatus.PENDING
    rejected, changed = service.reject(order.id)
    again, duplicate = service.reject(order.id)
    assert changed and not duplicate and rejected.status is again.status is OrderStatus.CANCELLED
