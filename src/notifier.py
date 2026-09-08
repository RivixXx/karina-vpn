import asyncio
import re
import sqlite3
import subprocess
import time
from pathlib import Path

from telegram import (
    Bot,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

ENV_FILE = Path("/opt/karina-bot/.env")
DB_FILE = Path("/opt/karina-bot/karina.db")


def load_env(path):
    data = {}

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()

        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)

        data[key.strip()] = (
            value.strip()
            .strip('"')
            .strip("'")
        )

    return data


ENV = load_env(ENV_FILE)
BOT_TOKEN = ENV["BOT_TOKEN"]


def db_connect():
    db = sqlite3.connect(DB_FILE)
    db.row_factory = sqlite3.Row
    return db


def init_db():
    with db_connect() as db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS notifications_sent (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL,
                expiry_key TEXT NOT NULL,
                notification_day INTEGER NOT NULL,
                sent_at INTEGER NOT NULL,
                UNIQUE(
                    email,
                    expiry_key,
                    notification_day
                )
            )
            """
        )


def run_karina(args):
    try:
        result = subprocess.run(
            [
                "/usr/local/bin/karina-user",
                *args,
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )

        output = (
            (result.stdout or "")
            + (
                "\n"
                if result.stdout and result.stderr
                else ""
            )
            + (result.stderr or "")
        ).strip()

        return result.returncode, output

    except subprocess.TimeoutExpired:
        return 1, "Timeout"

    except Exception as exc:
        return 1, str(exc)


def get_tg_id(email):
    with db_connect() as db:
        row = db.execute(
            """
            SELECT tg_id
            FROM telegram_links
            WHERE email = ?
            """,
            (email,),
        ).fetchone()

    if not row:
        return None

    return row["tg_id"]


def already_sent(
    email,
    expiry_key,
    notification_day,
):
    with db_connect() as db:
        row = db.execute(
            """
            SELECT id
            FROM notifications_sent
            WHERE email = ?
              AND expiry_key = ?
              AND notification_day = ?
            """,
            (
                email,
                expiry_key,
                notification_day,
            ),
        ).fetchone()

    return row is not None


def mark_sent(
    email,
    expiry_key,
    notification_day,
):
    with db_connect() as db:
        db.execute(
            """
            INSERT OR IGNORE INTO notifications_sent
                (
                    email,
                    expiry_key,
                    notification_day,
                    sent_at
                )
            VALUES
                (?, ?, ?, ?)
            """,
            (
                email,
                expiry_key,
                notification_day,
                int(time.time()),
            ),
        )


def parse_expiring(raw):
    users = []

    for raw_line in raw.splitlines():
        line = raw_line.strip()

        if not line:
            continue

        # Ожидаем строку примерно:
        # Test ... осталось 3.2 дня
        match = re.search(
            r"^([A-Za-z0-9_.-]+).*?"
            r"осталось\s+"
            r"(-?\d+(?:[.,]\d+)?)",
            line,
            flags=re.IGNORECASE,
        )

        if not match:
            continue

        email = match.group(1)

        days = float(
            match.group(2).replace(",", ".")
        )

        users.append(
            {
                "email": email,
                "days": days,
            }
        )

    return users


def get_expiry_key(email):
    code, output = run_karina(
        [
            "info",
            email,
        ]
    )

    if code != 0:
        return "unknown"

    for raw_line in output.splitlines():
        line = raw_line.strip()

        for prefix in (
            "Истекает:",
            "Срок:",
        ):
            if line.startswith(prefix):
                return line[len(prefix):].strip()

    return "unknown"


def notification_stage(days):
    """
    Возвращает:
      7
      3
      1
      0

    либо None.
    """

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
            "⏰ Осталась неделя\n\n"
            "До окончания подписки "
            "Карина VPN осталось около 7 дней.\n\n"
            "Можно продлить её заранее — "
            "оставшиеся дни не потеряются 💗"
        )

    if stage == 3:
        return (
            "⏰ Осталось 3 дня\n\n"
            "Подписка Карина VPN скоро закончится.\n\n"
            "Продли доступ заранее, чтобы VPN "
            "продолжил работать без перерыва."
        )

    if stage == 1:
        return (
            "⚠️ Остался 1 день\n\n"
            "Завтра заканчивается подписка "
            "Карина VPN.\n\n"
            "Продли её сейчас, чтобы соединение "
            "не отключилось."
        )

    return (
        "🚨 Подписка заканчивается сегодня\n\n"
        "Срок действия Карина VPN подходит "
        "к концу.\n\n"
        "Для продолжения работы продли подписку."
    )


async def send_notification(
    bot,
    tg_id,
    stage,
):
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "💳 Продлить VPN",
                    callback_data="client_pay",
                )
            ],
            [
                InlineKeyboardButton(
                    "💗 Мой профиль",
                    callback_data="client_home",
                )
            ],
        ]
    )

    await bot.send_message(
        chat_id=tg_id,
        text=notification_text(stage),
        reply_markup=keyboard,
    )


async def main():
    init_db()

    bot = Bot(BOT_TOKEN)

    code, output = run_karina(
        [
            "expiring",
            "8",
        ]
    )

    if code != 0:
        print(
            "karina-user error:",
            output,
        )
        return

    users = parse_expiring(output)

    print(
        f"Found expiring users: {len(users)}"
    )

    for item in users:
        email = item["email"]
        days = item["days"]

        stage = notification_stage(days)

        if stage is None:
            continue

        tg_id = get_tg_id(email)

        if not tg_id:
            print(
                f"{email}: Telegram not linked"
            )
            continue

        expiry_key = get_expiry_key(email)

        if already_sent(
            email,
            expiry_key,
            stage,
        ):
            print(
                f"{email}: stage {stage} already sent"
            )
            continue

        try:
            await send_notification(
                bot,
                tg_id,
                stage,
            )

            mark_sent(
                email,
                expiry_key,
                stage,
            )

            print(
                f"{email}: stage {stage} sent"
            )

        except Exception as exc:
            print(
                f"{email}: send error: {exc}"
            )


if __name__ == "__main__":
    asyncio.run(main())
