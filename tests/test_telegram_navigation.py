from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from src.telegram_navigation import current_screen, show_compound, show_photo, show_text, show_video


def run(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    finally:
        coroutine.close()


def fixture(kind="text"):
    message = NS(message_id=10, photo=[] if kind != "photo" else [object()],
                 video=None if kind != "video" else object())
    query = NS(message=message, edit_message_text=AsyncMock(return_value=message))
    bot = NS(delete_message=AsyncMock(), send_message=AsyncMock(return_value=NS(message_id=11)),
             send_photo=AsyncMock(return_value=NS(message_id=12)),
             send_video=AsyncMock(return_value=NS(message_id=13)),
             send_sticker=AsyncMock(return_value=NS(message_id=14)),
             edit_message_text=AsyncMock(return_value=NS(message_id=11)))
    return NS(callback_query=query, effective_chat=NS(id=1)), NS(bot=bot, user_data={})


def test_text_navigation_edits_single_screen_and_not_modified_is_noop():
    update, context = fixture()
    run(show_text(update, context, "devices"))
    context.bot.send_message.assert_not_awaited()
    update.callback_query.edit_message_text.side_effect = RuntimeError("Message is not modified")
    run(show_text(update, context, "devices"))


def test_text_media_transitions_replace_current_ui_message():
    update, context = fixture()
    run(show_photo(update, context, "qr.png"))
    context.bot.delete_message.assert_awaited_once_with(chat_id=1, message_id=10)
    run(show_text(update, context, "home"))
    assert context.bot.delete_message.await_count == 2
    context.bot.send_message.assert_awaited_once()
    run(show_video(update, context, "video.mp4"))
    assert context.bot.delete_message.await_count == 3
    context.bot.send_video.assert_awaited_once()


def test_compound_screen_tracks_sticker_and_menu_and_clears_both():
    update, context = fixture()
    run(show_compound(update, context, screen_key="main", sticker="file-id", text="cabinet"))
    screen = current_screen(context)
    assert screen == {
        "screen_key": "main", "message_ids": [14, 11],
        "primary_message_id": 11, "content_type": "compound",
    }
    run(show_text(update, context, "devices"))
    deleted = [item.kwargs["message_id"] for item in context.bot.delete_message.await_args_list]
    assert deleted == [10, 14, 11]


def test_compound_refresh_edits_menu_without_new_sticker():
    update, context = fixture()
    run(show_compound(update, context, screen_key="main", sticker="file-id", text="cabinet"))
    update.callback_query.message.message_id = 11
    run(show_compound(update, context, screen_key="main", sticker="file-id", text="refreshed"))
    context.bot.send_sticker.assert_awaited_once()
    assert context.bot.send_message.await_count == 1


def test_compound_sticker_failure_still_shows_menu():
    update, context = fixture()
    context.bot.send_sticker.side_effect = RuntimeError("api unavailable")
    run(show_compound(update, context, screen_key="main", sticker="file-id", text="cabinet"))
    assert current_screen(context)["message_ids"] == [11]


def test_compound_delete_failure_is_safe():
    update, context = fixture()
    context.bot.delete_message.side_effect = RuntimeError("already deleted")
    run(show_compound(update, context, screen_key="main", sticker="file-id", text="cabinet"))
    run(show_text(update, context, "devices"))
    assert context.bot.send_message.await_count == 2


def test_main_to_connection_replaces_both_compound_messages():
    update, context = fixture()
    context.bot.send_sticker.side_effect = [NS(message_id=14), NS(message_id=24)]
    context.bot.send_message.side_effect = [NS(message_id=11), NS(message_id=21)]
    run(show_compound(update, context, screen_key="main", sticker="main", text="cabinet"))
    update.callback_query.message.message_id = 11
    run(show_compound(
        update, context, screen_key="connection", sticker="connection", text="links",
    ))
    deleted = [item.kwargs["message_id"] for item in context.bot.delete_message.await_args_list]
    assert deleted == [10, 14, 11]
    assert current_screen(context)["screen_key"] == "connection"
    assert current_screen(context)["message_ids"] == [24, 21]


@pytest.mark.parametrize("media", ["photo", "video"])
def test_connection_media_back_recreates_one_connection_compound(media):
    update, context = fixture()
    run(show_compound(
        update, context, screen_key="connection", sticker="connection", text="links",
    ))
    update.callback_query.message.message_id = 11
    if media == "photo":
        run(show_photo(update, context, "qr.png"))
        media_id = 12
    else:
        run(show_video(update, context, "video.mp4"))
        media_id = 13
    update.callback_query.message.message_id = media_id
    context.bot.send_sticker.return_value = NS(message_id=24)
    context.bot.send_message.return_value = NS(message_id=21)
    run(show_compound(
        update, context, screen_key="connection", sticker="connection", text="links",
    ))
    assert current_screen(context)["screen_key"] == "connection"
    assert current_screen(context)["message_ids"] == [24, 21]
    assert context.bot.send_sticker.await_count == 2


def test_repeated_connection_open_refreshes_without_duplicate_sticker():
    update, context = fixture()
    run(show_compound(
        update, context, screen_key="connection", sticker="connection", text="links",
    ))
    update.callback_query.message.message_id = 11
    run(show_compound(
        update, context, screen_key="connection", sticker="connection", text="fresh links",
    ))
    context.bot.send_sticker.assert_awaited_once()
    context.bot.send_message.assert_awaited_once()


def test_compound_to_confirmation_text_removes_sticker_and_menu():
    update, context = fixture()
    run(show_compound(update, context, screen_key="devices", sticker="help", text="devices"))
    update.callback_query.message.message_id = 11
    run(show_text(update, context, "confirm"))
    deleted = [item.kwargs["message_id"] for item in context.bot.delete_message.await_args_list]
    assert deleted == [10, 14, 11]
    context.bot.send_message.assert_awaited()


@pytest.mark.parametrize("kind", ["text", "compound"])
@pytest.mark.parametrize("old_edit_result", [None, "Message is not modified", "Message to edit not found"])
def test_command_after_history_clear_always_sends_visible_screen(kind, old_edit_result):
    update, context = fixture()
    update.callback_query = None
    update.message = NS(reply_text=AsyncMock(return_value=NS(message_id=30)))
    context.user_data.update(karina_ui_message_id=11, karina_ui_kind=kind,
        karina_ui_screen={"screen_key": "main", "content_type": kind,
                          "primary_message_id": 11, "message_ids": [14, 11]})
    if old_edit_result:
        context.bot.edit_message_text.side_effect = RuntimeError(old_edit_result)
    if kind == "compound":
        run(show_compound(update, context, screen_key="main", sticker="s", text="home"))
        context.bot.send_message.assert_awaited_once()
        context.bot.send_sticker.assert_awaited_once()
    else:
        run(show_text(update, context, "welcome"))
        update.message.reply_text.assert_awaited_once()
    context.bot.edit_message_text.assert_not_awaited()
    context.bot.delete_message.assert_not_awaited()
