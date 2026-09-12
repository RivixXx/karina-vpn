from telegram import InlineKeyboardButton, InlineKeyboardMarkup


FAQS = {
    "network": (
        "❓ VPN подключен, но интернет не работает",
        "1️⃣ Отключите VPN в Happ.\n"
        "2️⃣ Проверьте интернет без VPN.\n"
        "3️⃣ Если интернет работает — снова включите VPN.\n"
        "4️⃣ Обновите подписку или профиль в Happ, если эта команда доступна.\n"
        "5️⃣ Попробуйте другой обычный сервер.\n"
        "6️⃣ В мобильной сети попробуйте «Карина против глушилок».\n\n"
        "Если интернет не работает даже при выключенном VPN, проблема, скорее всего, "
        "не связана с Карина VPN.",
    ),
    "antiblock": (
        "❓ Что такое «Антиглушилка»?",
        "«Карина против глушилок» — специальное подключение для ситуаций, когда "
        "обычный VPN не соединяется или мобильный оператор ограничивает доступ.\n\n"
        "Используйте его, если обычные серверы не подключаются, интернет без VPN "
        "работает, проблемы возникают в мобильной сети или во время ограничений связи.\n\n"
        "Если обычное подключение работает нормально, лучше использовать обычные серверы.",
    ),
    "devices": (
        "❓ Как подключить другое устройство?",
        "Одна подписка работает на нескольких устройствах в пределах доступного лимита.\n\n"
        "📱 Второй телефон\nОткройте «Получить подключение» и используйте Happ или QR.\n\n"
        "💻 Компьютер\nОткройте инструкцию и используйте текущую подписку.\n\n"
        "📺 Smart TV\nСпособ подключения зависит от операционной системы телевизора.",
    ),
    "payment": (
        "❓ Оплата списалась, но подписка не обновилась",
        "Не создавайте повторную заявку сразу. Проверьте текущую заявку: платежи "
        "подтверждаются администратором после проверки.\n\n"
        "Если заявка уже подтверждена, но срок подписки не изменился, напишите оператору.",
    ),
    "speed": (
        "❓ Низкая скорость или зависает видео",
        "1. Попробуйте другой обычный сервер.\n"
        "2. Переключитесь между Wi-Fi и мобильной сетью.\n"
        "3. Проверьте скорость без VPN.\n"
        "4. Перезапустите подключение Happ.\n"
        "5. При мобильных ограничениях попробуйте «Карина против глушилок».\n\n"
        "Скорость зависит от сети, маршрута и нагрузки.",
    ),
    "quicklaunch": (
        "❓ Как настроить быстрый запуск?",
        "Быстрый запуск позволяет включать и выключать VPN без постоянного открытия бота.\n\n"
        "Откройте существующую инструкцию для вашего устройства. Названия пунктов могут "
        "отличаться в разных версиях Happ и операционной системы.",
    ),
    "transfer": (
        "❓ Как перенести Карина VPN на новый телефон?",
        "1. Откройте «Мои устройства».\n"
        "2. При необходимости отвяжите старый телефон.\n"
        "3. Нажмите «Получить подключение».\n"
        "4. Откройте подписку в Happ на новом телефоне.\n"
        "5. При наличии свободного слота старое устройство можно оставить до проверки нового.",
    ),
}


def support_home_view(support_url=None, knowledge_base_url=None):
    text = (
        "💬 ПОДДЕРЖКА И ЧАСТЫЕ ВОПРОСЫ • Карина VPN\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Перед обращением к оператору загляните в ответы на популярные вопросы:\n\n"
        "├ ❓ 1. VPN подключен, но интернет не работает\n"
        "├ ❓ 2. Что такое «Антиглушилка» и когда её включать?\n"
        "├ ❓ 3. Как подключить ПК, Smart TV или второй телефон?\n"
        "├ ❓ 4. Оплата списалась, но подписка не обновилась\n"
        "├ ❓ 5. Низкая скорость или зависает видео — что делать?\n"
        "├ ❓ 6. Как настроить быстрый запуск по кнопке / шторке?\n"
        "└ ❓ 7. Как перенести подписку на новый телефон?\n\n"
        "💡 Если вашего вопроса нет в списке, напишите оператору одним сообщением — мы на связи!"
    )
    if not support_url:
        text += "\n\nСсылка на оператора временно недоступна."
    rows = []
    if support_url:
        rows.append([InlineKeyboardButton("✍️ Написать оператору", url=support_url)])
    rows.extend([
        [InlineKeyboardButton("❓ 1. Не грузит сеть", callback_data="support_faq:network"),
         InlineKeyboardButton("❓ 2. Антиглушилка", callback_data="support_faq:antiblock")],
        [InlineKeyboardButton("❓ 3. Подключить ПК", callback_data="support_faq:devices"),
         InlineKeyboardButton("❓ 4. Проблема с оплатой", callback_data="support_faq:payment")],
        [InlineKeyboardButton("❓ 5. Медленная скорость", callback_data="support_faq:speed"),
         InlineKeyboardButton("❓ 6. Быстрый запуск", callback_data="support_faq:quicklaunch")],
        [InlineKeyboardButton("❓ 7. Перенести подписку", callback_data="support_faq:transfer")],
    ])
    if knowledge_base_url:
        rows.append([InlineKeyboardButton("🌐 Полная база знаний", url=knowledge_base_url)])
    rows.append([InlineKeyboardButton("← Назад в меню", callback_data="client_home")])
    return text, InlineKeyboardMarkup(rows)


def faq_view(slug, *, support_url=None, mobile_url=None, device_slots=None, pending_order=None):
    title, body = FAQS[slug]
    if slug == "devices" and device_slots is not None:
        used, limit = device_slots
        body += f"\n\nСейчас занято устройств: {used} из {limit or '∞'}."
    if slug == "payment":
        body += ("\n\nТекущая заявка ожидает подтверждения."
                 if pending_order else "\n\nАктивной заявки, ожидающей подтверждения, нет.")
    rows = []
    if slug in {"network", "antiblock", "speed"} and mobile_url:
        rows.append([InlineKeyboardButton("🛡 Карина против глушилок", url=mobile_url)])
    if slug == "devices":
        rows.extend([
            [InlineKeyboardButton("🚀 Получить подключение", callback_data="client_connect")],
            [InlineKeyboardButton("📱 Мои устройства", callback_data="client_devices"),
             InlineKeyboardButton("📖 Инструкция", callback_data="connect_help")],
        ])
    elif slug == "payment":
        rows.append([InlineKeyboardButton("💳 Проверить / Продлить", callback_data="client_pay")])
    elif slug == "quicklaunch":
        rows.append([InlineKeyboardButton("📖 Открыть инструкцию", callback_data="connect_help")])
    elif slug == "transfer":
        rows.append([InlineKeyboardButton("📱 Мои устройства", callback_data="client_devices"),
                     InlineKeyboardButton("🚀 Получить подключение", callback_data="client_connect")])
    if support_url and slug in {"network", "payment", "speed"}:
        rows.append([InlineKeyboardButton("✍️ Написать оператору", url=support_url)])
    rows.append([InlineKeyboardButton("← К частым вопросам", callback_data="client_support")])
    return f"{title}\n\n{body}", InlineKeyboardMarkup(rows)
