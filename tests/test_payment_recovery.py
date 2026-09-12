from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock
import sqlite3
import pytest

from src.models import OrderStatus, PLANS
from src.repositories import BillingRepository
from src.services import BillingService, CustomerOrderService, BillingStateError, ReconciliationRequiredError
from src.payment_delivery import deliver

DAY = 86400000
NOW = 1_800_000_000


class Clients:
    def __init__(self, expiry=None):
        self.bundle = self.make_bundle(expiry) if expiry else None
        self.created = 0
        self.targets = []
        self.failure = None

    @staticmethod
    def make_bundle(expiry):
        return NS(primary=NS(expiry_time_ms=expiry, expiry_text=str(expiry)),
                  mobile=NS(expiry_time_ms=expiry))

    def get_client_bundle(self, email):
        return self.bundle

    def create_client_bundle(self, email, days, *, target_expiry_ms=None):
        if self.bundle is None:
            self.created += 1
            self.bundle = self.make_bundle(target_expiry_ms)
        if self.failure:
            raise RuntimeError(self.failure)

    def set_bundle_expiry(self, email, target):
        self.targets.append(target)
        self.bundle.mobile.expiry_time_ms = target
        if self.failure == "partial":
            raise ReconciliationRequiredError("partial")
        self.bundle.primary.expiry_time_ms = target
        if self.failure:
            raise RuntimeError(self.failure)
        return self.bundle.primary


def setup(tmp_path, expiry=NOW * 1000):
    path = tmp_path / "recovery.sqlite"
    repo = BillingRepository(path, connect=lambda: sqlite3.Connection(path))
    repo.init_schema()
    clients = Clients(expiry)
    links = {42: {"email": "customer"}} if expiry else {}
    billing = BillingService(repo, clients, now_provider=lambda: NOW)
    service = CustomerOrderService(billing, clients, links.get,
        lambda tg, email: links.__setitem__(tg, {"email": email}), now_provider=lambda: NOW)
    return repo, clients, billing, service, links


def test_renewal_restart_after_commit_failure_keeps_target(tmp_path):
    repo, clients, billing, service, links = setup(tmp_path)
    order, _ = service.create_request(42, "m1")
    finish = repo.complete_operation
    repo.complete_operation = Mock(return_value=None)
    with pytest.raises(ReconciliationRequiredError):
        service.approve(order.id)
    target = NOW * 1000 + 30 * DAY
    assert clients.bundle.primary.expiry_time_ms == target
    assert repo.get_order(order.id).status is OrderStatus.PAID
    repo.complete_operation = finish
    restarted = CustomerOrderService(billing, clients, links.get, Mock(), now_provider=lambda: NOW + 3600)
    assert restarted.approve(order.id)[0].status is OrderStatus.COMPLETED
    assert clients.targets == [target, target]
    assert restarted.approve(order.id)[1] is False
    assert len(repo.due_effects(NOW)) == 3


def test_partial_mobile_write_is_reconciled_without_extra_days(tmp_path):
    repo, clients, _, service, _ = setup(tmp_path)
    order, _ = service.create_request(42, "m3")
    clients.failure = "partial"
    with pytest.raises(ReconciliationRequiredError):
        service.approve(order.id)
    assert clients.bundle.mobile.expiry_time_ms != clients.bundle.primary.expiry_time_ms
    assert repo.cancel_order(order.id) is None
    clients.failure = None
    service.approve(order.id)
    assert clients.bundle.primary.expiry_time_ms == NOW * 1000 + 90 * DAY
    assert clients.bundle.mobile.expiry_time_ms == clients.bundle.primary.expiry_time_ms


def test_new_customer_recovers_issuance_and_binding_failure(tmp_path):
    repo, clients, _, service, links = setup(tmp_path, expiry=None)
    order, _ = service.create_request(42, "m1")
    clients.failure = "issuance"
    with pytest.raises(RuntimeError):
        service.approve(order.id)
    assert not links and clients.created == 1
    clients.failure = None
    bind = service.create_binding
    service.create_binding = Mock(side_effect=RuntimeError("binding"))
    with pytest.raises(RuntimeError):
        service.approve(order.id)
    service.create_binding = bind
    service.approve(order.id)
    assert clients.created == 1 and links[42]["email"] == "tg_42"


def test_changed_catalog_does_not_change_purchased_days(tmp_path):
    repo, clients, billing, service, _ = setup(tmp_path)
    order, _ = service.create_request(42, "m1")
    billing.plans["m1"] = replace(PLANS[0], days=999, enabled=False)
    service.approve(order.id)
    assert clients.bundle.primary.expiry_time_ms == NOW * 1000 + 30 * DAY


def test_external_expiry_change_is_not_overwritten_by_retry(tmp_path):
    repo, clients, _, service, _ = setup(tmp_path)
    order, _ = service.create_request(42, "m1")
    clients.failure = "after-write"
    with pytest.raises(RuntimeError):
        service.approve(order.id)
    clients.failure = None
    clients.bundle = clients.make_bundle(NOW * 1000 + 100 * DAY)
    with pytest.raises(ReconciliationRequiredError):
        service.approve(order.id)
    assert len(clients.targets) == 1


def test_cancel_and_claim_are_mutually_exclusive(tmp_path):
    repo, _, _, service, _ = setup(tmp_path)
    order, _ = service.create_request(42, "m1")
    repo.cancel_order(order.id)
    assert repo.prepare_operation(order, "customer", NOW * 1000, None, NOW) is None
    order, _ = service.create_request(42, "m1")
    assert repo.prepare_operation(order, "customer", NOW * 1000, None, NOW)
    assert repo.cancel_order(order.id) is None
    with pytest.raises(BillingStateError):
        service.billing.mark_failed(order.id)


def test_parallel_creation_returns_one_active_order(tmp_path):
    repo, _, _, service, _ = setup(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: service.create_request(42, "m1"), range(4)))
    assert len({order.id for order, _ in results}) == 1
    assert sum(created for _, created in results) == 1


def test_cross_repository_operation_lock_is_exclusive(tmp_path):
    repo, _, _, _, _ = setup(tmp_path)
    with repo.operation_lock():
        with pytest.raises(OSError):
            with repo.operation_lock():
                pytest.fail("second writer acquired the lock")
    with repo.operation_lock():
        pass


def run(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    finally:
        coroutine.close()
    raise AssertionError("unexpected async I/O")


def test_notification_failure_retries_without_provisioning(tmp_path):
    repo, clients, _, service, _ = setup(tmp_path)
    order, _ = service.create_request(42, "m1")
    service.approve(order.id)
    context = NS(bot=NS(send_message=AsyncMock(side_effect=RuntimeError("telegram"))))
    referral = AsyncMock()
    run(deliver(context, repo, lambda: clients, referral, 1, now=lambda: NOW))
    assert repo.get_order(order.id).status is OrderStatus.COMPLETED
    assert repo.effect_summary()["failed"] == 1
    context.bot.send_message.side_effect = None
    run(deliver(context, repo, lambda: clients, referral, 1, now=lambda: NOW + 31))
    assert repo.effect_summary()["pending"] == 0
    assert len(clients.targets) == 1
    referral.assert_awaited_once()


def test_referral_failure_does_not_block_customer_receipt(tmp_path):
    repo, clients, _, service, _ = setup(tmp_path)
    order, _ = service.create_request(42, "m1")
    service.approve(order.id)
    context = NS(bot=NS(send_message=AsyncMock()))
    referral = AsyncMock(side_effect=RuntimeError("referral"))
    run(deliver(context, repo, lambda: clients, referral, 1, now=lambda: NOW))
    context.bot.send_message.assert_awaited_once()
    assert repo.effect_summary()["failed"] == 1
    referral.side_effect = None
    run(deliver(context, repo, lambda: clients, referral, 1, now=lambda: NOW + 31))
    context.bot.send_message.assert_awaited_once()
    assert repo.effect_summary()["pending"] == 0


def test_crashed_delivery_claim_expires(tmp_path):
    repo, _, _, service, _ = setup(tmp_path)
    order, _ = service.create_request(42, "m1")
    assert repo.claim_effect(order.id, "admin", NOW)
    assert not repo.claim_effect(order.id, "admin", NOW + 299)
    assert repo.claim_effect(order.id, "admin", NOW + 300)


def test_paid_adapter_uses_customer_service(tmp_path):
    repo, clients, billing, service, _ = setup(tmp_path)
    order, _ = service.create_request(42, "m1")
    billing.mark_paid(order.id, "synthetic", "payment-1")
    assert billing.apply_paid_order(order.id).status is OrderStatus.COMPLETED
    assert clients.targets == [NOW * 1000 + 30 * DAY]


def test_admin_recovery_is_private_and_admin_only(tmp_path, monkeypatch):
    from src import bot
    repo, _, _, service, _ = setup(tmp_path)
    order, _ = service.create_request(42, "m1")
    monkeypatch.setattr(bot, "ADMIN_TG_ID", 1)
    monkeypatch.setattr(bot, "BillingRepository", lambda path: repo)
    show = AsyncMock()
    monkeypatch.setattr(bot, "show_text", show)
    for user_id, chat_type in ((42, "private"), (1, "group")):
        update = NS(effective_user=NS(id=user_id), effective_chat=NS(type=chat_type))
        run(bot.payment_admin(update, NS(), order_id=order.id))
    show.assert_not_awaited()
    update = NS(effective_user=NS(id=1), effective_chat=NS(type="private"))
    run(bot.payment_admin(update, NS(), order_id=order.id))
    assert order.id in show.await_args.args[2]


def test_order_binding_never_steals_existing_owner(local_db, monkeypatch):
    from src import bot
    monkeypatch.setattr(bot, "db_connect", local_db)
    bot.bind_order_customer(42, "new_payment")
    bot.bind_order_customer(42, "new_payment")
    with pytest.raises(ReconciliationRequiredError):
        bot.bind_order_customer(99, "new_payment")
    with pytest.raises(ReconciliationRequiredError):
        bot.bind_order_customer(42, "different")
    with local_db() as db:
        assert db.execute("SELECT email FROM telegram_links WHERE tg_id=42").fetchone()[0] == "new_payment"


def test_paid_screen_has_no_cancel_or_change_buttons():
    from src.bot import pending_request_view
    from src.models import Order
    order = Order("KV-PAID", 42, "customer", "m1", 30, 199, OrderStatus.PAID, None, None, NOW, NOW, None)
    text, keyboard = pending_request_view(order, PLANS[0], back_callback="client_home")
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert not any("cancel" in value or "change" in value for value in callbacks)


def test_status_and_cancel_do_not_connect_to_xui(tmp_path, monkeypatch):
    from src import bot
    repo, _, _, service, _ = setup(tmp_path)
    order, _ = service.create_request(42, "m1")
    monkeypatch.setattr(bot, "BillingRepository", lambda path: repo)
    remote = Mock(side_effect=AssertionError("XUI must not be contacted"))
    monkeypatch.setattr(bot, "build_client_service", remote)
    orders = bot.build_customer_order_service()
    assert orders.get_request(42, order.id).id == order.id
    assert orders.reject(order.id)[0].status is OrderStatus.CANCELLED
    remote.assert_not_called()


def test_reserved_referral_days_survive_later_payment(tmp_path):
    from src.repositories import ReferralRepository
    repo, clients, _, service, _ = setup(tmp_path)
    referrals = ReferralRepository(repo.db_path, connect=repo._connect, now_provider=lambda: NOW)
    referrals.init_schema()
    code = referrals.get_or_create_profile(42)["referral_code"]
    referrals.attribute(code, 99)
    reward = referrals.prepare_reward(99, "FRIEND-ORDER", NOW * 1000)
    order, _ = service.create_request(42, "m1")
    service.approve(order.id)
    assert clients.bundle.primary.expiry_time_ms == reward["target_expiry_ms"] + 30 * DAY


def test_completion_and_outbox_are_one_transaction(tmp_path):
    repo, clients, _, service, _ = setup(tmp_path)
    order, _ = service.create_request(42, "m1")
    with repo._connect() as db:
        db.execute("CREATE TRIGGER fail_effect BEFORE INSERT ON payment_effects "
                   "WHEN NEW.kind='customer' BEGIN SELECT RAISE(ABORT, 'synthetic'); END")
    with pytest.raises(sqlite3.IntegrityError):
        service.approve(order.id)
    assert repo.get_order(order.id).status is OrderStatus.PAID
    target = clients.bundle.primary.expiry_time_ms
    with repo._connect() as db:
        db.execute("DROP TRIGGER fail_effect")
    service.approve(order.id)
    assert clients.bundle.primary.expiry_time_ms == target


def test_deleted_catalog_entry_still_has_snapshot_title(tmp_path):
    repo, _, billing, service, _ = setup(tmp_path)
    order, _ = service.create_request(42, "m1")
    billing.plans.clear()
    assert service.approve(order.id)[0].plan_title == PLANS[0].title
