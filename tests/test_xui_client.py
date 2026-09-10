import io
import json
import urllib.error

import pytest

from src.app_config import KarinaConfig
from src.integrations.xui import (
    XUIAuthError, XUIClient, XUIHTTPError, XUIOperationError,
    XUIResponseError, XUITransportError,
)


@pytest.fixture
def config():
    return KarinaConfig(
        xui_base="https://xui.example.test", xui_user="fixture_user",
        xui_pass="fixture_password", sub_base="https://sub.example.test",
        connect_base="https://connect.example.test", inbound_ids=(2, 3),
        default_hwid_limit=2,
    )


class Response:
    def __init__(self, payload):
        self.body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return None
    def read(self):
        return self.body


class Opener:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []
    def open(self, request, timeout):
        self.requests.append((request, timeout))
        result = self.responses.pop(0)
        if isinstance(result, BaseException):
            raise result
        return Response(result)


def client(config, *responses):
    result = XUIClient(config)
    result.opener = Opener(*responses)
    return result


def test_csrf_and_login_success(config):
    api = client(config, {"success": True, "obj": "csrf_fixture"}, {"success": True})
    api.login()
    assert api.csrf == "csrf_fixture"
    login = api.opener.requests[1][0]
    assert login.full_url.endswith("/login")
    assert login.get_header("X-csrf-token") == "csrf_fixture"
    assert json.loads(login.data) == {
        "username": "fixture_user", "password": "fixture_password", "twoFactorCode": "",
    }


@pytest.mark.parametrize("responses", [
    ({"success": False},),
    ({"success": True, "obj": "csrf_fixture"}, {"success": False, "msg": "denied"}),
])
def test_login_rejected(config, responses):
    with pytest.raises(XUIAuthError):
        client(config, *responses).login()


def test_http_error_is_bounded(config):
    error = urllib.error.HTTPError("https://xui.example.test", 500, "failure", {},
                                  io.BytesIO(b"x" * 1000))
    with pytest.raises(XUIHTTPError) as caught:
        client(config, error).request("GET", "/test")
    assert len(str(caught.value)) < 550
    assert "fixture_password" not in str(caught.value)


def test_transport_error(config):
    with pytest.raises(XUITransportError):
        client(config, urllib.error.URLError("offline")).request("GET", "/test")


def test_invalid_json(config):
    with pytest.raises(XUIResponseError, match="не JSON"):
        client(config, b"not json").request("GET", "/test")


def test_unexpected_json_shape(config):
    with pytest.raises(XUIResponseError, match="формат JSON"):
        client(config, []).request("GET", "/test")


def test_diagnostics_redact_password(config):
    with pytest.raises(XUIResponseError) as caught:
        client(config, b"invalid fixture_user fixture_password response").request("GET", "/test")
    assert "fixture_password" not in str(caught.value)
    assert "fixture_user" not in str(caught.value)


def test_get_client_semantics(config):
    assert client(config, {"success": True, "obj": {"client": {}}}).get_client("demo") == {"client": {}}
    assert client(config, {"success": False}).get_client("demo") is None


def test_create_failure(config):
    with pytest.raises(XUIOperationError, match="rejected"):
        client(config, {"success": False, "msg": "rejected"}).create_client({})


def test_hwid_email_encoding_and_csrf(config):
    api = client(config, {"success": True, "obj": []})
    api.csrf = "csrf_fixture"
    api.get_hwids("name+test@example")
    request = api.opener.requests[0][0]
    assert request.full_url.endswith("/hwids/name%2Btest%40example")
    assert request.get_header("X-csrf-token") == "csrf_fixture"


def test_delete_device_endpoint(config):
    api = client(config, {"success": True})
    api.csrf = "csrf_fixture"
    api.delete_hwid("name+test", 17)
    request = api.opener.requests[0][0]
    assert request.method == "DELETE"
    assert request.full_url.endswith("/hwids/name%2Btest/17")
    assert request.get_header("X-csrf-token") == "csrf_fixture"


def test_mutating_operation_has_csrf_header(config):
    api = client(config, {"success": True})
    api.csrf = "csrf_fixture"
    api.update_client("demo", {"email": "demo", "inboundIds": [2, 3]})
    assert api.opener.requests[0][0].get_header("X-csrf-token") == "csrf_fixture"


@pytest.mark.parametrize(("inbound_ids", "query"), [
    ([5], "5"),
    ([2, 3], "2%2C3"),
])
def test_modern_update_contract_is_flat_with_comma_separated_inbound_query(
        config, inbound_ids, query):
    api = client(config, {"success": True})
    api.csrf = "csrf_fixture"
    api.update_client("Testrouter__mobile", {
        "email": "Testrouter__mobile", "uuid": "fixture-uuid",
        "limitHwid": 0, "inboundIds": inbound_ids,
    })
    request = api.opener.requests[0][0]
    assert request.full_url == (
        config.xui_base
        + f"/panel/api/clients/update/Testrouter__mobile?inboundIds={query}"
    )
    assert json.loads(request.data) == {
        "email": "Testrouter__mobile", "uuid": "fixture-uuid", "limitHwid": 0,
    }
    assert "client" not in json.loads(request.data)


@pytest.mark.parametrize(("method_name", "args", "http_method", "path"), [
    ("list_inbounds", (), "GET", "/panel/api/inbounds/list"),
    ("create_client", ({"client": {}},), "POST", "/panel/api/clients/add"),
    ("update_client", ("name+test", {"email": "name+test"}), "POST",
     "/panel/api/clients/update/name%2Btest"),
    ("delete_client", ("name+test",), "POST", "/panel/api/clients/del/name%2Btest"),
    ("reset_hwids", ("name+test",), "DELETE", "/panel/api/clients/hwids/name%2Btest"),
])
def test_endpoint_contracts(config, method_name, args, http_method, path):
    api = client(config, {"success": True, "obj": []})
    api.csrf = "csrf_fixture"
    getattr(api, method_name)(*args)
    request = api.opener.requests[0][0]
    assert request.method == http_method
    assert request.full_url == config.xui_base + path
    if http_method != "GET":
        assert request.get_header("X-csrf-token") == "csrf_fixture"


def test_low_level_layer_has_no_cli_side_effects():
    import ast
    from pathlib import Path
    tree = ast.parse(Path("src/integrations/xui/client.py").read_text(encoding="utf-8"))
    calls = [node.func.id for node in ast.walk(tree)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)]
    assert "print" not in calls and "die" not in calls and "exit" not in calls


def test_external_links_exact_contract(config):
    from src.models import ExternalLinkInput
    api = client(config, {"success": True})
    api.csrf = "csrf_fixture"
    api.set_external_links("name+test", [
        ExternalLinkInput("link", "https://other.example", "other"),
        ExternalLinkInput("subscription", "https://sub.example.test/mobile", "karina-mobile"),
    ])
    request = api.opener.requests[0][0]
    assert request.method == "POST"
    assert request.full_url.endswith("/panel/api/clients/name%2Btest/externalLinks")
    assert json.loads(request.data) == {"externalLinks": [
        {"kind": "link", "value": "https://other.example", "remark": "other"},
        {"kind": "subscription", "value": "https://sub.example.test/mobile",
         "remark": "karina-mobile"},
    ]}


@pytest.mark.parametrize(("method", "suffix"), [
    ("attach_inbounds", "attach"), ("detach_inbounds", "detach"),
])
def test_inbound_membership_exact_contract(config, method, suffix):
    api = client(config, {"success": True})
    api.csrf = "csrf_fixture"
    getattr(api, method)("demo", [5])
    request = api.opener.requests[0][0]
    assert request.full_url.endswith(f"/panel/api/clients/demo/{suffix}")
    assert json.loads(request.data) == {"inboundIds": [5]}
