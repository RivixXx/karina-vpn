from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def connection_view(subscription_url, page_url=None):
    rows = [[InlineKeyboardButton("🔗 Открыть ссылку подписки", url=subscription_url)]]

    if page_url:
        rows.append([
            InlineKeyboardButton(
                "🌐 Страница подключения ↗",
                url=page_url,
            )
        ])

    rows.extend([
        [
            InlineKeyboardButton(
                "📱 Показать QR-код",
                callback_data="client_qr",
            ),
            InlineKeyboardButton(
                "📖 Видео подключения",
                callback_data="connect_video",
            ),
        ],
        [
            InlineKeyboardButton(
                "← Назад",
                callback_data="client_home",
            )
        ],
    ])

    text = (
        "🟣 Карина на связи\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Подключимся за пару минут:\n\n"
        "1️⃣ Установите приложение Happ, если его ещё нет.\n"
        "2️⃣ Нажмите «🔗 Открыть ссылку подписки» ниже.\n"
        "3️⃣ Если Happ не открылся сам, скопируйте эту ссылку и добавьте её в приложении:\n"
        f"{subscription_url}\n"
        "4️⃣ Выберите любой сервер и включите VPN.\n\n"
        "Если настраиваете другое устройство, откройте страницу подключения "
        "или покажите на нём QR-код.\n\n"
        "Не получилось? Нажмите «📖 Видео подключения» — Карина всё покажет."
    )

    return text, InlineKeyboardMarkup(rows)
