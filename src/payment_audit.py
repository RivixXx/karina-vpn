"""Read-only pre-deployment audit of a copied or explicitly selected billing DB."""
import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path


def inspect_database(db):
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    issues, warnings = [], []
    if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
        issues.append("SQLite integrity check failed")
    report = {"issues": issues, "warnings": warnings, "counts": {}}
    if "orders" not in tables:
        warnings.append("Billing schema has not been initialized")
        return report
    report["counts"] = dict(db.execute("SELECT status, COUNT(*) FROM orders GROUP BY status"))
    duplicate_count = db.execute("""SELECT COUNT(*) FROM
        (SELECT tg_id FROM orders WHERE status IN ('pending','paid')
         GROUP BY tg_id HAVING COUNT(*)>1)""").fetchone()[0]
    if duplicate_count:
        issues.append(f"{duplicate_count} users have duplicate active orders; reconcile before migration")
    if db.execute("SELECT COUNT(*) FROM orders WHERE days<=0 OR amount<=0").fetchone()[0]:
        issues.append("Orders with invalid purchased terms exist")
    active = db.execute("SELECT order_id FROM orders WHERE status IN ('pending','paid')").fetchall()
    if "payment_operations" not in tables:
        if active:
            warnings.append(f"{len(active)} legacy active orders need XUI/payment review before approval")
    else:
        legacy = db.execute("""SELECT COUNT(*) FROM orders o WHERE o.status IN ('pending','paid')
            AND NOT EXISTS (SELECT 1 FROM payment_operations p WHERE p.order_id=o.order_id)""").fetchone()[0]
        if legacy:
            warnings.append(f"{legacy} active orders have no saved activation intent")
        report["incomplete_operations"] = db.execute("""SELECT COUNT(*) FROM payment_operations p
            JOIN orders o ON o.order_id=p.order_id WHERE o.status='paid'""").fetchone()[0]
        orphan = db.execute("""SELECT COUNT(*) FROM payment_operations p LEFT JOIN orders o
            ON o.order_id=p.order_id WHERE o.order_id IS NULL OR o.status NOT IN ('paid','completed')""").fetchone()[0]
        if orphan:
            issues.append("Activation intents with missing or incompatible orders exist")
    if "payment_effects" in tables:
        report["undelivered_effects"] = db.execute(
            "SELECT COUNT(*) FROM payment_effects WHERE completed_at IS NULL").fetchone()[0]
        report["failed_effects"] = db.execute(
            "SELECT COUNT(*) FROM payment_effects WHERE completed_at IS NULL AND last_error IS NOT NULL").fetchone()[0]
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        with closing(sqlite3.connect(args.db.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            report = inspect_database(db)
    except sqlite3.Error as exc:
        print(json.dumps({"issues": [type(exc).__name__], "warnings": []}))
        return 2
    print(json.dumps(report, indent=2))
    return 1 if report["issues"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
