"""Outbox delivery; subscription changes stay in CustomerOrderService."""
import logging
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

try:
    from .models import OrderStatus
    from .services import ReconciliationRequiredError
    from .ui.tariffs import get_tariff
except ImportError:
    from models import OrderStatus
    from services import ReconciliationRequiredError
    from ui.tariffs import get_tariff

LOGGER = logging.getLogger(__name__)


async def deliver(context, repository, client_factory, referral_callback, admin_id, *, now=time.time):
    for effect in repository.due_effects(int(now())):
        order_id, kind = effect["order_id"], effect["kind"]
        if not repository.claim_effect(order_id, kind, int(now())):
            continue
        try:
            order = repository.get_order(order_id)
            if order is None:
                raise ReconciliationRequiredError("order is missing")
            plan = get_tariff(order.plan_id)
            title = order.plan_title or (plan.title if plan else order.plan_id)
            if kind == "referral":
                await referral_callback(order, context)
            elif kind == "customer":
                bundle = client_factory().get_client_bundle(order.email)
                if (bundle is None or bundle.mobile is None
                        or bundle.primary.expiry_time_ms != bundle.mobile.expiry_time_ms):
                    raise ReconciliationRequiredError("subscription is unavailable")
                await context.bot.send_message(
                    chat_id=order.tg_id,
                    text=("✅ ОПЛАТА ПОДТВЕРЖДЕНА • Карина VPN\n\n"
                          f"Заявка №{order.id}\nТариф: {title}\nДобавлено: {order.days} дней\n"
                          f"Подписка активна до: {bundle.primary.expiry_text}\n\n"
                          "🛡 Карина VPN готова к работе."),
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔑 Получить подключение", callback_data="client_connect")],
                        [InlineKeyboardButton("🎬 Как подключить", callback_data="connect_help")],
                        [InlineKeyboardButton("👤 Мой кабинет", callback_data="client_home")],
                    ]),
                )
            elif kind == "admin" and order.status is OrderStatus.PENDING:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=(f"💳 Новая заявка №{order.id}\n\nTelegram ID: {order.tg_id}\n"
                          f"Тариф: {title}\nСрок: {order.days} дней\nСумма: {order.amount_rub} ₽"),
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("Открыть заявку", callback_data=f"payment_admin:{order.id}"),
                    ]]),
                )
            elif kind == "cancelled":
                await context.bot.send_message(chat_id=order.tg_id,
                    text=f"Заявка №{order.id} отменена. Если это ошибка, обратитесь в поддержку.")
            repository.finish_effect(order_id, kind, int(now()))
        except Exception as exc:
            repository.retry_effect(order_id, kind, int(now()), exc)
            LOGGER.warning("Payment delivery failed: %s/%s", order_id, kind, exc_info=True)
