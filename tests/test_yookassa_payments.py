import json
import io
import sqlite3
import threading
from unittest.mock import Mock

import pytest

from src.models import OrderStatus
from src.payments.yookassa import YooKassaClient, YooKassaError, YooKassaPaymentService
from src.payments.webhook import handler_factory
from src.repositories import BillingRepository
from src.services import BillingService


class FakeTransport:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def __call__(self, method, url, headers, body=None):
        self.calls.append((method, url, headers, body))
        return next(self.responses)


@pytest.fixture
def payment_setup(tmp_path):
    path = tmp_path / "billing.sqlite"
    repo = BillingRepository(connect=lambda: sqlite3.Connection(path))
    repo.init_schema()
    billing = BillingService(repo, object(), now_provider=lambda: 100, token_factory=lambda: "ORDER")
    order = billing.create_order(10, "tg_10", "m1", kind="new")
    return repo, billing, order


def test_create_payment_is_idempotent_and_persists_reference(payment_setup):
    repo, billing, order = payment_setup
    created = {
        "id": "pay_1", "status": "pending",
        "confirmation": {"type": "redirect", "confirmation_url": "https://yoomoney.ru/pay/1"},
    }
    transport = FakeTransport([created, {"id": "pay_1", "status": "pending"}])
    client = YooKassaClient("shop", "secret", transport=transport)
    service = YooKassaPaymentService(billing, client, "https://vpn.example.test/payment-return")
    first = service.create_payment(order.id)
    second = service.create_payment(order.id)
    assert first.confirmation_url == second.confirmation_url == "https://yoomoney.ru/pay/1"
    assert [call[0] for call in transport.calls] == ["POST", "GET"]
    assert repo.get_order(order.id).provider_payment_id == "pay_1"
    request = json.loads(transport.calls[0][3].decode())
    assert request["capture"] is True and request["metadata"]["order_id"] == order.id
    assert request["payment_method_data"] == {"type": "sbp"}
    assert transport.calls[0][2]["Idempotence-Key"] == "karina-KV-ORDER-1"


@pytest.mark.parametrize("active_status", ["pending", "waiting_for_capture"])
def test_every_active_payment_state_reuses_existing_link(payment_setup, active_status):
    repo, billing, order = payment_setup
    repo.save_payment_session(order.id, "yookassa", "pay_active", "https://yoomoney.ru/pay/active", 1)
    repo.set_payment_session_status("pay_active", active_status)
    transport = FakeTransport([{"id": "pay_active", "status": active_status}])
    service = YooKassaPaymentService(billing, YooKassaClient("shop", "secret", transport=transport),
                                     "https://vpn.example.test/payment-return")
    assert service.create_payment(order.id).payment_id == "pay_active"
    assert [call[0] for call in transport.calls] == ["GET"]


def test_only_explicitly_canceled_payment_is_replaced(payment_setup):
    repo, billing, order = payment_setup
    repo.save_payment_session(order.id, "yookassa", "pay_old", "https://yoomoney.ru/pay/old", 1)
    transport = FakeTransport([
        {"id": "pay_old", "status": "canceled"},
        {"id": "pay_new", "status": "pending",
         "confirmation": {"confirmation_url": "https://yoomoney.ru/pay/new"}},
    ])
    service = YooKassaPaymentService(billing, YooKassaClient("shop", "secret", transport=transport),
                                     "https://vpn.example.test/payment-return")
    assert service.create_payment(order.id).payment_id == "pay_new"
    assert transport.calls[1][2]["Idempotence-Key"] == "karina-KV-ORDER-2"


def test_switching_to_stars_cancels_active_yookassa_payment(payment_setup):
    repo, billing, order = payment_setup
    repo.save_payment_session(order.id, "yookassa", "pay_sbp", "https://yoomoney.ru/pay/sbp", 1)
    transport = FakeTransport([
        {"id": "pay_sbp", "status": "pending"},
        {"id": "pay_sbp", "status": "canceled"},
    ])
    service = YooKassaPaymentService(
        billing, YooKassaClient("shop", "secret", transport=transport),
        "https://vpn.example.test/payment-return",
    )
    assert service.cancel_payment(order.id) is True
    assert repo.get_payment_session(order.id)["status"] == "canceled"
    assert transport.calls[1][0] == "POST"
    assert transport.calls[1][1].endswith("/payments/pay_sbp/cancel")


def test_concurrent_creation_uses_one_provider_idempotence_key(payment_setup):
    repo, billing, order = payment_setup
    barrier = threading.Barrier(2)
    calls = []

    def transport(method, url, headers, body=None):
        calls.append((method, headers.get("Idempotence-Key")))
        barrier.wait(timeout=5)
        return {"id": "pay_shared", "status": "pending",
                "confirmation": {"confirmation_url": "https://yoomoney.ru/pay/shared"}}

    service = YooKassaPaymentService(billing, YooKassaClient("shop", "secret", transport=transport),
                                     "https://vpn.example.test/payment-return")
    results = []
    threads = [threading.Thread(target=lambda: results.append(service.create_payment(order.id)))
               for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    assert len(results) == 2
    assert calls == [("POST", "karina-KV-ORDER-1"), ("POST", "karina-KV-ORDER-1")]
    assert {result.payment_id for result in results} == {"pay_shared"}


def test_webhook_refetches_and_applies_verified_payment_once(payment_setup):
    repo, billing, order = payment_setup
    repo.save_payment_session(order.id, "yookassa", "pay_1", "https://yoomoney.ru/pay/1", 1)
    billing.apply_order = lambda order_id: (repo.complete_operation(order_id, 101), True)
    verified = {"id": "pay_1", "status": "succeeded", "paid": True,
                "amount": {"value": "199.00", "currency": "RUB"},
                "metadata": {"order_id": order.id}}
    transport = FakeTransport([verified, verified])
    service = YooKassaPaymentService(billing, YooKassaClient("shop", "secret", transport=transport),
                                     "https://vpn.example.test/payment-return")
    assert service.handle_webhook({"object": {"id": "pay_1"}}) is True
    assert service.handle_webhook({"object": {"id": "pay_1"}}) is False
    assert repo.get_order(order.id).status is OrderStatus.COMPLETED
    assert repo.get_payment_session(order.id)["status"] == "succeeded"
    assert [call[0] for call in transport.calls] == ["GET", "GET"]
    warning = repo.get_receipt_warning(order.id)
    assert warning["payment_id"] == "pay_1" and warning["resolved_at"] is None


def test_concurrent_verified_webhooks_complete_effect_once(payment_setup):
    repo, billing, order = payment_setup
    repo.save_payment_session(order.id, "yookassa", "pay_race", "https://yoomoney.ru/pay/race", 1)
    apply_lock = threading.Lock()
    applied = []

    def apply_once(order_id):
        with apply_lock:
            if repo.get_order(order_id).status is OrderStatus.COMPLETED:
                return repo.get_order(order_id), False
            applied.append(order_id)
            return repo.complete_operation(order_id, 101), True

    billing.apply_order = apply_once
    payment = {"id": "pay_race", "status": "succeeded", "paid": True,
               "amount": {"value": "199.00", "currency": "RUB"},
               "metadata": {"order_id": order.id}}
    service = YooKassaPaymentService(
        billing, YooKassaClient("shop", "secret", transport=lambda *args: payment),
        "https://vpn.example.test/payment-return")
    results = []
    threads = [threading.Thread(target=lambda: results.append(
        service.handle_webhook({"object": {"id": "pay_race"}}))) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    assert sorted(results) == [False, True]
    assert applied == [order.id]
    assert repo.get_receipt_warning(order.id) is not None


@pytest.mark.parametrize("change", [
    {"status": "canceled"}, {"paid": False},
    {"amount": {"value": "200.00", "currency": "RUB"}},
    {"amount": {"value": "199.00", "currency": "USD"}},
    {"metadata": {"order_id": "KV-OTHER"}},
])
def test_webhook_rejects_unverified_or_mismatched_payment(payment_setup, change):
    repo, billing, order = payment_setup
    repo.save_payment_session(order.id, "yookassa", "pay_1", "https://yoomoney.ru/pay/1", 1)
    payment = {"id": "pay_1", "status": "succeeded", "paid": True,
               "amount": {"value": "199.00", "currency": "RUB"},
               "metadata": {"order_id": order.id}}
    payment.update(change)
    service = YooKassaPaymentService(
        billing, YooKassaClient("shop", "secret", transport=FakeTransport([payment])),
        "https://vpn.example.test/payment-return",
    )
    with pytest.raises(YooKassaError):
        service.handle_webhook({"object": {"id": "pay_1"}})
    assert repo.get_order(order.id).status is OrderStatus.PENDING


def _request(handler, method, path, body=b""):
    instance = handler.__new__(handler)
    instance.path = path
    instance.headers = {"Content-Length": str(len(body))}
    instance.rfile = io.BytesIO(body)
    instance._reply = Mock()
    getattr(instance, method)()
    return instance._reply


def test_webhook_http_rejects_invalid_and_oversized_bodies_and_has_local_health():
    service = Mock()
    handler = handler_factory(service)
    assert _request(handler, "do_POST", "/webhooks/yookassa", b"not-json").call_args.args[0] == 400
    oversized = b"x" * 65537
    assert _request(handler, "do_POST", "/webhooks/yookassa", oversized).call_args.args[0] == 400
    assert _request(handler, "do_GET", "/healthz").call_args.args == (200, {"status": "ok"})
    service.handle_webhook.assert_not_called()


def test_nginx_exposes_only_webhook_and_uses_explicit_server_include():
    from pathlib import Path
    snippet = Path("deploy/nginx/snippets/karina-yookassa-webhook.conf").read_text(encoding="utf-8")
    include = Path("deploy/nginx/karina-yookassa-server-include.conf").read_text(encoding="utf-8")
    assert "location = /webhooks/yookassa" in snippet
    assert "health" not in snippet.lower()
    assert "include /etc/nginx/snippets/karina-yookassa-webhook.conf;" in include
