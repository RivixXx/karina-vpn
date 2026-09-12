from contextlib import closing
import sqlite3

import pytest
from src.sqlite_backup import snapshot


def connect(path, **kwargs):
    return sqlite3.Connection(path, **kwargs)


def test_snapshot_includes_committed_wal_and_restores(tmp_path):
    source, destination = tmp_path / "live.db", tmp_path / "backup.db"
    with closing(connect(source)) as original:
        original.execute("PRAGMA journal_mode=WAL")
        original.execute("CREATE TABLE orders(id INTEGER PRIMARY KEY, status TEXT)")
        original.execute("INSERT INTO orders VALUES (1, 'paid')")
        original.commit()
        snapshot(source, destination, connect=connect)
        original.execute("UPDATE orders SET status='completed'")
        original.commit()
        with closing(connect(destination)) as restored:
            assert restored.execute("SELECT status FROM orders").fetchone()[0] == "paid"
            assert restored.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_snapshot_does_not_overwrite_existing_backup(tmp_path):
    destination = tmp_path / "saved.db"
    destination.write_bytes(b"previous snapshot")
    with pytest.raises(FileExistsError):
        snapshot(tmp_path / "missing.db", destination, connect=connect)
    assert destination.read_bytes() == b"previous snapshot"


def test_missing_source_leaves_no_partial_backup(tmp_path):
    with pytest.raises(sqlite3.OperationalError):
        snapshot(tmp_path / "missing.db", tmp_path / "backup.db", connect=connect)
    assert list(tmp_path.iterdir()) == []
