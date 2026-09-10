from telegram import InlineKeyboardButton, InlineKeyboardMarkup


PLATFORM_COPY = {
    "android": ("🤖 Подключение на Android", "happ_android_url", "telegraph_android_url"),
    "ios": ("🍎 Подключение на iPhone / iPad", "happ_ios_url", "telegraph_ios_url"),
    "windows": ("💻 Подключение на Windows", "happ_windows_url", "telegraph_windows_url"),
    "macos": ("🍏 Подключение на macOS", "happ_macos_url", "telegraph_macos_url"),
}


def platform_choice_view(back="client_home"):
    return ("📲 Выбери устройство", InlineKeyboardMarkup([
        [InlineKeyboardButton("🤖 Android", callback_data="platform:android"),
         InlineKeyboardButton("🍎 iPhone / iPad", callback_data="platform:ios")],
        [InlineKeyboardButton("💻 Windows", callback_data="platform:windows"),
         InlineKeyboardButton("🍏 macOS", callback_data="platform:macos")],
        [InlineKeyboardButton("← Назад", callback_data=back)],
    ]))


def platform_view(platform, config):
    if platform not in PLATFORM_COPY:
        raise ValueError("unknown platform")
    title, app_field, guide_field = PLATFORM_COPY[platform]
    text = (f"{title}\n\n1. Установи Happ.\n2. Получи подключение в боте.\n"
            "3. Импортируй подписку.\n4. Разреши создание VPN-подключения.\n"
            "5. Нажми «Подключить».")
    rows = []
    app_url, guide_url = getattr(config, app_field), getattr(config, guide_field)
    if app_url:
        rows.append([InlineKeyboardButton("⬇️ Скачать Happ", url=app_url)])
    if config.connect_video_file_id:
        rows.append([InlineKeyboardButton("🎬 Видеоинструкция", callback_data="connect_video")])
    if guide_url:
        rows.append([InlineKeyboardButton("📖 Инструкция со скриншотами", url=guide_url)])
    rows += [[InlineKeyboardButton("🔑 Получить подключение", callback_data="client_connect")],
             [InlineKeyboardButton("← Назад", callback_data="connect_help")]]
    return text, InlineKeyboardMarkup(rows)
