import sqlite3
from contextlib import closing
from pathlib import Path
from .operation_lock import operation_lock

try:
    from ..models import Order, OrderStatus
except ImportError:
    from models import Order, OrderStatus


DEFAULT_DB_FILE = Path("/opt/karina-bot/karina.db")


class BillingRepository:
    def __init__(self, db_path=DEFAULT_DB_FILE, *, connect=None):
        self.db_path = db_path
        self._connect_factory = connect

    def _connect(self):
        db = self._connect_factory() if self._connect_factory else sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        return db

    def init_schema(self):
        with closing(self._connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id TEXT NOT NULL UNIQUE,
                    tg_id INTEGER NOT NULL,
                    email TEXT NOT NULL,
                    plan_id TEXT NOT NULL,
                    plan_title TEXT NOT NULL,
                    days INTEGER NOT NULL,
                    amount INTEGER NOT NULL,
                    currency TEXT NOT NULL DEFAULT 'RUB',
                    status TEXT NOT NULL DEFAULT 'pending',
                    provider TEXT,
                    provider_payment_id TEXT,
                    created_at INTEGER NOT NULL,
                    paid_at INTEGER,
                    applied_at INTEGER
                    , order_kind TEXT NOT NULL DEFAULT 'renewal'
                );
                CREATE INDEX IF NOT EXISTS idx_orders_tg ON orders(tg_id);
                CREATE INDEX IF NOT EXISTS idx_orders_email ON orders(email);
                CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_provider_payment_unique
                    ON orders(provider_payment_id) WHERE provider_payment_id IS NOT NULL;
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(orders)")}
            if "order_kind" not in columns:
                db.execute("ALTER TABLE orders ADD COLUMN order_kind TEXT NOT NULL DEFAULT 'renewal'")
            db.executescript("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_one_active_user
                    ON orders(tg_id) WHERE status IN ('pending', 'paid');
                CREATE TABLE IF NOT EXISTS payment_operations (
                    order_id TEXT PRIMARY KEY,
                    email TEXT NOT NULL,
                    target_expiry_ms INTEGER NOT NULL CHECK(target_expiry_ms > 0),
                    original_expiry_ms INTEGER,
                    created_at INTEGER NOT NULL,
                    last_error TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS payment_effects (
                    order_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at INTEGER NOT NULL DEFAULT 0,
                    completed_at INTEGER,
                    last_error TEXT,
                    PRIMARY KEY(order_id, kind)
                );
            """)

    def operation_lock(self):
        return operation_lock(self._connect)

    def reserved_referral_expiry(self, tg_id):
        with closing(self._connect()) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name=\'referral_rewards\'").fetchone():
                return 0
            return db.execute("SELECT COALESCE(MAX(target_expiry_ms), 0) FROM referral_rewards "
                              "WHERE recipient_tg_id=? AND applied_at IS NULL", (tg_id,)).fetchone()[0]

    def get_operation(self, order_id):
        with closing(self._connect()) as db:
            return db.execute("SELECT * FROM payment_operations WHERE order_id=?", (order_id,)).fetchone()

    def prepare_operation(self, order, email, target, original, now):
        """Claim payment before XUI writes; cancellation can no longer win."""
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM orders WHERE order_id=?", (order.id,)).fetchone()
            if row is None or row[0] not in ('pending', 'paid'):
                return None
            db.execute("""INSERT OR IGNORE INTO payment_operations
                (order_id, email, target_expiry_ms, original_expiry_ms, created_at)
                VALUES (?, ?, ?, ?, ?)""", (order.id, email, target, original, now))
            db.execute("UPDATE orders SET status='paid', paid_at=COALESCE(paid_at, ?) WHERE order_id=?",
                       (now, order.id))
        return self.get_operation(order.id)

    def record_operation_error(self, order_id, error):
        with closing(self._connect()) as db, db:
            db.execute("UPDATE payment_operations SET attempts=attempts+1, last_error=? WHERE order_id=?",
                       (type(error).__name__, order_id))

    def complete_operation(self, order_id, now):
        with closing(self._connect()) as db, db:
            cursor = db.execute("UPDATE orders SET status='completed', applied_at=? WHERE order_id=? AND status='paid'",
                                (now, order_id))
            if cursor.rowcount != 1:
                return None
            db.execute("UPDATE payment_operations SET last_error=NULL WHERE order_id=?", (order_id,))
            for kind in ('customer', 'referral'):
                db.execute("INSERT OR IGNORE INTO payment_effects(order_id, kind) VALUES (?, ?)", (order_id, kind))
        return self.get_order(order_id)

    def due_effects(self, now, limit=20):
        with closing(self._connect()) as db:
            return db.execute("""SELECT * FROM payment_effects WHERE completed_at IS NULL
                AND next_attempt_at<=? ORDER BY next_attempt_at, order_id, kind LIMIT ?""", (now, limit)).fetchall()

    def claim_effect(self, order_id, kind, now):
        with closing(self._connect()) as db, db:
            cursor = db.execute("""UPDATE payment_effects SET next_attempt_at=?
                WHERE order_id=? AND kind=? AND completed_at IS NULL AND next_attempt_at<=?""",
                (now + 300, order_id, kind, now))
            return cursor.rowcount == 1

    def effect_summary(self):
        with closing(self._connect()) as db:
            return db.execute("""SELECT COUNT(*) pending,
                COALESCE(SUM(last_error IS NOT NULL), 0) failed
                FROM payment_effects WHERE completed_at IS NULL""").fetchone()

    def finish_effect(self, order_id, kind, now):
        with closing(self._connect()) as db, db:
            db.execute("UPDATE payment_effects SET completed_at=?, last_error=NULL WHERE order_id=? AND kind=?",
                       (now, order_id, kind))

    def retry_effect(self, order_id, kind, now, error):
        with closing(self._connect()) as db, db:
            db.execute("""UPDATE payment_effects SET attempts=attempts+1, last_error=?,
                next_attempt_at=? + MIN(3600, 30 * (1 << MIN(attempts, 7)))
                WHERE order_id=? AND kind=?""", (type(error).__name__, now, order_id, kind))

    def recovery_orders(self, limit=10, offset=0):
        with closing(self._connect()) as db:
            rows = db.execute("""SELECT * FROM orders WHERE status IN ('pending','paid')
                ORDER BY CASE status WHEN 'paid' THEN 0 ELSE 1 END, id LIMIT ? OFFSET ?""", (limit, offset)).fetchall()
        return [self._order(row) for row in rows]

    @staticmethod
    def _order(row):
        if row is None:
            return None
        return Order(
            id=row["order_id"], tg_id=row["tg_id"], email=row["email"],
            plan_id=row["plan_id"], days=row["days"], amount_rub=row["amount"],
            status=OrderStatus(row["status"]), provider=row["provider"],
            provider_payment_id=row["provider_payment_id"], created_at=row["created_at"],
            paid_at=row["paid_at"], applied_at=row["applied_at"],
            kind=row["order_kind"] if "order_kind" in row.keys() else "renewal",
            plan_title=row["plan_title"],
        )

    def create_order(self, order, plan_title):
        with closing(self._connect()) as db, db:
            db.execute("""
                INSERT INTO orders
                    (order_id, tg_id, email, plan_id, plan_title, days, amount,
                     currency, status, provider, provider_payment_id, created_at,
                     paid_at, applied_at, order_kind)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'RUB', ?, ?, ?, ?, ?, ?, ?)
            """, (order.id, order.tg_id, order.email, order.plan_id, plan_title,
                  order.days, order.amount_rub, order.status.value, order.provider,
                  order.provider_payment_id, order.created_at, order.paid_at,
                  order.applied_at, order.kind))
            db.execute("INSERT INTO payment_effects(order_id, kind) VALUES (?, 'admin')", (order.id,))
        return self.get_order(order.id)

    def get_order(self, order_id):
        with closing(self._connect()) as db:
            row = db.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
        return self._order(row)

    def list_user_orders(self, tg_id, limit=10):
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT * FROM orders WHERE tg_id = ? ORDER BY id DESC LIMIT ?",
                (tg_id, limit),
            ).fetchall()
        return [self._order(row) for row in rows]

    def get_pending_for_user(self, tg_id):
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT * FROM orders WHERE tg_id = ? AND status IN ('pending', 'paid') "
                "ORDER BY id DESC LIMIT 1", (tg_id,),
            ).fetchone()
        return self._order(row)

    def approve_pending(self, order_id, applied_at):
        """Finalize a manually confirmed order after provisioning succeeds."""
        return self._transition(
            order_id, OrderStatus.PENDING, OrderStatus.COMPLETED,
            "paid_at = ?, applied_at = ?", (applied_at, applied_at),
        )

    def update_provider_reference(self, order_id, provider, provider_payment_id):
        return self._update(order_id, "provider = ?, provider_payment_id = ?",
                            (provider, provider_payment_id))

    def mark_paid(self, order_id, paid_at, provider=None, payment_id=None):
        if provider is None:
            return self._transition(order_id, OrderStatus.PENDING, OrderStatus.PAID,
                                    "paid_at = ?", (paid_at,))
        return self._transition(order_id, OrderStatus.PENDING, OrderStatus.PAID,
            "paid_at = ?, provider = ?, provider_payment_id = ?", (paid_at, provider, payment_id))

    def mark_completed(self, order_id, applied_at):
        return self._transition(order_id, OrderStatus.PAID, OrderStatus.COMPLETED,
                                "applied_at = ?", (applied_at,))

    def mark_failed(self, order_id, expected_status):
        with closing(self._connect()) as db, db:
            cursor = db.execute("""UPDATE orders SET status='failed' WHERE order_id=? AND status=?
                AND NOT EXISTS (SELECT 1 FROM payment_operations WHERE order_id=?)""",
                (order_id, expected_status.value, order_id))
        return self.get_order(order_id) if cursor.rowcount else None

    def cancel_order(self, order_id):
        with closing(self._connect()) as db, db:
            cursor = db.execute("UPDATE orders SET status='cancelled' WHERE order_id=? AND status='pending'", (order_id,))
            if cursor.rowcount:
                db.execute("INSERT OR IGNORE INTO payment_effects(order_id, kind) VALUES (?, 'cancelled')", (order_id,))
        return self.get_order(order_id) if cursor.rowcount else None

    def _update(self, order_id, assignments, values):
        with closing(self._connect()) as db, db:
            cursor = db.execute(f"UPDATE orders SET {assignments} WHERE order_id = ?",
                                (*values, order_id))
        return self.get_order(order_id) if cursor.rowcount else None

    def _transition(self, order_id, expected, target, extra=None, values=()):
        assignments = "status = ?" + (f", {extra}" if extra else "")
        with closing(self._connect()) as db, db:
            cursor = db.execute(
                f"UPDATE orders SET {assignments} WHERE order_id = ? AND status = ?",
                (target.value, *values, order_id, expected.value),
            )
        return self.get_order(order_id) if cursor.rowcount else None
