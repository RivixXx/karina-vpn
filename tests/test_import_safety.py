import importlib
import sys
from pathlib import Path

import pytest

from src.integrations.xui import XUIOperationError


def test_karina_user_import_has_no_production_access(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Import attempted production access")

    monkeypatch.setattr("pathlib.Path.read_text", blocked)
    monkeypatch.setattr("urllib.request.build_opener", blocked)
    monkeypatch.setattr("urllib.request.urlopen", blocked)
    monkeypatch.setattr("sqlite3.connect", blocked)
    monkeypatch.setattr("subprocess.Popen", blocked)
    monkeypatch.setattr(sys, "exit", blocked)
    sys.modules.pop("src.karina_user", None)
    module = importlib.import_module("src.karina_user")
    assert module.CONFIG.as_posix() == "/etc/karina-vpn/config.env"


def test_direct_script_import_is_safe(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Import attempted production access")

    monkeypatch.syspath_prepend(str(Path("src").resolve()))
    monkeypatch.setattr("pathlib.Path.read_text", blocked)
    monkeypatch.setattr("urllib.request.build_opener", blocked)
    monkeypatch.setattr("sqlite3.connect", blocked)
    monkeypatch.setattr("subprocess.Popen", blocked)
    sys.modules.pop("karina_user", None)
    module = importlib.import_module("karina_user")
    assert module.CONFIG.as_posix() == "/etc/karina-vpn/config.env"


def test_client_service_import_has_no_production_access(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Service import attempted production access")

    monkeypatch.setattr("pathlib.Path.read_text", blocked)
    monkeypatch.setattr("urllib.request.build_opener", blocked)
    monkeypatch.setattr("sqlite3.connect", blocked)
    monkeypatch.setattr("subprocess.Popen", blocked)
    module = importlib.import_module("src.services.client_service")
    importlib.reload(module)
    assert module.ClientService.__name__ == "ClientService"


def test_cli_formats_expected_xui_errors(monkeypatch, capsys):
    module = importlib.import_module("src.karina_user")
    monkeypatch.setattr(module, "cmd_info", lambda args: (_ for _ in ()).throw(
        XUIOperationError("synthetic failure")
    ))
    monkeypatch.setattr(sys, "argv", ["karina-user", "info", "demo"])
    with pytest.raises(SystemExit) as exited:
        module.main()
    assert exited.value.code == 1
    assert capsys.readouterr().err == "Ошибка: synthetic failure\n"
