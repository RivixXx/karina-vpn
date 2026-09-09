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
    ClientServiceError, ValidationError,
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
        self.clients[email] = deepcopy(payload)
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


def test_create_duplicate(config, tmp_path):
    service, _ = make_service(config, tmp_path, {"demo": raw_client()})
    with pytest.raises(ClientAlreadyExistsError):
        service.create_client("demo")


@pytest.mark.parametrize(("custom", "expected"), [(None, 2), (0, 0), (7, 7)])
def test_create_hwid_default_and_custom(config, tmp_path, custom, expected):
    service, xui = make_service(config, tmp_path)
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
    assert result == [ExpiringClient("sooner", NOW + DAY, 1.0),
                      ExpiringClient("later", NOW + 2 * DAY, 2.0)]


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
