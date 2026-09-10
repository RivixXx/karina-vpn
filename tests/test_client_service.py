from copy import deepcopy
from pathlib import Path

import pytest

from src.app_config import KarinaConfig
from src.integrations.xui import XUIOperationError
from src.models import (
    ClientInfo, CreateClientResult, DeleteClientResult, DeviceInfo,
    ExpiringClient, TrafficInfo,
)
from src.services import (
    ClientAlreadyExistsError, ClientNotFoundError, ClientService,
    ClientServiceError, ReconciliationRequiredError, ValidationError,
)


NOW = 2_000_000_000_000
DAY = 86_400_000


def raw_client(email="demo", *, expiry=NOW + 10 * DAY, enabled=True,
               hwid=2, traffic=0, used=0, sub_id="safe_id123", inbounds=(1, 2)):
    return {
        "client": {
            "email": email, "subId": sub_id, "expiryTime": expiry,
            "totalGB": traffic, "limitIp": 0, "limitHwid": hwid,
            "enable": enabled,
        },
        "inboundIds": list(inbounds),
        "usedTraffic": used,
    }


class FakeXUI:
    def __init__(self, clients=None, inbounds=None, devices=None):
        self.clients = deepcopy(clients or {})
        self.inbounds = deepcopy(inbounds or [])
        self.devices = deepcopy(devices or {})
        self.created_payload = None
        self.updated = []
        self.deleted = []
        self.removed_devices = []
        self.reset = []
        self.external_updates = []
        self.detached = []

    def get_client(self, email):
        value = self.clients.get(email)
        return deepcopy(value) if value is not None else None

    def list_inbounds(self):
        return deepcopy(self.inbounds)

    def create_client(self, payload):
        self.created_payload = deepcopy(payload)
        client = deepcopy(payload["client"])
        self.clients[client["email"]] = {
            "client": client, "inboundIds": list(payload["inboundIds"]),
            "usedTraffic": 0,
        }

    def update_client(self, email, payload):
        used = self.clients[email].get("usedTraffic", 0)
        inbound_ids = list(payload.get("inboundIds", self.clients[email]["inboundIds"]))
        client = {key: value for key, value in payload.items() if key != "inboundIds"}
        self.clients[email] = {"client": deepcopy(client), "inboundIds": inbound_ids}
        self.clients[email]["usedTraffic"] = used
        self.updated.append((email, deepcopy(payload)))

    def delete_client(self, email):
        self.deleted.append(email)
        self.clients.pop(email)

    def get_hwids(self, email):
        return deepcopy(self.devices.get(email, []))

    def delete_hwid(self, email, device_id):
        self.removed_devices.append((email, device_id))

    def reset_hwids(self, email):
        self.reset.append(email)

    def set_external_links(self, email, links):
        self.external_updates.append((email, deepcopy(links)))
        self.clients[email]["externalLinks"] = [
            {"kind": link.kind, "value": link.value, "remark": link.remark}
            for link in links
        ]

    def detach_inbounds(self, email, inbound_ids):
        self.detached.append((email, tuple(inbound_ids)))
        self.clients[email]["inboundIds"] = [
            value for value in self.clients[email]["inboundIds"] if value not in inbound_ids
        ]


@pytest.fixture
def config():
    return KarinaConfig(
        xui_base="https://xui.example.test", xui_user="synthetic",
        xui_pass="synthetic", sub_base="https://sub.example.test",
        connect_base="https://vpn.example.test/connect", inbound_ids=(1, 2),
        default_hwid_limit=2,
    )


def make_service(config, tmp_path, clients=None, inbounds=None, devices=None, issue=None):
    xui = FakeXUI(clients, inbounds, devices)
    return ClientService(config, xui, issue_subscription=issue,
                         now_provider=lambda: NOW, connect_dir=tmp_path), xui


def test_get_existing_client_maps_typed_result(config, tmp_path):
    service, _ = make_service(config, tmp_path, {"demo": raw_client(used=7)},
                              devices={"demo": [{"id": 1}]})
    result = service.get_client("demo")
    assert isinstance(result, ClientInfo)
    assert (result.email, result.device_count, result.used_traffic_bytes) == ("demo", 1, 7)
    assert result.inbound_ids == (1, 2)
    assert result.connect_url.endswith("/safe_id123.html")


def test_get_missing_client_returns_none(config, tmp_path):
    service, _ = make_service(config, tmp_path)
    assert service.get_client("demo") is None


def test_create_success_preserves_payload(config, tmp_path):
    service, xui = make_service(config, tmp_path, issue=lambda sub_id: f"page/{sub_id}")
    result = service.create_client("demo")
    assert isinstance(result, CreateClientResult)
    assert result.subscription_page == f"page/{result.client.sub_id}"
    client = xui.created_payload["client"]
    assert len(client["subId"]) == 16
    assert client["expiryTime"] == NOW + 30 * DAY
    assert client["limitHwid"] == 2
    assert client["security"] == "auto"
    assert xui.created_payload["inboundIds"] == [1, 2]


def test_create_issues_from_authoritative_xui_readback_sub_id(config, tmp_path):
    issued = []
    service, xui = make_service(config, tmp_path, issue=lambda sub_id: issued.append(sub_id))
    original_create = xui.create_client

    def create_with_authoritative_id(payload):
        original_create(payload)
        xui.clients[payload["client"]["email"]]["client"]["subId"] = "authoritative123"

    xui.create_client = create_with_authoritative_id
    result = service.create_client("demo")

    assert result.client.sub_id == "authoritative123"
    assert issued == ["authoritative123"]


def test_create_duplicate(config, tmp_path):
    service, _ = make_service(config, tmp_path, {"demo": raw_client()})
    with pytest.raises(ClientAlreadyExistsError):
        service.create_client("demo")


@pytest.mark.parametrize(("custom", "expected"), [(None, 2), (0, 0), (7, 7)])
def test_create_hwid_default_and_custom(config, tmp_path, custom, expected):
    service, xui = make_service(config, tmp_path, issue=lambda sub_id: f"page/{sub_id}")
    service.create_client("demo", hwid_limit=custom)
    assert xui.created_payload["client"]["limitHwid"] == expected


def test_create_traffic_bytes_conversion(config, tmp_path):
    service, xui = make_service(config, tmp_path)
    service.create_client("demo", traffic_gb=1.5)
    assert xui.created_payload["client"]["totalGB"] == int(1.5 * 1024 ** 3)


def test_subscription_failure_returns_warning_and_keeps_client(config, tmp_path):
    def fail(_):
        raise RuntimeError("synthetic issue failure")
    service, xui = make_service(config, tmp_path, issue=fail)
    result = service.create_client("demo")
    assert result.client.email == "demo" and result.subscription_page is None
    assert result.issue_warning == "synthetic issue failure"
    assert "demo" in xui.clients


@pytest.mark.parametrize(("expiry", "expected"), [
    (NOW + 5 * DAY, NOW + 8 * DAY),
    (NOW - DAY, NOW + 3 * DAY),
])
def test_extend_active_and_expired(config, tmp_path, expiry, expected):
    service, xui = make_service(config, tmp_path, {"demo": raw_client(expiry=expiry)})
    service.extend_client("demo", 3)
    assert xui.clients["demo"]["client"]["expiryTime"] == expected


@pytest.mark.parametrize(("method", "enabled"), [("enable_client", True), ("disable_client", False)])
def test_enable_disable(config, tmp_path, method, enabled):
    service, xui = make_service(config, tmp_path, {"demo": raw_client(enabled=not enabled)})
    result = getattr(service, method)("demo")
    assert result.enabled is enabled
    assert xui.clients["demo"]["client"]["enable"] is enabled


def test_set_hwid(config, tmp_path):
    service, xui = make_service(config, tmp_path, {"demo": raw_client()})
    assert service.set_hwid_limit("demo", 4).device_limit == 4
    assert xui.clients["demo"]["client"]["limitHwid"] == 4


def test_set_traffic(config, tmp_path):
    service, xui = make_service(config, tmp_path, {"demo": raw_client()})
    result = service.set_traffic_limit("demo", 2.5)
    assert result.total_traffic_bytes == int(2.5 * 1024 ** 3)
    assert xui.clients["demo"]["client"]["totalGB"] == int(2.5 * 1024 ** 3)


@pytest.mark.parametrize("operation", ["extend", "enable", "disable", "hwid", "traffic"])
def test_update_operations_send_full_flat_writable_client(config, tmp_path, operation):
    record = raw_client()
    record["client"].update({
        "id": 42, "uuid": "fixture-uuid", "allowedIPs": "10.0.0.2/32",
        "reverse": {"tag": "fixture"}, "serverManaged": "drop-me",
    })
    service, xui = make_service(config, tmp_path, {"demo": record})
    if operation == "extend":
        service.extend_client("demo", 1)
    elif operation == "enable":
        service.enable_client("demo")
    elif operation == "disable":
        service.disable_client("demo")
    elif operation == "hwid":
        service.set_hwid_limit("demo", 4)
    else:
        service.set_traffic_limit("demo", 2)
    _, payload = xui.updated[-1]
    assert payload["email"] == "demo" and payload["inboundIds"] == [1, 2]
    assert payload["uuid"] == "fixture-uuid"
    assert payload["allowedIPs"] == ["10.0.0.2/32"]
    assert payload["reverse"] == {"tag": "fixture"}
    assert "client" not in payload and "id" not in payload
    assert "serverManaged" not in payload


@pytest.mark.parametrize(("raw", "expected"), [
    (None, []),
    ("", []),
    ([], []),
    (["1.2.3.4/32"], ["1.2.3.4/32"]),
    ('["1.2.3.4/32"]', ["1.2.3.4/32"]),
    ("1.2.3.4/32, 10.0.0.2/32", ["1.2.3.4/32", "10.0.0.2/32"]),
])
def test_allowed_ips_update_normalization(config, tmp_path, raw, expected):
    record = raw_client()
    record["client"]["allowedIPs"] = raw
    service, xui = make_service(config, tmp_path, {"demo": record})
    service.set_traffic_limit("demo", 1)
    assert xui.updated[-1][1]["allowedIPs"] == expected


@pytest.mark.parametrize("raw", ["[broken", '["valid", 3]', {"address": "1.2.3.4/32"}])
def test_malformed_allowed_ips_aborts_update(config, tmp_path, raw):
    record = raw_client()
    record["client"]["allowedIPs"] = raw
    service, xui = make_service(config, tmp_path, {"demo": record})
    with pytest.raises(ClientServiceError, match="allowedIPs"):
        service.set_traffic_limit("demo", 1)
    assert not xui.updated


def test_exact_production_empty_allowed_ips_becomes_list(config, tmp_path):
    record = raw_client("Testrouter__mobile", hwid=5, inbounds=(5,))
    record["client"]["allowedIPs"] = ""
    service, xui = make_service(config, tmp_path, {"Testrouter__mobile": record})
    service._update("Testrouter__mobile", lambda client: client.__setitem__("limitHwid", 0))
    payload = xui.updated[-1][1]
    assert payload["email"] == "Testrouter__mobile"
    assert payload["allowedIPs"] == [] and payload["limitHwid"] == 0


def test_update_normalizes_audited_scalar_and_reverse_types(config, tmp_path):
    record = raw_client()
    record["client"].update({
        "uuid": "fixture-uuid", "tgId": "17", "group": None,
        "reset": "1", "resetDay": "2", "resetMax": "3",
        "trafficReset": "monthly", "trafficResetDay": "4",
        "limitIp": "5", "limitHwid": "6", "totalGB": "7",
        "expiryTime": "8", "enable": "true", "reverse": '{"tag":"edge"}',
    })
    service, xui = make_service(config, tmp_path, {"demo": record})
    service.set_traffic_limit("demo", 1)
    payload = xui.updated[-1][1]
    assert payload["uuid"] == "fixture-uuid" and payload["tgId"] == 17
    assert payload["group"] == "" and payload["enable"] is True
    assert payload["reverse"] == {"tag": "edge"}
    assert all(isinstance(payload[key], int) for key in (
        "reset", "resetDay", "resetMax", "trafficResetDay", "limitIp",
        "limitHwid", "totalGB", "expiryTime",
    ))


def test_unlimited_traffic(config, tmp_path):
    service, _ = make_service(config, tmp_path, {"demo": raw_client(used=123)})
    result = service.get_traffic("demo")
    assert result == TrafficInfo(123, 0, None, None)


def test_traffic_percent(config, tmp_path):
    service, _ = make_service(config, tmp_path, {
        "demo": raw_client(traffic=1000, used=250),
    })
    assert service.get_traffic("demo") == TrafficInfo(250, 1000, 750, 25.0)


def test_list_deduplicates_and_sorts(config, tmp_path):
    inbounds = [{"settings": {"clients": [{"email": "zed"}, {"email": "Alpha"}]}},
                {"settings": {"clients": [{"email": "zed"}]}}]
    clients = {name: raw_client(name) for name in ("zed", "Alpha")}
    service, _ = make_service(config, tmp_path, clients, inbounds)
    assert [client.email for client in service.list_clients()] == ["Alpha", "zed"]


def test_devices_mapping(config, tmp_path):
    raw = {"id": 9, "deviceModel": "Phone", "deviceOs": "OS", "osVersion": "1",
           "userAgent": "Agent", "firstSeen": 10, "lastSeen": 20}
    service, _ = make_service(config, tmp_path, {"demo": raw_client()},
                              devices={"demo": [raw]})
    assert service.get_devices("demo") == [DeviceInfo(9, "Phone", "OS", "1", "Agent", 10, 20)]


def test_remove_and_reset_devices(config, tmp_path):
    service, xui = make_service(config, tmp_path, {"demo": raw_client()})
    service.remove_device("demo", 9)
    service.reset_devices("demo")
    assert xui.removed_devices == [("demo", 9)] and xui.reset == ["demo"]


def test_expiring_selection_and_sorting(config, tmp_path):
    clients = {
        "later": raw_client("later", expiry=NOW + 2 * DAY),
        "sooner": raw_client("sooner", expiry=NOW + DAY),
        "expired": raw_client("expired", expiry=NOW - 1),
        "disabled": raw_client("disabled", expiry=NOW + DAY, enabled=False),
        "forever": raw_client("forever", expiry=0),
    }
    inbound = [{"settings": {"clients": [{"email": email} for email in clients]}}]
    service, _ = make_service(config, tmp_path, clients, inbound)
    result = service.get_expiring(2)
    assert [(item.email, item.expiry_time_ms, item.days_remaining, item.enabled)
            for item in result] == [
        ("sooner", NOW + DAY, 1.0, True),
        ("later", NOW + 2 * DAY, 2.0, True),
    ]
    assert all(item.expiry_text for item in result)


def test_delete_valid_files(config, tmp_path):
    for suffix in (".html", ".png", ".crypt5", ".keep"):
        (tmp_path / ("safe_id123" + suffix)).write_text("synthetic", encoding="utf-8")
    service, xui = make_service(config, tmp_path, {"demo": raw_client()})
    result = service.delete_client("demo")
    assert result == DeleteClientResult("demo", True, None)
    assert xui.deleted == ["demo"]
    assert [path.name for path in tmp_path.iterdir()] == ["safe_id123.keep"]


@pytest.mark.parametrize("sub_id", ["../escape", "/absolute", "a/b", "short", "x" * 129, None])
def test_delete_malicious_subid_is_safe(config, tmp_path, sub_id):
    outside = tmp_path.parent / "escape.html"
    outside.write_text("keep", encoding="utf-8")
    service, xui = make_service(config, tmp_path, {"demo": raw_client(sub_id=sub_id)})
    result = service.delete_client("demo")
    assert result.removed and result.file_cleanup_warning
    assert outside.read_text(encoding="utf-8") == "keep"
    assert xui.deleted == ["demo"]


def test_delete_resolved_path_outside_is_skipped(config, tmp_path, monkeypatch):
    inside = tmp_path / "safe_id123.html"
    outside = tmp_path.parent / "outside.html"
    inside.write_text("inside", encoding="utf-8")
    outside.write_text("outside", encoding="utf-8")
    original_resolve = Path.resolve
    monkeypatch.setattr(
        Path, "resolve",
        lambda path: outside if path == inside else original_resolve(path),
    )
    service, _ = make_service(config, tmp_path, {"demo": raw_client()})
    result = service.delete_client("demo")
    assert result.file_cleanup_warning == "Unsafe .html path; cleanup skipped"
    assert inside.exists() and outside.exists()


def test_xui_delete_failure_leaves_files(config, tmp_path):
    target = tmp_path / "safe_id123.html"
    target.write_text("keep", encoding="utf-8")
    service, xui = make_service(config, tmp_path, {"demo": raw_client()})

    def fail(_):
        raise XUIOperationError("synthetic")

    xui.delete_client = fail
    with pytest.raises(ClientServiceError):
        service.delete_client("demo")
    assert target.read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize("email", ["", "a", "../demo", "bad@email", "x" * 65])
def test_email_validation(config, tmp_path, email):
    service, _ = make_service(config, tmp_path)
    with pytest.raises(ValidationError):
        service.get_client(email)


@pytest.mark.parametrize("days", [0, -1, 1.5, True])
def test_days_validation(config, tmp_path, days):
    service, _ = make_service(config, tmp_path)
    with pytest.raises(ValidationError):
        service.create_client("demo", days=days)


def test_create_mobile_bundle(config, tmp_path):
    config = config.__class__(**{**config.__dict__, "primary_inbound_ids": (2, 3, 4),
                                "mobile_inbound_id": 5,
                                "mobile_traffic_bytes": 53687091200})
    service, xui = make_service(config, tmp_path, issue=lambda sub_id: f"page/{sub_id}")
    result = service.create_client_bundle("Mikhail")
    primary, mobile = result.bundle.primary, result.bundle.mobile
    assert primary.inbound_ids == (2, 3, 4) and primary.total_traffic_bytes == 0
    assert mobile.inbound_ids == (5,)
    assert mobile.total_traffic_bytes == 53687091200
    assert primary.device_limit == 2 and mobile.device_limit == 0
    assert primary.sub_id != mobile.sub_id
    assert primary.expiry_time_ms == mobile.expiry_time_ms
    link = xui.clients["Mikhail"]["externalLinks"][0]
    assert link == {"kind": "subscription",
                    "value": config.sub_base + "/" + mobile.sub_id,
                    "remark": "karina-mobile"}
    assert "localhost" not in link["value"] and "vless://" not in link["value"]


def test_migration_detaches_mobile_inbound_last(config, tmp_path):
    config = config.__class__(**{**config.__dict__, "primary_inbound_ids": (2, 3, 4),
                                "mobile_inbound_id": 5,
                                "mobile_traffic_bytes": 53687091200})
    service, xui = make_service(config, tmp_path, {"Mikhail": raw_client(
        "Mikhail", inbounds=(2, 3, 4, 5))})
    result = service.migrate_client_to_mobile_bundle("Mikhail")
    assert result.mobile.inbound_ids == (5,)
    assert xui.detached == [("Mikhail", (5,))]


def test_production_mobile_hwid_repair_uses_zero_and_flat_update(config, tmp_path):
    config = config.__class__(**{**config.__dict__, "primary_inbound_ids": (2, 3, 4),
                                "mobile_inbound_id": 5,
                                "mobile_traffic_bytes": 53687091200})
    primary = raw_client("Testrouter", hwid=5, inbounds=(2, 3, 4))
    mobile = raw_client("Testrouter__mobile", hwid=5, inbounds=(5,),
                        traffic=53687091200, sub_id="mobile-secret")
    primary["externalLinks"] = [{
        "kind": "subscription", "value": config.sub_base + "/mobile-secret",
        "remark": "karina-mobile",
    }]
    service, xui = make_service(
        config, tmp_path, {"Testrouter": primary, "Testrouter__mobile": mobile},
    )
    plan = service.plan_mobile_migration("Testrouter")
    assert plan.needs_hwid_sync and not plan.already_migrated
    result = service.migrate_client_to_mobile_bundle("Testrouter")
    assert result.primary.device_limit == 5 and result.mobile.device_limit == 0
    email, payload = xui.updated[-1]
    assert email == "Testrouter__mobile"
    assert payload["email"] == email and payload["limitHwid"] == 0
    assert payload["inboundIds"] == [5] and "client" not in payload


def test_migration_repairs_mobile_quota_expiry_and_hwid(config, tmp_path):
    config = config.__class__(**{**config.__dict__, "primary_inbound_ids": (2, 3, 4),
                                "mobile_inbound_id": 5,
                                "mobile_traffic_bytes": 53687091200})
    primary = raw_client("Mikhail", expiry=NOW + 10 * DAY, hwid=5,
                         inbounds=(2, 3, 4))
    mobile = raw_client("Mikhail__mobile", expiry=NOW + DAY, hwid=5,
                        traffic=1, inbounds=(5,), sub_id="mobile-secret")
    primary["externalLinks"] = [{
        "kind": "subscription", "value": config.sub_base + "/mobile-secret",
        "remark": "karina-mobile",
    }]
    service, xui = make_service(
        config, tmp_path, {"Mikhail": primary, "Mikhail__mobile": mobile},
    )
    plan = service.plan_mobile_migration("Mikhail")
    assert plan.needs_mobile_quota_fix and plan.needs_expiry_sync and plan.needs_hwid_sync
    result = service.migrate_client_to_mobile_bundle("Mikhail")
    assert result.mobile.total_traffic_bytes == config.mobile_traffic_bytes
    assert result.mobile.expiry_time_ms == result.primary.expiry_time_ms
    assert result.mobile.device_limit == 0
    assert all("client" not in payload and payload["inboundIds"] == [5]
               for email, payload in xui.updated if email == "Mikhail__mobile")


def test_migration_plan_is_read_only_and_redacts_state(config, tmp_path):
    config = config.__class__(**{**config.__dict__, "primary_inbound_ids": (2, 3, 4),
                                "mobile_inbound_id": 5,
                                "mobile_traffic_bytes": 53687091200})
    clients = {"Mikhail": raw_client("Mikhail", inbounds=(2, 3, 4, 5),
                                      sub_id="primary-secret")}
    clients["Mikhail"]["externalLinks"] = [{
        "id": 7, "kind": "link", "value": "https://unrelated.example/path",
        "remark": "unrelated", "lastFetchError": "server-only",
    }]
    service, xui = make_service(config, tmp_path, clients)
    before = deepcopy(xui.clients)
    plan = service.plan_mobile_migration("Mikhail")
    assert xui.clients == before
    assert not xui.updated and not xui.deleted and not xui.external_updates and not xui.detached
    assert plan.needs_mobile_create and plan.needs_external_link_update
    assert plan.needs_primary_detach and not plan.blocking_errors


def test_already_migrated_plan_is_noop(config, tmp_path):
    config = config.__class__(**{**config.__dict__, "primary_inbound_ids": (2, 3, 4),
                                "mobile_inbound_id": 5,
                                "mobile_traffic_bytes": 53687091200})
    primary = raw_client("Mikhail", inbounds=(2, 3, 4))
    mobile = raw_client("Mikhail__mobile", inbounds=(5,), traffic=53687091200,
                        hwid=0, sub_id="mobile-secret")
    primary["externalLinks"] = [{"kind": "subscription",
                                  "value": config.sub_base + "/mobile-secret",
                                  "remark": "karina-mobile"}]
    service, _ = make_service(config, tmp_path,
                              {"Mikhail": primary, "Mikhail__mobile": mobile})
    plan = service.plan_mobile_migration("Mikhail")
    assert plan.already_migrated and not plan.blocking_errors


def test_migration_plan_blocks_unsafe_states(config, tmp_path):
    config = config.__class__(**{**config.__dict__, "primary_inbound_ids": (2, 3, 4),
                                "mobile_inbound_id": 5,
                                "mobile_traffic_bytes": 53687091200})
    service, _ = make_service(config, tmp_path)
    assert service.plan_mobile_migration("missing").blocking_errors
    assert service.plan_mobile_migration("name__mobile").blocking_errors
    primary = raw_client("Mikhail", inbounds=(2, 3, 4, 5))
    primary["externalLinks"] = [
        {"kind": "subscription", "value": "one", "remark": "karina-mobile"},
        {"kind": "subscription", "value": "two", "remark": "karina-mobile"},
    ]
    service, _ = make_service(config, tmp_path, {"Mikhail": primary})
    plan = service.plan_mobile_migration("Mikhail")
    assert plan.blocking_errors and plan.managed_external_link_count == 2


@pytest.mark.parametrize("limit", [-1, 1.5, True])
def test_hwid_validation(config, tmp_path, limit):
    service, _ = make_service(config, tmp_path)
    with pytest.raises(ValidationError):
        service.create_client("demo", hwid_limit=limit)


@pytest.mark.parametrize("traffic", [-1, float("nan"), float("inf"), "1", True])
def test_traffic_validation(config, tmp_path, traffic):
    service, _ = make_service(config, tmp_path)
    with pytest.raises(ValidationError):
        service.create_client("demo", traffic_gb=traffic)


def test_missing_client_operations_raise_typed_error(config, tmp_path):
    service, _ = make_service(config, tmp_path)
    with pytest.raises(ClientNotFoundError):
        service.disable_client("demo")


def test_xui_error_is_translated_with_cause(config, tmp_path):
    service, xui = make_service(config, tmp_path)
    def fail(_):
        raise XUIOperationError("synthetic")
    xui.get_client = fail
    with pytest.raises(ClientServiceError) as caught:
        service.get_client("demo")
    assert isinstance(caught.value.__cause__, XUIOperationError)


def bundle_clients():
    return {
        "demo": raw_client("demo", expiry=NOW + DAY, hwid=2, inbounds=(1, 2)),
        "demo__mobile": raw_client(
            "demo__mobile", expiry=NOW + DAY, hwid=0, traffic=50 * 1024 ** 3,
            used=1024 ** 2, sub_id="mobile_id123", inbounds=(5,),
        ),
    }


def test_unlimited_bundle_and_issuer_failure_contract(config, tmp_path):
    service, xui = make_service(config, tmp_path, issue=lambda sub_id: f"page/{sub_id}")
    result = service.create_client_bundle("demo", days=None, hwid_limit=0)
    assert result.bundle.primary.expiry_time_ms == 0
    assert result.bundle.mobile.expiry_time_ms == 0
    assert result.bundle.primary.device_limit == result.bundle.mobile.device_limit == 0

    service, _ = make_service(config, tmp_path, issue=lambda _: (_ for _ in ()).throw(OSError()))
    with pytest.raises(ReconciliationRequiredError):
        service.create_client_bundle("other")


def test_bundle_extend_hwid_toggle_and_mobile_traffic(config, tmp_path):
    service, xui = make_service(config, tmp_path, bundle_clients())
    extended = service.extend_client("demo", 30)
    assert xui.clients["demo__mobile"]["client"]["expiryTime"] == extended.expiry_time_ms
    service.set_hwid_limit("demo", 5)
    assert xui.clients["demo"]["client"]["limitHwid"] == 5
    assert xui.clients["demo__mobile"]["client"]["limitHwid"] == 0
    service.disable_client("demo")
    assert not xui.clients["demo"]["client"]["enable"]
    assert not xui.clients["demo__mobile"]["client"]["enable"]
    service.enable_client("demo")
    assert xui.clients["demo"]["client"]["enable"]
    assert xui.clients["demo__mobile"]["client"]["enable"]
    traffic = service.get_mobile_traffic("demo")
    assert traffic.used_bytes == 1024 ** 2 and traffic.limit_bytes == 50 * 1024 ** 3


def test_bundle_devices_are_merged_and_mutated_without_mobile_name(config, tmp_path):
    raw = lambda identifier, model: {"id": identifier, "deviceModel": model}
    service, xui = make_service(
        config, tmp_path, bundle_clients(),
        devices={"demo": [raw(1, "one"), raw(2, "shared")],
                 "demo__mobile": [raw(9, "shared"), raw(3, "three")]},
    )
    assert [item.id for item in service.get_bundle_devices("demo")] == [1, 2, 3]
    service.remove_bundle_device("demo", 2)
    assert xui.removed_devices == [("demo", 2), ("demo__mobile", 9)]
    service.reset_bundle_devices("demo")
    assert xui.reset == ["demo", "demo__mobile"]


def test_connection_uses_fresh_authoritative_subid(config, tmp_path):
    issued = []
    clients = {"demo": raw_client("demo", sub_id="actual_readback123")}
    service, _ = make_service(
        config, tmp_path, clients,
        issue=lambda sub_id: issued.append(sub_id) or f"page/{sub_id}",
    )
    assert service.ensure_connection("demo") == "page/actual_readback123"
    assert issued == ["actual_readback123"]


def test_bundle_delete_mobile_then_primary(config, tmp_path):
    for sub_id in ("safe_id123", "mobile_id123"):
        (tmp_path / f"{sub_id}.html").write_text("page", encoding="utf-8")
    clients = bundle_clients()
    service, xui = make_service(config, tmp_path, clients)
    result = service.delete_client_bundle("demo")
    assert result.removed
    assert xui.deleted == ["demo__mobile", "demo"]
    assert not list(tmp_path.glob("*.html"))


def test_bundle_delete_partial_failure_requires_reconciliation(config, tmp_path):
    service, xui = make_service(config, tmp_path, bundle_clients())
    original = xui.delete_client

    def fail_primary(email):
        if email == "demo":
            raise XUIOperationError("synthetic")
        original(email)

    xui.delete_client = fail_primary
    with pytest.raises(ReconciliationRequiredError):
        service.delete_client_bundle("demo")
    assert xui.deleted == ["demo__mobile"]


@pytest.mark.parametrize("operation", ["hwid", "toggle"])
def test_bundle_partial_updates_require_reconciliation(config, tmp_path, operation):
    clients = bundle_clients()
    if operation == "hwid":
        clients["demo__mobile"]["client"]["limitHwid"] = 5
    service, xui = make_service(config, tmp_path, clients)
    original = xui.update_client

    def fail_mobile(email, payload):
        if email.endswith("__mobile"):
            raise XUIOperationError("synthetic")
        original(email, payload)

    xui.update_client = fail_mobile
    with pytest.raises(ReconciliationRequiredError):
        if operation == "hwid":
            service.set_hwid_limit("demo", 3)
        else:
            service.disable_client("demo")
