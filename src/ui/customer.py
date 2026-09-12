import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def format_cabinet(bundle, mobile_traffic, *, traffic_unavailable=False, now_ms=None,
                   telegram_id=None):
    primary = bundle.primary
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    expired = bool(primary.expiry_time_ms and primary.expiry_time_ms <= now_ms)
    if expired:
        status = f"Подписка закончилась · {primary.expiry_text.split()[0]}"
        protection = "Неактивна"
    elif not primary.enabled:
        status = "Подписка отключена"
        protection = "Отключена"
    elif not primary.expiry_time_ms:
        status = "VPN работает · без ограничений"
        protection = "Активна"
    else:
        status = f"VPN работает до {primary.expiry_text.split()[0]}"
        protection = "Активна"
    limit = primary.device_limit or "∞"
    if bundle.mobile is None:
        mobile = "Не подключена"
    elif traffic_unavailable or mobile_traffic is None:
        mobile = "Данные временно недоступны"
    else:
        used = mobile_traffic.used_bytes / 1024 ** 3
        cap = mobile_traffic.limit_bytes / 1024 ** 3
        free_percent = max(0, 100 - round(used / cap * 100)) if cap else 100
        used_percent = 100 - free_percent
        filled = min(10, max(0, round(used_percent / 10)))
        mobile = (f"{used:.1f} ГБ / {cap:.0f} ГБ ({free_percent}% свободно)\n"
                  f"{'█' * filled}{'░' * (10 - filled)} {used_percent}%")
        if mobile_traffic.limit_bytes and mobile_traffic.used_bytes >= mobile_traffic.limit_bytes:
            mobile += "\n🔴 Лимит исчерпан"
    identity = f"\n👤 ID: <code>{int(telegram_id)}</code>" if telegram_id is not None else ""
    icon = "🟢" if protection == "Активна" else "🔴"
    return (f"💖 КАРИНА VPN • Личный кабинет\n━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{identity}\n{icon} Статус: {status}\n\n"
            f"📊 Трафик «Антиглушилка»:\n{mobile}\n\n"
            f"📱 Подключено устройств: {primary.device_count} из {limit}\n"
            f"🛡 Защита: {protection}\n\n"
            "💡 Чтобы начать пользоваться интернетом без ограничений,\n"
            "нажмите «Получить подключение» ниже.")


def cabinet_keyboard(*, news_url=None, support_url=None):
    rows = [
        [InlineKeyboardButton("🚀 Получить подключение", callback_data="client_connect")],
        [InlineKeyboardButton("💳 Купить / Продлить", callback_data="tariffs"),
         InlineKeyboardButton("📱 Мои устройства", callback_data="client_devices")],
        [InlineKeyboardButton("📖 Как подключить", callback_data="connect_help"),
         InlineKeyboardButton("👥 Пригласить друга", callback_data="client_invite")],
        [InlineKeyboardButton("💬 Поддержка", callback_data="client_support"),
         InlineKeyboardButton("🔄 Обновить", callback_data="client_home")],
    ]
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
