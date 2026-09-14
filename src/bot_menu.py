"""Public Telegram command menu shared by all private chats."""
import logging

from telegram import BotCommand, BotCommandScopeDefault, BotCommandScopeAllPrivateChats, MenuButtonCommands

LOGGER = logging.getLogger(__name__)
COMMANDS = (
    ("start", "Открыть главное меню"),
    ("support", "Связаться с поддержкой"),
    ("terms", "Условия оказания услуги"),
    ("refunds", "Порядок возврата"),
    ("privacy", "Персональные данные"),
    ("paysupport", "Помощь с оплатой"),
)


async def configure_menu(bot):
    commands = [BotCommand(name, description) for name, description in COMMANDS]
    for scope in (BotCommandScopeDefault(), BotCommandScopeAllPrivateChats()):
        for language in ("", "ru"):
            try:
                await bot.set_my_commands(commands, scope=scope, language_code=language)
            except Exception:
                LOGGER.warning("Unable to configure Telegram commands", exc_info=True)
    try:
        await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    except Exception:
        LOGGER.warning("Unable to configure Telegram menu button", exc_info=True)
