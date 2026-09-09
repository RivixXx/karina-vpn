import re
import logging
import secrets
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ChatType, ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

try:
    from .app_config import ConfigError
    from .application import build_client_service
    from .integrations.xui import XUIError
    from .models import ClientInfo, DeviceInfo, OrderStatus, TrafficInfo, is_mobile_email
    from .repositories import BillingRepository
    from .services import (
        BillingAccessError, BillingError, BillingService, ClientServiceError,
        ReconciliationRequiredError, ValidationError,
    )
except ImportError:  # Direct execution from the src directory.
    from app_config import ConfigError
    from application import build_client_service
    from integrations.xui import XUIError
    from models import ClientInfo, DeviceInfo, OrderStatus, TrafficInfo, is_mobile_email
    from repositories import BillingRepository
    from services import (
        BillingAccessError, BillingError, BillingService, ClientServiceError,
        ReconciliationRequiredError, ValidationError,
    )

ENV_FILE = Path("/opt/karina-bot/.env")
DB_FILE = Path("/opt/karina-bot/karina.db")

SUPPORT_URL = "https://t.me/rivixxx"
LOGGER = logging.getLogger(__name__)
EXPECTED_SERVICE_ERRORS = (ConfigError, XUIError, ClientServiceError)
EXPECTED_BILLING_ERRORS = (BillingError, sqlite3.Error)


def build_billing_service():
    repository = BillingRepository(DB_FILE)
    repository.init_schema()
    return BillingService(repository, build_client_service())


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


BOT_TOKEN = None
ADMIN_TG_ID = None


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

            CREATE TABLE IF NOT EXISTS client_refs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                created_at INTEGER NOT NULL
            );
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


def delete_client_local_state(email, db=None):
    """Revoke access; an existing transaction may include ref deletion."""
    if db is None:
        with closing(db_connect()) as connection, connection:
            delete_client_local_state(email, connection)
        return
    db.execute("DELETE FROM telegram_links WHERE email = ?", (email,))
    db.execute("DELETE FROM bind_tokens WHERE email = ?", (email,))


def get_or_create_client_ref(email):
    with closing(db_connect()) as db, db:
        db.execute("INSERT OR IGNORE INTO client_refs (email, created_at) VALUES (?, ?)",
                   (email, int(time.time())))
        return db.execute("SELECT id FROM client_refs WHERE email = ?", (email,)).fetchone()[0]


def get_client_email_by_ref(ref_id):
    if type(ref_id) is not int or not 0 < ref_id <= 9223372036854775807:
        return None
    with closing(db_connect()) as db:
        row = db.execute("SELECT email FROM client_refs WHERE id = ?", (ref_id,)).fetchone()
        return row[0] if row else None


def delete_client_ref(email, db=None):
    if db is None:
        with closing(db_connect()) as connection, connection:
            delete_client_ref(email, connection)
        return
    db.execute("DELETE FROM client_refs WHERE email = ?", (email,))


def client_callback(action, email):
    if action not in {"u", "ub", "ud", "uc", "udel", "uy"}:
        raise ValueError("Unknown client action")
    return f"{action}:{get_or_create_client_ref(email)}"


def is_private_chat(update):
    return bool(update.effective_chat and update.effective_chat.type == ChatType.PRIVATE)


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
# CLIENT PRESENTATION
# ============================================================

def bytes_to_human(value):
    if not value:
        return "0 Б"
    value = float(value)
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if value < 1024 or unit == "ТБ":
            return f"{int(value)} {unit}" if unit == "Б" else f"{value:.1f} {unit}"
        value /= 1024


def traffic_limit_text(value):
    return "∞" if not value else f"{value / 1024 ** 3:.1f} ГБ"


def status_text(status):
    return {"active": "Активен", "expired": "Истёк", "disabled": "Отключён"}.get(
        status, "Неизвестно"
    )


def format_client_profile(client: ClientInfo) -> str:
    limit = client.device_limit or "∞"
    status = status_text(client.status)
    icon = {"active": "🟢", "expired": "🟠"}.get(client.status, "🔴")
    return (
        "💗 *Карина VPN*\n\n"
        "👑 *Мой профиль*\n\n"
        f"├ 📅 Подписка: *до {esc(client.expiry_text)}*\n"
        f"├ 📱 Устройства: *{esc(f'{client.device_count} / {limit}')}*\n"
        f"├ 📊 Использовано: *{esc(bytes_to_human(client.used_traffic_bytes))}*\n"
        f"├ 🎚 Лимит: *{esc(traffic_limit_text(client.total_traffic_bytes))}*\n"
        f"└ 🛡 VPN: {icon} *{esc(status)}*\n\n"
        "✨ _Свобода быть онлайн_"
    )


def format_devices(devices: list[DeviceInfo], limit: int) -> str:
    lines = [f"Использовано: {len(devices)} / {limit or '∞'}", ""]
    if not devices:
        return "\n".join(lines + ["Зарегистрированных устройств нет."])
    for device in devices:
        os_text = device.os_name or "Неизвестная ОС"
        if device.os_version:
            os_text += " " + device.os_version
        lines.extend([
            f"ID:         {device.id}",
            f"Устройство: {device.model or 'Неизвестное устройство'}",
            f"ОС:         {os_text}",
            f"Клиент:     {device.user_agent}",
            "",
        ])
    return "\n".join(lines).rstrip()


def format_traffic(traffic: TrafficInfo) -> str:
    lines = [f"Использовано: {bytes_to_human(traffic.used_bytes)}"]
    if traffic.limit_bytes:
        lines.extend([
            f"Лимит:        {bytes_to_human(traffic.limit_bytes)}",
            f"Осталось:     {bytes_to_human(traffic.remaining_bytes)}",
            f"Использовано: {traffic.percent_used:.1f}%",
        ])
    else:
        lines.append("Лимит:        Безлимит")
    return "\n".join(lines)


def safe_user_error(exc, admin=False):
    LOGGER.warning("Client operation failed: %s", type(exc).__name__, exc_info=True)
    if admin:
        return f"Не удалось выполнить операцию ({type(exc).__name__}). Попробуйте ещё раз."
    return "Не удалось выполнить операцию. Попробуйте позже или обратитесь в поддержку."


def format_admin_stats(clients) -> str:
    counts = {"active": 0, "expired": 0, "disabled": 0}
    for client in clients:
        counts[client.status] = counts.get(client.status, 0) + 1
    return (
        f"Всего:      {len(clients)}\n"
        f"Активных:   {counts['active']}\n"
        f"Истекло:    {counts['expired']}\n"
        f"Отключено:  {counts['disabled']}"
    )


def format_bundle_profile(bundle, mobile_traffic) -> str:
    primary = bundle.primary
    status_icon = {"active": "🟢", "expired": "🟠"}.get(primary.status, "🔴")
    expiry = "безлимит" if not primary.expiry_time_ms else primary.expiry_text
    lines = [
        f"👤 {primary.email}", "", f"{status_icon} {status_text(primary.status)}",
        f"📅 Срок: {expiry}", f"📱 HWID: {primary.device_limit or '∞'}", "",
        "🌐 Основной VPN", "Безлимит", "", "📡 Карина против глушилок",
    ]
    if bundle.mobile is None or mobile_traffic is None:
        lines.append("Отдельный мобильный лимит ещё не активирован.")
    elif mobile_traffic.limit_bytes and mobile_traffic.percent_used is not None:
        lines.extend([
            f"{bytes_to_human(mobile_traffic.used_bytes)} / {bytes_to_human(mobile_traffic.limit_bytes)}",
            f"Осталось: {bytes_to_human(mobile_traffic.remaining_bytes)}",
            f"Использовано: {mobile_traffic.percent_used:.1f}%",
        ])
    else:
        lines.append("⚠️ Мобильный лимит требует проверки.")
    return "\n".join(lines)


def format_migration_plan(plan) -> str:
    if plan.already_migrated:
        return "✅ Мобильный доступ уже активирован."
    lines = ["📱 План активации 50 ГБ", ""]
    if plan.needs_mobile_create:
        lines.append("CREATE mobile")
    if plan.needs_external_link_update:
        lines.append("LINK+VERIFY")
    if plan.needs_primary_detach:
        lines.append("DETACH inbound")
    lines.extend(f"⚠️ {warning}" for warning in plan.warnings)
    lines.extend(f"⛔ {error}" for error in plan.blocking_errors)
    return "\n".join(lines)


def format_expiring(clients) -> str:
    if not clients:
        return "Таких подписок нет."
    return "\n".join(
        f"{client.email:<20} осталось {client.days_remaining:.1f} дн."
        for client in clients
    )


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


def billing_plans_keyboard(plans):
    rows = []
    for plan in plans:
        badge = f" · {plan.badge}" if plan.badge else ""
        rows.append([InlineKeyboardButton(
            f"{plan.title} — {plan.price_rub} ₽{badge}",
            callback_data=f"bill_plan:{plan.id}",
        )])
    rows.append([InlineKeyboardButton("⬅️ Мой профиль", callback_data="client_home")])
    return InlineKeyboardMarkup(rows)


def billing_order_keyboard(order):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Перейти к оплате", callback_data=f"bill_pay:{order.id}")],
        [InlineKeyboardButton("❌ Отменить заказ", callback_data=f"bill_cancel:{order.id}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="client_pay")],
    ])


def format_billing_order(order, plan):
    return (
        "🧾 *Заказ создан*\n\n"
        f"Тариф: {esc(plan.title)}\n"
        f"Срок: {order.days} дней\n"
        f"Стоимость: {order.amount_rub} ₽\n"
        f"Заказ: `{esc(order.id)}`"
    )


async def render_client_home(update, email):
    try:
        client = build_client_service().get_client(email)
    except EXPECTED_SERVICE_ERRORS as exc:
        text = f"❌ {esc(safe_user_error(exc))}"
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("💬 Поддержка", url=SUPPORT_URL)]]
        )
    else:
        if client is None:
            text = "❌ Подписка не найдена\\. Обратитесь в поддержку\\."
            keyboard = InlineKeyboardMarkup(
                [[InlineKeyboardButton("💬 Поддержка", url=SUPPORT_URL)]]
            )
        else:
            text = format_client_profile(client)
            keyboard = client_keyboard(email, client.connect_url)

    if update.callback_query:
        await update.callback_query.edit_message_text(
            text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN_V2,
        )
    else:
        await update.message.reply_text(
            text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN_V2,
        )


async def client_devices(update, email):
    try:
        service = build_client_service()
        client = service.get_client(email)
        if client is None:
            text = "❌ Подписка не найдена\\."
        else:
            text = "📱 *Мои устройства*\n\n```text\n" + format_devices(
                service.get_devices(email), client.device_limit
            ) + "\n```"
    except EXPECTED_SERVICE_ERRORS as exc:
        text = f"❌ {esc(safe_user_error(exc))}"

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
    try:
        output = format_traffic(build_client_service().get_traffic(email))
        text = "📊 *Использование VPN*\n\n" f"```text\n{output}\n```"
    except EXPECTED_SERVICE_ERRORS as exc:
        text = f"❌ {esc(safe_user_error(exc))}"

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
    try:
        client = build_client_service().get_client(email)
        url = client.connect_url if client else None
    except EXPECTED_SERVICE_ERRORS as exc:
        LOGGER.warning("Unable to load client key: %s", type(exc).__name__, exc_info=True)
        url = None

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
            [InlineKeyboardButton("🔧 Сервис", callback_data="admin_service")],
        ]
    )


async def render_admin_home(update):
    text = (
        "💗 *Карина VPN*\n\n"
        "👑 *Администратор*\n\n"
        "🔐 Серверы работают\n"
        "📱 HWID контроль активен\n"
        "🔗 Прямая HTTPS-выдача включена\n\n"
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


async def admin_users(update):
    try:
        clients = build_client_service().list_clients()
    except EXPECTED_SERVICE_ERRORS as exc:
        await update.callback_query.edit_message_text(
            f"❌ {esc(safe_user_error(exc, admin=True))}"
        )
        return
    rows = []
    for client in clients:
        email = client.email
        linked = get_link_by_email(email)
        link_icon = "🔗" if linked else "⚪"
        status_icon = {"active": "🟢", "expired": "🟠"}.get(client.status, "🔴")
        device_limit = client.device_limit or "∞"
        rows.append(
            [
                InlineKeyboardButton(
                    f"{link_icon} {status_icon} {email} · {client.device_count}/{device_limit} · {client.expiry_text}",
                    callback_data=client_callback("u", email),
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
    try:
        client = build_client_service().get_client(email)
    except EXPECTED_SERVICE_ERRORS as exc:
        await update.callback_query.edit_message_text(
            f"❌ {esc(safe_user_error(exc, admin=True))}"
        )
        return
    if client is None:
        await update.callback_query.edit_message_text("Пользователь больше не существует")
        return

    linked = get_link_by_email(email)

    if linked:
        telegram_text = (
            f"✅ Telegram: `{linked['tg_id']}`"
        )

        bind_button = InlineKeyboardButton(
            "🔄 Перепривязать Telegram",
            callback_data=client_callback("ub", email),
        )
    else:
        telegram_text = "⚪ Telegram: не привязан"

        bind_button = InlineKeyboardButton(
            "🔗 Привязать Telegram",
            callback_data=client_callback("ub", email),
        )

    text = (
        "👑 *Пользователь*\n\n"
        f"👤 *{esc(email)}*\n"
        f"📅 {esc(client.expiry_text)}\n"
        f"📱 {esc(str(client.device_count) + ' / ' + str(client.device_limit or '∞'))}\n"
        f"🛡 {esc(status_text(client.status))}\n\n"
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
                    callback_data=client_callback("ud", email),
                ),
                InlineKeyboardButton(
                    "🔗 Подключение",
                    callback_data=client_callback("uc", email),
                ),
            ],
            [
                InlineKeyboardButton(
                    "🗑 Удалить",
                    callback_data=client_callback("udel", email),
                )
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


async def admin_bundle_user(update, email):
    try:
        service = build_client_service()
        bundle = service.get_client_bundle(email)
        traffic = service.get_mobile_traffic(email) if bundle and bundle.mobile else None
    except EXPECTED_SERVICE_ERRORS as exc:
        await update.callback_query.edit_message_text(safe_user_error(exc, admin=True))
        return
    if bundle is None:
        await update.callback_query.edit_message_text("Пользователь больше не существует")
        return
    ref_id = get_or_create_client_ref(email)
    linked = get_link_by_email(email)
    bind_text = "🔄 Перепривязать Telegram" if linked else "🔗 Привязать Telegram"
    toggle = "⛔ Отключить" if bundle.primary.enabled else "✅ Включить"
    rows = [
        [InlineKeyboardButton("🔑 Подключение", callback_data=f"uc:{ref_id}"),
         InlineKeyboardButton("📱 Устройства", callback_data=f"ud:{ref_id}")],
        [InlineKeyboardButton("⏳ Продлить", callback_data=f"ue:{ref_id}"),
         InlineKeyboardButton("🎚 HWID", callback_data=f"uh:{ref_id}")],
        [InlineKeyboardButton(toggle, callback_data=f"ut:{ref_id}")],
        [InlineKeyboardButton(bind_text, callback_data=f"ub:{ref_id}")],
    ]
    if bundle.mobile is None:
        rows.append([InlineKeyboardButton("📱 Активировать 50 ГБ", callback_data=f"um:{ref_id}")])
    rows.extend([
        [InlineKeyboardButton("🗑 Удалить", callback_data=f"udel:{ref_id}")],
        [InlineKeyboardButton("⬅ Назад", callback_data="admin_users")],
    ])
    await update.callback_query.edit_message_text(
        format_bundle_profile(bundle, traffic), reply_markup=InlineKeyboardMarkup(rows),
    )


async def admin_ref_callback(update, context, data):
    if not is_private_chat(update) or not is_admin(update):
        return
    query = update.callback_query
    back = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Пользователи", callback_data="admin_users")]])
    match = re.fullmatch(r"(u|ub|ud|uc|udel|uy):([1-9][0-9]{0,18})", data)
    email = get_client_email_by_ref(int(match[2])) if match else None
    if email is None:
        await query.edit_message_text("Пользователь больше не существует", reply_markup=back)
        return
    action, ref_id = match[1], int(match[2])
    if action == "u":
        context.user_data.pop("delete_confirmation", None)
        await admin_bundle_user(update, email)
        return
    # Keep a successful service result for retry if SQLite cleanup fails.
    pending = context.user_data.get("deleted_vpn")
    retry_cleanup = action == "uy" and pending and pending["ref"] == ref_id
    service = None
    client = None
    if not retry_cleanup:
        try:
            service = build_client_service()
            client = service.get_client(email)
        except EXPECTED_SERVICE_ERRORS as exc:
            await query.edit_message_text(safe_user_error(exc, admin=True), reply_markup=back)
            return
        if client is None:
            await query.edit_message_text("Пользователь больше не существует", reply_markup=back)
            return
    if action == "ub":
        await create_bind_link(update, context, email)
    elif action in {"ud", "uc"}:
        rows = [[InlineKeyboardButton("⬅️ Профиль", callback_data=f"u:{ref_id}")]]
        if action == "ud":
            try:
                devices = service.get_bundle_devices(email)
                text = format_devices(devices, client.device_limit)
                rows = [[InlineKeyboardButton(
                    f"Удалить устройство {device.id}", callback_data=f"ur{device.id}:{ref_id}",
                )] for device in devices]
                rows.append([InlineKeyboardButton("Сбросить все устройства", callback_data=f"uda:{ref_id}")])
                rows.append([InlineKeyboardButton("⬅ Профиль", callback_data=f"u:{ref_id}")])
            except EXPECTED_SERVICE_ERRORS as exc:
                text = safe_user_error(exc, admin=True)
        else:
            try:
                url = service.ensure_connection(email)
            except EXPECTED_SERVICE_ERRORS as exc:
                await query.edit_message_text(safe_user_error(exc, admin=True), reply_markup=back)
                return
            if url:
                rows.insert(0, [InlineKeyboardButton("💗 Подключить Карина VPN", url=url)])
            text = "🔗 Подключение"
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(rows))
    elif action == "udel":
        context.user_data["delete_confirmation"] = ref_id
        await query.edit_message_text(
            f"⚠️ Удалить пользователя {email}?\n\n"
            "Будут удалены:\n• VPN-клиент\n• ссылка подключения\n"
            "• Telegram-привязка\n• активные ссылки привязки\n\n"
            "История уведомлений и заказов сохранится.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🗑 Да, удалить", callback_data=f"uy:{ref_id}")],
                [InlineKeyboardButton("Отмена", callback_data=f"u:{ref_id}")],
            ]),
        )
    elif action == "uy":
        if not retry_cleanup:
            if context.user_data.get("delete_confirmation") != ref_id:
                await query.edit_message_text("Подтвердите удаление в профиле пользователя.", reply_markup=back)
                return
            try:
                result = service.delete_client_bundle(email)
            except EXPECTED_SERVICE_ERRORS as exc:
                LOGGER.warning("VPN client deletion failed: %s", type(exc).__name__, exc_info=True)
                await query.edit_message_text("Не удалось удалить VPN-клиента. Можно повторить из профиля.",
                                              reply_markup=InlineKeyboardMarkup([
                                                  [InlineKeyboardButton("⬅️ Профиль", callback_data=f"u:{ref_id}")]]))
                return
            pending = {"ref": ref_id, "warnings": (
                [result.file_cleanup_warning] if result.file_cleanup_warning else []
            )}
            context.user_data["deleted_vpn"] = pending
        try:
            with closing(db_connect()) as db, db:
                delete_client_local_state(email, db)
                delete_client_ref(email, db)
        except sqlite3.Error:
            await query.edit_message_text("VPN удалён, но локальный доступ ещё не отозван. Повторите очистку.",
                                          reply_markup=InlineKeyboardMarkup([
                                              [InlineKeyboardButton("Повторить очистку", callback_data=f"uy:{ref_id}")]]))
            return
        context.user_data.pop("deleted_vpn", None)
        context.user_data.pop("delete_confirmation", None)
        text = "✅ Пользователь удалён"
        if pending["warnings"]:
            text += "\n⚠️ Очистка файлов подключения не завершена; требуется проверка на сервере."
        await query.edit_message_text(text, reply_markup=back)


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
                    callback_data=client_callback("u", email),
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


CREATE_TIMEOUT_SECONDS = 600


def _create_state_valid(update, state):
    chat_id = getattr(update.effective_chat, "id", None)
    user_id = getattr(update.effective_user, "id", None)
    return bool(state and state.get("chat_id") == chat_id and state.get("user_id") == user_id
                and time.time() - state.get("updated_at", 0) <= CREATE_TIMEOUT_SECONDS)


def _choice_keyboard(prefix, values):
    rows = [[InlineKeyboardButton(label, callback_data=f"{prefix}:{value}")]
            for label, value in values]
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="ac:cancel")])
    return InlineKeyboardMarkup(rows)


async def admin_create_callback(update, context, data):
    if not is_private_chat(update) or not is_admin(update):
        return
    query = update.callback_query
    state = context.user_data.get("admin_create")
    if data == "admin_create_help":
        context.user_data["admin_create"] = {
            "step": "username", "chat_id": getattr(update.effective_chat, "id", None),
            "user_id": getattr(update.effective_user, "id", None), "updated_at": time.time(),
        }
        await query.edit_message_text(
            "Введите имя пользователя (2–56 символов):",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отмена", callback_data="ac:cancel")]]),
        )
        return
    if data == "ac:cancel":
        context.user_data.pop("admin_create", None)
        await render_admin_home(update)
        return
    if not _create_state_valid(update, state):
        context.user_data.pop("admin_create", None)
        await query.edit_message_text("Сессия создания устарела. Начните заново.")
        return
    state["updated_at"] = time.time()
    if data.startswith("ac:t:") and state.get("step") == "term":
        raw = data.removeprefix("ac:t:")
        if raw not in {"30", "90", "180", "365", "unlimited"}:
            return
        state["days"] = None if raw == "unlimited" else int(raw)
        state["step"] = "hwid"
        await query.edit_message_text(
            "Выберите лимит HWID:",
            reply_markup=_choice_keyboard("ac:h", [(str(v), str(v)) for v in (1, 2, 3, 5)] + [("Безлимит", "0")]),
        )
        return
    if data.startswith("ac:h:") and state.get("step") == "hwid":
        raw = data.removeprefix("ac:h:")
        if raw not in {"0", "1", "2", "3", "5"}:
            return
        state["hwid"] = int(raw)
        state["step"] = "confirm"
        term = "безлимит" if state["days"] is None else f"{state['days']} дней"
        hwid = state["hwid"] or "безлимит"
        await query.edit_message_text(
            f"Создать пользователя?\n\nИмя: {state['email']}\nСрок: {term}\nHWID: {hwid}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Создать", callback_data="ac:ok")],
                [InlineKeyboardButton("❌ Отмена", callback_data="ac:cancel")],
            ]),
        )
        return
    if data == "ac:ok" and state.get("step") == "confirm":
        # Consume before the mutation so repeated callbacks cannot create twice.
        context.user_data.pop("admin_create", None)
        try:
            result = build_client_service().create_client_bundle(
                state["email"], days=state["days"], hwid_limit=state["hwid"],
            )
        except ReconciliationRequiredError:
            LOGGER.warning("Bundle creation requires reconciliation", exc_info=True)
            await query.edit_message_text("⚠️ Требуется проверка состояния клиента")
            return
        except EXPECTED_SERVICE_ERRORS as exc:
            await query.edit_message_text(safe_user_error(exc, admin=True))
            return
        ref_id = get_or_create_client_ref(state["email"])
        term = "безлимит" if state["days"] is None else f"{state['days']} дней"
        hwid = state["hwid"] or "безлимит"
        text = ("✅ Пользователь создан\n\n"
                f"Имя: {state['email']}\nСрок: {term}\nHWID: {hwid}\n"
                "Primary status: активен\nMobile status: активен\nMobile quota: 50 ГБ")
        rows = []
        if result.subscription_page:
            rows.append([InlineKeyboardButton("📱 Открыть подключение", url=result.subscription_page)])
        rows.extend([
            [InlineKeyboardButton("🔗 Привязать Telegram", callback_data=f"ub:{ref_id}")],
            [InlineKeyboardButton("👤 Профиль", callback_data=f"u:{ref_id}")],
            [InlineKeyboardButton("🏠 Главная", callback_data="admin_home")],
        ])
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(rows))


async def admin_create_text(update, context):
    state = context.user_data.get("admin_create")
    if not is_private_chat(update) or not is_admin(update) or not _create_state_valid(update, state):
        context.user_data.pop("admin_create", None)
        return
    if state.get("step") != "username":
        return
    email = (update.message.text or "").strip()
    try:
        service = build_client_service()
        service.validate_email(email)
        if is_mobile_email(email):
            raise ValidationError("служебный суффикс запрещён")
        if len(email) > 56:
            raise ValidationError("имя слишком длинное для bundle")
        if service.get_client(email) is not None:
            raise ValidationError("пользователь уже существует")
    except ValidationError as exc:
        await update.message.reply_text(f"Ошибка: {exc}. Введите другое имя или отмените операцию.")
        return
    except EXPECTED_SERVICE_ERRORS as exc:
        await update.message.reply_text(safe_user_error(exc, admin=True))
        return
    state.update(email=email, step="term", updated_at=time.time())
    await update.message.reply_text(
        "Выберите срок подписки:",
        reply_markup=_choice_keyboard("ac:t", [(f"{v} дней", str(v)) for v in (30, 90, 180, 365)] + [("Безлимит", "unlimited")]),
    )


async def admin_bundle_action_callback(update, context, data):
    if not is_private_chat(update) or not is_admin(update):
        return
    query = update.callback_query
    match = re.fullmatch(r"(ue|uh|ut|uda|um|umy|ue(?:30|90|180|365)|uh(?:0|1|2|3|5)|ur[1-9][0-9]{0,18}):([1-9][0-9]{0,18})", data)
    if not match:
        return
    action, ref_id = match[1], int(match[2])
    email = get_client_email_by_ref(ref_id)
    if email is None:
        await query.edit_message_text("Пользователь больше не существует")
        return
    back = InlineKeyboardMarkup([[InlineKeyboardButton("⬅ Профиль", callback_data=f"u:{ref_id}")]])
    try:
        service = build_client_service()
        if action == "ue":
            await query.edit_message_text("На сколько продлить?", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(f"{days} дней", callback_data=f"ue{days}:{ref_id}")]
                for days in (30, 90, 180, 365)
            ] + [[InlineKeyboardButton("❌ Отмена", callback_data=f"u:{ref_id}")]]))
        elif action.startswith("ue"):
            client = service.extend_client(email, int(action[2:]))
            await query.edit_message_text(f"✅ Новый срок: {client.expiry_text}", reply_markup=back)
        elif action == "uh":
            await query.edit_message_text("Выберите HWID:", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(str(value) if value else "Безлимит", callback_data=f"uh{value}:{ref_id}")]
                for value in (1, 2, 3, 5, 0)
            ] + [[InlineKeyboardButton("❌ Отмена", callback_data=f"u:{ref_id}")]]))
        elif action.startswith("uh"):
            service.set_hwid_limit(email, int(action[2:]))
            await query.edit_message_text("✅ HWID обновлён для всего bundle", reply_markup=back)
        elif action == "ut":
            client = service.get_client(email)
            if client.enabled:
                service.disable_client(email)
                text = "⛔ Bundle отключён"
            else:
                service.enable_client(email)
                text = "✅ Bundle включён"
            await query.edit_message_text(text, reply_markup=back)
        elif action == "uda":
            service.reset_bundle_devices(email)
            await query.edit_message_text("✅ Устройства сброшены", reply_markup=back)
        elif action.startswith("ur"):
            service.remove_bundle_device(email, int(action[2:]))
            await query.edit_message_text("✅ Устройство удалено", reply_markup=back)
        elif action == "um":
            plan = service.plan_mobile_migration(email)
            rows = [[InlineKeyboardButton("⬅ Профиль", callback_data=f"u:{ref_id}")]]
            if not plan.already_migrated and not plan.blocking_errors:
                context.user_data["migration_confirmation"] = ref_id
                rows.insert(0, [InlineKeyboardButton("✅ Выполнить миграцию", callback_data=f"umy:{ref_id}")])
            await query.edit_message_text(format_migration_plan(plan), reply_markup=InlineKeyboardMarkup(rows))
        elif action == "umy":
            if context.user_data.pop("migration_confirmation", None) != ref_id:
                await query.edit_message_text("Сначала откройте план миграции.", reply_markup=back)
                return
            service.migrate_client_to_mobile_bundle(email)
            await query.edit_message_text("✅ Мобильный доступ активирован", reply_markup=back)
    except ReconciliationRequiredError:
        LOGGER.warning("Bundle mutation requires reconciliation", exc_info=True)
        await query.edit_message_text("⚠️ Требуется проверка состояния клиента", reply_markup=back)
    except EXPECTED_SERVICE_ERRORS as exc:
        await query.edit_message_text(safe_user_error(exc, admin=True), reply_markup=back)


# ============================================================
# START
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not is_private_chat(update):
        await update.message.reply_text("Карина VPN работает только в личном чате с ботом.")
        return

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
    if not is_private_chat(update):
        await query.answer("Карина VPN работает только в личном чате с ботом.", show_alert=True)
        return
    await query.answer()

    data = query.data
    if not isinstance(data, str):
        return
    if data == "admin_create_help" or data.startswith("ac:"):
        if is_admin(update):
            await admin_create_callback(update, context, data)
        return
    if re.fullmatch(r"(?:ue|uh|ut|uda|um|umy|ue(?:30|90|180|365)|uh(?:0|1|2|3|5)|ur[1-9][0-9]{0,18}):.*", data):
        if is_admin(update):
            await admin_bundle_action_callback(update, context, data)
        return
    if re.fullmatch(r"(?:u|ub|ud|uc|udel|uy):.*", data):
        if is_admin(update):
            await admin_ref_callback(update, context, data)
        return

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

        if data == "admin_stats":
            try:
                output = format_admin_stats(build_client_service().list_clients())
            except EXPECTED_SERVICE_ERRORS as exc:
                output = safe_user_error(exc, admin=True)

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
            try:
                output = format_expiring(build_client_service().get_expiring(7))
            except EXPECTED_SERVICE_ERRORS as exc:
                output = safe_user_error(exc, admin=True)

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

        if data == "admin_service":
            try:
                service = build_client_service()
                connect_ok = service.connect_dir.is_dir() and service.connect_dir.exists()
                output = (
                    "🔧 Сервис\n\nBot: running logically\nXUI API: reachable\n"
                    "Subscription issuer: ready\n"
                    f"Connect directory: {'accessible' if connect_ok else 'unavailable'}\n"
                    "Billing: configured / provider absent\nNotifier: configured"
                )
            except EXPECTED_SERVICE_ERRORS as exc:
                output = safe_user_error(exc, admin=True)
            await query.edit_message_text(
                output,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Главная", callback_data="admin_home")]]),
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
        try:
            plans = build_billing_service().list_plans()
        except EXPECTED_BILLING_ERRORS:
            LOGGER.warning("Unable to load billing plans", exc_info=True)
            await query.answer("Не удалось загрузить тарифы", show_alert=True)
            return

        await query.edit_message_text(
            "💳 *Продлить Карина VPN*\n\nВыберите тариф:",
            reply_markup=billing_plans_keyboard(plans), parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    if data.startswith("bill_plan:"):
        plan_id = data.removeprefix("bill_plan:")
        try:
            billing = build_billing_service()
            plan = next((item for item in billing.list_plans() if item.id == plan_id), None)
            order = billing.create_order(user.id, email, plan_id)
        except EXPECTED_BILLING_ERRORS:
            LOGGER.warning("Unable to create billing order", exc_info=True)
            await query.answer("Не удалось создать заказ", show_alert=True)
            return
        await query.edit_message_text(
            format_billing_order(order, plan), reply_markup=billing_order_keyboard(order),
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    if data.startswith(("bill_pay:", "bill_cancel:")):
        action, order_id = data.split(":", 1)
        try:
            billing = build_billing_service()
            order = billing.get_order(order_id)
            if order is None or order.tg_id != user.id or order.email != email:
                raise BillingAccessError("Заказ принадлежит другому пользователю")
            if action == "bill_cancel":
                billing.cancel_order(order_id, user.id)
        except EXPECTED_BILLING_ERRORS:
            LOGGER.warning("Billing order access failed", exc_info=True)
            await query.answer("Заказ недоступен", show_alert=True)
            return
        if action == "bill_pay":
            if order.status is OrderStatus.COMPLETED:
                text = "✅ *Заказ выполнен\\.*"
            else:
                text = "💳 *Платёжный провайдер ещё не подключён\\.*"
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⬅️ Назад", callback_data="client_pay")
            ]]), parse_mode=ParseMode.MARKDOWN_V2)
        else:
            await query.edit_message_text(
                "❌ Заказ отменён\\.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("⬅️ К тарифам", callback_data="client_pay")
                ]]), parse_mode=ParseMode.MARKDOWN_V2,
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
    global BOT_TOKEN, ADMIN_TG_ID
    env = load_env(ENV_FILE)
    BOT_TOKEN = env["BOT_TOKEN"]
    ADMIN_TG_ID = int(env["ADMIN_TG_ID"])
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
        MessageHandler(filters.TEXT & ~filters.COMMAND, admin_create_text)
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
