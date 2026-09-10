from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from ..models import PLANS


def get_tariff(code):
    return next((plan for plan in PLANS if plan.id == code and plan.enabled), None)


def tariff_list_view(*, support_url=None, back_callback=None):
    text = (
        "💖 Выбери тариф\n\nВсе тарифы включают:\n"
        "• VPN для повседневного использования\n• до 5 устройств\n"
        "• профиль «Карина против глушилок»\n• 50 ГБ мобильного трафика\n"
        "• поддержку"
    )
    icons = {"m1": "", "m3": "⭐ ", "m6": "🔥 ", "y1": "👑 "}
    rows = [[InlineKeyboardButton(
        f"{icons.get(plan.id, '')}{plan.title} — {plan.price_rub:,} ₽".replace(",", " "),
        callback_data=f"tariff:{plan.id}",
    )] for plan in PLANS if plan.enabled]
    if support_url:
        rows.append([InlineKeyboardButton("💬 Поддержка", url=support_url)])
    if back_callback:
        rows.append([InlineKeyboardButton("← Назад", callback_data=back_callback)])
    return text, InlineKeyboardMarkup(rows)


def tariff_detail_view(code, *, back_callback="tariffs"):
    plan = get_tariff(code)
    if plan is None:
        raise ValueError("unknown tariff")
    months = 12 if plan.days == 365 else max(1, plan.days // 30)
    monthly = round(plan.price_rub / months)
    saving = max(0, 199 * months - plan.price_rub)
    icon = {"m1": "💖", "m3": "⭐", "m6": "🔥", "y1": "👑"}.get(code, "💖")
    text = (f"{icon} Тариф «{plan.title}»\n\nСрок: {plan.days} дней\n"
            f"Стоимость: {plan.price_rub:,} ₽\n≈ {monthly} ₽ в месяц".replace(",", " "))
    if saving:
        text += f"\n\nЭкономия: {saving} ₽\nпо сравнению с помесячной оплатой."
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🧾 Создать заявку", callback_data=f"order:{plan.id}")],
        [InlineKeyboardButton("← Другой тариф", callback_data=back_callback)],
    ])
    return text, keyboard
