import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def format_cabinet(bundle, mobile_traffic, *, traffic_unavailable=False, now_ms=None):
    primary = bundle.primary
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    expired = bool(primary.expiry_time_ms and primary.expiry_time_ms <= now_ms)
    if expired:
        status = f"🔴 Подписка закончилась\nДата окончания: {primary.expiry_text.split()[0]}"
    elif not primary.enabled:
        status = "⛔ Подписка отключена"
    elif not primary.expiry_time_ms:
        status = "🟢 VPN работает\nСрок: без ограничений"
    else:
        status = f"🟢 VPN работает\nДо: {primary.expiry_text.split()[0]}"
    limit = primary.device_limit or "∞"
    if bundle.mobile is None:
        mobile = "Не подключена"
    elif traffic_unavailable or mobile_traffic is None:
        mobile = "Данные временно недоступны"
    else:
        used = mobile_traffic.used_bytes / 1024 ** 3
        cap = mobile_traffic.limit_bytes / 1024 ** 3
        mobile = f"{used:.1f} / {cap:.0f} ГБ"
        if mobile_traffic.limit_bytes and mobile_traffic.used_bytes >= mobile_traffic.limit_bytes:
            mobile = f"🔴 {mobile} · лимит исчерпан"
    return (f"💖 Карина VPN\n\n{status}\n\n📱 Устройства: "
            f"{primary.device_count} из {limit}\n\n🚀 Против глушилок:\n{mobile}")


def cabinet_keyboard(*, news_url=None, support_url=None):
    rows = [
        [InlineKeyboardButton("🔑 Получить подключение", callback_data="client_connect")],
        [InlineKeyboardButton("💳 Купить / Продлить", callback_data="tariffs"),
         InlineKeyboardButton("📱 Мои устройства", callback_data="client_devices")],
        [InlineKeyboardButton("🎬 Как подключить", callback_data="connect_help"),
         InlineKeyboardButton("👥 Пригласить друга", callback_data="client_invite")],
    ]
    external = []
    if news_url:
        external.append(InlineKeyboardButton("🎁 Новости и акции", url=news_url))
    if support_url:
        external.append(InlineKeyboardButton("💬 Поддержка", url=support_url))
    if external:
        rows.append(external)
    return InlineKeyboardMarkup(rows)


def stale_binding_view(support_url=None):
    rows = [[InlineKeyboardButton("💬 Поддержка", url=support_url)]] if support_url else []
    return ("⚠️ Привязка Telegram найдена, но подписка временно недоступна.\n\n"
            "Привязка сохранена. Напишите в поддержку для восстановления доступа.",
            InlineKeyboardMarkup(rows))


def support_view(support_url=None):
    rows = []
    if support_url:
        rows.append([InlineKeyboardButton("💬 Написать в поддержку", url=support_url)])
    rows.append([InlineKeyboardButton("← Назад", callback_data="client_home")])
    return ("💬 Поддержка\n\nОпишите вопрос одним сообщением. Мы поможем с оплатой, "
            "подключением и работой VPN.", InlineKeyboardMarkup(rows))
