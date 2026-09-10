from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def connection_view(page_url):
    return ("🔑 Подключение готово\n\nВыбери удобный способ.", InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Открыть в Happ", url=page_url)],
        [InlineKeyboardButton("📷 Показать QR-код", callback_data="client_qr")],
        [InlineKeyboardButton("🎬 Видео подключения", callback_data="connect_video")],
        [InlineKeyboardButton("← Назад", callback_data="client_home")],
    ]))
