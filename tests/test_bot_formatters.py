from src.models import ClientInfo, DeviceInfo, TrafficInfo


def load_formatters(source_functions):
    names = {
        "esc", "bytes_to_human", "traffic_limit_text", "status_text",
        "format_client_profile", "format_devices", "format_traffic", "safe_user_error",
    }
    return source_functions("bot.py", names, LOGGER=type("Log", (), {"warning": lambda *a, **k: None})())


def test_profile_formatter_uses_typed_client(source_functions):
    functions = load_formatters(source_functions)
    client = ClientInfo(
        "demo", "active", True, 1, "15.01.2030 12:00", 1, 2,
        100 * 1024**3, 12 * 1024**3, (1,), "safe_id123", "https://example.test",
    )
    text = functions["format_client_profile"](client)
    assert r"15\.01\.2030 12:00" in text
    assert "1 / 2" in text and r"12\.0 ГБ" in text and "🟢" in text


def test_devices_formatter(source_functions):
    functions = load_formatters(source_functions)
    device = DeviceInfo(7, "Phone", "Android", "15", "Happ", 1, 2)
    text = functions["format_devices"]([device], 2)
    assert "Использовано: 1 / 2" in text
    assert "Phone" in text and "Android 15" in text and "Happ" in text


def test_traffic_formatter(source_functions):
    functions = load_formatters(source_functions)
    text = functions["format_traffic"](TrafficInfo(25, 100, 75, 25.0))
    assert "Осталось:     75 Б" in text and "25.0%" in text
    assert "Безлимит" in functions["format_traffic"](TrafficInfo(25, 0, None, None))


def test_safe_errors_hide_details(source_functions):
    functions = load_formatters(source_functions)
    error = RuntimeError("credential-like internal detail")
    client_text = functions["safe_user_error"](error)
    admin_text = functions["safe_user_error"](error, admin=True)
    assert "internal detail" not in client_text + admin_text
    assert "RuntimeError" in admin_text


def test_markdown_escape(source_functions):
    assert load_formatters(source_functions)["esc"]("demo_client. (1)!") == r"demo\_client\. \(1\)\!"
