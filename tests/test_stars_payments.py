from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

from src import bot
from src.models import OrderStatus
from src.ui.tariffs import STAR_PRICES


def run(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    finally:
        coroutine.close()


def test_precheckout_accepts_only_exact_pending_invoice(monkeypatch):
    order = NS(id="KV-ONE", tg_id=42, plan_id="m1", status=OrderStatus.PENDING)
    service = NS(get_request=Mock(return_value=order))
    monkeypatch.setattr(bot, "build_customer_order_service", lambda: service)
    query = NS(
        invoice_payload="karina:KV-ONE:42", from_user=NS(id=42),
        currency="XTR", total_amount=STAR_PRICES["m1"], answer=AsyncMock(),
    )
    run(bot.precheckout(NS(pre_checkout_query=query), NS()))
    query.answer.assert_awaited_once_with(ok=True)


def test_precheckout_rejects_wrong_stars_amount(monkeypatch):
    order = NS(id="KV-ONE", tg_id=42, plan_id="m1", status=OrderStatus.PENDING)
    monkeypatch.setattr(
        bot, "build_customer_order_service",
        lambda: NS(get_request=Mock(return_value=order)),
    )
    query = NS(
        invoice_payload="karina:KV-ONE:42", from_user=NS(id=42),
        currency="XTR", total_amount=STAR_PRICES["m1"] - 1, answer=AsyncMock(),
    )
    run(bot.precheckout(NS(pre_checkout_query=query), NS()))
    assert query.answer.await_args.kwargs["ok"] is False


def test_donation_precheckout_accepts_fixed_amount_without_subscription(monkeypatch):
    monkeypatch.setattr(
        bot, "build_customer_order_service",
        lambda: (_ for _ in ()).throw(AssertionError("subscription order accessed")),
    )
    query = NS(invoice_payload="karina-donation:100:42", from_user=NS(id=42),
               currency="XTR", total_amount=100, answer=AsyncMock())
    run(bot.precheckout(NS(pre_checkout_query=query), NS()))
    query.answer.assert_awaited_once_with(ok=True)


def test_successful_donation_does_not_provision_subscription(monkeypatch):
    monkeypatch.setattr(
        bot, "build_customer_order_service",
        lambda: (_ for _ in ()).throw(AssertionError("subscription order accessed")),
    )
    payment = NS(invoice_payload="karina-donation:250:42", currency="XTR",
                 total_amount=250, telegram_payment_charge_id="donation-charge-1")
    message = NS(successful_payment=payment, reply_text=AsyncMock())
    run(bot.successful_stars_payment(NS(message=message, effective_user=NS(id=42)), NS()))
    assert "Спасибо" in message.reply_text.await_args.args[0]


def test_successful_stars_payment_marks_paid_and_provisions(monkeypatch):
    order = NS(
        id="KV-ONE", tg_id=42, plan_id="m1", status=OrderStatus.PENDING,
        provider=None, provider_payment_id=None,
    )
    billing = NS(mark_paid=Mock())
    service = NS(get_request=Mock(return_value=order), billing=billing,
                 approve=Mock(return_value=(order, True)))
    monkeypatch.setattr(bot, "build_customer_order_service", lambda: service)
    delivery = AsyncMock()
    monkeypatch.setattr(bot, "deliver_payment_effects", delivery)
    payment = NS(
        invoice_payload="karina:KV-ONE:42", currency="XTR",
        total_amount=STAR_PRICES["m1"], telegram_payment_charge_id="stars-charge-1",
    )
    message = NS(successful_payment=payment, reply_text=AsyncMock())
    run(bot.successful_stars_payment(
        NS(message=message, effective_user=NS(id=42)), NS(),
    ))
    billing.mark_paid.assert_called_once_with("KV-ONE", "telegram_stars", "stars-charge-1")
    service.approve.assert_called_once_with("KV-ONE")
    delivery.assert_awaited_once()


def test_paid_order_recovery_retries_only_paid_orders(monkeypatch):
    paid = NS(id="KV-PAID", status=OrderStatus.PAID)
    pending = NS(id="KV-PENDING", status=OrderStatus.PENDING)
    repository = NS(recovery_orders=Mock(return_value=[paid, pending]))
    orders = NS(approve=Mock())
    monkeypatch.setattr(bot, "BillingRepository", lambda path: repository)
    monkeypatch.setattr(bot, "build_customer_order_service", lambda: orders)
    to_thread = AsyncMock(return_value=None)
    monkeypatch.setattr(bot.asyncio, "to_thread", to_thread)
    run(bot.recover_paid_payments(NS()))
    to_thread.assert_awaited_once_with(orders.approve, "KV-PAID")
