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
        _post=AsyncMock(return_value={"stickers": [
            {"file_id": "first"}, {"file_id": "second"},
        ]}),
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
    api._post.assert_awaited_once_with("getStickerSet", data={"name": "KarinaVPN"})
    api.send_sticker.assert_awaited_once_with(chat_id=44, sticker="first")


def test_registered_cabinet_survives_sticker_api_failure(monkeypatch):
    monkeypatch.setattr(bot, "ParseMode", NS(HTML="HTML", MARKDOWN_V2="MarkdownV2"))
    primary = NS(expiry_time_ms=0, expiry_text="∞", enabled=True,
                 device_limit=4, device_count=0)
    service = NS(get_client_bundle=lambda email: NS(primary=primary, mobile=None),
                 get_mobile_traffic=lambda email: None)
    monkeypatch.setattr(bot, "build_client_service", lambda: service)
    monkeypatch.setattr(bot, "CUSTOMER_CONFIG", NS(news_channel_url=None))
    api = NS(_post=AsyncMock(side_effect=RuntimeError("api")),
             send_message=AsyncMock(return_value=NS(message_id=21)),
             send_sticker=AsyncMock(), delete_message=AsyncMock())
    context = NS(bot=api, user_data={}, application=NS(bot_data={}))
    update = NS(callback_query=None, message=NS(reply_text=AsyncMock()),
                effective_chat=NS(id=44), effective_user=NS(id=44))
    run(bot.render_client_home(update, "fixture", context))
    api.send_sticker.assert_not_awaited()
    api.send_message.assert_awaited_once()


def _registered_start(monkeypatch, sticker_result):
    monkeypatch.setattr(bot, "ParseMode", NS(HTML="HTML", MARKDOWN_V2="MarkdownV2"))
    monkeypatch.setattr(bot, "ADMIN_TG_ID", 1)
    monkeypatch.setattr(bot, "REQUIRED_MEMBERSHIP_MODE", "new_users")
    monkeypatch.setattr(bot, "CUSTOMER_CONFIG", NS(news_channel_url=None))
    monkeypatch.setattr(bot, "get_link_by_tg", lambda tg_id: {"email": "fixture"})
    primary = NS(expiry_time_ms=0, expiry_text="∞", enabled=True,
                 device_limit=4, device_count=0)
    service = NS(get_client_bundle=lambda email: NS(primary=primary, mobile=None),
                 get_mobile_traffic=lambda email: None)
    monkeypatch.setattr(bot, "build_client_service", lambda: service)
    raw = AsyncMock(side_effect=sticker_result) if isinstance(sticker_result, Exception) else AsyncMock(return_value=sticker_result)
    api = NS(_post=raw, send_message=AsyncMock(return_value=NS(message_id=21)),
             send_sticker=AsyncMock(return_value=NS(message_id=20)),
             delete_message=AsyncMock())
    context = NS(bot=api, user_data={}, application=NS(bot_data={}), args=[])
    update = NS(callback_query=None, message=NS(reply_text=AsyncMock()),
                effective_chat=NS(id=44, type="private"), effective_user=NS(id=44))
    run(bot.start(update, context))
    return api


def test_registered_non_admin_start_shows_sticker_and_cabinet(monkeypatch):
    api = _registered_start(monkeypatch, {"stickers": [{"file_id": "first"}]})
    api.send_sticker.assert_awaited_once_with(chat_id=44, sticker="first")
    assert "Личный кабинет" in api.send_message.await_args.kwargs["text"]


def test_registered_non_admin_start_sticker_error_still_shows_cabinet(monkeypatch):
    api = _registered_start(monkeypatch, RuntimeError("telegram"))
    api.send_sticker.assert_not_awaited()
    assert "Личный кабинет" in api.send_message.await_args.kwargs["text"]
