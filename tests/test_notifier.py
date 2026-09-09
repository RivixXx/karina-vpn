from contextlib import closing
import sqlite3
from types import SimpleNamespace as NS

import pytest

from src.models import ExpiringClient


def run(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    finally:
        coroutine.close()
    pytest.fail("Unexpected asynchronous I/O")


@pytest.fixture
def notifier(source_functions, local_db):
    names = {
        "get_tg_id", "already_sent", "mark_sent", "notification_stage",
        "notification_text", "process_expiring_client", "run_notification_pass",
    }
    functions = source_functions(
        "notifier.py", names, db_connect=local_db, sqlite3=sqlite3,
        time=NS(time=lambda: 1000), LOGGER=NS(warning=lambda *args, **kwargs: None),
    )
    functions["_connect"] = local_db
    return functions


def expiring(email="demo_target", days=1.0, expiry=2_000_000_000_000,
             enabled=True, expiry_text="18.05.2033 06:33"):
    return ExpiringClient(email, expiry, days, enabled, expiry_text)


@pytest.mark.parametrize(("days", "expected"), [
    (7.5001, None), (7.5, 7), (7.4999, 7),
    (3.5001, 7), (3.5, 3), (3.4999, 3),
    (1.5001, 3), (1.5, 1), (1.4999, 1),
    (0.5001, 1), (0.5, 0), (0.4999, 0),
])
def test_stage_boundaries(notifier, days, expected):
    assert notifier["notification_stage"](days) == expected


@pytest.mark.parametrize("client", [
    expiring(enabled=False),
    expiring(days=-0.01, expiry=1_000),
    expiring(expiry=0),
])
def test_disabled_expired_and_unlimited_are_ignored(notifier, client):
    sender = __import__("unittest.mock", fromlist=["AsyncMock"]).AsyncMock()
    assert run(notifier["process_expiring_client"](
        client, sender, connect=notifier["_connect"],
    )) == "ineligible"
    sender.assert_not_awaited()


def test_no_telegram_binding_is_ignored(notifier):
    sender = __import__("unittest.mock", fromlist=["AsyncMock"]).AsyncMock()
    result = run(notifier["process_expiring_client"](
        expiring("not_linked"), sender, connect=notifier["_connect"],
    ))
    assert result == "not_linked"
    sender.assert_not_awaited()


def insert_sent(connect, email, expiry_key, stage):
    with closing(connect()) as db, db:
        db.execute(
            "INSERT INTO notifications_sent "
            "(email, expiry_key, notification_day, sent_at) VALUES (?, ?, ?, 0)",
            (email, expiry_key, stage),
        )


def sent_keys(connect, email="demo_target"):
    with closing(connect()) as db:
        return db.execute(
            "SELECT expiry_key, notification_day FROM notifications_sent "
            "WHERE email = ? AND notification_day = 1 ORDER BY id", (email,),
        ).fetchall()


def test_already_sent_stable_key_is_ignored(notifier):
    client = expiring()
    insert_sent(notifier["_connect"], client.email, str(client.expiry_time_ms), 1)
    sender = __import__("unittest.mock", fromlist=["AsyncMock"]).AsyncMock()
    assert run(notifier["process_expiring_client"](
        client, sender, connect=notifier["_connect"],
    )) == "already_sent"
    sender.assert_not_awaited()


def test_legacy_localized_expiry_key_is_recognized(notifier):
    client = expiring()
    insert_sent(notifier["_connect"], client.email, client.expiry_text, 1)
    sender = __import__("unittest.mock", fromlist=["AsyncMock"]).AsyncMock()
    assert run(notifier["process_expiring_client"](
        client, sender, connect=notifier["_connect"],
    )) == "already_sent"
    sender.assert_not_awaited()


def test_successful_send_is_recorded_with_stable_key(notifier):
    client = expiring()
    sender = __import__("unittest.mock", fromlist=["AsyncMock"]).AsyncMock()
    result = run(notifier["process_expiring_client"](
        client, sender, connect=notifier["_connect"], now_provider=lambda: 123,
    ))
    assert result == "sent"
    sender.assert_awaited_once_with(1, 1)
    assert [(row[0], row[1]) for row in sent_keys(notifier["_connect"])] == [
        (str(client.expiry_time_ms), 1),
    ]


def test_failed_send_is_not_recorded(notifier):
    async def fail(*args):
        raise RuntimeError("synthetic Telegram failure")

    with pytest.raises(RuntimeError):
        run(notifier["process_expiring_client"](
            expiring(), fail, connect=notifier["_connect"],
        ))
    assert sent_keys(notifier["_connect"]) == []


def test_extension_with_new_expiry_can_notify_same_stage_again(notifier):
    first = expiring(expiry=2_000_000_000_000)
    insert_sent(notifier["_connect"], first.email, str(first.expiry_time_ms), 1)
    extended = expiring(expiry=2_100_000_000_000, expiry_text="14.07.2036 04:00")
    sender = __import__("unittest.mock", fromlist=["AsyncMock"]).AsyncMock()
    assert run(notifier["process_expiring_client"](
        extended, sender, connect=notifier["_connect"],
    )) == "sent"
    assert len(sent_keys(notifier["_connect"])) == 2


def test_one_client_failure_does_not_abort_remaining_clients(notifier):
    service = NS(get_expiring=lambda days: [expiring("demo_target"), expiring("demo_other")])
    calls = []

    async def sender(tg_id, stage):
        calls.append((tg_id, stage))
        if tg_id == 1:
            raise RuntimeError("synthetic")

    result = run(notifier["run_notification_pass"](
        service, sender, connect=notifier["_connect"],
    ))
    assert result == [("demo_target", "error"), ("demo_other", "sent")]
    assert calls == [(1, 1), (2, 1)]
    assert sent_keys(notifier["_connect"], "demo_target") == []
    assert len(sent_keys(notifier["_connect"], "demo_other")) == 1


def test_notifier_uses_service_candidates(notifier):
    service = NS(get_expiring=__import__("unittest.mock", fromlist=["Mock"]).Mock(return_value=[]))
    sender = __import__("unittest.mock", fromlist=["AsyncMock"]).AsyncMock()
    assert run(notifier["run_notification_pass"](service, sender)) == []
    service.get_expiring.assert_called_once_with(8)
    sender.assert_not_awaited()


def test_database_connections_are_closed(notifier):
    connections = []

    def connect():
        connection = notifier["_connect"]()
        connections.append(connection)
        return connection

    sender = __import__("unittest.mock", fromlist=["AsyncMock"]).AsyncMock()
    run(notifier["process_expiring_client"](expiring(), sender, connect=connect))
    assert connections
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")


def test_source_has_no_cli_parser_or_subprocess():
    source = __import__("pathlib").Path("src/notifier.py").read_text(encoding="utf-8")
    assert "subprocess" not in source
    assert "run_karina" not in source
    assert "parse_expiring" not in source
    assert "karina_user" not in source
