import json
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.services import ValidationError


@pytest.fixture
def cli(source_functions):
    service = SimpleNamespace(delete_client=Mock(return_value=SimpleNamespace(
        removed=True, file_cleanup_warning=None,
    )))

    def die(message):
        raise SystemExit(message)

    functions = source_functions(
        "karina_user.py",
        {"delete_client_impl", "cmd_delete", "cmd_delete_confirmed"},
        build_service=lambda: service,
        die=die,
        json=json,
        sys=sys,
        ClientService=__import__("src.services", fromlist=["ClientService"]).ClientService,
    )
    return functions, service


def test_shared_delete_and_confirmation(cli, capsys):
    functions, _ = cli
    implementation = Mock(return_value={"vpn_deleted": True, "warnings": []})
    functions["delete_client_impl"] = implementation
    functions["input"] = lambda prompt: "NO"
    functions["cmd_delete"](["demo"])
    implementation.assert_not_called()

    functions["input"] = lambda prompt: "YES"
    functions["cmd_delete"](["demo"])
    implementation.assert_called_once_with("demo")

    functions["input"] = lambda prompt: pytest.fail("confirmed delete must not prompt")
    capsys.readouterr()
    functions["cmd_delete_confirmed"](["demo"])
    assert json.loads(capsys.readouterr().out)["vpn_deleted"] is True
    assert implementation.call_count == 2


def test_delete_adapter_maps_service_result(cli):
    functions, service = cli
    service.delete_client.return_value.file_cleanup_warning = "synthetic warning"
    result = functions["delete_client_impl"]("demo")
    assert result == {"vpn_deleted": True, "warnings": ["synthetic warning"]}
    service.delete_client.assert_called_once_with("demo")


@pytest.mark.parametrize("args", [[], ["demo", "extra"]])
def test_confirmed_validates_argument_count(cli, args):
    functions, service = cli
    with pytest.raises(SystemExit):
        functions["cmd_delete_confirmed"](args)
    service.delete_client.assert_not_called()


def test_confirmed_rejects_invalid_email(cli):
    functions, service = cli
    with pytest.raises(ValidationError):
        functions["cmd_delete_confirmed"](["../demo"])
    service.delete_client.assert_not_called()
