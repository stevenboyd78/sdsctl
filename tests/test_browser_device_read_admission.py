"""Bound the physical queue, not just the number of still-waiting HTTP tasks."""
from __future__ import annotations

import asyncio
import threading

import pytest

import sds200.browser_device_http as device_http
from sds200.browser_device_http import _Busy, _Workers


@pytest.mark.parametrize("ending", ["cancel", "timeout", "close"])
def test_read_queue_bound_and_abandoned_work_never_runs(monkeypatch, ending) -> None:
    monkeypatch.setattr(device_http, "_READ_OUTSTANDING_LIMIT", 4)
    monkeypatch.setattr(device_http, "_READ_WAIT_SECONDS", 0.1 if ending == "timeout" else 2)
    workers = _Workers(read_only=True)
    entered = [threading.Event(), threading.Event()]
    finish = threading.Event()
    ran, abandoned = [], []

    def operation(index):
        ran.append(index)
        if index < 2:
            entered[index].set()
            assert finish.wait(3)
        return index

    async def check():
        tasks = [asyncio.create_task(workers.run(lambda i=i: operation(i), abandoned.append))
                 for i in range(4)]
        try:
            for event in entered:
                assert await asyncio.to_thread(event.wait, 1)
            with pytest.raises(_Busy):
                await workers.run(lambda: operation(4))
            assert workers._pool._work_queue.qsize() == 2
            if ending == "cancel":
                for task in tasks:
                    task.cancel()
                results = await asyncio.gather(*tasks, return_exceptions=True)
                assert all(isinstance(result, asyncio.CancelledError) for result in results)
            elif ending == "timeout":
                results = await asyncio.gather(*tasks, return_exceptions=True)
                assert all(isinstance(result, _Busy) for result in results)
            else:
                workers.close()
            # Cancellation/timeout must not admit another physical executor item.
            for _ in range(20):
                with pytest.raises(_Busy):
                    await workers.run(lambda: operation(4))
            assert workers._pool._work_queue.qsize() == (3 if ending == "close" else 2)
            finish.set()
            results = await asyncio.gather(*tasks, return_exceptions=True)
            if ending == "close":
                assert results[:2] == [0, 1]
                assert all(isinstance(result, _Busy) for result in results[2:])
            await asyncio.to_thread(workers._pool.shutdown, wait=True)
            assert sorted(ran) == [0, 1]
            assert sorted(abandoned) == ([] if ending == "close" else [0, 1])
        finally:
            finish.set()
            await asyncio.gather(*tasks, return_exceptions=True)
            workers.close()

    asyncio.run(check())


def test_read_capacity_returns_after_completion() -> None:
    workers = _Workers(read_only=True)

    async def check():
        for _ in range(4):
            assert await asyncio.gather(*(workers.run(lambda i=i: i) for i in range(16))) == list(
                range(16)
            )

    try:
        asyncio.run(check())
    finally:
        workers.close()


def test_submission_failure_returns_reserved_capacity(monkeypatch) -> None:
    workers = _Workers(read_only=True)

    def fail(*args):
        raise RuntimeError("executor unavailable")

    monkeypatch.setattr(workers._pool, "submit", fail)

    async def check():
        for _ in range(40):
            with pytest.raises(_Busy):
                await workers.run(lambda: None)
        assert workers._slots._value == device_http._READ_OUTSTANDING_LIMIT

    try:
        asyncio.run(check())
    finally:
        workers.close()


def test_operation_timeout_is_not_confused_with_admission_timeout() -> None:
    workers = _Workers(read_only=True)

    def operation():
        raise TimeoutError("operation's own timeout")

    async def check():
        with pytest.raises(TimeoutError, match="operation's own timeout"):
            await workers.run(operation)

    try:
        asyncio.run(check())
    finally:
        workers.close()
