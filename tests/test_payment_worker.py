import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from src import payment_worker


def run(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    finally:
        coroutine.close()


def test_delivery_cycle_failure_is_isolated(monkeypatch):
    deliver = AsyncMock(side_effect=RuntimeError("temporary"))
    sleep = AsyncMock(side_effect=asyncio.CancelledError())
    monkeypatch.setattr(payment_worker.asyncio, "sleep", sleep)
    app = NS(bot_data={"deliver_payment_effects": deliver})
    with pytest.raises(asyncio.CancelledError):
        run(payment_worker.delivery_loop(app))
    deliver.assert_awaited_once_with(app)
    sleep.assert_awaited_once_with(30)


def test_shutdown_cancels_worker_and_is_idempotent():
    class Task:
        cancelled = False

        def cancel(self):
            self.cancelled = True

        def __await__(self):
            yield from ()
            raise asyncio.CancelledError()

    task = Task()
    app = NS(bot_data={payment_worker.TASK_KEY: task})
    run(payment_worker.stop_delivery(app))
    run(payment_worker.stop_delivery(app))
    assert task.cancelled and not app.bot_data
