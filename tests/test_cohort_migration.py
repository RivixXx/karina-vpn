from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, call

import pytest

from src.cohort_migration import (
    EXCLUDED_CLIENTS, MIGRATION_EVENT, MIGRATION_EVENT_KEY, PRIMARY_HWID_LIMIT,
    TARGET_CLIENTS, apply_cohort, build_dry_run, format_dry_run, migration_message,
)
from src import bot, karina_user, notifier


DAY_MS = 86_400_000


def run(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    finally:
        coroutine.close()
    pytest.fail("unexpected asynchronous I/O")


def primary(email, *, hwid=2, expiry=10 * DAY_MS):
    return NS(email=email, inbound_ids=(2, 3, 4, 5, 10), expiry_time_ms=expiry,
              device_limit=hwid)


def plan(*, already=False, blocking=()):
    return NS(
        needs_mobile_create=not already, needs_mobile_quota_fix=False,
        needs_expiry_sync=False, needs_hwid_sync=False,
        needs_external_link_update=not already, needs_primary_detach=not already,
        blocking_errors=blocking, already_migrated=already,
        mobile_inbound_ids=(5, 10) if already else (),
    )


def report_service(*, already=False, hwid=2):
    clients = {email: primary(email, hwid=hwid) for email in TARGET_CLIENTS}
    return NS(
        config=NS(primary_inbound_ids=(2, 3, 4), inbound_ids=(2, 3, 4, 5, 10),
                  mobile_inbound_ids=(5, 10), mobile_inbound_id=5),
        get_client=Mock(side_effect=lambda email: clients[email]),
        get_devices=Mock(return_value=[NS(model="Android", os_name="Android"),
                                      NS(model="", os_name="Windows")]),
        plan_mobile_migration=Mock(side_effect=lambda email: plan(already=already)),
        migrate_client_to_mobile_bundle=Mock(),
        set_hwid_limit=Mock(side_effect=lambda email, limit: setattr(clients[email], "device_limit", limit)),
    )


def test_fixed_cohort_and_exclusions_are_disjoint():
    assert TARGET_CLIENTS == ("Vlad__K", "Nikolay_p", "Olga_K", "Sergey_B")
    assert EXCLUDED_CLIENTS == ("Mikhail", "Home", "Anastasia_A")
    assert not set(TARGET_CLIENTS) & set(EXCLUDED_CLIENTS)
    assert PRIMARY_HWID_LIMIT == 4


def test_dry_run_is_read_only_and_reports_required_state():
    service = report_service()
    bindings = {"Vlad__K": {"tg_id": 101}}
    reports = build_dry_run(service, bindings.get, Mock(return_value=False))
    output = format_dry_run(reports)
    assert "Mikhail: EXCLUDED / untouched" in output
    assert "Home: EXCLUDED / untouched" in output
    assert "Anastasia_A: EXCLUDED / untouched" in output
    assert "Current primary inbounds: 2,3,4,5,10" in output
    assert "Current mobile inbounds: -" in output
    assert "Primary expiry (ms):" in output
    assert "Telegram binding: 101" in output
    assert "Primary HWID usage: 2 / 4" in output
    assert "create mobile credential on inbounds 5,10" in output
    assert "Notification after success: YES" in output
    assert "No changes performed" in output
    service.migrate_client_to_mobile_bundle.assert_not_called()
    service.set_hwid_limit.assert_not_called()
    assert [item.args[0] for item in service.get_client.call_args_list] == list(TARGET_CLIENTS)


def test_wrong_production_inbound_config_blocks_apply():
    service = report_service()
    service.config.mobile_inbound_ids = (5, 6)
    reports = build_dry_run(service, lambda email: {"tg_id": 101})
    assert all("configured mobile inbounds are not 5,10" in report.blocking_errors
               for report in reports)
    sender = AsyncMock()
    results = run(apply_cohort(
        service, lambda email: {"tg_id": 101}, sender,
        Mock(return_value=False), Mock(), lambda: 0,
    ))
    assert all(status == "blocked" for _, status in results)
    service.migrate_client_to_mobile_bundle.assert_not_called()
    sender.assert_not_awaited()


def test_notification_uses_primary_expiry_and_real_device_names_only():
    text = migration_message(
        primary("Vlad__K", hwid=4, expiry=10 * DAY_MS), 2,
        ("Android", "Windows"), 5 * DAY_MS,
    )
    assert "До окончания подписки: 5 дней" in text
    assert "Использовано устройств: 2 из 4" in text
    assert "- Android" in text and "- Windows" in text
    no_names = migration_message(primary("Vlad__K", hwid=4), 2, (), 0)
    assert "Использовано устройств: 2 из 4" in no_names
    assert "- Android" not in no_names and "- Windows" not in no_names


def test_apply_migrates_verifies_then_notifies_and_records():
    service = report_service()
    states = {email: 0 for email in TARGET_CLIENTS}

    def migrate(email):
        states[email] = 1

    def migration_plan(email):
        return plan(already=bool(states[email]))

    service.migrate_client_to_mobile_bundle.side_effect = migrate
    service.plan_mobile_migration.side_effect = migration_plan
    sender = AsyncMock()
    sent = Mock(return_value=False)
    mark = Mock()
    results = run(apply_cohort(
        service, lambda email: {"tg_id": 101} if email == "Vlad__K" else None,
        sender, sent, mark, lambda: 0,
    ))
    assert results == tuple((email, "complete") for email in TARGET_CLIENTS)
    assert service.migrate_client_to_mobile_bundle.call_args_list == [call(email) for email in TARGET_CLIENTS]
    assert service.set_hwid_limit.call_args_list == [call(email, 4) for email in TARGET_CLIENTS]
    sender.assert_awaited_once()
    mark.assert_called_once_with("Vlad__K", MIGRATION_EVENT_KEY, MIGRATION_EVENT)


def test_repeat_apply_does_not_repeat_message_or_mutations_beyond_idempotent_verify():
    service = report_service(already=True, hwid=4)
    sender = AsyncMock()
    results = run(apply_cohort(
        service, lambda email: {"tg_id": 101}, sender,
        Mock(return_value=True), Mock(), lambda: 0,
    ))
    assert all(status == "complete" for _, status in results)
    service.set_hwid_limit.assert_not_called()
    sender.assert_not_awaited()


def test_blocked_client_is_not_mutated_or_notified():
    service = report_service()
    service.plan_mobile_migration.side_effect = lambda email: plan(blocking=("unsafe",))
    sender = AsyncMock()
    results = run(apply_cohort(
        service, lambda email: {"tg_id": 101}, sender,
        Mock(return_value=False), Mock(), lambda: 0,
    ))
    assert all(status == "blocked" for _, status in results)
    service.migrate_client_to_mobile_bundle.assert_not_called()
    service.set_hwid_limit.assert_not_called()
    sender.assert_not_awaited()


def test_cli_defaults_to_dry_run_without_loading_bot_token(monkeypatch, capsys):
    service = report_service()
    monkeypatch.setattr(karina_user, "build_service", lambda: service)
    monkeypatch.setattr(bot, "get_link_by_email", lambda email: None)
    monkeypatch.setattr(notifier, "event_sent", Mock(side_effect=AssertionError("write path")))
    monkeypatch.setattr(notifier, "mark_event_sent", Mock(side_effect=AssertionError("write path")))
    karina_user.cmd_migrate_bundle_cohort([])
    output = capsys.readouterr().out
    assert "Mode: DRY RUN" in output and "No changes performed" in output
    service.migrate_client_to_mobile_bundle.assert_not_called()
    service.set_hwid_limit.assert_not_called()
