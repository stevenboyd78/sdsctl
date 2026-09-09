from __future__ import annotations

import asyncio
import logging
import signal
from threading import Event

import pytest
from textual.widgets import Static

import sds200.cli as cli
import sds200.managed_display as managed
import sds200.managed_display_wait as waiting
from sds200.daemon_remote_client import (
    DaemonRemoteClientConfiguration,
    DaemonRemoteClientError,
    DaemonRemoteClientErrorReason,
)
from sds200.daemon_remote_service import DaemonRemoteService
from sds200.logging_config import configure_logging
from sds200.tui_logging import TuiLogBuffer, capture_package_logs


def unavailable() -> DaemonRemoteClientError:
    return DaemonRemoteClientError(DaemonRemoteClientErrorReason.CONNECT_FAILED)


async def eventually(predicate) -> None:
    for _ in range(200):
        if predicate():
            return
        await asyncio.sleep(0.01)
    assert predicate()


@pytest.fixture(autouse=True)
def no_real_journal(monkeypatch):
    # Never write fixture failures to the workstation's real service journal.
    monkeypatch.setattr(waiting, "report_managed_display_wait", lambda *args: None)
    monkeypatch.setattr(cli, "report_managed_display_wait", lambda *args: None)


@pytest.mark.parametrize("size", [(64, 20), (100, 30), (160, 45)])
def test_waiting_screen_fits_and_retries_in_place_without_scanner_data(size) -> None:
    now = [0.0]
    calls = []

    def probe() -> None:
        calls.append(now[0])
        raise unavailable()

    app = waiting.ManagedDisplayWaitingApp("scanner.invalid:50443", probe, clock=lambda: now[0])

    async def exercise() -> None:
        async with app.run_test(size=size) as pilot:
            panel = app.query_one("#connection-wait")
            initial_region = panel.region
            assert panel.region.right <= size[0]
            assert panel.region.bottom < size[1]
            assert app.screen.max_scroll_y == 0
            assert len(panel.query(Static)) == 4
            assert "15 seconds" in str(app.query_one("#wait-status", Static).content)
            assert calls == []
            for attempt in range(1, 5):
                now[0] = attempt * 15.0
                app._tick()
                await eventually(
                    lambda expected=attempt: len(calls) == expected and not app._probe_running
                )
                await pilot.pause()
                assert panel.region == initial_region
                assert app.screen.max_scroll_y == 0
                assert len(panel.query(Static)) == 4
                assert app.query_one("#connection-wait") is panel
            await pilot.press("q")
        assert app.return_value is False
        assert calls == [15.0, 30.0, 45.0, 60.0]

    asyncio.run(exercise())


def test_readiness_returns_true_after_one_successful_probe() -> None:
    now = [0.0]
    calls = []
    app = waiting.ManagedDisplayWaitingApp(
        "[fd00::1]:50443", lambda: calls.append("api"), clock=lambda: now[0]
    )

    async def exercise() -> None:
        async with app.run_test() as pilot:
            assert "[fd00::1]:50443" in str(app.query_one("#wait-target", Static).content)
            now[0] = 15
            app._tick()
            await eventually(lambda: app.return_value is True)
            await pilot.pause()
        assert calls == ["api"]

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "reason",
    [
        reason
        for reason in DaemonRemoteClientErrorReason
        if reason != DaemonRemoteClientErrorReason.CONNECT_FAILED
    ],
)
def test_permanent_probe_failure_is_not_retried_or_rendered_raw(reason) -> None:
    now = [0.0]
    error = DaemonRemoteClientError(reason)
    calls = []

    def probe() -> None:
        calls.append("attempt")
        raise error

    app = waiting.ManagedDisplayWaitingApp("scanner.invalid:50443", probe, clock=lambda: now[0])

    async def exercise() -> None:
        async with app.run_test() as pilot:
            now[0] = 15
            app._tick()
            await eventually(lambda: app.return_value is error)
            await pilot.pause()
        assert calls == ["attempt"]

    asyncio.run(exercise())


@pytest.mark.parametrize("key", ["q", "ctrl+c"])
def test_quit_during_probe_ignores_late_success_and_never_overlaps(key) -> None:
    now = [0.0]
    entered = Event()
    release = Event()
    finished = Event()
    calls = []

    def probe() -> None:
        calls.append("attempt")
        entered.set()
        try:
            assert release.wait(3)
        finally:
            finished.set()

    app = waiting.ManagedDisplayWaitingApp("scanner.invalid:50443", probe, clock=lambda: now[0])

    async def exercise() -> None:
        try:
            async with app.run_test() as pilot:
                now[0] = 15
                app._tick()
                await eventually(entered.is_set)
                now[0] = 500
                for _ in range(20):
                    app._tick()
                assert calls == ["attempt"]
                await pilot.press(key)
                assert app._stopping.is_set()
                release.set()
                await eventually(finished.is_set)
                await pilot.pause()
            assert app.return_value is False
            assert calls == ["attempt"]
        finally:
            release.set()

    asyncio.run(exercise())


def test_wait_wrapper_restores_sigterm_and_does_not_swallow_permanent_errors(monkeypatch) -> None:
    before = signal.getsignal(signal.SIGTERM)
    error = unavailable()

    def fake_run(app):
        assert signal.getsignal(signal.SIGTERM) != before
        return error

    monkeypatch.setattr(waiting.ManagedDisplayWaitingApp, "run", fake_run)
    with pytest.raises(DaemonRemoteClientError) as caught:
        waiting.wait_for_managed_display("scanner.invalid:50443", lambda: None)
    assert caught.value is error
    assert signal.getsignal(signal.SIGTERM) == before


def test_wait_wrapper_handles_sigterm_as_intentional_stop(monkeypatch) -> None:
    before = signal.getsignal(signal.SIGTERM)

    def fake_run(app):
        handler = signal.getsignal(signal.SIGTERM)
        handler(signal.SIGTERM, None)
        assert app._stopping.is_set()
        return app.return_value

    monkeypatch.setattr(waiting.ManagedDisplayWaitingApp, "run", fake_run)
    assert waiting.wait_for_managed_display("scanner.invalid:50443", lambda: None) is False
    assert signal.getsignal(signal.SIGTERM) == before


def test_journal_event_is_bounded_sanitized_and_file_log_is_preserved(
    tmp_path, monkeypatch, capsys
) -> None:
    records = []

    class Journal:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def settimeout(self, value):
            assert value == 0.1

        def sendto(self, payload, path):
            records.append((payload, path))

    monkeypatch.setattr(managed.socket, "socket", lambda *args: Journal())
    logfile = tmp_path / "display.log"
    configure_logging(log_file=logfile)
    buffer = TuiLogBuffer()
    try:
        with capture_package_logs(buffer):
            managed.report_managed_display_wait("waiting", unavailable())
            managed.report_managed_display_wait(
                "retry", RuntimeError("private secret\nMESSAGE=injection")
            )
        for handler in logging.getLogger("sds200").handlers:
            handler.flush()
        assert "event=waiting reason=connect_failed" in logfile.read_text()
        assert len(buffer.snapshot().lines) == 2
        assert capsys.readouterr().err == ""
        assert len(records) == 2
        for payload, path in records:
            assert path == "/run/systemd/journal/socket"
            assert len(payload) < 256
            assert payload.count(b"MESSAGE=") == 1
            assert b"SYSLOG_IDENTIFIER=sdsctl-display\n" in payload
            assert b"private" not in payload
            assert b"secret" not in payload
    finally:
        configure_logging()


def test_missing_journal_never_leaks_traceback_to_console(monkeypatch, capsys) -> None:
    def absent(*args):
        raise OSError("private journal location")

    monkeypatch.setattr(managed.socket, "socket", absent)
    configure_logging()
    with capture_package_logs(TuiLogBuffer()):
        managed.report_managed_display_wait("retry", unavailable())
    assert capsys.readouterr().err == ""


def _cli_fixture(tmp_path, monkeypatch):
    configuration = DaemonRemoteClientConfiguration(
        address="192.168.20.41",
        port=50443,
        server_hostname="scanner.invalid",
        certificate_file=tmp_path / "ca.pem",
        client_id="observe",
        credential_file=tmp_path / "client.secret",
    )
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(
        cli, "_selected_remote_client_configuration", lambda *args, **kwargs: configuration
    )
    return configuration


ARGS = ["tui", "--daemon-client", "--remote-profile", "observe", "--managed-display"]


def test_managed_cli_reenters_normal_startup_after_readiness(tmp_path, monkeypatch, capsys) -> None:
    configuration = _cli_fixture(tmp_path, monkeypatch)
    starts = []
    probes = []

    def start(*args, **kwargs):
        starts.append(kwargs["log_buffer"])
        if len(starts) < 3:
            raise unavailable()
        return 0

    def wait(target, probe):
        assert target == "192.168.20.41:50443"
        probe()
        return True

    monkeypatch.setattr(cli, "_run_tui", start)
    monkeypatch.setattr(waiting, "wait_for_managed_display", wait)
    monkeypatch.setattr(
        cli,
        "_probe_remote_display_service",
        lambda config, service, **kwargs: probes.append((config, service, kwargs)),
    )
    assert cli.main(ARGS + ["--daemon-timeout", "2.5"], environ={}) == 0
    assert len(starts) == 3
    assert all(buffer is starts[0] for buffer in starts)
    assert probes == [(configuration, DaemonRemoteService.API, {"timeout": 2.5})] * 2
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


def test_managed_cli_quit_does_not_retry_or_request_restart(tmp_path, monkeypatch) -> None:
    _cli_fixture(tmp_path, monkeypatch)
    calls = []

    def start(*args, **kwargs):
        calls.append("start")
        raise unavailable()

    monkeypatch.setattr(cli, "_run_tui", start)
    monkeypatch.setattr(waiting, "wait_for_managed_display", lambda *args: False)
    assert cli.main(ARGS, environ={}) == 0
    assert calls == ["start"]


@pytest.mark.parametrize("permanent", [True, False])
def test_managed_cli_preserves_noninteractive_and_permanent_failure_status(
    tmp_path, monkeypatch, capsys, permanent
) -> None:
    _cli_fixture(tmp_path, monkeypatch)
    reason = (
        DaemonRemoteClientErrorReason.AUTHENTICATION_FAILED
        if permanent
        else DaemonRemoteClientErrorReason.CONNECT_FAILED
    )
    if not permanent:
        monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)

    def start(*args, **kwargs):
        raise DaemonRemoteClientError(reason)

    def no_wait(*args):
        pytest.fail("must not open the waiting screen")

    monkeypatch.setattr(cli, "_run_tui", start)
    monkeypatch.setattr(waiting, "wait_for_managed_display", no_wait)
    assert cli.main(ARGS, environ={}) == (78 if permanent else 75)
    assert "scanner.invalid" not in capsys.readouterr().out
