from datetime import date
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

import pytest

from src.avatar_scheduler import _set_bot_photo, apply_avatar, desired_key, select_avatar


def run(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    finally:
        coroutine.close()


@pytest.mark.parametrize(("day", "key"), [
    (date(2026, 9, 12), "autumn"), (date(2026, 7, 15), "summer"),
    (date(2026, 1, 15), "winter"), (date(2026, 4, 10), "spring"),
    (date(2026, 2, 14), "st_valentine_day"),
    (date(2026, 2, 23), "defender_of_the_Fatherland_Day"),
    (date(2026, 3, 8), "womens_day"), (date(2026, 4, 12), "cosmonautics_day"),
    (date(2026, 5, 9), "victory_day"), (date(2026, 6, 12), "russian_day"),
    (date(2026, 9, 1), "day_of_knowledge"),
    (date(2026, 10, 31), "halloween_day"),
    (date(2026, 12, 20), "new_year"), (date(2026, 1, 1), "new_year"),
    (date(2026, 1, 9), "winter"),
])
def test_calendar_rules(day, key):
    assert desired_key(day)[0] == key


def test_missing_files_fall_back_to_season_then_main(tmp_path):
    (tmp_path / "autumn.png").write_bytes(b"x")
    assert select_avatar(date(2026, 10, 31), tmp_path).key == "autumn"
    (tmp_path / "autumn.png").unlink()
    (tmp_path / "main.png").write_bytes(b"x")
    assert select_avatar(date(2026, 10, 31), tmp_path).key == "main"
    (tmp_path / "main.png").unlink()
    assert select_avatar(date(2026, 10, 31), tmp_path).key is None


def test_ptb_20_compatibility_uses_exact_bot_api_method(tmp_path, monkeypatch):
    path = tmp_path / "avatar.png"
    path.write_bytes(b"image")
    class InputFile:
        def __init__(self, stream, filename=None, attach=False):
            self.attach_uri = "attach://fixture" if attach else None
    import telegram
    monkeypatch.setattr(telegram, "InputFile", InputFile, raising=False)
    bot = NS(_post=AsyncMock())
    assert run(_set_bot_photo(bot, path)) is True
    call = bot._post.await_args
    assert call.args[0] == "setMyProfilePhoto"
    assert '"type": "static"' in call.kwargs["data"]["photo"]


def test_repeated_run_is_idempotent_and_failures_are_isolated(tmp_path):
    (tmp_path / "autumn.png").write_bytes(b"image")
    config = NS(avatar_dir=tmp_path, avatar_state_file=tmp_path / "state.json",
                avatar_chat_id=-1001)
    bot = NS(set_chat_photo=AsyncMock())
    with patch("src.avatar_scheduler._set_bot_photo", AsyncMock(return_value=True)) as set_bot:
        run(apply_avatar(bot, config, day=date(2026, 9, 12)))
        run(apply_avatar(bot, config, day=date(2026, 9, 12)))
    set_bot.assert_awaited_once()
    bot.set_chat_photo.assert_awaited_once()

    config.avatar_state_file.unlink()
    bot.set_chat_photo = AsyncMock(side_effect=RuntimeError("insufficient rights"))
    with patch("src.avatar_scheduler._set_bot_photo", AsyncMock(side_effect=RuntimeError("api"))):
        assert run(apply_avatar(bot, config, day=date(2026, 9, 12))).key == "autumn"
