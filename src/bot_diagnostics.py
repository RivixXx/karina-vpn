import argparse
import asyncio
import sys

from .app_config import load_config
from .bot import ENV_FILE, load_env


async def membership_chat(bot, config, emit=print):
    me = await bot.get_me()
    chat = await bot.get_chat(config.required_tg_chat_id)
    membership = await bot.get_chat_member(config.required_tg_chat_id, me.id)
    raw_status = getattr(membership, "status", "unknown")
    status = str(getattr(raw_status, "value", raw_status)).lower().rsplit(".", 1)[-1]
    capable = status.lower() in {"creator", "administrator"}
    emit(f"Bot: @{getattr(me, 'username', '')}")
    emit(f"Chat: {getattr(chat, 'title', '')}")
    emit(f"Chat ID: {getattr(chat, 'id', '')}")
    emit(f"Bot membership: {status}")
    emit(f"Membership check capability: {'OK' if capable else 'FAIL'}")
    return 0 if capable else 1


def main(argv=None):
    from telegram import Bot
    parser = argparse.ArgumentParser(prog="karina-bot-diagnostics")
    parser.add_argument("command", choices=["membership-chat"])
    args = parser.parse_args(argv)
    try:
        config = load_config()
        env = load_env(ENV_FILE)
        bot = Bot(env["BOT_TOKEN"])
        if args.command == "membership-chat":
            return asyncio.run(membership_chat(bot, config))
    except Exception as exc:
        print(f"Membership diagnostic: FAIL ({type(exc).__name__})", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
