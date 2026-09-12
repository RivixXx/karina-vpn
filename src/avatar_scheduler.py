import json
import logging
import asyncio
from contextlib import suppress
from dataclasses import dataclass
from datetime import date, datetime, time as clock, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram.ext import Application


LOGGER = logging.getLogger(__name__)
TIMEZONE = ZoneInfo("Europe/Moscow")
HOLIDAYS = {
    (2, 14): "st_valentine_day",
    (2, 23): "defender_of_the_Fatherland_Day",
    (3, 8): "womens_day",
    (4, 12): "cosmonautics_day",
    (5, 9): "victory_day",
    (6, 12): "russian_day",
    (9, 1): "day_of_knowledge",
    (10, 31): "halloween_day",
}
FILENAMES = {
    "main": "main.png", "spring": "spring.png", "summer": "summer.png",
    "autumn": "autumn.png", "winter": "winter.png", "new_year": "new_year.png",
    "russian_day": "russian_day.png", "victory_day": "victory_day.png",
    "womens_day": "woomens_day.png", "st_valentine_day": "st_valentine_day.png",
    "defender_of_the_Fatherland_Day": "defender_of_the_Fatherland_Day.png",
    "day_of_knowledge": "day_of_knowledge.png",
    "cosmonautics_day": "cosmonautics_day.png", "halloween_day": "halloween_day.png",
    "TG_day": "TG_day.png",
}
SCHEDULER_TASK_KEY = "karina_avatar_scheduler_task"


@dataclass(frozen=True)
class AvatarSelection:
    key: str | None
    path: Path | None
    rule: str


def seasonal_key(day):
    if 3 <= day.month <= 5:
        return "spring"
    if 6 <= day.month <= 8:
        return "summer"
    if 9 <= day.month <= 11:
        return "autumn"
    return "winter"


def desired_key(day: date):
    if (day.month == 12 and day.day >= 20) or (day.month == 1 and day.day <= 8):
        return "new_year", "holiday new_year"
    key = HOLIDAYS.get((day.month, day.day))
    return (key, f"holiday {key}") if key else (seasonal_key(day), f"seasonal {seasonal_key(day)}")


def select_avatar(day, directory):
    directory = Path(directory)
    key, rule = desired_key(day)
    candidate = directory / FILENAMES[key]
    if candidate.is_file():
        return AvatarSelection(key, candidate, rule)
    season = seasonal_key(day)
    candidate = directory / FILENAMES[season]
    if candidate.is_file():
        return AvatarSelection(season, candidate, f"fallback seasonal {season}")
    candidate = directory / FILENAMES["main"]
    if candidate.is_file():
        return AvatarSelection("main", candidate, "fallback main")
    return AvatarSelection(None, None, "no avatar file available")


def load_state(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}


def save_state(path, state):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


async def _set_bot_photo(bot, path):
    method = getattr(bot, "set_my_profile_photo", None)
    from telegram import InputFile
    with path.open("rb") as stream:
        if method is not None:
            from telegram import InputProfilePhotoStatic
            await method(InputProfilePhotoStatic(photo=InputFile(stream)))
        else:
            # PTB 20.8 predates the public wrapper for Bot API setMyProfilePhoto.
            image = InputFile(stream, filename=path.name, attach=True)
            await bot._post("setMyProfilePhoto", data={
                "photo": json.dumps({"type": "static", "photo": image.attach_uri}),
                "avatar_file": image,
            })
    return True


async def apply_avatar(bot, config, *, day=None):
    day = day or __import__("datetime").datetime.now(TIMEZONE).date()
    selection = select_avatar(day, config.avatar_dir)
    state = load_state(config.avatar_state_file)
    if selection.path is None:
        LOGGER.warning("No applicable avatar image found")
        return selection
    changed = False
    if state.get("bot") != selection.key:
        try:
            if await _set_bot_photo(bot, selection.path):
                state["bot"] = selection.key
                changed = True
        except Exception:
            LOGGER.warning("Unable to update Telegram bot avatar", exc_info=True)
    if config.avatar_chat_id is not None and state.get("chat") != selection.key:
        try:
            with selection.path.open("rb") as stream:
                await bot.set_chat_photo(config.avatar_chat_id, photo=stream)
            state["chat"] = selection.key
            changed = True
        except Exception:
            LOGGER.warning("Unable to update Telegram chat avatar; check administrator rights", exc_info=True)
    if changed:
        try:
            save_state(config.avatar_state_file, state)
        except OSError:
            LOGGER.warning("Unable to persist avatar scheduler state", exc_info=True)
    return selection


async def scheduler_loop(application):
    await apply_avatar(application.bot, application.bot_data["config"])
    while True:
        now = datetime.now(TIMEZONE)
        next_run = datetime.combine(now.date(), clock(0, 5), TIMEZONE)
        if next_run <= now:
            next_run += timedelta(days=1)
        await asyncio.sleep((next_run - now).total_seconds())
        await apply_avatar(application.bot, application.bot_data["config"])


def start_scheduler(application):
    if not application.running:
        raise RuntimeError("avatar scheduler requires a running PTB Application")
    task = application.create_task(
        scheduler_loop(application), name="karina-avatar-scheduler",
    )
    application.bot_data[SCHEDULER_TASK_KEY] = task
    return task


async def stop_scheduler(application):
    task = application.bot_data.pop(SCHEDULER_TASK_KEY, None)
    if task is None:
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


class AvatarApplication(Application):
    async def start(self):
        await super().start()
        start_scheduler(self)

    async def stop(self):
        await stop_scheduler(self)
        await super().stop()
