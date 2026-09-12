import sqlite3
from contextlib import closing
from src.payment_audit import inspect_database
from src.repositories import BillingRepository


def test_audit_detects_legacy_duplicates_without_modifying(tmp_path):
    path = tmp_path / "legacy.sqlite"
    with closing(sqlite3.Connection(path)) as db, db:
        db.execute("CREATE TABLE orders(order_id TEXT, tg_id INTEGER, status TEXT, days INTEGER, amount INTEGER)")
        db.executemany("INSERT INTO orders VALUES (?, 1, 'pending', 30, 199)", [("one",), ("two",)])
        before = list(db.iterdump())
        report = inspect_database(db)
        assert report["issues"] and report["warnings"]
        assert list(db.iterdump()) == before


def test_fresh_schema_audit_is_clean(tmp_path):
    path = tmp_path / "fresh.sqlite"
    repo = BillingRepository(path, connect=lambda: sqlite3.Connection(path))
    repo.init_schema()
    with closing(sqlite3.Connection(path)) as db:
        assert inspect_database(db)["issues"] == []
