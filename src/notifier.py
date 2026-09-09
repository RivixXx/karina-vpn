import asyncio
import logging
import sqlite3
import time
from pathlib import Path

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

try:
    from .app_config import ConfigError
    from .application import build_client_service
    from .integrations.xui import XUIError
    from .services import ClientServiceError
except ImportError:  # Direct execution from the src directory.
    from app_config import ConfigError
    from application import build_client_service
    from integrations.xui import XUIError
    from services import ClientServiceError


ENV_FILE = Path("/opt/karina-bot/.env")
DB_FILE = Path("/opt/karina-bot/karina.db")
LOGGER = logging.getLogger(__name__)
SERVICE_ERRORS = (ConfigError, XUIError, ClientServiceError)


def load_env(path):
    data = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip().strip('"').strip("'")
    return data


def db_connect(path=DB_FILE):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    return db


def init_db(connect=None):
    connect = connect or db_connect
    db = connect()
    try:
        with db:
            db.execute(
            """
            CREATE TABLE IF NOT EXISTS notifications_sent (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL,
                expiry_key TEXT NOT NULL,
                notification_day INTEGER NOT NULL,
                sent_at INTEGER NOT NULL,
                UNIQUE(email, expiry_key, notification_day)
            )
            """
            )
    finally:
        db.close()


def get_tg_id(email, connect=None):
    connect = connect or db_connect
    db = connect()
    try:
        row = db.execute(
            "SELECT tg_id FROM telegram_links WHERE email = ?", (email,),
        ).fetchone()
    finally:
        db.close()
    return row[0] if row else None


def already_sent(email, expiry_time_ms, legacy_expiry_key, notification_day, connect=None):
    connect = connect or db_connect
    stable_key = str(expiry_time_ms)
    db = connect()
    try:
        row = db.execute(
            """
            SELECT id FROM notifications_sent
            WHERE email = ? AND expiry_key IN (?, ?) AND notification_day = ?
            """,
            (email, stable_key, legacy_expiry_key, notification_day),
        ).fetchone()
    finally:
        db.close()
    return row is not None


def mark_sent(email, expiry_time_ms, notification_day, connect=None, now_provider=None):
    connect = connect or db_connect
    now_provider = now_provider or (lambda: int(time.time()))
    db = connect()
    try:
        with db:
            db.execute(
            """
            INSERT OR IGNORE INTO notifications_sent
                (email, expiry_key, notification_day, sent_at)
            VALUES (?, ?, ?, ?)
            """,
            (email, str(expiry_time_ms), notification_day, now_provider()),
            )
    finally:
        db.close()


def notification_stage(days):
    if days <= 0.5:
        return 0
    if days <= 1.5:
        return 1
    if days <= 3.5:
        return 3
    if days <= 7.5:
        return 7
    return None


def notification_text(stage):
    if stage == 7:
        return (
            "⏰ Осталась неделя\n\nДо окончания подписки Карина VPN осталось "
            "около 7 дней.\n\nМожно продлить её заранее — оставшиеся дни не потеряются 💗"
        )
    if stage == 3:
        return (
            "⏰ Осталось 3 дня\n\nПодписка Карина VPN скоро закончится.\n\n"
            "Продли доступ заранее, чтобы VPN продолжил работать без перерыва."
        )
    if stage == 1:
        return (
            "⚠️ Остался 1 день\n\nЗавтра заканчивается подписка Карина VPN.\n\n"
            "Продли её сейчас, чтобы соединение не отключилось."
        )
    return (
        "🚨 Подписка заканчивается сегодня\n\nСрок действия Карина VPN подходит "
        "к концу.\n\nДля продолжения работы продли подписку."
    )


def notification_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Продлить VPN", callback_data="client_pay")],
        [InlineKeyboardButton("💗 Мой профиль", callback_data="client_home")],
    ])


async def send_notification(bot, tg_id, stage):
    await bot.send_message(
        chat_id=tg_id,
        text=notification_text(stage),
        reply_markup=notification_keyboard(),
    )


async def process_expiring_client(client, sender, connect=None, now_provider=None):
    if not client.enabled or client.expiry_time_ms <= 0 or client.days_remaining < 0:
        return "ineligible"
    stage = notification_stage(client.days_remaining)
    if stage is None:
        return "outside_window"
    tg_id = get_tg_id(client.email, connect)
    if not tg_id:
        return "not_linked"
    if already_sent(
        client.email, client.expiry_time_ms, client.expiry_text, stage, connect,
    ):
        return "already_sent"
    await sender(tg_id, stage)
    mark_sent(client.email, client.expiry_time_ms, stage, connect, now_provider)
    return "sent"


async def run_notification_pass(service, sender, connect=None, now_provider=None, logger=None):
    logger = logger or LOGGER
    clients = service.get_expiring(8)
    results = []
    for client in clients:
        try:
            result = await process_expiring_client(
                client, sender, connect=connect, now_provider=now_provider,
            )
            results.append((client.email, result))
        except sqlite3.Error as exc:
            logger.warning(
                "Notification database operation failed (%s)",
                type(exc).__name__,
                exc_info=True,
            )
            results.append((client.email, "error"))
        except Exception as exc:
            logger.warning(
                "Notification processing failed (%s)", type(exc).__name__, exc_info=True,
            )
            results.append((client.email, "error"))
    return results


async def main():
    env = load_env(ENV_FILE)
    init_db()
    bot = Bot(env["BOT_TOKEN"])

    async def sender(tg_id, stage):
        await send_notification(bot, tg_id, stage)

    try:
        service = build_client_service()
        await run_notification_pass(service, sender)
    except SERVICE_ERRORS as exc:
        LOGGER.error("Unable to load notification candidates (%s)", type(exc).__name__)


if __name__ == "__main__":
    asyncio.run(main())
