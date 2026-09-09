from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from src import karina_user
from src.services import ClientServiceError


def migration_plan(**changes):
    values = dict(
        primary_email="Mikhail", mobile_email="Mikhail__mobile",
        primary_exists=True, mobile_exists=False, primary_inbound_ids=(2, 3, 4, 5),
        mobile_inbound_ids=(), primary_total_bytes=0, primary_expiry=100,
        primary_hwid_limit=2, mobile_total_bytes=None,
        mobile_subscription_url_present=False, mobile_subscription_url_correct=False,
        needs_mobile_create=True, needs_mobile_quota_fix=False, needs_expiry_sync=False,
        needs_hwid_sync=False, needs_external_link_update=True, needs_primary_detach=True,
        already_migrated=False, warnings=("historical traffic is not transferred",),
        blocking_errors=(),
    )
    values.update(changes)
    return NS(**values)


def test_default_and_explicit_dry_run_are_read_only(monkeypatch, capsys):
    service = NS(plan_mobile_migration=Mock(return_value=migration_plan()),
                 migrate_client_to_mobile_bundle=Mock())
    monkeypatch.setattr(karina_user, "build_service", lambda: service)
    karina_user.cmd_migrate_mobile(["Mikhail"])
    karina_user.cmd_migrate_mobile(["Mikhail", "--dry-run"])
    service.migrate_client_to_mobile_bundle.assert_not_called()
    output = capsys.readouterr().out
    assert "No changes performed" in output
    assert "SUB_ID" not in output and "vless://" not in output and "secret" not in output


def test_apply_plans_then_invokes_existing_migration_once(monkeypatch):
    service = NS(plan_mobile_migration=Mock(return_value=migration_plan()),
                 migrate_client_to_mobile_bundle=Mock())
    monkeypatch.setattr(karina_user, "build_service", lambda: service)
    karina_user.cmd_migrate_mobile(["Mikhail", "--apply"])
    service.plan_mobile_migration.assert_called_once_with("Mikhail")
    service.migrate_client_to_mobile_bundle.assert_called_once_with("Mikhail")


def test_blocking_plan_never_applies(monkeypatch):
    service = NS(plan_mobile_migration=Mock(return_value=migration_plan(
        blocking_errors=("unsafe state",))), migrate_client_to_mobile_bundle=Mock())
    monkeypatch.setattr(karina_user, "build_service", lambda: service)
    with pytest.raises(ClientServiceError):
        karina_user.cmd_migrate_mobile(["Mikhail", "--apply"])
    service.migrate_client_to_mobile_bundle.assert_not_called()
