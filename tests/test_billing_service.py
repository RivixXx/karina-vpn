from unittest.mock import Mock

import pytest

from src.models import OrderStatus, PLANS
from src.repositories import BillingRepository
from src.services import (
    BillingAccessError, BillingService, BillingStateError, UnknownPlanError,
)


@pytest.fixture
def billing(tmp_path):
    import sqlite3
    path = tmp_path / "service.sqlite"
    repo = BillingRepository(connect=lambda: sqlite3.Connection(path))
    repo.init_schema()
    client = Mock()
    return BillingService(repo, client, now_provider=lambda: 100,
                          token_factory=iter(("ONE", "TWO", "THREE")).__next__), client


def test_canonical_plans():
    assert [plan.id for plan in PLANS] == ["m1", "m3", "m6", "y1"]
    assert len({plan.id for plan in PLANS}) == 4
    assert all(plan.days > 0 and plan.price_rub > 0 for plan in PLANS)


def test_create_and_unknown_plan(billing):
    service, _ = billing
    created = service.create_order(10, "synthetic_user", "m3")
    assert (created.id, created.days, created.amount_rub, created.status) == (
        "KV-ONE", 90, 499, OrderStatus.PENDING,
    )
    with pytest.raises(UnknownPlanError):
        service.create_order(10, "synthetic_user", "missing")


def test_cancel_requires_owner_and_pending(billing):
    service, _ = billing
    order = service.create_order(10, "synthetic_user", "m1")
    with pytest.raises(BillingAccessError):
        service.cancel_order(order.id, 11)
    assert service.cancel_order(order.id, 10).status is OrderStatus.CANCELLED
    with pytest.raises(BillingStateError):
        service.cancel_order(order.id, 10)


def test_valid_and_invalid_state_transitions(billing):
    service, _ = billing
    order = service.create_order(10, "synthetic_user", "m1")
    paid = service.mark_paid(order.id, "provider", "payment")
    assert paid.status is OrderStatus.PAID and paid.paid_at == 100
    with pytest.raises(BillingStateError):
        service.mark_paid(order.id, "provider", "again")
    with pytest.raises(BillingStateError):
        service.cancel_order(order.id, 10)


def test_apply_is_idempotent_and_extends_exact_days(billing):
    service, client = billing
    order = service.create_order(10, "synthetic_user", "m6")
    service.mark_paid(order.id, "provider", "payment")
    completed = service.apply_paid_order(order.id)
    assert completed.status is OrderStatus.COMPLETED
    client.extend_client.assert_called_once_with("synthetic_user", 180)
    assert service.apply_paid_order(order.id) == completed
    client.extend_client.assert_called_once()


def test_extend_failure_leaves_order_paid(billing):
    service, client = billing
    order = service.create_order(10, "synthetic_user", "y1")
    service.mark_paid(order.id, "provider", "payment")
    client.extend_client.side_effect = RuntimeError("synthetic")
    with pytest.raises(RuntimeError):
        service.apply_paid_order(order.id)
    assert service.get_order(order.id).status is OrderStatus.PAID


def test_pending_and_paid_can_fail(billing):
    service, _ = billing
    first = service.create_order(10, "synthetic_user", "m1")
    assert service.mark_failed(first.id).status is OrderStatus.FAILED
    second = service.create_order(10, "synthetic_user", "m1")
    service.mark_paid(second.id, "provider", "second-payment")
    assert service.mark_failed(second.id).status is OrderStatus.FAILED
