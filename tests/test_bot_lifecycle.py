"""Lifecycle tests use only synthetic data in a tmp_path SQLite database."""
from contextlib import closing
import sqlite3
from types import SimpleNamespace

import pytest


TABLES = ("telegram_links", "bind_tokens", "notifications_sent", "orders")


def snapshot(connect, email):
    with closing(connect()) as db:
        return {table: db.execute(f"SELECT * FROM {table} WHERE email = ?", (email,)).fetchall()
                for table in TABLES}


@pytest.fixture
def cleanup(source_functions, local_db):
    return source_functions(
        "bot.py", {"delete_client_local_state"}, db_connect=local_db, closing=closing,
    )["delete_client_local_state"]


def test_cleanup_removes_access_and_preserves_history(cleanup, local_db):
    before = snapshot(local_db, "demo_target")
    assert len(before["telegram_links"]) == 1 and len(before["bind_tokens"]) == 2
    cleanup("demo_target")
    after = snapshot(local_db, "demo_target")
    assert after["telegram_links"] == []
    assert after["bind_tokens"] == []
    assert after["notifications_sent"] == before["notifications_sent"]
    assert after["orders"] == before["orders"]


def test_other_user_is_untouched(cleanup, local_db):
    before = snapshot(local_db, "demo_other")
    cleanup("demo_target")
    assert snapshot(local_db, "demo_other") == before


def test_cleanup_is_idempotent(cleanup, local_db):
    cleanup("demo_target")
    after_first = snapshot(local_db, "demo_target")
    cleanup("demo_target")
    assert snapshot(local_db, "demo_target") == after_first


def test_cleanup_rolls_back_both_deletes_on_failure(cleanup, local_db):
    before = snapshot(local_db, "demo_target")
    with closing(local_db()) as db, db:
        db.execute("CREATE TRIGGER fail_token_delete BEFORE DELETE ON bind_tokens "
                   "BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="synthetic failure"):
        cleanup("demo_target")
    assert snapshot(local_db, "demo_target") == before


def test_cleanup_parameterizes_email(cleanup, local_db):
    before = snapshot(local_db, "demo_target")
    cleanup("' OR 1=1 --")
    assert snapshot(local_db, "demo_target") == before


def test_new_bind_token_still_replaces_old_tokens(source_functions, local_db):
    other_before = snapshot(local_db, "demo_other")
    create_token = source_functions(
        "bot.py", {"create_bind_token"}, db_connect=local_db,
        time=SimpleNamespace(time=lambda: 1000),
        secrets=SimpleNamespace(token_urlsafe=lambda size: "synthetic_replacement"),
    )["create_bind_token"]
    assert create_token("demo_target") == "synthetic_replacement"
    assert snapshot(local_db, "demo_target")["bind_tokens"] == [
        ("synthetic_replacement", "demo_target", 1000, 87400, 0),
    ]
    assert snapshot(local_db, "demo_other") == other_before
