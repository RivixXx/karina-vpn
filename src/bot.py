import re
import logging
import secrets
import sqlite3
import time
from datetime import datetime
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
    from .app_config import ConfigError, load_config
    from .application import build_client_service
    from .integrations.xui import XUIError
    from .models import ClientInfo, DeviceInfo, OrderStatus, TrafficInfo, is_mobile_email
    from .repositories import BillingRepository
    from .telegram_format import markdown_v2_escape as _markdown_v2_escape
    from .services import (
        BillingAccessError, BillingError, BillingService, ClientServiceError,
        CustomerOrderError, CustomerOrderService, ReconciliationRequiredError, ValidationError,
    )
    from .ui.customer import cabinet_keyboard, format_cabinet, stale_binding_view, support_view
    from .ui.connection import connection_view
    from .ui.help import platform_choice_view, platform_view
    from .ui.tariffs import get_tariff, tariff_detail_view, tariff_list_view
    from .telegram_navigation import current_screen, show_compound, show_photo, show_text, show_video
    from .avatar_scheduler import AvatarApplication, TIMEZONE as MOSCOW_TIMEZONE
except ImportError:  # Direct execution from the src directory.
    from app_config import ConfigError, load_config
    from application import build_client_service
    from integrations.xui import XUIError
    from models import ClientInfo, DeviceInfo, OrderStatus, TrafficInfo, is_mobile_email
    from repositories import BillingRepository
    from telegram_format import markdown_v2_escape as _markdown_v2_escape
    from services import (
        BillingAccessError, BillingError, BillingService, ClientServiceError,
        CustomerOrderError, CustomerOrderService, ReconciliationRequiredError, ValidationError,
    )
    from ui.customer import cabinet_keyboard, format_cabinet, stale_binding_view, support_view
    from ui.connection import connection_view
    from ui.help import platform_choice_view, platform_view
    from ui.tariffs import get_tariff, tariff_detail_view, tariff_list_view
    from telegram_navigation import current_screen, show_compound, show_photo, show_text, show_video
    from avatar_scheduler import AvatarApplication, TIMEZONE as MOSCOW_TIMEZONE

ENV_FILE = Path("/opt/karina-bot/.env")
DB_FILE = Path("/opt/karina-bot/karina.db")

LOGGER = logging.getLogger(__name__)
SUPPORT_URL = None  # Populated from typed configuration at startup.
EXPECTED_SERVICE_ERRORS = (ConfigError, XUIError, ClientServiceError)
EXPECTED_BILLING_ERRORS = (BillingError, sqlite3.Error)


def build_billing_service():
    repository = BillingRepository(DB_FILE)
    repository.init_schema()
    return BillingService(repository, build_client_service())


def create_telegram_link(tg_id, email, username="", first_name=""):
    with db_connect() as db:
        db.execute("DELETE FROM telegram_links WHERE tg_id = ? OR email = ?", (tg_id, email))
        db.execute(
            "INSERT INTO telegram_links "
            "(tg_id, email, tg_username, first_name, linked_at) VALUES (?, ?, ?, ?, ?)",
            (tg_id, email, username or "", first_name or "", int(time.time())),
        )


def build_customer_order_service():
    return CustomerOrderService(
        build_billing_service(), build_client_service(), get_link_by_tg,
        lambda tg_id, email: create_telegram_link(tg_id, email),
    )


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
REQUIRED_TG_CHAT_ID = None
REQUIRED_TG_CHAT_URL = None
REQUIRED_MEMBERSHIP_MODE = "new_users"
CUSTOMER_CONFIG = None


class MembershipCheckError(Exception):
    pass


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
    if action not in {"u", "ub", "ud", "uc", "udel", "udc", "uy"}:
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
        f"├ 📅 Подписка: *до {markdown_v2_escape(client.expiry_text)}*\n"
        f"├ 📱 Устройства: *{markdown_v2_escape(f'{client.device_count} / {limit}')}*\n"
        f"├ 📊 Использовано: *{markdown_v2_escape(bytes_to_human(client.used_traffic_bytes))}*\n"
        f"├ 🎚 Лимит: *{markdown_v2_escape(traffic_limit_text(client.total_traffic_bytes))}*\n"
        f"└ 🛡 VPN: {icon} *{markdown_v2_escape(status)}*\n\n"
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


def device_display_name(device: DeviceInfo, position: int) -> str:
    return device.model or device.os_name or f"устройство {position}"


def device_slots_bar(used: int, limit: int) -> str:
    if not limit:
        return ""
    segments = min(limit, 10)
    filled = min(segments, (min(used, limit) * segments + limit - 1) // limit)
    return "[" + "■" * filled + "□" * (segments - filled) + "]"


def format_client_devices(devices: list[DeviceInfo], limit: int) -> str:
    limit_text = str(limit) if limit else "∞"
    lines = [
        "📱 МОИ УСТРОЙСТВА • Карина VPN",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "",
        f"Занято слотов: {len(devices)} из {limit_text}",
    ]
    bar = device_slots_bar(len(devices), limit)
    if bar:
        lines.append(bar)
    lines.append("")
    if not devices:
        lines.extend(["Подключённых устройств пока нет.", ""])
    for position, device in enumerate(devices, 1):
        name = device_display_name(device, position)
        icon = "🍏" if "ios" in device.os_name.lower() else "💻"
        lines.append(f"{position}. {icon} {name}")
        os_text = " ".join(value for value in (device.os_name, device.os_version) if value)
        if os_text:
            lines.append(f"   ОС: {os_text}")
        if device.user_agent:
            lines.append(f"   Приложение: {device.user_agent}")
        if device.last_seen_ms:
            seen = datetime.fromtimestamp(
                device.last_seen_ms / 1000, tz=MOSCOW_TIMEZONE,
            ).strftime("%d.%m.%Y %H:%M")
            lines.append(f"   Последняя активность: {seen}")
        lines.append("")
    if limit:
        free = max(limit - len(devices), 0)
        lines.extend([f"Свободно слотов: {free}", ""])
        if not free:
            lines.extend([
                "⚠️ Все доступные слоты заняты.",
                "Отвяжите одно из устройств, чтобы подключить новое.",
                "",
            ])
    lines.extend([
        "💡 Вы можете отвязать конкретное устройство",
        "или сбросить все активные сессии сразу.",
    ])
    return "\n".join(lines)


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


def format_bundle_profile(
        bundle, mobile_traffic, inbound_names=None, traffic_unavailable=False) -> str:
    primary = bundle.primary
    status_icon = {"active": "🟢", "expired": "🟠"}.get(primary.status, "🔴")
    expiry = "безлимит" if not primary.expiry_time_ms else primary.expiry_text
    lines = [
        f"👤 {primary.email}", "", f"Статус: {status_icon} {status_text(primary.status)}",
        f"До: {expiry}",
        f"Устройства: {primary.device_count} / {primary.device_limit or '∞'}", "",
        "Основные подключения:",
    ]
    inbound_names = inbound_names or ()
    lines.extend(f"🇩🇪 {name}" for name in inbound_names)
    if not inbound_names:
        lines.append("Нет активных подключений")
    lines.extend(["", "📱 Карина против глушилок"])
    if bundle.mobile is None:
        lines.append("Не подключена")
    elif traffic_unavailable or mobile_traffic is None:
        lines.append("Данные временно недоступны")
    elif mobile_traffic.limit_bytes and mobile_traffic.percent_used is not None:
        usage = f"{bytes_to_human(mobile_traffic.used_bytes)} / {bytes_to_human(mobile_traffic.limit_bytes)}"
        if mobile_traffic.used_bytes >= mobile_traffic.limit_bytes:
            usage += " · лимит исчерпан"
        lines.append(usage)
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


def markdown_v2_escape(value):
    return _markdown_v2_escape(value)


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
    rows = [[InlineKeyboardButton("🔗 Подключить VPN", callback_data="client_connect")]]

    rows.extend(
        [
            [
                InlineKeyboardButton(
                    "📱 Мои устройства",
                    callback_data="client_devices",
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
                    "❓ Помощь",
                    callback_data="client_help",
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
        f"Тариф: {markdown_v2_escape(plan.title)}\n"
        f"Срок: {order.days} дней\n"
        f"Стоимость: {order.amount_rub} ₽\n"
        f"Заказ: {markdown_v2_escape(order.id)}"
    )


def format_user_cabinet(bundle, mobile_traffic, traffic_unavailable=False, now_ms=None):
    primary = bundle.primary
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    expired = bool(primary.expiry_time_ms and primary.expiry_time_ms <= now_ms)
    if expired:
        status = "🔴 Подписка закончилась"
        expiry = f"Дата окончания: {primary.expiry_text.split()[0]}"
    elif not primary.enabled:
        status, expiry = "⛔ Подписка отключена", ""
    elif not primary.expiry_time_ms:
        status, expiry = "🟢 Подписка активна", "Срок: без ограничений"
    else:
        status, expiry = "🟢 Подписка активна", f"До: {primary.expiry_text.split()[0]}"
    lines = ["👤 Карина VPN", "", status]
    if expiry:
        lines.append(expiry)
    lines.extend(["", f"📱 Устройства: {primary.device_count} / {primary.device_limit or '∞'}",
                  "", "📱 Карина против глушилок"])
    if bundle.mobile is None:
        lines.append("Не подключена")
    elif traffic_unavailable or mobile_traffic is None:
        lines.append("Данные временно недоступны")
    else:
        used = mobile_traffic.used_bytes / 1024 ** 3
        limit = mobile_traffic.limit_bytes / 1024 ** 3
        usage = f"Использовано: {used:.1f} / {limit:.0f} ГБ"
        if mobile_traffic.limit_bytes and mobile_traffic.used_bytes >= mobile_traffic.limit_bytes:
            usage += " · лимит исчерпан"
        lines.append(usage)
    return markdown_v2_escape("\n".join(lines))


async def resolve_sticker(context, index):
    cache = (context.application.bot_data if getattr(context, "application", None)
             else getattr(context, "user_data", {}))
    cache_key = f"karina_sticker_file_id:{int(index)}"
    if cache.get(cache_key):
        return cache[cache_key]
    try:
        # PTB 20.8's StickerSet model predates fields returned by the current
        # Bot API. _post returns the raw result dict before model conversion.
        result = await context.bot._post("getStickerSet", data={"name": "KarinaVPN"})
        stickers = result.get("stickers") if isinstance(result, dict) else None
        sticker = stickers[index] if isinstance(stickers, list) and len(stickers) > index else None
        file_id = sticker.get("file_id") if isinstance(sticker, dict) else None
        if not isinstance(file_id, str) or not file_id:
            raise ValueError(f"getStickerSet result has no sticker file_id at index {index}")
        cache[cache_key] = file_id
        return file_id
    except Exception:
        LOGGER.warning(
            "Unable to resolve KarinaVPN sticker at index %s", index, exc_info=True,
        )
        return None


async def resolve_main_sticker(context):
    return await resolve_sticker(context, 0)


async def resolve_connection_sticker(context):
    return await resolve_sticker(context, 8)


async def render_client_home(update, email, context=None):
    try:
        service = build_client_service()
        bundle = service.get_client_bundle(email)
    except EXPECTED_SERVICE_ERRORS as exc:
        text = f"❌ {safe_user_error(exc)}"
        keyboard = support_view(SUPPORT_URL)[1]
    else:
        if bundle is None:
            LOGGER.warning("Stale Telegram binding points to missing VPN client")
            text, keyboard = stale_binding_view(SUPPORT_URL)
        else:
            unavailable = False
            try:
                mobile_traffic = service.get_mobile_traffic(email)
            except EXPECTED_SERVICE_ERRORS:
                LOGGER.warning("Mobile traffic unavailable for cabinet", exc_info=True)
                mobile_traffic, unavailable = None, True
            text = format_cabinet(
                bundle, mobile_traffic, traffic_unavailable=unavailable,
                telegram_id=getattr(getattr(update, "effective_user", None), "id", None),
            )
            keyboard = cabinet_keyboard(
                news_url=getattr(CUSTOMER_CONFIG, "news_channel_url", None),
                support_url=SUPPORT_URL,
            )

    if context is not None:
        sticker = await resolve_main_sticker(context)
        await show_compound(
            update, context, screen_key="main", sticker=sticker, text=text,
            reply_markup=keyboard, parse_mode=ParseMode.HTML,
        )
    elif update.callback_query:
        await update.callback_query.edit_message_text(
            text, reply_markup=keyboard,
        )
    else:
        await update.message.reply_text(
            text, reply_markup=keyboard,
        )


async def client_devices(update, email, context=None):
    try:
        service = build_client_service()
        bundle = service.get_client_bundle(email)
        if bundle is None:
            text = "❌ Подписка не найдена."
            devices, limit = [], 0
        else:
            devices = service.get_bundle_devices(email)
            limit = bundle.primary.device_limit
            text = format_client_devices(devices, limit)
    except EXPECTED_SERVICE_ERRORS as exc:
        text, devices, limit = f"❌ {safe_user_error(exc)}", [], 0

    rows = [[InlineKeyboardButton(
        f"❌ Отвязать {device_display_name(device, position)}",
        callback_data=f"client_device_unlink:{device.id}",
    )] for position, device in enumerate(devices, 1)]
    if not limit or len(devices) < limit:
        rows.append([InlineKeyboardButton("＋ Добавить устройство", callback_data="client_connect")])
    rows.extend([
        [InlineKeyboardButton("🔄 Обновить", callback_data="client_devices")],
        [InlineKeyboardButton("🔄 Сбросить все устройства", callback_data="client_reset_devices")],
        [InlineKeyboardButton("← Назад", callback_data="client_home")],
    ])
    keyboard = InlineKeyboardMarkup(rows)

    if context is not None:
        sticker = await resolve_sticker(context, 6)
        await show_compound(
            update, context, screen_key="devices", sticker=sticker,
            text=text, reply_markup=keyboard,
        )
    else:
        await update.callback_query.edit_message_text(
            text, reply_markup=keyboard,
        )


async def client_traffic(update, email):
    try:
        output = format_traffic(build_client_service().get_traffic(email))
        text = "📊 *Использование VPN*\n\n" + markdown_v2_escape(output)
    except EXPECTED_SERVICE_ERRORS as exc:
        text = f"❌ {markdown_v2_escape(safe_user_error(exc))}"

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
        "🔗 Прямая HTTPS\\-выдача включена\n\n"
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


ADMIN_USERS_PAGE_SIZE = 20
ACTION_TIMEOUT_SECONDS = 600


async def admin_users(update, page=0):
    try:
        clients = build_client_service().list_clients()
    except EXPECTED_SERVICE_ERRORS as exc:
        await update.callback_query.edit_message_text(
            f"❌ {markdown_v2_escape(safe_user_error(exc, admin=True))}"
        )
        return
    page = max(int(page), 0)
    rows = []
    clients = [client for client in clients if not is_mobile_email(client.email)]
    pages = max((len(clients) + ADMIN_USERS_PAGE_SIZE - 1) // ADMIN_USERS_PAGE_SIZE, 1)
    page = min(page, pages - 1)
    for client in clients[page * ADMIN_USERS_PAGE_SIZE:(page + 1) * ADMIN_USERS_PAGE_SIZE]:
        email = client.email
        linked = get_link_by_email(email)
        link_icon = "🔗" if linked else "⚪"
        status_icon = {"active": "🟢", "expired": "🟠"}.get(client.status, "🔴")
        rows.append(
            [
                InlineKeyboardButton(
                    f"{link_icon} {status_icon} {email} · {status_text(client.status).lower()} · до {client.expiry_text[:5]}",
                    callback_data=client_callback("u", email),
                )
            ]
        )

    navigation = []
    if page:
        navigation.append(InlineKeyboardButton("⬅", callback_data=f"admin_users:{page - 1}"))
    if page + 1 < pages:
        navigation.append(InlineKeyboardButton("➡", callback_data=f"admin_users:{page + 1}"))
    if navigation:
        rows.append(navigation)
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
            f"❌ {markdown_v2_escape(safe_user_error(exc, admin=True))}"
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
        f"👤 *{markdown_v2_escape(email)}*\n"
        f"📅 {markdown_v2_escape(client.expiry_text)}\n"
        f"📱 {markdown_v2_escape(str(client.device_count) + ' / ' + str(client.device_limit or '∞'))}\n"
        f"🛡 {markdown_v2_escape(status_text(client.status))}\n\n"
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


async def admin_bundle_user(update, email, notice=None):
    try:
        service = build_client_service()
        bundle = service.get_client_bundle(email)
        inbound_names = service.get_primary_inbound_names(email) if bundle else ()
    except EXPECTED_SERVICE_ERRORS as exc:
        await update.callback_query.edit_message_text(safe_user_error(exc, admin=True))
        return
    if bundle is None:
        await update.callback_query.edit_message_text("Пользователь больше не существует")
        return
    traffic = None
    traffic_unavailable = False
    if bundle.mobile:
        try:
            traffic = service.get_mobile_traffic(email)
        except EXPECTED_SERVICE_ERRORS:
            LOGGER.warning("Mobile traffic lookup failed", exc_info=True)
            traffic_unavailable = True
    ref_id = get_or_create_client_ref(email)
    linked = get_link_by_email(email)
    bind_text = "🔄 Перепривязать Telegram" if linked else "🔗 Привязать Telegram"
    toggle = "⛔ Отключить" if bundle.primary.enabled else "✅ Включить"
    rows = [
        [InlineKeyboardButton("⏳ Продлить", callback_data=f"ue:{ref_id}")],
        [InlineKeyboardButton("🎚 Лимит устройств", callback_data=f"uh:{ref_id}")],
        [InlineKeyboardButton("♻ Сбросить устройства", callback_data=f"uda:{ref_id}")],
        [InlineKeyboardButton(toggle, callback_data=f"ut:{ref_id}")],
        [InlineKeyboardButton("🔄 Перевыпустить подключение", callback_data=f"uc:{ref_id}")],
        [InlineKeyboardButton(bind_text, callback_data=f"ub:{ref_id}")],
    ]
    if bundle.mobile is None:
        rows.append([InlineKeyboardButton("📱 Активировать 50 ГБ", callback_data=f"um:{ref_id}")])
    rows.extend([
        [InlineKeyboardButton("🗑 Удалить", callback_data=f"udel:{ref_id}")],
        [InlineKeyboardButton("⬅ Назад", callback_data="admin_users")],
    ])
    await update.callback_query.edit_message_text(
        ((notice + "\n\n") if notice else "")
        + format_bundle_profile(bundle, traffic, inbound_names, traffic_unavailable),
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def admin_ref_callback(update, context, data):
    if not is_private_chat(update) or not is_admin(update):
        return
    query = update.callback_query
    back = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Пользователи", callback_data="admin_users")]])
    match = re.fullmatch(r"(u|ub|ud|uc|udel|udc|uy):([1-9][0-9]{0,18})", data)
    email = get_client_email_by_ref(int(match[2])) if match else None
    if email is None or is_mobile_email(email):
        await query.edit_message_text("Пользователь больше не существует", reply_markup=back)
        return
    action, ref_id = match[1], int(match[2])
    if action == "u":
        context.user_data.pop("delete_confirmation", None)
        context.user_data.pop("device_reset_confirmation", None)
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
                url = service.reissue_bundle_connection(email)
            except EXPECTED_SERVICE_ERRORS as exc:
                await query.edit_message_text(safe_user_error(exc, admin=True), reply_markup=back)
                return
            if url:
                rows.insert(0, [InlineKeyboardButton("💗 Подключить Карина VPN", url=url)])
            text = "✅ Подключение перевыпущено"
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(rows))
    elif action == "udel":
        context.user_data["delete_confirmation"] = {
            "ref": ref_id, "step": 1, "updated_at": time.time(),
        }
        await query.edit_message_text(
            f"⚠️ Удалить пользователя {email}?\n\n"
            "Будут удалены:\n• основной профиль\n• мобильный профиль\n"
            "• привязки устройств\n• внешняя мобильная подписка\n"
            "• файлы подключения\n\nДействие необратимо.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Продолжить", callback_data=f"udc:{ref_id}")],
                [InlineKeyboardButton("Отмена", callback_data=f"u:{ref_id}")],
            ]),
        )
    elif action == "udc":
        state = context.user_data.get("delete_confirmation", {})
        if (state.get("ref") != ref_id or state.get("step") != 1
                or time.time() - state.get("updated_at", 0) > ACTION_TIMEOUT_SECONDS):
            context.user_data.pop("delete_confirmation", None)
            await query.edit_message_text("Подтверждение устарело. Откройте профиль снова.", reply_markup=back)
            return
        context.user_data["delete_confirmation"] = {
            "ref": ref_id, "step": 2, "updated_at": time.time(),
        }
        await query.edit_message_text(
            f"Подтвердите удаление пользователя {email}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("УДАЛИТЬ", callback_data=f"uy:{ref_id}")],
                [InlineKeyboardButton("Отмена", callback_data=f"u:{ref_id}")],
            ]),
        )
    elif action == "uy":
        if not retry_cleanup:
            state = context.user_data.get("delete_confirmation", {})
            if (state.get("ref") != ref_id or state.get("step") != 2
                    or time.time() - state.get("updated_at", 0) > ACTION_TIMEOUT_SECONDS):
                context.user_data.pop("delete_confirmation", None)
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
        f"Пользователь: *{markdown_v2_escape(email)}*\n\n"
        "Отправь клиенту эту одноразовую ссылку:\n\n"
        f"{markdown_v2_escape(link)}\n\n"
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
    user_data = context.user_data or {}
    if not is_private_chat(update) or not is_admin(update):
        return
    state = user_data.get("admin_create")
    if not state:
        return
    if not _create_state_valid(update, state):
        user_data.pop("admin_create", None)
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
    match = re.fullmatch(r"(ue|uh|ut|uda|uday|um|umy|ue(?:30|90|180|365|u)|uh(?:0|1|2|3|5)|ur[1-9][0-9]{0,18}):([1-9][0-9]{0,18})", data)
    if not match:
        return
    action, ref_id = match[1], int(match[2])
    email = get_client_email_by_ref(ref_id)
    if email is None or is_mobile_email(email):
        await query.edit_message_text("Пользователь больше не существует")
        return
    back = InlineKeyboardMarkup([[InlineKeyboardButton("⬅ Профиль", callback_data=f"u:{ref_id}")]])
    try:
        service = build_client_service()
        if action == "ue":
            await query.edit_message_text("На сколько продлить?", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(f"{days} дней", callback_data=f"ue{days}:{ref_id}")]
                for days in (30, 90, 180, 365)
            ] + [[InlineKeyboardButton("Без срока", callback_data=f"ueu:{ref_id}")],
                 [InlineKeyboardButton("❌ Отмена", callback_data=f"u:{ref_id}")]]))
        elif action.startswith("ue"):
            days = None if action == "ueu" else int(action[2:])
            client = service.extend_bundle(email, days)
            await admin_bundle_user(
                update, email,
                "✅ Срок обновлён: без срока" if days is None else f"✅ Новый срок: {client.expiry_text}",
            )
        elif action == "uh":
            await query.edit_message_text("Выберите HWID:", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(str(value) if value else "Безлимит", callback_data=f"uh{value}:{ref_id}")]
                for value in (1, 2, 3, 5, 0)
            ] + [[InlineKeyboardButton("❌ Отмена", callback_data=f"u:{ref_id}")]]))
        elif action.startswith("uh"):
            current = service.get_client(email)
            limit = int(action[2:])
            service.set_hwid_limit(email, limit)
            notice = "✅ Лимит устройств обновлён"
            if limit and current.device_count > limit:
                notice += ("\n⚠️ Подключено устройств больше нового лимита. "
                           "При необходимости сбросьте устройства.")
            await admin_bundle_user(update, email, notice)
        elif action == "ut":
            client = service.get_client(email)
            if client.enabled:
                service.set_bundle_enabled(email, False)
                text = "⛔ Пользователь отключён"
            else:
                service.set_bundle_enabled(email, True)
                text = "✅ Пользователь включён"
            await admin_bundle_user(update, email, text)
        elif action == "uda":
            context.user_data["device_reset_confirmation"] = {
                "ref": ref_id, "updated_at": time.time(),
            }
            await query.edit_message_text(
                f"Сбросить привязанные устройства пользователя {email}?",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("Да, сбросить", callback_data=f"uday:{ref_id}")],
                    [InlineKeyboardButton("Отмена", callback_data=f"u:{ref_id}")],
                ]),
            )
        elif action == "uday":
            state = context.user_data.pop("device_reset_confirmation", {})
            if (state.get("ref") != ref_id
                    or time.time() - state.get("updated_at", 0) > ACTION_TIMEOUT_SECONDS):
                await query.edit_message_text("Подтверждение устарело. Откройте профиль снова.", reply_markup=back)
                return
            service.reset_bundle_devices(email)
            await admin_bundle_user(update, email, "✅ Устройства сброшены")
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


async def telegram_error_handler(update, context):
    error = context.error
    if error and "message is not modified" in str(error).lower():
        return
    LOGGER.error(
        "Unhandled Telegram update exception",
        exc_info=(type(error), error, error.__traceback__) if error else None,
    )
    if update and getattr(getattr(update, "effective_chat", None), "type", None) == "channel":
        return
    message = getattr(update, "effective_message", None) if update else None
    if message is None:
        return
    try:
        await message.reply_text("Произошла ошибка. Попробуйте ещё раз позже.")
    except Exception:
        LOGGER.warning("Unable to send safe Telegram error response", exc_info=True)


def membership_required(linked):
    return REQUIRED_MEMBERSHIP_MODE == "all_users" or (
        REQUIRED_MEMBERSHIP_MODE == "new_users" and not linked
    )


async def check_required_membership(bot, telegram_user_id):
    if REQUIRED_MEMBERSHIP_MODE == "disabled":
        return True
    try:
        member = await bot.get_chat_member(REQUIRED_TG_CHAT_ID, telegram_user_id)
    except Exception as exc:
        LOGGER.warning("Required Telegram membership check failed (%s)",
                       type(exc).__name__, exc_info=True)
        raise MembershipCheckError("membership check unavailable") from exc
    raw_status = getattr(member, "status", "")
    status = str(getattr(raw_status, "value", raw_status)).lower().rsplit(".", 1)[-1]
    if status in {"creator", "administrator", "member"}:
        return True
    return status == "restricted" and bool(getattr(member, "is_member", False))


def membership_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Подписаться на Karina VPN", url=REQUIRED_TG_CHAT_URL)],
        [InlineKeyboardButton("✅ Я подписался — проверить", callback_data="membership_check")],
    ])


async def render_membership_gate(update, still_missing=False):
    text = ("Пока не вижу вас среди участников канала. Нажмите кнопку после подписки."
            if still_missing else
            "💖 Карина VPN\n\nЧтобы пользоваться ботом, подпишись на наш канал.\n\n"
            "Там публикуем:\n• новости сервиса\n• акции\n• важные обновления\n• инструкции")
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=membership_keyboard())
    else:
        await update.message.reply_text(text, reply_markup=membership_keyboard())


async def render_membership_error(update):
    text = "Не удалось проверить подписку на канал. Попробуйте немного позже."
    if update.callback_query:
        await update.callback_query.edit_message_text(text)
    else:
        await update.message.reply_text(text)


async def render_tariffs(update, back_callback=None, context=None):
    text, keyboard = tariff_list_view(support_url=SUPPORT_URL, back_callback=back_callback)
    if context is not None:
        await show_text(update, context, text, reply_markup=keyboard)
    elif update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=keyboard)
    else:
        await update.message.reply_text(text, reply_markup=keyboard)


def welcome_view():
    return (
        "💗 Карина VPN\n\nБыстрый и простой VPN для ваших устройств.\n\n"
        "Одна подписка включает обычные серверы и отдельные подключения для "
        "работы при мобильных ограничениях. Управление — прямо в этом боте.",
        InlineKeyboardMarkup([[
            InlineKeyboardButton("🚀 Начать", callback_data="welcome_start")
        ]]),
    )


def pending_request_view(order, plan, *, back_callback):
    created = datetime.fromtimestamp(order.created_at, MOSCOW_TIMEZONE).strftime("%d.%m.%Y %H:%M")
    text = (f"🧾 Заявка на оплату\n\nТариф: {plan.title}\n"
            f"Сумма: {plan.price_rub} ₽\nСтатус: ожидает оплаты\nСоздана: {created}")
    return text, InlineKeyboardMarkup([
        [InlineKeyboardButton("Изменить тариф", callback_data=f"order_change:{order.id}")],
        [InlineKeyboardButton("Отменить заявку", callback_data=f"order_cancel:{order.id}")],
        [InlineKeyboardButton("← Назад", callback_data=back_callback)],
    ])


async def send_menu_animation(update, context):
    file_id = getattr(CUSTOMER_CONFIG, "menu_animation_file_id", None)
    if not file_id:
        return
    try:
        await context.bot.send_animation(chat_id=update.effective_chat.id, animation=file_id)
    except Exception:
        LOGGER.warning("Customer menu animation could not be sent", exc_info=True)


# ============================================================
# START
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not is_private_chat(update):
        if getattr(update.effective_chat, "type", None) == "channel":
            return
        await update.message.reply_text("Карина VPN работает только в личном чате с ботом.")
        return

    user = update.effective_user

    args = context.args

    # Administration always wins over customer onboarding and bind deep links.
    if is_admin(update):
        await render_admin_home(update)
        return

    link = get_link_by_tg(user.id)

    if not is_admin(update) and membership_required(bool(link)):
        try:
            member = await check_required_membership(context.bot, user.id)
        except MembershipCheckError:
            await render_membership_error(update)
            return
        if not member:
            await render_membership_gate(update)
            return

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

        await show_text(update, context, "✅ Telegram успешно привязан к Карина VPN.")

        await render_client_home(update, email, context)

        return

    # Client.
    if link:
        await render_client_home(update, link["email"], context)

        return

    text, keyboard = welcome_view()
    await show_text(update, context, text, reply_markup=keyboard)


# ============================================================
# CALLBACKS
# ============================================================

async def callbacks(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    if not is_private_chat(update):
        if getattr(update.effective_chat, "type", None) == "channel":
            return
        await query.answer("Карина VPN работает только в личном чате с ботом.", show_alert=True)
        return
    await query.answer()

    data = query.data
    if not isinstance(data, str):
        return
    if data == "welcome_start":
        await render_tariffs(update, context=context)
        return
    if re.fullmatch(r"order_change:[A-Za-z0-9_-]+", data):
        context.user_data["replace_order_id"] = data.split(":", 1)[1]
        await render_tariffs(update, back_callback="client_home" if get_link_by_tg(update.effective_user.id) else None, context=context)
        return
    if re.fullmatch(r"order_cancel:[A-Za-z0-9_-]+", data):
        order_id = data.split(":", 1)[1]
        await show_text(update, context, "Отменить заявку на оплату?", reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("Да, отменить", callback_data=f"order_cancel_yes:{order_id}"),
            InlineKeyboardButton("Нет", callback_data=f"order_cancel_no:{order_id}"),
        ]]))
        return
    if re.fullmatch(r"order_cancel_(?:yes|no):[A-Za-z0-9_-]+", data):
        answer, order_id = data.split(":", 1)
        try:
            orders = build_customer_order_service()
            order = orders.get_request(update.effective_user.id, order_id)
        except (CustomerOrderError, BillingError, sqlite3.Error):
            await query.answer("Заявка недоступна", show_alert=True)
            return
        if answer.endswith("no"):
            plan = get_tariff(order.plan_id)
            text, keyboard = pending_request_view(order, plan, back_callback="client_home" if get_link_by_tg(order.tg_id) else "tariffs")
            await show_text(update, context, text, reply_markup=keyboard)
            return
        try:
            orders.reject(order_id)
        except (CustomerOrderError, BillingError, sqlite3.Error):
            await query.answer("Заявка уже закрыта", show_alert=True)
            return
        context.user_data.pop("replace_order_id", None)
        await render_tariffs(update, back_callback="client_home" if get_link_by_tg(order.tg_id) else None, context=context)
        return
    if data == "membership_check":
        try:
            member = await check_required_membership(context.bot, update.effective_user.id)
        except MembershipCheckError:
            await render_membership_error(update)
            return
        if not member:
            await render_membership_gate(update, still_missing=True)
            return
        link = get_link_by_tg(update.effective_user.id)
        if link:
            await render_client_home(update, link["email"], context)
        else:
            await render_tariffs(update, context=context)
        return

    if data.startswith(("oa:", "or:")):
        if not is_admin(update):
            await query.answer("Действие доступно только администратору", show_alert=True)
            return
        action, order_id = data.split(":", 1)
        try:
            orders = build_customer_order_service()
            if action == "oa":
                order, changed = orders.approve(order_id)
                await query.edit_message_text("✅ Заявка подтверждена." if changed else "✅ Заявка уже подтверждена.")
                if changed:
                    bundle = build_client_service().get_client_bundle(order.email)
                    expiry = bundle.primary.expiry_text.split()[0] if bundle else "уточняется"
                    await context.bot.send_message(
                        chat_id=order.tg_id,
                        text=("🎉 Карина VPN активирована!\n\n"
                              f"Тариф: {get_tariff(order.plan_id).title}\nАктивна до: {expiry}\n\n"
                              "Теперь осталось подключить VPN."),
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("🔑 Получить подключение", callback_data="client_connect")],
                            [InlineKeyboardButton("🎬 Как подключить", callback_data="connect_help")],
                            [InlineKeyboardButton("👤 Мой кабинет", callback_data="client_home")],
                        ]),
                    )
            else:
                order, changed = orders.reject(order_id)
                await query.edit_message_text("❌ Заявка отклонена." if changed else "❌ Заявка уже отклонена.")
                if changed:
                    await context.bot.send_message(
                        chat_id=order.tg_id,
                        text="❌ Заявка отклонена.\n\nЕсли это ошибка, обратитесь в поддержку.",
                    )
        except (CustomerOrderError, BillingError, ClientServiceError, sqlite3.Error) as exc:
            LOGGER.warning("Order moderation failed", exc_info=True)
            await query.edit_message_text(f"⚠️ {safe_user_error(exc, admin=True)}")
        return
    if data == "admin_create_help" or data.startswith("ac:"):
        if is_admin(update):
            await admin_create_callback(update, context, data)
        return
    if re.fullmatch(r"(?:ue|uh|ut|uda|uday|um|umy|ue(?:30|90|180|365|u)|uh(?:0|1|2|3|5)|ur[1-9][0-9]{0,18}):.*", data):
        if is_admin(update):
            await admin_bundle_action_callback(update, context, data)
        return
    if re.fullmatch(r"(?:u|ub|ud|uc|udel|udc|uy):.*", data):
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

        users_match = re.fullmatch(r"admin_users(?::([0-9]+))?", data)
        if users_match:
            await admin_users(
                update, int(users_match[1] or 0)
            )
            return

        if data == "admin_stats":
            try:
                output = format_admin_stats(build_client_service().list_clients())
            except EXPECTED_SERVICE_ERRORS as exc:
                output = safe_user_error(exc, admin=True)

            await query.edit_message_text(
                "📊 *Статистика*\n\n"
                f"{markdown_v2_escape(output)}",
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
                f"{markdown_v2_escape(output)}",
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

    # Customer acquisition and renewal. Callback payloads carry tariff codes only.
    if data == "tariffs" or re.fullmatch(r"tariff:[a-z0-9]+", data):
        link = get_link_by_tg(update.effective_user.id)
        if not link and membership_required(False):
            try:
                member = await check_required_membership(context.bot, update.effective_user.id)
            except MembershipCheckError:
                await render_membership_error(update)
                return
            if not member:
                await render_membership_gate(update, still_missing=True)
                return
        if data == "tariffs":
            await render_tariffs(update, back_callback="client_home" if link else None, context=context)
        else:
            code = data.split(":", 1)[1]
            try:
                text, keyboard = tariff_detail_view(
                    code, back_callback="tariffs",
                )
            except ValueError:
                await query.answer("Тариф недоступен", show_alert=True)
                return
            await query.edit_message_text(text, reply_markup=keyboard)
        return

    if re.fullmatch(r"order:[a-z0-9]+", data):
        user = update.effective_user
        link = get_link_by_tg(user.id)
        if not link and membership_required(False):
            try:
                member = await check_required_membership(context.bot, user.id)
            except MembershipCheckError:
                await render_membership_error(update)
                return
            if not member:
                await render_membership_gate(update, still_missing=True)
                return
        code = data.split(":", 1)[1]
        try:
            if link:
                bundle = build_client_service().get_client_bundle(link["email"])
                if bundle and bundle.primary.expiry_time_ms == 0:
                    await query.edit_message_text(
                        "💖 У вас уже бессрочная подписка — продление не требуется.",
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton("← В кабинет", callback_data="client_home")
                        ]]),
                    )
                    return
            orders = build_customer_order_service()
            replacing = context.user_data.pop("replace_order_id", None)
            if replacing:
                order, created = orders.replace_request(user.id, replacing, code), True
            else:
                order, created = orders.create_request(user.id, code)
            plan = get_tariff(order.plan_id)
            if plan is None:
                raise CustomerOrderError("Тариф недоступен")
        except (CustomerOrderError, BillingError, ClientServiceError, sqlite3.Error) as exc:
            LOGGER.warning("Order creation failed", exc_info=True)
            await query.answer("Не удалось создать заявку", show_alert=True)
            return
        text, keyboard = pending_request_view(
            order, plan, back_callback="client_home" if link else "tariffs",
        )
        await show_text(update, context, text, reply_markup=keyboard)
        if created:
            display = user.full_name or user.username or "Пользователь Telegram"
            await context.bot.send_message(
                chat_id=ADMIN_TG_ID,
                text=(f"💳 Новая заявка\n\nПользователь: {display}\n"
                      f"Тариф: {plan.title}\nСтоимость: {plan.price_rub} ₽"),
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ Подтвердить", callback_data=f"oa:{order.id}"),
                    InlineKeyboardButton("❌ Отклонить", callback_data=f"or:{order.id}"),
                ]]),
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

    if membership_required(True):
        try:
            member = await check_required_membership(context.bot, user.id)
        except MembershipCheckError:
            await render_membership_error(update)
            return
        if not member:
            await render_membership_gate(update, still_missing=True)
            return

    if data == "client_home":
        await render_client_home(update, email, context)
        return

    if data == "client_devices":
        await client_devices(update, email, context)
        return

    if data == "client_connect":
        try:
            service = build_client_service()
            page = service.reissue_bundle_connection(email)
            mobile_url = service.get_mobile_subscription_url(email)
            text, keyboard = connection_view(page, mobile_url)
            sticker = await resolve_connection_sticker(context)
            await show_compound(
                update, context, screen_key="connection", sticker=sticker,
                text=text, reply_markup=keyboard,
            )
        except EXPECTED_SERVICE_ERRORS as exc:
            await show_text(update, context, safe_user_error(exc))
        return

    if data == "client_qr":
        try:
            page = build_client_service().reissue_bundle_connection(email)
            qr = page[:-5] + ".png" if page.endswith(".html") else None
            if not qr:
                raise ClientServiceError("QR-код временно недоступен")
            context.user_data["connect_media_parent"] = "client_connect"
            await show_photo(
                update, context, qr,
                caption="📷 QR-код подключения\n\nОткрой Happ → добавление подписки → сканирование QR.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🎬 Как это сделать", callback_data="connect_video")],
                    [InlineKeyboardButton("← Назад", callback_data="client_connect")],
                ]),
            )
        except EXPECTED_SERVICE_ERRORS as exc:
            await query.edit_message_text(safe_user_error(exc))
        return

    if data in {"client_help", "connect_help"}:
        text, keyboard = platform_choice_view()
        await show_text(update, context, text, reply_markup=keyboard)
        return

    if re.fullmatch(r"platform:(android|ios|windows|macos)", data):
        text, keyboard = platform_view(data.split(":", 1)[1], CUSTOMER_CONFIG)
        context.user_data["connect_media_parent"] = "connect_help"
        await show_text(update, context, text, reply_markup=keyboard)
        return

    if data == "connect_video":
        file_id = getattr(CUSTOMER_CONFIG, "connect_video_file_id", None)
        local_video = getattr(CUSTOMER_CONFIG, "connect_video_path", None)
        video = local_video if local_video and Path(local_video).is_file() else file_id
        screen = current_screen(context) or {}
        back_callback = ("client_connect" if screen.get("screen_key") == "connection"
                         else context.user_data.get("connect_media_parent", "connect_help"))
        if video:
            try:
                await show_video(
                    update, context, video,
                    caption="🎬 Как подключить Карина VPN\n\nВесь процесс занимает меньше минуты.",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🔑 Получить подключение", callback_data="client_connect")
                    ], [InlineKeyboardButton("← Назад", callback_data=back_callback)]]),
                )
                return
            except Exception:
                LOGGER.warning("Connection video could not be sent", exc_info=True)
        text, keyboard = platform_choice_view()
        await query.edit_message_text(
            "Видео временно недоступно. Выбери устройство — откроем инструкцию.\n\n" + text,
            reply_markup=keyboard,
        )
        return

    if data == "client_support":
        text, keyboard = support_view(SUPPORT_URL)
        await show_text(update, context, text, reply_markup=keyboard)
        return

    if data == "client_invite":
        bot_user = await context.bot.get_me()
        share_url = f"https://t.me/share/url?url=https%3A%2F%2Ft.me%2F{bot_user.username}&text=%D0%9A%D0%B0%D1%80%D0%B8%D0%BD%D0%B0%20VPN"
        await query.edit_message_text(
            "👥 Пригласи друга\n\nПоделись ссылкой на Карина VPN.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("👥 Поделиться", url=share_url)],
                [InlineKeyboardButton("← Назад", callback_data="client_home")],
            ]),
        )
        return

    unlink_match = re.fullmatch(r"client_device_unlink:([1-9][0-9]{0,18})", data)
    if unlink_match:
        device_id = int(unlink_match[1])
        context.user_data["client_device_unlink"] = {
            "tg_id": user.id, "device_id": device_id, "updated_at": time.time(),
        }
        await show_text(
            update, context,
            "⚠️ ОТВЯЗАТЬ УСТРОЙСТВО?\n\n"
            "Устройство потребуется подключить заново.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    "✅ Да, отвязать",
                    callback_data=f"client_device_unlink_confirm:{device_id}",
                )],
                [InlineKeyboardButton("← Отмена", callback_data="client_devices")],
            ]),
        )
        return

    unlink_confirm = re.fullmatch(
        r"client_device_unlink_confirm:([1-9][0-9]{0,18})", data,
    )
    if unlink_confirm:
        device_id = int(unlink_confirm[1])
        state = context.user_data.pop("client_device_unlink", {})
        if (state.get("tg_id") != user.id or state.get("device_id") != device_id
                or time.time() - state.get("updated_at", 0) > ACTION_TIMEOUT_SECONDS):
            await show_text(update, context, "Подтверждение устарело.")
            return
        try:
            build_client_service().remove_bundle_device(email, device_id)
            await client_devices(update, email, context)
        except EXPECTED_SERVICE_ERRORS as exc:
            await show_text(update, context, safe_user_error(exc))
        return

    if data == "client_reset_devices":
        context.user_data["client_device_reset"] = {"tg_id": user.id, "updated_at": time.time()}
        await show_text(
            update, context,
            "⚠️ СБРОСИТЬ ВСЕ УСТРОЙСТВА?\n\n"
            "Все текущие привязки устройств будут удалены.\n"
            "После этого устройства потребуется подключить заново.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Да, сбросить", callback_data="client_reset_confirm")],
                [InlineKeyboardButton("← Отмена", callback_data="client_devices")],
            ]),
        )
        return

    if data == "client_reset_confirm":
        state = context.user_data.pop("client_device_reset", {})
        if state.get("tg_id") != user.id or time.time()-state.get("updated_at",0)>ACTION_TIMEOUT_SECONDS:
            await query.edit_message_text("Подтверждение устарело.")
            return
        try:
            build_client_service().reset_bundle_devices(email)
            await client_devices(update, email, context)
        except EXPECTED_SERVICE_ERRORS as exc:
            await show_text(update, context, safe_user_error(exc))
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
    global BOT_TOKEN, ADMIN_TG_ID, REQUIRED_TG_CHAT_ID, REQUIRED_TG_CHAT_URL
    global REQUIRED_MEMBERSHIP_MODE, CUSTOMER_CONFIG, SUPPORT_URL
    env = load_env(ENV_FILE)
    BOT_TOKEN = env["BOT_TOKEN"]
    ADMIN_TG_ID = int(env["ADMIN_TG_ID"])
    config = load_config()
    REQUIRED_TG_CHAT_ID = config.required_tg_chat_id
    REQUIRED_TG_CHAT_URL = config.required_tg_chat_url
    REQUIRED_MEMBERSHIP_MODE = config.required_membership_mode
    CUSTOMER_CONFIG = config
    SUPPORT_URL = config.support_url
    init_db()

    app = (
        Application
        .builder()
        .token(BOT_TOKEN)
        .application_class(AvatarApplication)
        .build()
    )
    app.bot_data["config"] = config

    app.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )

    app.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & filters.User(user_id=ADMIN_TG_ID)
            & filters.TEXT & ~filters.COMMAND,
            admin_create_text,
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            callbacks,
        )
    )

    app.add_error_handler(telegram_error_handler)

    app.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
