import re
import sqlite3
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from src.models import OrderStatus
from src.repositories import ReferralRepository
from src.services import ReferralService


def repository(local_db, now=1_700_000_000):
    repo = ReferralRepository("unused", connect=local_db, now_provider=lambda: now)
    repo.init_schema()
    return repo


def order(tg_id=2, order_id="KV-FIRST", status=OrderStatus.COMPLETED):
    return NS(tg_id=tg_id, id=order_id, status=status)


def user(tg_id, username="friend", first_name="Friend"):
    return NS(id=tg_id, username=username, first_name=first_name)


def service(repo, expiry=1_800_000_000_000):
    client = Mock()
    client.get_client_bundle.return_value = NS(primary=NS(expiry_time_ms=expiry), mobile=NS())
    client.set_bundle_expiry.side_effect = lambda email, target: NS(expiry_time_ms=target)
    bindings = {1: {"email": "referrer"}}
    return ReferralService(repo, lambda: client, bindings.get), client


def test_codes_are_stable_unique_and_url_safe(local_db):
    repo = repository(local_db)
    first = repo.get_or_create_profile(1)
    again = repo.get_or_create_profile(1)
    second = repo.get_or_create_profile(2)
    assert first["referral_code"] == again["referral_code"]
    assert first["referral_code"] != second["referral_code"]
    assert re.fullmatch(r"[A-Za-z0-9_-]+", first["referral_code"])


def test_attribution_is_single_self_safe_and_invalid_safe(local_db):
    repo = repository(local_db)
    code = repo.get_or_create_profile(1)["referral_code"]
    other = repo.get_or_create_profile(3)["referral_code"]
    assert repo.attribute("invalid", 2) is None
    assert repo.attribute(code, 1) is None
    first = repo.attribute(code, 2, "friend", "Friend")
    assert first["referrer_tg_id"] == 1
    assert repo.attribute(other, 2)["referrer_tg_id"] == 1
    assert len(repo.list_referrals(1)) == 1


@pytest.mark.parametrize("status", [OrderStatus.PENDING, OrderStatus.CANCELLED, OrderStatus.FAILED])
def test_uncompleted_payment_never_qualifies(local_db, status):
    repo = repository(local_db)
    code = repo.get_or_create_profile(1)["referral_code"]
    repo.attribute(code, 2)
    referrals, client = service(repo)
    assert referrals.qualify_after_first_successful_payment(order(status=status)) is None
    client.set_bundle_expiry.assert_not_called()
    assert repo.get_by_referred(2)["status"] == "attributed"


def test_first_completed_payment_applies_three_days_once(local_db):
    repo = repository(local_db)
    code = repo.get_or_create_profile(1)["referral_code"]
    repo.attribute(code, 2)
    referrals, client = service(repo)
    reward = referrals.qualify_after_first_successful_payment(order())
    again = referrals.qualify_after_first_successful_payment(order())
    assert reward["amount"] == 3 and reward["applied_at"] is not None
    assert again["id"] == reward["id"]
    client.set_bundle_expiry.assert_called_once_with("referrer", reward["target_expiry_ms"])
    assert repo.stats(1)["earned_days"] == 3
    assert len(repo.list_rewards(1)) == 1


def test_second_payment_cannot_reward_same_referred_user(local_db):
    repo = repository(local_db)
    code = repo.get_or_create_profile(1)["referral_code"]
    repo.attribute(code, 2)
    referrals, client = service(repo)
    referrals.qualify_after_first_successful_payment(order())
    assert referrals.qualify_after_first_successful_payment(order(order_id="KV-SECOND")) is None
    client.set_bundle_expiry.assert_called_once()


def test_failed_extension_is_unapplied_and_retry_uses_same_absolute_target(local_db):
    repo = repository(local_db)
    code = repo.get_or_create_profile(1)["referral_code"]
    repo.attribute(code, 2)
    referrals, client = service(repo)
    client.set_bundle_expiry.side_effect = RuntimeError("xui")
    with pytest.raises(RuntimeError):
        referrals.qualify_after_first_successful_payment(order())
    pending = repo.list_rewards(1)
    assert pending == []
    client.set_bundle_expiry.side_effect = None
    client.set_bundle_expiry.return_value = NS(expiry_time_ms=1_800_000_000_000 + 3 * 86400000)
    reward = referrals.qualify_after_first_successful_payment(order())
    assert reward["applied_at"] is not None
    assert client.set_bundle_expiry.call_args_list[0] == client.set_bundle_expiry.call_args_list[1]


def test_restart_preserves_attribution_and_reward_idempotency(local_db):
    repo = repository(local_db)
    code = repo.get_or_create_profile(1)["referral_code"]
    repo.attribute(code, 2)
    referrals, client = service(repo)
    referrals.qualify_after_first_successful_payment(order())
    reopened = ReferralRepository("unused", connect=local_db, now_provider=lambda: 1_700_000_100)
    restarted, restarted_client = service(reopened)
    restarted.qualify_after_first_successful_payment(order())
    restarted_client.set_bundle_expiry.assert_not_called()
