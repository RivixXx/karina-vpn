import json
from pathlib import Path
import sys
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest


@pytest.fixture
def cli(source_functions, tmp_path):
    api = NS(login=Mock(), get_client=Mock(return_value={"client": {"subId": "safe_id123"}}),
             delete_client=Mock())
    directory = tmp_path / "connect"
    directory.mkdir()
    def die(message):
        raise SystemExit(message)
    funcs = source_functions("karina_user.py", {"delete_client_impl", "cmd_delete", "cmd_delete_confirmed", "normalize_email"},
                             XUI=lambda: api, CONNECT_DIR=directory, die=die, json=json, sys=sys)
    return funcs, api, directory


def test_shared_delete_and_confirmation(cli, capsys):
    funcs, api, directory = cli
    impl = Mock(return_value={"vpn_deleted": True, "warnings": []})
    funcs["delete_client_impl"] = impl
    funcs["input"] = lambda prompt: "NO"
    funcs["cmd_delete"](["demo"])
    impl.assert_not_called()
    funcs["input"] = lambda prompt: "YES"
    funcs["cmd_delete"](["demo"])
    impl.assert_called_once_with("demo")
    funcs["input"] = lambda prompt: pytest.fail("Confirmed delete must not prompt")
    capsys.readouterr()
    funcs["cmd_delete_confirmed"](["demo"])
    assert json.loads(capsys.readouterr().out)["vpn_deleted"] is True
    assert impl.call_count == 2


def test_safe_files_only(cli):
    funcs, api, directory = cli
    for suffix in (".html", ".png", ".crypt5", ".keep"):
        (directory / ("safe_id123" + suffix)).write_text("synthetic")
    assert funcs["delete_client_impl"]("demo") == {"vpn_deleted": True, "warnings": []}
    assert [p.name for p in directory.iterdir()] == ["safe_id123.keep"]
    api.delete_client.assert_called_once_with("demo")


@pytest.mark.parametrize("subid", ["../something", "../../etc/passwd", "/absolute/path", "C:\\outside", "a/b", "a\\b", "short", "x" * 129, None])
def test_malicious_subid_skips_files_but_deletes_vpn(cli, subid):
    funcs, api, directory = cli
    outside = directory.parent / "something.html"
    outside.write_text("keep")
    inside = directory / "safe_id123.html"
    inside.write_text("keep")
    api.get_client.return_value = {"client": {"subId": subid}}
    result = funcs["delete_client_impl"]("demo")
    assert result["vpn_deleted"] and result["warnings"]
    api.delete_client.assert_called_once_with("demo")
    assert outside.read_text() == inside.read_text() == "keep"


def test_resolved_path_outside_skipped(cli, monkeypatch):
    funcs, api, directory = cli
    outside = directory.parent / "outside.html"
    outside.write_text("keep")
    inside = directory / "safe_id123.html"
    inside.write_text("keep")
    original = Path.resolve
    monkeypatch.setattr(Path, "resolve", lambda path: outside if path == inside else original(path))
    result = funcs["delete_client_impl"]("demo")
    assert result["warnings"]
    assert inside.exists() and outside.exists()


def test_xui_delete_failure_leaves_files(cli):
    funcs, api, directory = cli
    target = directory / "safe_id123.html"
    target.write_text("keep")
    api.delete_client.side_effect = SystemExit("failure")
    with pytest.raises(SystemExit):
        funcs["delete_client_impl"]("demo")
    assert target.exists()


def test_unlink_failure_is_reported_after_vpn_success(cli, monkeypatch):
    funcs, api, directory = cli
    def fail(path):
        raise PermissionError("synthetic")
    monkeypatch.setattr(Path, "unlink", fail)
    result = funcs["delete_client_impl"]("demo")
    assert result["vpn_deleted"] is True and len(result["warnings"]) == 3
    api.delete_client.assert_called_once_with("demo")


@pytest.mark.parametrize("args", [[], ["demo", "extra"], ["../demo"]])
def test_confirmed_validates_arguments(cli, args):
    funcs, api, directory = cli
    with pytest.raises(SystemExit):
        funcs["cmd_delete_confirmed"](args)
    api.delete_client.assert_not_called()
