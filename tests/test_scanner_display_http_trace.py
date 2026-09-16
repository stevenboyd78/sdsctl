from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import asdict

import pytest
from fastapi.testclient import TestClient

from sds200 import scanner_display_http_trace as module
from sds200.web_auth import WebDashboardAuthentication
from sds200.web_dashboard import create_web_dashboard_app
from tests.test_web_dashboard import FakeDaemonApiClient

LABEL = b"0123456789abcdef-1"
PRIVATE = "PRIVATE_SCANNER_COOKIE_PATH_BODY"


class Sink:
    def __init__(self):
        self.events = []

    def submit(self, event):
        self.events.append(event)
        return True


def scope(**changes):
    return {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/display-frame",
        "query_string": b"",
        "headers": [(module.TRACE_HEADER, LABEL)],
        "state": {},
        **changes,
    }


async def invoke(app, request_scope=None):
    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    await app(scope() if request_scope is None else request_scope, receive, send)
    return messages


async def response(request_scope, receive, send):
    module.mark_display_handler(request_scope)
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": PRIVATE.encode(), "more_body": False})


def test_records_each_boundary_without_recording_values_or_mutating_scope():
    clock = [10.0]

    async def delayed(request_scope, receive, send):
        clock[0] += 0.2
        module.mark_display_handler(request_scope)
        clock[0] += 0.3
        await send({"type": "http.response.start", "status": 200, "headers": []})
        clock[0] += 0.4
        await send({"type": "http.response.body", "body": PRIVATE.encode(), "more_body": True})
        clock[0] += 0.1
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    middleware = module.MimicRequestTimingMiddleware(delayed, clock=lambda: clock[0])
    middleware._sink = sink = Sink()
    incoming = scope(headers=[(module.TRACE_HEADER, LABEL), (b"cookie", PRIVATE.encode())])
    messages = asyncio.run(invoke(middleware, incoming))
    assert incoming["state"] == {}
    assert dict(messages[0]["headers"])[module.TRACE_HEADER] == LABEL
    assert [item.stage for item in sink.events] == [
        "received",
        "handler",
        "headers_sent",
        "body_sent",
    ]
    assert [item.elapsed_ms for item in sink.events] == [0, 200, 500, 1000]
    assert sink.events[-1].sent_bytes == len(PRIVATE) and sink.events[-1].status == 200
    assert PRIVATE not in repr([asdict(item) for item in sink.events])
    assert all(
        item.identifier == LABEL.decode() and item.utc.endswith("+00:00") for item in sink.events
    )


@pytest.mark.parametrize(
    "label",
    [
        b"",
        b"private-secret",
        b"f" * 1000,
        b"0123456789ABCDEF-1",
        b"0123456789abcdef-0",
        b"0123456789abcdef-1000000",
        b"0123456789abcdef-1\n",
        b"\xff" * 20,
    ],
)
def test_malformed_labels_are_ignored_without_echo_or_logging(label):
    middleware = module.MimicRequestTimingMiddleware(response)
    messages = asyncio.run(invoke(middleware, scope(headers=[(module.TRACE_HEADER, label)])))
    assert module.TRACE_HEADER not in dict(messages[0]["headers"])
    assert middleware._remaining == 512 and middleware._sink._thread is None


@pytest.mark.parametrize(
    "changes",
    [
        {"headers": []},
        {"headers": [(module.TRACE_HEADER, LABEL)] * 2},
        {"path": "/api/v1/status"},
        {"method": "POST"},
        {"query_string": b"secret=value"},
    ],
)
def test_only_explicit_unambiguous_frame_gets_can_trace(changes):
    middleware = module.MimicRequestTimingMiddleware(response)
    messages = asyncio.run(invoke(middleware, scope(**changes)))
    assert module.TRACE_HEADER not in dict(messages[0]["headers"])
    assert middleware._remaining == 512 and middleware._sink._thread is None


def test_rate_and_lifetime_budgets_limit_tracing_not_responses():
    clock = [0.0]
    middleware = module.MimicRequestTimingMiddleware(response, clock=lambda: clock[0])
    middleware._sink = sink = Sink()
    assert module.TRACE_HEADER in dict(asyncio.run(invoke(middleware))[0]["headers"])
    assert module.TRACE_HEADER not in dict(asyncio.run(invoke(middleware))[0]["headers"])
    for _ in range(511):
        clock[0] += 1
        asyncio.run(invoke(middleware))
    assert middleware._remaining == 0 and len(sink.events) == 512 * 4
    clock[0] += 100
    messages = asyncio.run(invoke(middleware))
    assert messages[0]["status"] == 200 and module.TRACE_HEADER not in dict(messages[0]["headers"])


@pytest.mark.parametrize("error", [RuntimeError(PRIVATE), asyncio.CancelledError(PRIVATE)])
def test_exceptions_keep_their_semantics_without_private_exception_logging(error):
    async def failing(request_scope, receive, send):
        module.mark_display_handler(request_scope)
        raise error

    middleware = module.MimicRequestTimingMiddleware(failing)
    middleware._sink = sink = Sink()
    with pytest.raises(type(error)):
        asyncio.run(invoke(middleware))
    assert [event.stage for event in sink.events] == [
        "received",
        "handler",
        "interrupted",
        "incomplete",
    ]
    assert PRIVATE not in repr(sink.events)


def test_broken_diagnostic_sink_never_replaces_response():
    class Broken:
        def submit(self, event):
            raise RuntimeError(PRIVATE)

    middleware = module.MimicRequestTimingMiddleware(response)
    middleware._sink = Broken()
    messages = asyncio.run(invoke(middleware))
    assert messages[0]["status"] == 200 and module.TRACE_HEADER not in dict(messages[0]["headers"])


def test_slow_logger_is_off_request_thread_and_queue_is_bounded(monkeypatch):
    entered, release, drained = threading.Event(), threading.Event(), threading.Event()
    thread_ids = []

    def slow_log(*args):
        thread_ids.append(threading.get_ident())
        entered.set()
        assert release.wait(3)
        if len(thread_ids) == 129:
            drained.set()

    monkeypatch.setattr(module.logger, "warning", slow_log)
    sink = module._TraceSink()
    event = module._Event(LABEL.decode(), "received", "2026-09-16T00:00:00+00:00", 0, 0, 0)
    try:
        assert sink.submit(event) and entered.wait(1)
        assert sum(sink.submit(event) for _ in range(140)) == 128
        assert sink._queue.qsize() == 128 and sink._dropped == 12
        middleware = module.MimicRequestTimingMiddleware(response)
        middleware._sink = sink
        messages = asyncio.run(invoke(middleware))
        assert messages[0]["status"] == 200  # Full queue does not block the request.
        assert module.TRACE_HEADER not in dict(messages[0]["headers"])
    finally:
        release.set()
    assert drained.wait(2) and all(value != threading.get_ident() for value in thread_ids)


def test_requested_trace_is_visible_at_default_warning_level_without_private_data():
    records = []
    complete = threading.Event()

    class Handler(logging.Handler):
        def emit(self, record):
            records.append(record)
            if "stage=body_sent " in record.getMessage():
                complete.set()

    handler = Handler(logging.WARNING)
    old_level = module.logger.level
    module.logger.addHandler(handler)
    module.logger.setLevel(logging.WARNING)
    try:
        middleware = module.MimicRequestTimingMiddleware(response)
        messages = asyncio.run(invoke(middleware))
        assert messages[0]["status"] == 200 and complete.wait(2)
        assert len(records) == 4
        assert all(record.levelno == logging.WARNING for record in records)
        assert all(PRIVATE not in record.getMessage() for record in records)
        assert all("dropped=0" in record.getMessage() for record in records)
    finally:
        module.logger.removeHandler(handler)
        module.logger.setLevel(old_level)


@pytest.mark.parametrize("access", ["local", "ingress", "operator", "display"])
def test_existing_auth_guards_enclose_trace_and_payload_is_unchanged(access, monkeypatch):
    captured = []
    monkeypatch.setattr(
        module._TraceSink, "submit", lambda self, event: captured.append(event) or True
    )

    class Client(FakeDaemonApiClient):
        def display_frame(self):
            return {"test": PRIVATE}

    origin = "https://scanner.example.test"
    auth = (
        WebDashboardAuthentication(
            "operator-password-testing",
            origin,
            display_password="display-password-testing",
        )
        if access in {"operator", "display"}
        else None
    )
    app = create_web_dashboard_app(
        Client, home_assistant_ingress=access == "ingress", lan_authentication=auth
    )
    headers = {module.TRACE_HEADER.decode(): LABEL.decode()}
    peer = ("172.30.32.2", 1234) if access == "ingress" else ("127.0.0.1", 1234)
    if access == "ingress":
        with TestClient(app, client=("192.0.2.10", 1234)) as denied:
            assert denied.get("/api/v1/display-frame", headers=headers).status_code == 403
            assert captured == []
    with TestClient(app, base_url=origin, client=peer) as client:
        if auth:
            assert client.get("/api/v1/display-frame", headers=headers).status_code == 401
            assert captured == []
            path = "/auth/display/login" if access == "display" else "/auth/login"
            assert (
                client.post(
                    path,
                    data={"password": f"{access}-password-testing"},
                    headers={"Origin": origin},
                    follow_redirects=False,
                ).status_code
                == 303
            )
        baseline = client.get("/api/v1/display-frame")
        assert captured == []
        result = client.get("/api/v1/display-frame", headers=headers)
        assert (
            result.status_code == 200
            and result.headers[module.TRACE_HEADER.decode()] == LABEL.decode()
        )
        assert result.json() == {
            "protocol": "sdsctl.web",
            "version": 1,
            "display": {"test": PRIVATE},
        }
        assert result.json() == baseline.json()
        assert {
            name: value
            for name, value in result.headers.items()
            if name != module.TRACE_HEADER.decode()
        } == dict(baseline.headers)
        assert result.headers["cache-control"] == "no-store"
        assert [event.stage for event in captured] == [
            "received",
            "handler",
            "headers_sent",
            "body_sent",
        ]
        assert PRIVATE not in repr(captured)
