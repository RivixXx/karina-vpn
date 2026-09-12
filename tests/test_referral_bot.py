from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock
import pytest

from src import bot


def run(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    finally:
        coroutine.close()
    raise AssertionError("unexpected asynchronous I/O")


def update(user_id=2):
    return NS(
        effective_user=NS(id=user_id, username="friend", first_name="Friend"),
        effective_chat=NS(id=user_id, type="private"), callback_query=NS(
            data="client_referral", message=NS(message_id=1),
            edit_message_text=AsyncMock(), answer=AsyncMock(),
        ), message=NS(reply_text=AsyncMock()),
    )


def test_referral_home_uses_index_14_real_stats_and_encoded_share(monkeypatch):
    repo = NS(stats=Mock(return_value={"invited": 2, "qualified": 1, "earned_days": 3}))
    service = NS(profile=Mock(return_value={"referral_code": "K7mP2x9Q"}), repository=repo)
    monkeypatch.setattr(bot, "build_referral_service", lambda: service)
    resolver = AsyncMock(return_value="sticker-14")
    screen = AsyncMock()
    monkeypatch.setattr(bot, "resolve_sticker", resolver)
    monkeypatch.setattr(bot, "show_compound", screen)
    context = NS(bot=NS(get_me=AsyncMock(return_value=NS(username="KarinaBot"))), user_data={})
    run(bot.render_referral_home(update(), context, 2))
    resolver.assert_awaited_once_with(context, 14)
    kwargs = screen.await_args.kwargs
    assert kwargs["screen_key"] == "referral" and kwargs["sticker"] == "sticker-14"
    assert "Приглашено: 2" in kwargs["text"] and "Заработано: 3 дней" in kwargs["text"]
    assert "https://t.me/KarinaBot?start=ref_K7mP2x9Q" in kwargs["text"]
    share = kwargs["reply_markup"].inline_keyboard[0][0].url
    assert share.startswith("https://t.me/share/url?") and "%3Fstart%3Dref_K7mP2x9Q" in share


def test_referral_sticker_failure_keeps_text_screen(monkeypatch):
    repo = NS(stats=Mock(return_value={"invited": 0, "qualified": 0, "earned_days": 0}))
    service = NS(profile=Mock(return_value={"referral_code": "safe_code"}), repository=repo)
    monkeypatch.setattr(bot, "build_referral_service", lambda: service)
    monkeypatch.setattr(bot, "resolve_sticker", AsyncMock(return_value=None))
    screen = AsyncMock()
    monkeypatch.setattr(bot, "show_compound", screen)
    context = NS(bot=NS(get_me=AsyncMock(return_value=NS(username="KarinaBot"))), user_data={})
    run(bot.render_referral_home(update(), context, 2))
    assert screen.await_args.kwargs["sticker"] is None
    assert "ПРИГЛАСИ ДРУГА" in screen.await_args.kwargs["text"]


def test_referral_list_and_reward_empty_states():
    assert "никого не пригласили" in bot.format_referral_list([])
    assert "бонусов пока нет" in bot.format_referral_rewards([])


def test_notification_failure_does_not_rollback_applied_reward(monkeypatch):
    reward = {"id": 7, "recipient_tg_id": 1, "target_expiry_ms": 1_800_000_000_000,
              "applied_at": 1, "notified_at": None}
    repository = NS(mark_notified=Mock())
    service = NS(qualify_after_first_successful_payment=Mock(return_value=reward),
                 repository=repository)
    monkeypatch.setattr(bot, "build_referral_service", lambda: service)
    monkeypatch.setattr(bot, "LOGGER", NS(warning=Mock()))
    context = NS(bot=NS(send_message=AsyncMock(side_effect=RuntimeError("telegram"))))
    with pytest.raises(RuntimeError):
        run(bot.apply_referral_reward(NS(), context))
    repository.mark_notified.assert_not_called()
    assert reward["applied_at"] == 1


def test_successful_notification_is_recorded(monkeypatch):
    reward = {"id": 7, "recipient_tg_id": 1, "target_expiry_ms": 1_800_000_000_000,
              "applied_at": 1, "notified_at": None}
    repository = NS(mark_notified=Mock())
    service = NS(qualify_after_first_successful_payment=Mock(return_value=reward),
                 repository=repository)
    monkeypatch.setattr(bot, "build_referral_service", lambda: service)
    context = NS(bot=NS(send_message=AsyncMock()))
    run(bot.apply_referral_reward(NS(), context))
    repository.mark_notified.assert_called_once_with(7)


def test_start_attributes_valid_referral_then_continues_onboarding(monkeypatch):
    referral = NS(attribute=Mock())
    monkeypatch.setattr(bot, "build_referral_service", lambda: referral)
    monkeypatch.setattr(bot, "is_private_chat", lambda update: True)
    monkeypatch.setattr(bot, "is_admin", lambda update: False)
    monkeypatch.setattr(bot, "get_link_by_tg", lambda tg_id: None)
    monkeypatch.setattr(bot, "membership_required", lambda linked: False)
    show = AsyncMock()
    monkeypatch.setattr(bot, "show_text", show)
    context = NS(args=["ref_K7mP2x9Q"], user_data={})
    upd = update()
    run(bot.start(upd, context))
    referral.attribute.assert_called_once_with("K7mP2x9Q", upd.effective_user)
    show.assert_awaited_once()


def test_registered_user_is_not_rebound_by_referral_start(monkeypatch):
    referral = NS(attribute=Mock())
    monkeypatch.setattr(bot, "build_referral_service", lambda: referral)
    monkeypatch.setattr(bot, "is_private_chat", lambda update: True)
    monkeypatch.setattr(bot, "is_admin", lambda update: False)
    monkeypatch.setattr(bot, "get_link_by_tg", lambda tg_id: {"email": "existing"})
    monkeypatch.setattr(bot, "membership_required", lambda linked: False)
    monkeypatch.setattr(bot, "render_client_home", AsyncMock())
    run(bot.start(update(), NS(args=["ref_K7mP2x9Q"], user_data={})))
    referral.attribute.assert_not_called()


def test_invalid_referral_code_does_not_break_onboarding(monkeypatch):
    referral = NS(attribute=Mock())
    monkeypatch.setattr(bot, "build_referral_service", lambda: referral)
    monkeypatch.setattr(bot, "is_private_chat", lambda update: True)
    monkeypatch.setattr(bot, "is_admin", lambda update: False)
    monkeypatch.setattr(bot, "get_link_by_tg", lambda tg_id: None)
    monkeypatch.setattr(bot, "membership_required", lambda linked: False)
    show = AsyncMock()
    monkeypatch.setattr(bot, "show_text", show)
    run(bot.start(update(), NS(args=["ref_!!!"], user_data={})))
    referral.attribute.assert_not_called()
    show.assert_awaited_once()
