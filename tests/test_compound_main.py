from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

from src import bot


def run(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    finally:
        coroutine.close()


def test_registered_cabinet_is_sticker_and_menu_compound(monkeypatch):
    monkeypatch.setattr(bot, "ParseMode", NS(HTML="HTML", MARKDOWN_V2="MarkdownV2"))
    primary = NS(expiry_time_ms=2_000_000_000_000, expiry_text="18.05.2033 06:33",
                 enabled=True, device_limit=4, device_count=2)
    bundle = NS(primary=primary, mobile=NS())
    service = NS(get_client_bundle=lambda email: bundle, get_mobile_traffic=lambda email: None)
    monkeypatch.setattr(bot, "build_client_service", lambda: service)
    monkeypatch.setattr(bot, "CUSTOMER_CONFIG", NS(news_channel_url=None))
    api = NS(
        get_sticker_set=AsyncMock(return_value=NS(stickers=[NS(file_id="first"), NS(file_id="second")])),
        send_sticker=AsyncMock(return_value=NS(message_id=20)),
        send_message=AsyncMock(return_value=NS(message_id=21)),
        delete_message=AsyncMock(),
    )
    context = NS(bot=api, user_data={}, application=NS(bot_data={}))
    update = NS(callback_query=None, message=NS(reply_text=AsyncMock()),
                effective_chat=NS(id=44), effective_user=NS(id=44))
    run(bot.render_client_home(update, "fixture", context))
    assert context.user_data["karina_ui_screen"] == {
        "screen_key": "main", "message_ids": [20, 21],
        "primary_message_id": 21, "content_type": "compound",
    }
    api.get_sticker_set.assert_awaited_once_with("KarinaVPN")
    api.send_sticker.assert_awaited_once_with(chat_id=44, sticker="first")


def test_registered_cabinet_survives_sticker_api_failure(monkeypatch):
    monkeypatch.setattr(bot, "ParseMode", NS(HTML="HTML", MARKDOWN_V2="MarkdownV2"))
    primary = NS(expiry_time_ms=0, expiry_text="∞", enabled=True,
                 device_limit=4, device_count=0)
    service = NS(get_client_bundle=lambda email: NS(primary=primary, mobile=None),
                 get_mobile_traffic=lambda email: None)
    monkeypatch.setattr(bot, "build_client_service", lambda: service)
    monkeypatch.setattr(bot, "CUSTOMER_CONFIG", NS(news_channel_url=None))
    api = NS(get_sticker_set=AsyncMock(side_effect=RuntimeError("api")),
             send_message=AsyncMock(return_value=NS(message_id=21)),
             send_sticker=AsyncMock(), delete_message=AsyncMock())
    context = NS(bot=api, user_data={}, application=NS(bot_data={}))
    update = NS(callback_query=None, message=NS(reply_text=AsyncMock()),
                effective_chat=NS(id=44), effective_user=NS(id=44))
    run(bot.render_client_home(update, "fixture", context))
    api.send_sticker.assert_not_awaited()
    api.send_message.assert_awaited_once()
