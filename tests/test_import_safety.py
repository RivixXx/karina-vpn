import importlib
import sys
from types import ModuleType, SimpleNamespace
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


def test_billing_modules_import_without_external_access(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Billing import attempted external access")

    monkeypatch.setattr("pathlib.Path.read_text", blocked)
    monkeypatch.setattr("urllib.request.build_opener", blocked)
    monkeypatch.setattr("sqlite3.connect", blocked)
    for name in ("src.models.billing", "src.repositories.billing_repository",
                 "src.services.billing_service"):
        module = importlib.import_module(name)
        importlib.reload(module)


def test_application_import_has_no_production_access(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Application import attempted production access")

    monkeypatch.setattr("pathlib.Path.read_text", blocked)
    monkeypatch.setattr("urllib.request.build_opener", blocked)
    module = importlib.import_module("src.application")
    importlib.reload(module)
    assert callable(module.build_client_service)


def test_bot_import_has_no_production_access(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Bot import attempted production access")

    telegram = ModuleType("telegram")
    telegram.InlineKeyboardButton = type("InlineKeyboardButton", (), {})
    telegram.InlineKeyboardMarkup = type("InlineKeyboardMarkup", (), {})
    telegram.Update = type("Update", (), {"ALL_TYPES": ()})
    constants = ModuleType("telegram.constants")
    constants.ChatType = SimpleNamespace(PRIVATE="private")
    constants.ParseMode = SimpleNamespace(MARKDOWN_V2="MarkdownV2")
    extension = ModuleType("telegram.ext")
    extension.Application = type("Application", (), {})
    extension.CallbackQueryHandler = type("CallbackQueryHandler", (), {})
    extension.CommandHandler = type("CommandHandler", (), {})
    extension.ContextTypes = SimpleNamespace(DEFAULT_TYPE=object)
    monkeypatch.setitem(sys.modules, "telegram", telegram)
    monkeypatch.setitem(sys.modules, "telegram.constants", constants)
    monkeypatch.setitem(sys.modules, "telegram.ext", extension)
    monkeypatch.setattr("pathlib.Path.read_text", blocked)
    monkeypatch.setattr("urllib.request.build_opener", blocked)
    monkeypatch.setattr("sqlite3.connect", blocked)
    sys.modules.pop("src.bot", None)
    module = importlib.import_module("src.bot")
    assert module.BOT_TOKEN is None and module.ADMIN_TG_ID is None


def test_notifier_import_has_no_production_access(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Notifier import attempted production access")

    telegram = ModuleType("telegram")
    telegram.Bot = type("Bot", (), {})
    telegram.InlineKeyboardButton = type("InlineKeyboardButton", (), {})
    telegram.InlineKeyboardMarkup = type("InlineKeyboardMarkup", (), {})
    monkeypatch.setitem(sys.modules, "telegram", telegram)
    monkeypatch.setattr("pathlib.Path.read_text", blocked)
    monkeypatch.setattr("urllib.request.build_opener", blocked)
    monkeypatch.setattr("sqlite3.connect", blocked)
    sys.modules.pop("src.notifier", None)
    module = importlib.import_module("src.notifier")
    assert callable(module.run_notification_pass)


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
