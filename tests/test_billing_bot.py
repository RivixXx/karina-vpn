from types import SimpleNamespace as NS
from types import ModuleType
from unittest.mock import AsyncMock, Mock
import sys


class Button:
    def __init__(self, text, **kwargs):
        self.text = text
        self.__dict__.update(kwargs)
        self.callback_data = kwargs.get("callback_data")


class Markup:
    def __init__(self, rows):
        self.inline_keyboard = rows


telegram = ModuleType("telegram")
telegram.InlineKeyboardButton = Button
telegram.InlineKeyboardMarkup = Markup
telegram.Update = type("Update", (), {"ALL_TYPES": ()})
constants = ModuleType("telegram.constants")
constants.ChatType = NS(PRIVATE="private")
constants.ParseMode = NS(MARKDOWN_V2="MarkdownV2")
extension = ModuleType("telegram.ext")
extension.Application = type("Application", (), {})
extension.CallbackQueryHandler = type("CallbackQueryHandler", (), {})
extension.CommandHandler = type("CommandHandler", (), {})
extension.ContextTypes = NS(DEFAULT_TYPE=object)
sys.modules.setdefault("telegram", telegram)
sys.modules.setdefault("telegram.constants", constants)
sys.modules.setdefault("telegram.ext", extension)

from src import bot
from src.models import Order, OrderStatus, PLANS


def run(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    finally:
        coroutine.close()
    raise AssertionError("Unexpected asynchronous I/O")


def update(data, user_id=10):
    return NS(
        effective_chat=NS(type="private"), effective_user=NS(id=user_id),
        callback_query=NS(data=data, answer=AsyncMock(), edit_message_text=AsyncMock()),
    )


def pending(order_id="KV-SYNTHETIC", tg_id=10, status=OrderStatus.PENDING):
    return Order(order_id, tg_id, "synthetic_user", "m3", 90, 499, status,
                 None, None, 100, None, 200 if status is OrderStatus.COMPLETED else None)


async def invoke(monkeypatch, data, billing, user_id=10):
    monkeypatch.setattr(bot, "get_link_by_tg", lambda unused: {"email": "synthetic_user"})
    monkeypatch.setattr(bot, "build_billing_service", lambda: billing)
    item = update(data, user_id)
    await bot.callbacks(item, NS(user_data={}))
    return item


def test_plan_keyboard_and_callback_limit():
    keyboard = bot.billing_plans_keyboard(PLANS)
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row
                 if button.callback_data]
    assert len(callbacks) == 5
    assert all(len(value.encode("utf-8")) <= 64 for value in callbacks)
    order_keyboard = bot.billing_order_keyboard(pending("KV-" + "A" * 16))
    assert all(len(button.callback_data.encode("utf-8")) <= 64
               for row in order_keyboard.inline_keyboard for button in row)


def test_select_plan_creates_pending_order(monkeypatch):
    billing = NS(
        list_plans=Mock(return_value=list(PLANS)),
        create_order=Mock(return_value=pending()),
    )
    item = run(invoke(monkeypatch, "bill_plan:m3", billing))
    billing.create_order.assert_called_once_with(10, "synthetic_user", "m3")
    assert "499" in item.callback_query.edit_message_text.call_args.args[0]


def test_user_cannot_open_another_users_order(monkeypatch):
    billing = NS(get_order=Mock(return_value=pending(tg_id=99)))
    item = run(invoke(monkeypatch, "bill_pay:KV-SYNTHETIC", billing))
    assert item.callback_query.answer.await_args.kwargs["show_alert"] is True
    item.callback_query.edit_message_text.assert_not_awaited()


def test_cancel_pending_order(monkeypatch):
    order = pending()
    billing = NS(get_order=Mock(return_value=order), cancel_order=Mock(return_value=order))
    item = run(invoke(monkeypatch, "bill_cancel:KV-SYNTHETIC", billing))
    billing.cancel_order.assert_called_once_with("KV-SYNTHETIC", 10)
    assert "отменён" in item.callback_query.edit_message_text.call_args.args[0]


def test_payment_is_safe_placeholder_and_completed_status_is_visible(monkeypatch):
    billing = NS(get_order=Mock(return_value=pending()))
    item = run(invoke(monkeypatch, "bill_pay:KV-SYNTHETIC", billing))
    assert "провайдер ещё не" in item.callback_query.edit_message_text.call_args.args[0]
    billing.get_order.return_value = pending(status=OrderStatus.COMPLETED)
    item = run(invoke(monkeypatch, "bill_pay:KV-SYNTHETIC", billing))
    assert "выполнен" in item.callback_query.edit_message_text.call_args.args[0]


def test_bot_has_no_manual_payment_confirmation():
    source = __import__("pathlib").Path("src/bot.py").read_text(encoding="utf-8")
    assert "Я оплатил" not in source
    assert "mark_paid(" not in source
