import re
import secrets
import sqlite3
import subprocess
import time
from contextlib import closing
from pathlib import Path

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

ENV_FILE = Path("/opt/karina-bot/.env")
DB_FILE = Path("/opt/karina-bot/karina.db")

SUPPORT_URL = "https://t.me/rivixxx"


# ============================================================
# CONFIG
# ============================================================

def load_env(path):
    data = {}

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()

        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        data[key.strip()] = value.strip().strip('"').strip("'")

    return data


ENV = load_env(ENV_FILE)

BOT_TOKEN = ENV["BOT_TOKEN"]
ADMIN_TG_ID = int(ENV["ADMIN_TG_ID"])


# ============================================================
# DATABASE
# ============================================================

def db_connect():
    db = sqlite3.connect(DB_FILE)
    db.row_factory = sqlite3.Row
    return db


def init_db():
    with db_connect() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS telegram_links (
                tg_id INTEGER PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                tg_username TEXT,
                first_name TEXT,
                linked_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS bind_tokens (
                token TEXT PRIMARY KEY,
                email TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                used INTEGER NOT NULL DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_bind_email
                ON bind_tokens(email);

            CREATE INDEX IF NOT EXISTS idx_links_email
                ON telegram_links(email);
            """
        )


def get_link_by_tg(tg_id):
    with db_connect() as db:
        return db.execute(
            """
            SELECT *
            FROM telegram_links
            WHERE tg_id = ?
            """,
            (tg_id,),
        ).fetchone()


def get_link_by_email(email):
    with db_connect() as db:
        return db.execute(
            """
            SELECT *
            FROM telegram_links
            WHERE email = ?
            """,
            (email,),
        ).fetchone()


def delete_client_local_state(email):
    """Revoke local access after confirmed XUI deletion; preserve audit history.

    No Telegram delete flow exists yet, so integration is intentionally pending.
    """
    with closing(db_connect()) as db, db:
        db.execute("DELETE FROM telegram_links WHERE email = ?", (email,))
        db.execute("DELETE FROM bind_tokens WHERE email = ?", (email,))


def create_bind_token(email):
    token = secrets.token_urlsafe(24)

    now = int(time.time())
    expires = now + 24 * 60 * 60

    with db_connect() as db:
        db.execute(
            """
            DELETE FROM bind_tokens
            WHERE email = ?
            """,
            (email,),
        )

        db.execute(
            """
            INSERT INTO bind_tokens
                (token, email, created_at, expires_at, used)
            VALUES
                (?, ?, ?, ?, 0)
            """,
            (
                token,
                email,
                now,
                expires,
            ),
        )

    return token


def consume_bind_token(token, user):
    now = int(time.time())

    with db_connect() as db:
        row = db.execute(
            """
            SELECT *
            FROM bind_tokens
            WHERE token = ?
              AND used = 0
              AND expires_at >= ?
            """,
            (
                token,
                now,
            ),
        ).fetchone()

        if not row:
            return None

        email = row["email"]

        db.execute(
            """
            DELETE FROM telegram_links
            WHERE email = ?
               OR tg_id = ?
            """,
            (
                email,
                user.id,
            ),
        )

        db.execute(
            """
            INSERT INTO telegram_links
                (
                    tg_id,
                    email,
                    tg_username,
                    first_name,
                    linked_at
                )
            VALUES
                (?, ?, ?, ?, ?)
            """,
            (
                user.id,
                email,
                user.username or "",
                user.first_name or "",
                now,
            ),
        )

        db.execute(
            """
            UPDATE bind_tokens
            SET used = 1
            WHERE token = ?
            """,
            (token,),
        )

    return email


# ============================================================
# KARINA USER BACKEND
# ============================================================

def run_karina(args):
    try:
        result = subprocess.run(
            [
                "/usr/local/bin/karina-user",
                *args,
            ],
            capture_output=True,
            text=True,
            timeout=90,
        )

        stdout = result.stdout or ""
        stderr = result.stderr or ""

        output = stdout

        if stdout and stderr:
            output += "\n"

        output += stderr

        return result.returncode, output.strip()

    except subprocess.TimeoutExpired:
        return 1, "Превышено время ожидания."

    except Exception as exc:
        return 1, f"Ошибка backend: {exc}"


def extract_info(raw):
    data = {}

    for raw_line in raw.splitlines():
        line = raw_line.strip()

        if not line:
            continue

        mapping = {
            "Пользователь:": "email",
            "Статус:": "status",
            "Истекает:": "expiry",
            "Срок:": "expiry",
            "Устройства:": "devices",
            "Использовано:": "used",
            "Лимит:": "limit",
            "Трафик:": "limit",
            "Серверы:": "servers",
        }

        for prefix, key in mapping.items():
            if line.startswith(prefix):
                data[key] = line[len(prefix):].strip()

        if line.startswith(
            "https://vpn.parsekk.ru/connect/"
        ):
            data["url"] = line

    return data


def esc(value):
    value = str(value)

    chars = r"_*[]()~`>#+-=|{}.!"

    for char in chars:
        value = value.replace(
            char,
            "\\" + char,
        )

    return value


# ============================================================
# ACCESS
# ============================================================

def is_admin(update):
    user = update.effective_user

    return bool(
        user
        and user.id == ADMIN_TG_ID
    )


# ============================================================
# CLIENT UI
# ============================================================

def client_keyboard(email, url=None):
    rows = []

    if url:
        rows.append(
            [
                InlineKeyboardButton(
                    "📱 Подключить VPN",
                    url=url,
                )
            ]
        )

    rows.extend(
        [
            [
                InlineKeyboardButton(
                    "📱 Мои устройства",
                    callback_data="client_devices",
                ),
                InlineKeyboardButton(
                    "📊 Трафик",
                    callback_data="client_traffic",
                ),
            ],
            [
                InlineKeyboardButton(
                    "💳 Продлить VPN",
                    callback_data="client_pay",
                )
            ],
            [
                InlineKeyboardButton(
                    "🔑 Ключ доступа",
                    callback_data="client_key",
                ),
                InlineKeyboardButton(
                    "🎁 Бонусы",
                    callback_data="client_bonus",
                ),
            ],
            [
                InlineKeyboardButton(
                    "💬 Помощь",
                    url=SUPPORT_URL,
                ),
                InlineKeyboardButton(
                    "🔄 Обновить",
                    callback_data="client_home",
                ),
            ],
        ]
    )

    return InlineKeyboardMarkup(rows)


async def render_client_home(update, email):
    code, output = run_karina(
        [
            "info",
            email,
        ]
    )

    if code != 0:
        text = (
            "❌ Не удалось получить данные подписки\\.\n\n"
            f"{esc(output)}"
        )

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "💬 Поддержка",
                        url=SUPPORT_URL,
                    )
                ]
            ]
        )

    else:
        data = extract_info(output)

        status = data.get(
            "status",
            "Неизвестно",
        )

        expiry = data.get(
            "expiry",
            "—",
        )

        devices = data.get(
            "devices",
            "—",
        )

        used = data.get(
            "used",
            "—",
        )

        limit = data.get(
            "limit",
            "—",
        )

        url = data.get("url")

        if "Активен" in status:
            vpn_icon = "🟢"
        elif "Истёк" in status:
            vpn_icon = "🟠"
        else:
            vpn_icon = "🔴"

        text = (
            "💗 *Карина VPN*\n\n"
            "👑 *Мой профиль*\n\n"
            f"├ 📅 Подписка: *до {esc(expiry)}*\n"
            f"├ 📱 Устройства: *{esc(devices)}*\n"
            f"├ 📊 Использовано: *{esc(used)}*\n"
            f"├ 🎚 Лимит: *{esc(limit)}*\n"
            f"└ 🛡 VPN: {vpn_icon} *{esc(status)}*\n\n"
            "✨ _Свобода быть онлайн_"
        )

        keyboard = client_keyboard(
            email,
            url,
        )

    if update.callback_query:
        await update.callback_query.edit_message_text(
            text,
            reply_markup=keyboard,
            parse_mode=ParseMode.MARKDOWN_V2,
        )

    else:
        await update.message.reply_text(
            text,
            reply_markup=keyboard,
            parse_mode=ParseMode.MARKDOWN_V2,
        )


async def client_devices(update, email):
    code, output = run_karina(
        [
            "devices",
            email,
        ]
    )

    if code != 0:
        text = f"❌ {esc(output)}"

    else:
        text = (
            "📱 *Мои устройства*\n\n"
            f"```text\n{output}\n```"
        )

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🏠 Мой профиль",
                    callback_data="client_home",
                )
            ],
            [
                InlineKeyboardButton(
                    "💬 Сменить устройство",
                    url=SUPPORT_URL,
                )
            ],
        ]
    )

    await update.callback_query.edit_message_text(
        text,
        reply_markup=keyboard,
        parse_mode=ParseMode.MARKDOWN_V2,
    )


async def client_traffic(update, email):
    code, output = run_karina(
        [
            "traffic",
            email,
        ]
    )

    if code != 0:
        text = f"❌ {esc(output)}"

    else:
        text = (
            "📊 *Использование VPN*\n\n"
            f"```text\n{output}\n```"
        )

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🏠 Мой профиль",
                    callback_data="client_home",
                )
            ]
        ]
    )

    await update.callback_query.edit_message_text(
        text,
        reply_markup=keyboard,
        parse_mode=ParseMode.MARKDOWN_V2,
    )


async def client_key(update, email):
    code, output = run_karina(
        [
            "info",
            email,
        ]
    )

    data = extract_info(output)
    url = data.get("url")

    buttons = []

    if url:
        buttons.append(
            [
                InlineKeyboardButton(
                    "🔑 Открыть подключение",
                    url=url,
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                "🏠 Мой профиль",
                callback_data="client_home",
            )
        ]
    )

    await update.callback_query.edit_message_text(
        "🔑 *Ключ доступа*\n\n"
        "В целях безопасности техническая конфигурация скрыта\\.\n\n"
        "Используй кнопку ниже для подключения устройства\\.",
        reply_markup=InlineKeyboardMarkup(
            buttons
        ),
        parse_mode=ParseMode.MARKDOWN_V2,
    )


# ============================================================
# ADMIN UI
# ============================================================

def admin_home_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "👥 Пользователи",
                    callback_data="admin_users",
                ),
                InlineKeyboardButton(
                    "➕ Создать",
                    callback_data="admin_create_help",
                ),
            ],
            [
                InlineKeyboardButton(
                    "⏰ Заканчиваются",
                    callback_data="admin_expiring",
                ),
                InlineKeyboardButton(
                    "📊 Статистика",
                    callback_data="admin_stats",
                ),
            ],
        ]
    )


async def render_admin_home(update):
    text = (
        "💗 *Карина VPN*\n\n"
        "👑 *Администратор*\n\n"
        "🔐 Серверы работают\n"
        "📱 HWID контроль активен\n"
        "🔒 Crypt5 включён\n\n"
        "Выбери действие 👇"
    )

    if update.callback_query:
        await update.callback_query.edit_message_text(
            text,
            reply_markup=admin_home_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2,
        )

    else:
        await update.message.reply_text(
            text,
            reply_markup=admin_home_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2,
        )


def parse_user_names(raw):
    names = []

    for line in raw.splitlines():
        match = re.match(
            r"^([A-Za-z0-9_.-]+)\s+"
            r"(?:🟢|🟠|🔴)",
            line.strip(),
        )

        if match:
            names.append(
                match.group(1)
            )

    return names


async def admin_users(update):
    code, output = run_karina(
        ["list"]
    )

    if code != 0:
        await update.callback_query.edit_message_text(
            f"❌ {esc(output)}"
        )
        return

    names = parse_user_names(output)

    rows = []

    for email in names:
        linked = get_link_by_email(email)

        icon = "🔗" if linked else "⚪"

        rows.append(
            [
                InlineKeyboardButton(
                    f"{icon} {email}",
                    callback_data=f"admin_user:{email}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                "🏠 Главная",
                callback_data="admin_home",
            )
        ]
    )

    await update.callback_query.edit_message_text(
        "👥 *Пользователи*\n\n"
        "🔗 — Telegram привязан\n"
        "⚪ — не привязан",
        reply_markup=InlineKeyboardMarkup(
            rows
        ),
        parse_mode=ParseMode.MARKDOWN_V2,
    )


async def admin_user(update, email):
    code, output = run_karina(
        [
            "info",
            email,
        ]
    )

    if code != 0:
        await update.callback_query.edit_message_text(
            f"❌ {esc(output)}"
        )
        return

    data = extract_info(output)

    linked = get_link_by_email(email)

    if linked:
        telegram_text = (
            f"✅ Telegram: `{linked['tg_id']}`"
        )

        bind_button = InlineKeyboardButton(
            "🔄 Перепривязать Telegram",
            callback_data=f"admin_bind:{email}",
        )
    else:
        telegram_text = "⚪ Telegram: не привязан"

        bind_button = InlineKeyboardButton(
            "🔗 Привязать Telegram",
            callback_data=f"admin_bind:{email}",
        )

    text = (
        "👑 *Пользователь*\n\n"
        f"👤 *{esc(email)}*\n"
        f"📅 {esc(data.get('expiry', '—'))}\n"
        f"📱 {esc(data.get('devices', '—'))}\n"
        f"🛡 {esc(data.get('status', '—'))}\n\n"
        f"{telegram_text}"
    )

    keyboard = InlineKeyboardMarkup(
        [
            [
                bind_button
            ],
            [
                InlineKeyboardButton(
                    "📱 Устройства",
                    callback_data=f"admin_devices:{email}",
                ),
                InlineKeyboardButton(
                    "🔗 Подключение",
                    callback_data=f"admin_connect:{email}",
                ),
            ],
            [
                InlineKeyboardButton(
                    "⬅️ Пользователи",
                    callback_data="admin_users",
                )
            ],
        ]
    )

    await update.callback_query.edit_message_text(
        text,
        reply_markup=keyboard,
        parse_mode=ParseMode.MARKDOWN_V2,
    )


async def create_bind_link(
    update,
    context,
    email,
):
    token = create_bind_token(email)

    bot = await context.bot.get_me()

    link = (
        f"https://t.me/"
        f"{bot.username}"
        f"?start=bind_{token}"
    )

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🔗 Открыть ссылку",
                    url=link,
                )
            ],
            [
                InlineKeyboardButton(
                    "⬅️ Профиль",
                    callback_data=f"admin_user:{email}",
                )
            ],
        ]
    )

    text = (
        "🔗 *Привязка Telegram*\n\n"
        f"Пользователь: *{esc(email)}*\n\n"
        "Отправь клиенту эту одноразовую ссылку:\n\n"
        f"`{esc(link)}`\n\n"
        "⏳ Ссылка действует 24 часа\\."
    )

    await update.callback_query.edit_message_text(
        text,
        reply_markup=keyboard,
        parse_mode=ParseMode.MARKDOWN_V2,
    )


# ============================================================
# START
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user = update.effective_user

    args = context.args

    # One-time binding.
    if args and args[0].startswith("bind_"):
        token = args[0][5:]

        email = consume_bind_token(
            token,
            user,
        )

        if not email:
            await update.message.reply_text(
                "❌ Ссылка привязки недействительна или уже использована."
            )
            return

        await update.message.reply_text(
            "✅ Telegram успешно привязан к Карина VPN."
        )

        await render_client_home(
            update,
            email,
        )

        return

    # Admin.
    if is_admin(update):
        await render_admin_home(
            update
        )
        return

    # Client.
    link = get_link_by_tg(
        user.id
    )

    if link:
        await render_client_home(
            update,
            link["email"],
        )

        return

    # Unknown user.
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "💬 Поддержка",
                    url=SUPPORT_URL,
                )
            ]
        ]
    )

    await update.message.reply_text(
        "💗 Карина VPN\n\n"
        "У тебя пока нет привязанной подписки.\n\n"
        "Получить доступ можно через администратора.",
        reply_markup=keyboard,
    )


# ============================================================
# CALLBACKS
# ============================================================

async def callbacks(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    data = query.data

    # ADMIN
    if is_admin(update):
        if data == "admin_home":
            await render_admin_home(
                update
            )
            return

        if data == "admin_users":
            await admin_users(
                update
            )
            return

        if data.startswith(
            "admin_user:"
        ):
            email = data.split(
                ":",
                1,
            )[1]

            await admin_user(
                update,
                email,
            )
            return

        if data.startswith(
            "admin_bind:"
        ):
            email = data.split(
                ":",
                1,
            )[1]

            await create_bind_link(
                update,
                context,
                email,
            )
            return

        if data.startswith(
            "admin_devices:"
        ):
            email = data.split(
                ":",
                1,
            )[1]

            code, output = run_karina(
                [
                    "devices",
                    email,
                ]
            )

            await query.edit_message_text(
                f"📱 *Устройства {esc(email)}*\n\n"
                f"```text\n{output}\n```",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "⬅️ Профиль",
                                callback_data=f"admin_user:{email}",
                            )
                        ]
                    ]
                ),
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return

        if data.startswith(
            "admin_connect:"
        ):
            email = data.split(
                ":",
                1,
            )[1]

            code, output = run_karina(
                [
                    "info",
                    email,
                ]
            )

            info = extract_info(
                output
            )

            url = info.get(
                "url"
            )

            keyboard = []

            if url:
                keyboard.append(
                    [
                        InlineKeyboardButton(
                            "💗 Подключить Карина VPN",
                            url=url,
                        )
                    ]
                )

            keyboard.append(
                [
                    InlineKeyboardButton(
                        "⬅️ Профиль",
                        callback_data=f"admin_user:{email}",
                    )
                ]
            )

            await query.edit_message_text(
                "🔗 *Подключение*\n\n"
                "Можно отправить пользователю кнопку ниже\\.",
                reply_markup=InlineKeyboardMarkup(
                    keyboard
                ),
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return

        if data == "admin_stats":
            code, output = run_karina(
                ["list"]
            )

            await query.edit_message_text(
                "📊 *Статистика*\n\n"
                f"```text\n{output}\n```",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "🏠 Главная",
                                callback_data="admin_home",
                            )
                        ]
                    ]
                ),
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return

        if data == "admin_expiring":
            code, output = run_karina(
                [
                    "expiring",
                    "7",
                ]
            )

            await query.edit_message_text(
                "⏰ *Заканчиваются за 7 дней*\n\n"
                f"```text\n{output}\n```",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "🏠 Главная",
                                callback_data="admin_home",
                            )
                        ]
                    ]
                ),
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return

    # CLIENT
    user = update.effective_user

    link = get_link_by_tg(
        user.id
    )

    if not link:
        await query.answer(
            "Подписка не привязана",
            show_alert=True,
        )
        return

    email = link["email"]

    if data == "client_home":
        await render_client_home(
            update,
            email,
        )
        return

    if data == "client_devices":
        await client_devices(
            update,
            email,
        )
        return

    if data == "client_traffic":
        await client_traffic(
            update,
            email,
        )
        return

    if data == "client_key":
        await client_key(
            update,
            email,
        )
        return

    if data == "client_pay":
        await query.edit_message_text(
            "💳 *Продление Карина VPN*\n\n"
            "Онлайн\\-оплата сейчас готовится\\.\n\n"
            "Пока для продления напиши в поддержку 👇",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "💬 Продлить через поддержку",
                            url=SUPPORT_URL,
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "🏠 Мой профиль",
                            callback_data="client_home",
                        )
                    ],
                ]
            ),
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    if data == "client_bonus":
        await query.edit_message_text(
            "🎁 *Бонусы Карина VPN*\n\n"
            "Система бонусов и приглашений появится совсем скоро\\.\n\n"
            "Мы уже готовим её к запуску 💗",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🏠 Мой профиль",
                            callback_data="client_home",
                        )
                    ]
                ]
            ),
            parse_mode=ParseMode.MARKDOWN_V2,
        )


# ============================================================
# MAIN
# ============================================================

def main():
    init_db()

    app = (
        Application
        .builder()
        .token(BOT_TOKEN)
        .build()
    )

    app.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            callbacks,
        )
    )

    app.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
