"""Bound HTTP service tests; source/install/process-exit proof stays separate."""

import asyncio
import importlib.util
import signal
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sds200.web_dashboard import WEB_DASHBOARD_HOME_ASSISTANT_INGRESS_CLIENT as PEER
from sds200.web_supplemental import DEMAND_PATH

from .test_supplemental_recording_web_peer import m as peer
from .test_supplemental_recording_web_peer import tree as tree
from .test_supplemental_recording_web_scope import m as scope

sys.modules["supplemental_recording_web_peer"] = peer
sys.modules["supplemental_recording_web_scope"] = scope
PATH = Path(peer.__file__).with_name("supplemental_recording_web_service.py")
SPEC = importlib.util.spec_from_file_location("supplemental_recording_web_service", PATH)
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


def service(tree, **changes):
    owner = tree.bind(**changes)
    return m.Service(owner, home_assistant_ingress=True), owner


def http(path="/", method="GET"):
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [],
        "client": (PEER, 1234),
        "server": ("test", 80),
    }


def test_native_page_file_and_auth_remain_scoped_to_original_peer(tree):
    app, owner = service(tree)
    with TestClient(app, client=(PEER, 1)) as client:
        assert 'data-sdsctl-supplemental="demand"' in client.get("/").text
        assert client.get("/assets/mimic-sds.js").status_code == 200
        result = client.get("/api/v1/recordings/file/2026/test.wav")
        assert result.status_code == 200 and result.content == b"RIFFtest"
        assert client.post("/api/v1/recording/start").status_code == 404
        assert client.post("/api/v1/home-assistant/integration/install").status_code == 404
        assert not app._active
    assert owner._closed and not owner._connections


def test_untrusted_ingress_peer_still_fails_before_ipc(tree):
    app, owner = service(tree)
    with TestClient(app, client=("127.0.0.1", 1)) as client:
        assert client.get("/").status_code == 403
        assert not owner._connections


def test_invalid_service_auth_closes_owned_peers(tree):
    owner = tree.bind()
    with pytest.raises(ValueError):
        m.Service(owner)
    assert owner._closed and not owner._handles
    with pytest.raises(ValueError):
        m.Service(object(), home_assistant_ingress=True)


def test_expired_service_returns_503_without_reading_body(tree):
    app, owner = service(tree)
    owner.close()

    async def run():
        output = []

        async def receive():
            pytest.fail("Expired service read a request body")

        async def send(value):
            output.append(value)

        await app(http(DEMAND_PATH, "POST"), receive, send)
        assert [value["type"] for value in output] == ["http.response.start", "http.response.body"]
        assert output[0]["status"] == 503
        assert dict(output[0]["headers"])[b"cache-control"] == b"no-store"

    asyncio.run(run())


def test_native_slow_demand_body_is_cancelled_on_original_expiry(tree):
    app, owner = service(tree, deadline=time.monotonic() + 0.3)

    async def run():
        output = []
        entered, cancelled = asyncio.Event(), asyncio.Event()

        async def receive():
            entered.set()
            try:
                await asyncio.Future()
            finally:
                cancelled.set()

        async def send(value):
            output.append(value)

        task = asyncio.create_task(app(http(DEMAND_PATH, "POST"), receive, send))
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.wait_for(task, 2)
        assert cancelled.is_set() and not app._active
        assert output[0]["status"] == 503
        assert owner._closed and not owner._connections

    asyncio.run(run())


def test_original_actor_failure_closes_inflight_native_audio_stream(tree):
    app, owner = service(tree)

    async def run():
        output = []
        started = asyncio.Event()

        async def receive():
            await asyncio.Future()

        async def send(value):
            output.append(value)
            if value["type"] == "http.response.start":
                started.set()

        task = asyncio.create_task(app(http("/api/v1/audio"), receive, send))
        await asyncio.wait_for(started.wait(), 2)
        signal.pidfd_send_signal(tree.handles[2], signal.SIGSTOP)
        with pytest.raises((RuntimeError, ExceptionGroup)):
            await asyncio.wait_for(task, 2)
        assert not app._active and owner._closed and not owner._connections
        assert not any(value.get("more_body") is False for value in output)

    asyncio.run(run())


def test_browser_disconnect_cancels_service_tasks_but_not_original_owner(tree):
    app, owner = service(tree)

    async def run():
        entered = asyncio.Event()

        async def receive():
            entered.set()
            await asyncio.Future()

        async def send(value):
            pass

        task = asyncio.create_task(app(http(DEMAND_PATH, "POST"), receive, send))
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not app._active and owner.check() > 0

    asyncio.run(run())


def test_late_response_cannot_be_reported_as_success(tree):
    app, owner = service(tree)

    async def late_response(selected_scope, receive, send):
        # Explicit fixture for a late native handler, not patched product code.
        owner.close()
        await send({"type": "http.response.start", "status": 200, "headers": []})

    app._surface = late_response

    async def run():
        output = []

        async def receive():
            pytest.fail("Unexpected body read")

        async def send(value):
            output.append(value)

        await app(http(), receive, send)
        assert output[0]["status"] == 503 and len(output) == 2

    asyncio.run(run())


def test_uncancellable_fixture_task_is_retained_and_never_claimed_exited(tree):
    app, owner = service(tree)

    async def run():
        release = asyncio.Event()

        async def stalled(selected_scope, receive, send):
            owner.close()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                await release.wait()

        app._surface = stalled

        async def receive():
            await asyncio.Future()

        async def send(value):
            pass

        try:
            with pytest.raises(RuntimeError, match="cleanup is unconfirmed"):
                await asyncio.wait_for(app(http(), receive, send), 2)
            assert len(app._active) == 1 and owner._closed
        finally:
            # Only release our explicit fixture. This is not a production
            # retry or an assertion that cancelling a worker proves its exit.
            release.set()
            await asyncio.gather(*app._active)
            await asyncio.sleep(0)
        assert not app._active

    asyncio.run(run())


def test_quiet_browser_audio_disconnect_preserves_other_request(tree):
    app, owner = service(tree)

    async def run():
        started = [asyncio.Event(), asyncio.Event()]
        disconnect = [asyncio.Event(), asyncio.Event()]

        async def request(index):
            async def receive():
                await disconnect[index].wait()
                return {"type": "http.disconnect"}

            async def send(value):
                if value["type"] == "http.response.start":
                    assert value["status"] == 200
                    started[index].set()

            await app(http("/api/v1/audio"), receive, send)

        tasks = [asyncio.create_task(request(index)) for index in range(2)]
        try:
            await asyncio.wait_for(asyncio.gather(*(value.wait() for value in started)), 2)
            assert len(owner._connections) == len(owner._requests) == 2
            disconnect[0].set()
            await asyncio.wait_for(tasks[0], 1)
            assert not tasks[1].done()
            assert len(owner._connections) == len(owner._requests) == 1
            assert owner.check() > 0
            disconnect[1].set()
            await asyncio.wait_for(tasks[1], 1)
            assert owner.check() > 0 and not owner._connections and not owner._requests
            assert not app._active
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(run())


def test_browser_file_disconnect_wakes_read_before_native_close_lock(tree):
    app, owner = service(tree)

    async def run():
        started, disconnect = asyncio.Event(), asyncio.Event()

        async def receive():
            await disconnect.wait()
            return {"type": "http.disconnect"}

        async def send(value):
            if value["type"] == "http.response.start":
                assert value["status"] == 200
                started.set()

        task = asyncio.create_task(app(http("/api/v1/recordings/file/blocked.wav"), receive, send))
        try:
            await asyncio.wait_for(started.wait(), 2)
            # Let the real native download enter its locked read. This fixture
            # intentionally supplies only half its declared body, no user file.
            await asyncio.sleep(0.05)
            began = time.monotonic()
            disconnect.set()
            await asyncio.wait_for(task, 1)
            assert time.monotonic() - began < 1
            assert owner.check() > 0 and not owner._connections and not owner._requests
            assert not app._active
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())
