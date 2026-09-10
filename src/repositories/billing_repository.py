import sqlite3
from contextlib import closing
from pathlib import Path

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
                "SELECT * FROM orders WHERE tg_id = ? AND status = 'pending' "
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

    def mark_paid(self, order_id, paid_at):
        return self._transition(order_id, OrderStatus.PENDING, OrderStatus.PAID,
                                "paid_at = ?", (paid_at,))

    def mark_completed(self, order_id, applied_at):
        return self._transition(order_id, OrderStatus.PAID, OrderStatus.COMPLETED,
                                "applied_at = ?", (applied_at,))

    def mark_failed(self, order_id, expected_status):
        return self._transition(order_id, expected_status, OrderStatus.FAILED)

    def cancel_order(self, order_id):
        return self._transition(order_id, OrderStatus.PENDING, OrderStatus.CANCELLED)

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
