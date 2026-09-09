"""Real terminal lifecycle smoke tests, with fictional probes and no network."""

from __future__ import annotations

import errno
import fcntl
import os
import select
import signal
import struct
import subprocess
import sys
import termios
import time

import pytest

CHILD = r"""
import sys
import time
from types import SimpleNamespace
import sds200.cli as cli
import sds200.managed_display_wait as waiting
from sds200.daemon_remote_client import DaemonRemoteClientError, DaemonRemoteClientErrorReason

mode = sys.argv[1]
real_app = waiting.ManagedDisplayWaitingApp
waiting.ManagedDisplayWaitingApp = lambda target, probe: real_app(
    target, probe, retry_seconds=0.3
)
cli.report_managed_display_wait = lambda *args: None
waiting.report_managed_display_wait = lambda *args: None
cli._selected_remote_client_configuration = lambda *args, **kwargs: SimpleNamespace(
    address="192.168.20.41", port=50443
)
starts = 0
probes = 0

def fail():
    return DaemonRemoteClientError(DaemonRemoteClientErrorReason.CONNECT_FAILED)

def start(*args, **kwargs):
    global starts
    starts += 1
    if starts == 1:
        raise fail()
    print("FULL_TUI_STARTUP_REENTERED", flush=True)
    return 0

def probe(*args, **kwargs):
    global probes
    probes += 1
    if mode == "recover" and probes >= 3:
        return
    if mode == "late":
        time.sleep(0.4)
        return
    raise fail()

cli._run_tui = start
cli._probe_remote_display_service = probe
status = cli.main([
    "tui", "--daemon-client", "--remote-profile", "fictional", "--managed-display"
], environ={})
print(f"FINAL_STATUS={status};STARTS={starts};PROBES={probes}", flush=True)
raise SystemExit(status)
"""


@pytest.mark.parametrize(
    "mode,size",
    [
        ("recover", (30, 100)),
        ("recover", (45, 160)),
        ("quit", (30, 100)),
        ("term", (45, 160)),
        ("late", (30, 100)),
    ],
)
def test_real_tty_recovery_quit_and_service_stop(mode, size) -> None:
    master, slave = os.openpty()
    process = None
    output = bytearray()
    try:
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", *size, 0, 0))
        before = termios.tcgetattr(slave)
        process = subprocess.Popen(
            [sys.executable, "-c", CHILD, mode],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            env={**os.environ, "TERM": "xterm-256color"},
            start_new_session=True,
        )

        def drain(timeout=0.1):
            if select.select([master], [], [], timeout)[0]:
                try:
                    output.extend(os.read(master, 65536))
                except OSError as error:
                    if error.errno != errno.EIO:
                        raise

        deadline = time.monotonic() + 10
        acted = False
        while process.poll() is None and time.monotonic() < deadline:
            drain()
            marker = b"Checking the daemon connection" if mode == "late" else b"Daemon disconnected"
            if not acted and marker in output:
                acted = True
                if mode in {"quit", "late"}:
                    os.write(master, b"q")
                elif mode == "term":
                    process.send_signal(signal.SIGTERM)
        assert process.poll() is not None, "terminal child did not stop within 10 seconds"
        for _ in range(5):
            drain(0.02)
        assert process.returncode == 0, bytes(output[-2000:])
        assert b"Daemon disconnected" in output
        assert b"Target: 192.168.20.41:50443" in output
        assert b"Traceback" not in output
        assert b"Managed sdsctl display temporarily unavailable" not in output
        assert output.count(b"\x1b[?1049h") == 1
        assert output.count(b"\x1b[?1049l") == 1
        if mode == "recover":
            assert b"FULL_TUI_STARTUP_REENTERED" in output
            assert b"FINAL_STATUS=0;STARTS=2;PROBES=3" in output
        else:
            assert acted
            assert b"FULL_TUI_STARTUP_REENTERED" not in output
            assert b"FINAL_STATUS=0;STARTS=1;" in output
        after = termios.tcgetattr(slave)
        assert after == before, "terminal mode was not restored"
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=3)
        os.close(slave)
        os.close(master)
