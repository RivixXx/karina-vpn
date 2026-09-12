import sqlite3

import pytest

from src.models import Order, OrderStatus
from src.repositories import BillingRepository

SQLITE_CONNECT = sqlite3.connect


def repository(tmp_path):
    path = tmp_path / "billing.sqlite"
    repo = BillingRepository(connect=lambda: sqlite3.Connection(path))
    repo.init_schema()
    return repo


def order(order_id="KV-ONE", tg_id=10):
    return Order(order_id, tg_id, "synthetic_user", "m1", 30, 199,
                 OrderStatus.PENDING, None, None, 100, None, None)


def test_create_get_and_list_orders(tmp_path):
    repo = repository(tmp_path)
    created = repo.create_order(order(), "Synthetic")
    repo.cancel_order(created.id)
    repo.create_order(order("KV-TWO"), "Synthetic")
    repo.create_order(order("KV-OTHER", 20), "Synthetic")
    assert repo.get_order("KV-ONE").status is OrderStatus.CANCELLED
    assert [item.id for item in repo.list_user_orders(10)] == ["KV-TWO", "KV-ONE"]


def test_provider_reference_and_transitions(tmp_path):
    repo = repository(tmp_path)
    repo.create_order(order(), "Synthetic")
    updated = repo.update_provider_reference("KV-ONE", "provider", "payment-one")
    assert updated.provider == "provider" and updated.provider_payment_id == "payment-one"
    assert repo.mark_paid("KV-ONE", 200).status is OrderStatus.PAID
    completed = repo.mark_completed("KV-ONE", 300)
    assert completed.status is OrderStatus.COMPLETED and completed.applied_at == 300


def test_duplicate_ids_are_rejected(tmp_path):
    repo = repository(tmp_path)
    repo.create_order(order(), "Synthetic")
    with pytest.raises(sqlite3.IntegrityError):
        repo.create_order(order(), "Synthetic")


def test_duplicate_provider_payment_id_is_rejected(tmp_path):
    repo = repository(tmp_path)
    repo.create_order(order(), "Synthetic")
    repo.create_order(order("KV-TWO", 20), "Synthetic")
    repo.update_provider_reference("KV-ONE", "provider", "same-payment")
    with pytest.raises(sqlite3.IntegrityError):
        repo.update_provider_reference("KV-TWO", "provider", "same-payment")


def test_repository_closes_connections(tmp_path):
    opened = []

    class TrackingConnection(sqlite3.Connection):
        closed = False

        def close(self):
            self.closed = True
            super().close()

    def connect():
        db = SQLITE_CONNECT(tmp_path / "connections.sqlite", factory=TrackingConnection)
        opened.append(db)
        return db

    repo = BillingRepository(connect=connect)
    repo.init_schema()
    repo.create_order(order(), "Synthetic")
    repo.get_order("KV-ONE")
    repo.list_user_orders(10)
    assert opened and all(db.closed for db in opened)
