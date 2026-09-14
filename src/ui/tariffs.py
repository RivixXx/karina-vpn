from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from ..models import PLANS


STAR_PRICES = {"m1": 100, "m3": 250, "m6": 500, "y1": 1000}


def get_tariff(code):
    return next((plan for plan in PLANS if plan.id == code and plan.enabled), None)


def tariff_list_view(*, support_url=None, back_callback=None, device_limit=None):
    devices = (f"до {device_limit} устройств" if device_limit else
               "без ограничения числа устройств" if device_limit == 0 else "устройства в пределах лимита подписки")
    text = (
        "💎 ВЫБОР ТАРИФА • Карина VPN\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🛡 В каждый тариф входит:\n├ VPN для повседневного использования\n"
        f"├ 📱 {devices}\n├ 📶 резервное мобильное подключение — 50 GiB\n└ 💬 Поддержка\n\n"
        "💡 Чем больше срок — тем выгоднее подписка."
    )
    icons = {"m1": "", "m3": "⭐ ", "m6": "🔥 ", "y1": "👑 "}
    rows = [[InlineKeyboardButton(
        (f"{icons.get(plan.id, '')}{plan.title} — {plan.price_rub:,} ₽"
         + (f" · −{round(saving_percent(plan))}%" if saving_percent(plan) else "")).replace(",", " "),
        callback_data=f"tariff:{plan.id}",
    )] for plan in PLANS if plan.enabled]
    if support_url:
        rows.append([InlineKeyboardButton("💬 Поддержка", callback_data="client_support")])
    if back_callback:
        rows.append([InlineKeyboardButton("← Назад", callback_data=back_callback)])
    return text, InlineKeyboardMarkup(rows)


def saving_percent(plan):
    months = 12 if plan.days == 365 else max(1, plan.days // 30)
    base = get_tariff("m1").price_rub * months
    return max(0, (base - plan.price_rub) * 100 / base)


def tariff_detail_view(code, *, back_callback="tariffs", order_id=None, sbp_url=None):
    plan = get_tariff(code)
    if plan is None:
        raise ValueError("unknown tariff")
    months = 12 if plan.days == 365 else max(1, plan.days // 30)
    monthly = round(plan.price_rub / months)
    saving = max(0, get_tariff("m1").price_rub * months - plan.price_rub)
    icon = {"m1": "💖", "m3": "⭐", "m6": "🔥", "y1": "👑"}.get(code, "💖")
    stars = STAR_PRICES[code]
    text = ("💳 ВЫБЕРИТЕ СПОСОБ ОПЛАТЫ • Карина VPN\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{icon} Тариф «{plan.title}»\n\nСрок: {plan.days} дней\n"
            f"По СБП: {plan.price_rub:,} ₽\nЗвёздами: {stars:,} ⭐\n≈ {monthly} ₽ в месяц".replace(",", " "))
    if saving:
        text += f"\n\nЭкономия: {saving} ₽\nпо сравнению с помесячной оплатой."
    text += "\n\nПосле успешной оплаты Карина сама активирует или продлит подписку."
    payment_rows = []
    if sbp_url:
        payment_rows.append([InlineKeyboardButton("🟢 Оплатить по СБП", url=sbp_url)])
    else:
        payment_rows.append([InlineKeyboardButton(
            "🔄 Повторить подготовку СБП",
            callback_data=f"order_status:{order_id}" if order_id else f"checkout:{plan.id}",
        )])
    payment_rows.append([InlineKeyboardButton(
        f"⭐ Оплатить звёздами · {stars}",
        callback_data=f"stars:{order_id}" if order_id else f"checkout_stars:{plan.id}",
    )])
    keyboard = InlineKeyboardMarkup(payment_rows + [[
        InlineKeyboardButton("📄 Условия и возвраты", callback_data="legal:home"),
        InlineKeyboardButton("← Другой тариф", callback_data=(
            f"order_change:{order_id}" if order_id else back_callback
        )),
    ],
        [InlineKeyboardButton("🏠 В главное меню", callback_data="client_home")],
    ])
    return text, keyboard
