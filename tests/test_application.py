from types import SimpleNamespace
from unittest.mock import Mock

from src import application


def test_factory_loads_config_logs_in_and_injects_service(monkeypatch, tmp_path):
    config = SimpleNamespace(
        sub_base="https://sub.example.test",
        connect_base="https://vpn.example.test/connect",
    )
    xui = SimpleNamespace(login=Mock())
    service_constructor = Mock(return_value=object())
    monkeypatch.setattr(application, "load_config", Mock(return_value=config))
    monkeypatch.setattr(application, "XUIClient", Mock(return_value=xui))
    monkeypatch.setattr(application, "ClientService", service_constructor)

    result = application.build_client_service("synthetic.env", connect_dir=tmp_path)

    assert result is service_constructor.return_value
    application.load_config.assert_called_once_with("synthetic.env")
    application.XUIClient.assert_called_once_with(config)
    xui.login.assert_called_once_with()
    kwargs = service_constructor.call_args.kwargs
    assert kwargs["config"] is config and kwargs["xui"] is xui
    assert callable(kwargs["issue_subscription"])
    assert kwargs["connect_dir"] == tmp_path
