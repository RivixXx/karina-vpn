from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def connection_view(page_url, mobile_url=None):
    rows = [
        [InlineKeyboardButton("🚀 Открыть в Happ ↗", url=page_url)],
        [InlineKeyboardButton("📱 Показать QR-код", callback_data="client_qr"),
         InlineKeyboardButton("📖 Видео подключения", callback_data="connect_video")],
        [InlineKeyboardButton("← Назад", callback_data="client_home")],
    ]
    if mobile_url:
        rows.insert(1, [InlineKeyboardButton("🛡 Карина против глушилок ↗", url=mobile_url)])
    text = ("🔑 ПОДКЛЮЧЕНИЕ ГОТОВО • Карина VPN\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Вы успешно получили настройки для вашего устройства.\n"
            "Выберите удобный способ подключения:\n\n"
            "🚀 Способ подключения:\n"
            "└ Быстрый запуск в 1 клик:\n"
            "└ Нажмите кнопку «Открыть в Happ» ниже.\n"
            "└ Приложение запустится и само добавит подписку.\n\n"
            "📱 Особый режим:\n"
            "Если обычное соединение блокируется, используйте «Карина против глушилок».\n\n"
            "💡 Нужна помощь?\n"
            "Откройте видео-инструкцию по настройке на другом устройстве.")
    return text, InlineKeyboardMarkup(rows)
