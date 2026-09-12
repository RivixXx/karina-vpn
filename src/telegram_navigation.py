import logging

LOGGER = logging.getLogger(__name__)
UI_MESSAGE_KEY = "karina_ui_message_id"
UI_KIND_KEY = "karina_ui_kind"
UI_SCREEN_KEY = "karina_ui_screen"


def _not_modified(exc):
    return "message is not modified" in str(exc).lower()


async def _delete_message(context, chat_id, message_id):
    if message_id is None or chat_id is None:
        return
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        LOGGER.warning("Unable to remove previous Karina UI message", exc_info=True)


async def clear_screen(update, context, *, exclude=()):
    chat_id = getattr(getattr(update, "effective_chat", None), "id", None)
    screen = context.user_data.get(UI_SCREEN_KEY, {})
    ids = list(screen.get("message_ids", ()))
    query_message = getattr(getattr(update, "callback_query", None), "message", None)
    query_id = getattr(query_message, "message_id", None)
    if not ids and query_id is not None:
        ids = [query_id]
    for message_id in dict.fromkeys(ids):
        if message_id not in exclude:
            await _delete_message(context, chat_id, message_id)


def _remember(context, message, kind):
    if message is not None and getattr(message, "message_id", None) is not None:
        context.user_data[UI_MESSAGE_KEY] = message.message_id
    context.user_data[UI_KIND_KEY] = kind
    message_id = getattr(message, "message_id", None)
    context.user_data[UI_SCREEN_KEY] = {
        "screen_key": None, "message_ids": [message_id] if message_id is not None else [],
        "primary_message_id": message_id, "content_type": kind,
    }


def _current_kind(update, context):
    remembered = context.user_data.get(UI_KIND_KEY)
    if remembered:
        return remembered
    message = getattr(getattr(update, "callback_query", None), "message", None)
    if getattr(message, "video", None):
        return "video"
    if getattr(message, "photo", None):
        return "photo"
    return "text"


def current_screen(context):
    return context.user_data.get(UI_SCREEN_KEY)


async def show_text(update, context, text, *, reply_markup=None, parse_mode=None):
    query = getattr(update, "callback_query", None)
    if query and _current_kind(update, context) == "text":
        try:
            message = await query.edit_message_text(
                text, reply_markup=reply_markup, parse_mode=parse_mode,
            )
            _remember(context, message or query.message, "text")
            return message
        except Exception as exc:
            if _not_modified(exc):
                _remember(context, query.message, "text")
                return query.message
            raise
    remembered_id = context.user_data.get(UI_MESSAGE_KEY)
    if not query and remembered_id is not None:
        try:
            message = await context.bot.edit_message_text(
                chat_id=update.effective_chat.id, message_id=remembered_id,
                text=text, reply_markup=reply_markup, parse_mode=parse_mode,
            )
            _remember(context, message, "text")
            return message
        except Exception as exc:
            if _not_modified(exc):
                return None
            LOGGER.info("Previous Karina UI message is unavailable; creating a new one")
    if query:
        message = await context.bot.send_message(
            chat_id=update.effective_chat.id, text=text,
            reply_markup=reply_markup, parse_mode=parse_mode,
        )
        await clear_screen(update, context)
    else:
        message = await update.message.reply_text(
            text, reply_markup=reply_markup, parse_mode=parse_mode,
        )
    _remember(context, message, "text")
    return message


async def show_photo(update, context, photo, *, caption=None, reply_markup=None):
    message = await context.bot.send_photo(
        chat_id=update.effective_chat.id, photo=photo,
        caption=caption, reply_markup=reply_markup,
    )
    if getattr(update, "callback_query", None):
        await clear_screen(update, context)
    _remember(context, message, "photo")
    return message


async def show_video(update, context, video, *, caption=None, reply_markup=None):
    message = await context.bot.send_video(
        chat_id=update.effective_chat.id, video=video,
        caption=caption, reply_markup=reply_markup,
    )
    if getattr(update, "callback_query", None):
        await clear_screen(update, context)
    _remember(context, message, "video")
    return message


async def show_compound(update, context, *, screen_key, text, reply_markup=None,
                        sticker=None, parse_mode=None):
    existing = current_screen(context) or {}
    query = getattr(update, "callback_query", None)
    if (existing.get("screen_key") == screen_key
            and existing.get("content_type") == "compound"):
        primary = existing.get("primary_message_id")
        try:
            if query and getattr(query.message, "message_id", None) == primary:
                message = await query.edit_message_text(
                    text, reply_markup=reply_markup, parse_mode=parse_mode,
                )
            else:
                message = await context.bot.edit_message_text(
                    chat_id=update.effective_chat.id, message_id=primary, text=text,
                    reply_markup=reply_markup, parse_mode=parse_mode,
                )
            return message
        except Exception as exc:
            if _not_modified(exc):
                return None
            LOGGER.warning("Unable to refresh compound Karina UI screen", exc_info=True)

    old_ids = tuple(existing.get("message_ids", ()))
    sticker_message = None
    if sticker:
        try:
            sticker_message = await context.bot.send_sticker(
                chat_id=update.effective_chat.id, sticker=sticker,
            )
        except Exception:
            LOGGER.warning("Unable to display Karina cabinet sticker", exc_info=True)
    menu_message = await context.bot.send_message(
        chat_id=update.effective_chat.id, text=text,
        reply_markup=reply_markup, parse_mode=parse_mode,
    )
    new_ids = [getattr(item, "message_id", None)
               for item in (sticker_message, menu_message) if item is not None]
    new_ids = [value for value in new_ids if value is not None]
    for message_id in old_ids:
        if message_id not in new_ids:
            await _delete_message(context, update.effective_chat.id, message_id)
    if not old_ids and query:
        query_id = getattr(query.message, "message_id", None)
        if query_id not in new_ids:
            await _delete_message(context, update.effective_chat.id, query_id)
    primary = getattr(menu_message, "message_id", None)
    context.user_data[UI_MESSAGE_KEY] = primary
    context.user_data[UI_KIND_KEY] = "compound"
    context.user_data[UI_SCREEN_KEY] = {
        "screen_key": screen_key, "message_ids": new_ids,
        "primary_message_id": primary, "content_type": "compound",
    }
    return menu_message
