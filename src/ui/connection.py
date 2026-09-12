from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def connection_view(page_url, mobile_url=None):
    rows = [
        [InlineKeyboardButton("🚀 Открыть в Happ", url=page_url)],
        [InlineKeyboardButton("📷 Показать QR-код", callback_data="client_qr")],
        [InlineKeyboardButton("🎬 Видео подключения", callback_data="connect_video")],
        [InlineKeyboardButton("← Назад", callback_data="client_home")],
    ]
    if mobile_url:
        rows.insert(1, [InlineKeyboardButton("🚀 Карина против глушилок", url=mobile_url)])
    return "🔑 Подключение готово\n\nВыбери удобный способ.", InlineKeyboardMarkup(rows)
