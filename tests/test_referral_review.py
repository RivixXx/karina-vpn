from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

from src import bot
from src.repositories import ReferralRepository


def run(coroutine):
    try:
        coroutine.send(None)
    except StopIteration:
        return
    finally:
        coroutine.close()
    raise AssertionError('Unexpected I/O')


def test_empty_referral_stats_are_numbers(local_db):
    repo = ReferralRepository('unused', connect=local_db)
    repo.init_schema()
    assert dict(repo.stats(123)) == {'invited': 0, 'qualified': 0, 'earned_days': 0}


def test_referral_survives_channel_gate_and_plain_start(local_db, monkeypatch):
    repo = ReferralRepository('unused', connect=local_db)
    repo.init_schema()
    code = repo.get_or_create_profile(111)['referral_code']
    monkeypatch.setattr(bot, 'is_admin', lambda update: False)
    monkeypatch.setattr(bot, 'get_link_by_tg', lambda uid: None)
    monkeypatch.setattr(bot, 'membership_required', lambda linked: True)
    monkeypatch.setattr(bot, 'check_required_membership', AsyncMock(side_effect=[False, True]))
    monkeypatch.setattr(bot, 'render_membership_gate', AsyncMock())
    monkeypatch.setattr(bot, 'show_text', AsyncMock())
    monkeypatch.setattr(bot, 'build_referral_service', lambda: NS(
        attribute=lambda code, user: repo.attribute(code, user.id)))
    update = NS(effective_user=NS(id=222), effective_chat=NS(type='private'))
    context = NS(args=['ref_' + code], bot=NS(), user_data={})
    run(bot.start(update, context))
    assert repo.get_by_referred(222)['referrer_tg_id'] == 111
    context.args = []
    run(bot.start(update, context))
    assert repo.get_by_referred(222)['referrer_tg_id'] == 111
