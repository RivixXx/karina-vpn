from src.models import ClientBundle, ClientInfo, DeviceInfo, TrafficInfo
from src.telegram_format import markdown_v2_escape
import pytest


def load_formatters(source_functions):
    names = {
        "markdown_v2_escape", "bytes_to_human", "traffic_limit_text", "status_text",
        "format_client_profile", "format_devices", "format_traffic", "safe_user_error",
        "format_user_cabinet", "device_display_name", "device_slots_bar",
        "format_client_devices",
    }
    return source_functions("bot.py", names, _markdown_v2_escape=markdown_v2_escape,
                            LOGGER=type("Log", (), {"warning": lambda *a, **k: None})(),
                            datetime=__import__("datetime").datetime,
                            MOSCOW_TIMEZONE=__import__("datetime").timezone.utc)


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


@pytest.mark.parametrize(("used", "limit", "expected"), [
    (0, 5, "[□□□□□]"), (2, 5, "[■■□□□]"), (5, 5, "[■■■■■]"),
    (6, 20, "[■■■□□□□□□□]"),
])
def test_device_slots_progress_bar(source_functions, used, limit, expected):
    assert load_formatters(source_functions)["device_slots_bar"](used, limit) == expected


def test_customer_devices_render_real_fields_and_unknown_name(source_functions):
    functions = load_formatters(source_functions)
    devices = [
        DeviceInfo(7, "Phone", "Android", "15", "Happ", 1, 2),
        DeviceInfo(8, "", "", "", "", 0, 0),
    ]
    text = functions["format_client_devices"](devices, 5)
    assert "Занято слотов: 2 из 5" in text
    assert "Свободно слотов: 3" in text
    assert "Phone" in text and "Android 15" in text and "Happ" in text
    assert "устройство 2" in text
    assert "ID:" not in text


def test_customer_devices_full_and_empty_states(source_functions):
    functions = load_formatters(source_functions)
    empty = functions["format_client_devices"]([], 5)
    assert "Подключённых устройств пока нет" in empty and "Свободно слотов: 5" in empty
    full = functions["format_client_devices"]([
        DeviceInfo(index, f"Device {index}", "", "", "", 0, 0) for index in range(1, 6)
    ], 5)
    assert "Все доступные слоты заняты" in full and "Свободно слотов: 0" in full


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
    assert load_formatters(source_functions)["markdown_v2_escape"]("demo_client. (1)!") == r"demo\_client\. \(1\)\!"


def cabinet_client(*, enabled=True, expiry=2_100_000_000_000, count=2):
    return ClientInfo("private_name", "active", enabled, expiry, "10.12.2026 00:00",
                      count, 5, 0, 0, (1,), "hidden_subid", "https://hidden")


@pytest.mark.parametrize(("enabled", "expiry", "expected"), [
    (True, 2_100_000_000_000, "Подписка активна"),
    (True, 0, "без ограничений"),
    (True, 1_900_000_000_000, "Подписка закончилась"),
    (False, 2_100_000_000_000, "Подписка отключена"),
])
def test_user_cabinet_statuses(source_functions, enabled, expiry, expected):
    functions = load_formatters(source_functions)
    text = functions["format_user_cabinet"](
        ClientBundle(cabinet_client(enabled=enabled, expiry=expiry), None), None,
        now_ms=2_000_000_000_000,
    )
    assert expected in text and "private_name" not in text and "hidden_subid" not in text


def test_user_cabinet_mobile_states_and_markdown(source_functions):
    functions = load_formatters(source_functions)
    primary = cabinet_client()
    mobile = cabinet_client(count=0)
    normal = functions["format_user_cabinet"](
        ClientBundle(primary, mobile), TrafficInfo(12.4*1024**3, 50*1024**3, 0, 24.8),
        now_ms=2_000_000_000_000,
    )
    exhausted = functions["format_user_cabinet"](
        ClientBundle(primary, mobile), TrafficInfo(50*1024**3, 50*1024**3, 0, 100),
        now_ms=2_000_000_000_000,
    )
    unavailable = functions["format_user_cabinet"](
        ClientBundle(primary, mobile), None, True, now_ms=2_000_000_000_000)
    assert "12\\.4 / 50" in normal and "лимит исчерпан" in exhausted
    assert "Данные временно недоступны" in unavailable and "__mobile" not in normal+exhausted
