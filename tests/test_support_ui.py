from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from src import bot
from src.ui.support import FAQS, faq_view, support_home_view


def run(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    finally:
        coroutine.close()
    raise AssertionError("unexpected asynchronous I/O")


def callbacks(markup):
    return [button.callback_data for row in markup.inline_keyboard for button in row
            if getattr(button, "callback_data", None)]


def urls(markup):
    return [button.url for row in markup.inline_keyboard for button in row
            if getattr(button, "url", None)]


def update():
    return NS(effective_chat=NS(id=2), effective_user=NS(id=2),
              callback_query=NS(message=NS(message_id=1), edit_message_text=AsyncMock()))


def test_support_home_has_all_faqs_operator_and_no_fake_kb():
    text, keyboard = support_home_view("https://t.me/support")
    assert "ПОДДЕРЖКА И ЧАСТЫЕ ВОПРОСЫ" in text
    assert callbacks(keyboard) == [
        "support_faq:network", "support_faq:antiblock", "support_faq:devices",
        "support_faq:payment", "support_faq:speed", "support_faq:quicklaunch",
        "support_faq:transfer", "client_home",
    ]
    assert urls(keyboard) == ["https://t.me/support"]


def test_missing_operator_is_safe_and_configured_kb_is_optional():
    text, keyboard = support_home_view(None, "https://kb.example.test/karina")
    assert "временно недоступна" in text
    assert urls(keyboard) == ["https://kb.example.test/karina"]


@pytest.mark.parametrize("slug", tuple(FAQS))
def test_all_faq_details_route_back_to_support(slug):
    text, keyboard = faq_view(
        slug, support_url="https://t.me/support",
        mobile_url="https://mobile.example.test/sub", device_slots=(2, 4),
    )
    assert text.startswith("❓")
    assert callbacks(keyboard)[-1] == "client_support"


def test_faq_actions_reuse_existing_flows_and_urls():
    _, network = faq_view("network", support_url="https://t.me/support",
                          mobile_url="https://mobile.example.test/sub")
    assert urls(network) == ["https://mobile.example.test/sub", "https://t.me/support"]
    _, devices = faq_view("devices", device_slots=(2, 4))
    assert callbacks(devices) == ["client_connect", "client_devices", "connect_help", "client_support"]
    _, payment = faq_view("payment", pending_order=NS())
    assert "ожидает подтверждения" in _
    assert callbacks(payment) == ["client_pay", "client_support"]
    _, quick = faq_view("quicklaunch")
    assert callbacks(quick) == ["connect_help", "client_support"]


def test_support_home_uses_seventh_sticker_and_compound(monkeypatch):
    monkeypatch.setattr(bot, "CUSTOMER_CONFIG", NS())
    monkeypatch.setattr(bot, "SUPPORT_URL", "https://t.me/support")
    resolver = AsyncMock(return_value="sticker-6")
    compound = AsyncMock()
    monkeypatch.setattr(bot, "resolve_sticker", resolver)
    monkeypatch.setattr(bot, "show_compound", compound)
    context = NS(user_data={})
    run(bot.render_support_home(update(), context))
    resolver.assert_awaited_once_with(context, 6)
    assert compound.await_args.kwargs["screen_key"] == "support"
    assert compound.await_args.kwargs["sticker"] == "sticker-6"


def test_support_resolver_failure_still_renders_text(monkeypatch):
    monkeypatch.setattr(bot, "CUSTOMER_CONFIG", NS())
    monkeypatch.setattr(bot, "SUPPORT_URL", None)
    monkeypatch.setattr(bot, "resolve_sticker", AsyncMock(return_value=None))
    compound = AsyncMock()
    monkeypatch.setattr(bot, "show_compound", compound)
    run(bot.render_support_home(update(), NS(user_data={})))
    assert compound.await_args.kwargs["sticker"] is None
    assert "ПОДДЕРЖКА" in compound.await_args.kwargs["text"]
