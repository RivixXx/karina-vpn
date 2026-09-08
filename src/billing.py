import secrets
import sqlite3
import time
from pathlib import Path

DB_FILE = Path("/opt/karina-bot/karina.db")


PLANS = {
    "m1": {
        "title": "💗 1 месяц",
        "days": 30,
        "price": 199,
    },
    "m3": {
        "title": "⭐ 3 месяца",
        "days": 90,
        "price": 499,
    },
    "m6": {
        "title": "🔥 6 месяцев",
        "days": 180,
        "price": 899,
    },
    "y1": {
        "title": "👑 12 месяцев",
        "days": 365,
        "price": 1499,
    },
}


def connect():
    db = sqlite3.connect(DB_FILE)
    db.row_factory = sqlite3.Row
    return db


def init_billing():
    with connect() as db:
        db.executescript(
            """
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
            );

            CREATE INDEX IF NOT EXISTS idx_orders_tg
                ON orders(tg_id);

            CREATE INDEX IF NOT EXISTS idx_orders_email
                ON orders(email);

            CREATE INDEX IF NOT EXISTS idx_orders_status
                ON orders(status);

            CREATE INDEX IF NOT EXISTS idx_orders_provider_payment
                ON orders(provider_payment_id);
            """
        )


def get_plan(plan_id):
    return PLANS.get(plan_id)


def create_order(tg_id, email, plan_id):
    plan = get_plan(plan_id)

    if not plan:
        raise ValueError("Неизвестный тариф")

    order_id = (
        "KV-"
        + str(int(time.time()))
        + "-"
        + secrets.token_hex(3).upper()
    )

    now = int(time.time())

    with connect() as db:
        db.execute(
            """
            INSERT INTO orders (
                order_id,
                tg_id,
                email,
                plan_id,
                plan_title,
                days,
                amount,
                currency,
                status,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'RUB', 'pending', ?)
            """,
            (
                order_id,
                tg_id,
                email,
                plan_id,
                plan["title"],
                plan["days"],
                plan["price"],
                now,
            ),
        )

    return get_order(order_id)


def get_order(order_id):
    with connect() as db:
        return db.execute(
            """
            SELECT *
            FROM orders
            WHERE order_id = ?
            """,
            (order_id,),
        ).fetchone()


def get_user_orders(tg_id, limit=10):
    with connect() as db:
        return db.execute(
            """
            SELECT *
            FROM orders
            WHERE tg_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (
                tg_id,
                limit,
            ),
        ).fetchall()


def set_provider_payment(
    order_id,
    provider,
    provider_payment_id,
):
    with connect() as db:
        db.execute(
            """
            UPDATE orders
            SET
                provider = ?,
                provider_payment_id = ?
            WHERE order_id = ?
            """,
            (
                provider,
                provider_payment_id,
                order_id,
            ),
        )


def mark_paid(order_id):
    now = int(time.time())

    with connect() as db:
        db.execute(
            """
            UPDATE orders
            SET
                status = 'paid',
                paid_at = ?
            WHERE order_id = ?
              AND status = 'pending'
            """,
            (
                now,
                order_id,
            ),
        )


def mark_applied(order_id):
    now = int(time.time())

    with connect() as db:
        db.execute(
            """
            UPDATE orders
            SET
                status = 'completed',
                applied_at = ?
            WHERE order_id = ?
            """,
            (
                now,
                order_id,
            ),
        )


def cancel_order(order_id):
    with connect() as db:
        db.execute(
            """
            UPDATE orders
            SET status = 'cancelled'
            WHERE order_id = ?
              AND status = 'pending'
            """,
            (order_id,),
        )
