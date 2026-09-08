import pytest


def test_expiring_fixture(pure_functions, fixture_text):
    functions = pure_functions("notifier.py")
    result = functions["parse_expiring"](fixture_text("cli_expiring.txt"))
    assert result == [
        {"email": "demo_week", "days": 7.2},
        {"email": "demo_three", "days": 3.2},
        {"email": "demo_one", "days": 1.2},
        {"email": "demo_today", "days": 0.2},
    ]
    assert [functions["notification_stage"](r["days"]) for r in result] == [7, 3, 1, 0]


@pytest.mark.parametrize(("days", "expected"), [
    (7.5, 7), (7, 7), (7.5001, None), (8, None),
    (3.5001, 7), (3.5, 3), (3, 3),
    (1.5001, 3), (1.5, 1), (1, 1),
    (0.5001, 1), (0.5, 0), (0, 0), (-1, 0),
])
def test_stage_boundaries(pure_functions, days, expected):
    # Negative days map to 0; the CLI filters expired clients upstream.
    assert pure_functions("notifier.py")["notification_stage"](days) == expected


def test_comma_decimal_and_negative_days(pure_functions):
    assert pure_functions("notifier.py")["parse_expiring"](
        "demo_left осталось 3,2 дн.\ndemo_past осталось -0.2 дн."
    ) == [{"email": "demo_left", "days": 3.2}, {"email": "demo_past", "days": -0.2}]


@pytest.mark.parametrize("text", ["", "Таких подписок нет.", "demo осталось ? дн."])
def test_non_client_lines(pure_functions, text):
    assert pure_functions("notifier.py")["parse_expiring"](text) == []
