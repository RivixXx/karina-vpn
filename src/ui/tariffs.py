from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from ..models import PLANS


def get_tariff(code):
    return next((plan for plan in PLANS if plan.id == code and plan.enabled), None)


def tariff_list_view(*, support_url=None, back_callback=None, device_limit=None):
    devices = (f"до {device_limit} устройств" if device_limit else
               "без ограничения числа устройств" if device_limit == 0 else "устройства в пределах лимита подписки")
    text = (
        "💎 ВЫБОР ТАРИФА • Карина VPN\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🛡 В каждый тариф входит:\n├ VPN для повседневного использования\n"
        f"├ 📱 {devices}\n├ 🚀 «Карина против глушилок» — 50 GiB\n└ 💬 Поддержка\n\n"
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


def tariff_detail_view(code, *, back_callback="tariffs"):
    plan = get_tariff(code)
    if plan is None:
        raise ValueError("unknown tariff")
    months = 12 if plan.days == 365 else max(1, plan.days // 30)
    monthly = round(plan.price_rub / months)
    saving = max(0, get_tariff("m1").price_rub * months - plan.price_rub)
    icon = {"m1": "💖", "m3": "⭐", "m6": "🔥", "y1": "👑"}.get(code, "💖")
    text = ("💳 ОФОРМЛЕНИЕ ПОДПИСКИ • Карина VPN\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{icon} Тариф «{plan.title}»\n\nСрок: {plan.days} дней\n"
            f"Стоимость: {plan.price_rub:,} ₽\n≈ {monthly} ₽ в месяц".replace(",", " "))
    if saving:
        text += f"\n\nЭкономия: {saving} ₽\nпо сравнению с помесячной оплатой."
    text += "\n\nПодписка будет активирована или продлена после подтверждения платежа администратором."
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Перейти к оплате", callback_data=f"order:{plan.id}")],
        [InlineKeyboardButton("← Другой тариф", callback_data=back_callback)],
        [InlineKeyboardButton("🏠 В главное меню", callback_data="client_home")],
    ])
    return text, keyboard
