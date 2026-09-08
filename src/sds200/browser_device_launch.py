"""Internal bounded recovery browser; no CLI, service changes or guard release.

An existing trusted bubblewrap creates a private PID namespace. Its PID 1 is
our small supervisor, not Chromium. Exit of that PID 1 terminates even detached
descendants. Keep host proc visibility solely for existing host-owner checks.
This is process containment, NOT a new filesystem/network or browser sandbox.
"""

from __future__ import annotations

import json
import os
import re
import select
import signal
import stat
import subprocess
import sys
import threading
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

from .browser_device_bundle import _json
from .browser_device_handoff import (
    _ACK,
    _LAUNCH,
    BrowserHandoffAcknowledgement,
    BrowserRecoveryHandoff,
    _digest,
    _process_identity,
    _write_file,
)
from .browser_device_native import _private_read
from .browser_device_profile_access import BrowserProfileAccessError, browser_profile_access
from .browser_device_recovery import _object
from .browser_device_registration import _matches

_START_SECONDS = 60
_TOTAL_SECONDS = 180
_STOP_SECONDS = 10
_NAMESPACE_SECONDS = 240  # Independent inner lifetime, including preflight.
_ENTRY = "from sds200.browser_device_launch import _child; _child()"


class BrowserRecoveryLaunchError(RuntimeError):
    def __init__(self) -> None:
        super().__init__(
            "Supervised recovery launch was not confirmed. Retain the browser profile, "
            "maintenance guard and all handoff evidence. Do not retry, remove locks, "
            "reset saved state or resume sign-in. Exact stopped confirmation is separate."
        )


def _executable(path: Path) -> tuple[int, int, int, int]:
    if not path.is_absolute() or path.resolve() != path:
        raise ValueError()
    info = path.stat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid not in {0, os.geteuid()}
            or info.st_mode & 0o022 or not os.access(path, os.X_OK)):
        raise ValueError()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def recovery_browser_command(handoff: BrowserRecoveryHandoff, browser: Path) -> tuple[str, ...]:
    """Fixed argv; no browser debugging, trust, password-store or sandbox overrides."""
    if not handoff._supervised or "," in str(handoff._recovery):
        raise ValueError()
    receipt = json.loads(_private_read(handoff._recovery, "bundle.json", 32768),
                         object_pairs_hook=_object)
    extension_id = receipt["extension_id"]
    if type(extension_id) is not str or re.fullmatch("[a-p]{32}", extension_id) is None:
        raise ValueError()
    return (str(browser), "--kiosk", "--no-first-run", "--no-default-browser-check",
            "--disable-background-networking", f"--user-data-dir={handoff._session._root}",
            f"--load-extension={handoff._recovery / 'extension'}",
            f"--disable-extensions-except={handoff._recovery / 'extension'}",
            f"chrome-extension://{extension_id}/recovery.html")


class _Namespace:
    """Own exactly one bubblewrap/PID-1 pair. Never signal a discovered process group."""

    def __init__(self, bwrap: Path, command: tuple[str, ...]) -> None:
        # No unshare-user-try/fallback, no host policy change. Chromium retains its
        # own sandbox and normal same-account trust stores/graphical environment.
        self.child = subprocess.Popen((str(bwrap), "--unshare-pid", "--as-pid-1",
            "--die-with-parent", "--bind", "/", "/", "--dev-bind", "/dev", "/dev",
            "--ro-bind", "/proc", "/proc",
            "--", sys.executable, "-I", "-c", _ENTRY, *command),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            start_new_session=True)
        self.pidfd: int | None = None
        self.process: list[int] = []
        self.namespace: list[int] = []
        self.owner_namespace: list[int] = []
        self._buffer = bytearray()
        self._total = 0

    def message(self, deadline: float) -> dict[str, Any]:
        stream = self.child.stdout
        assert stream is not None
        while b"\n" not in self._buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([stream], [], [], remaining)[0]:
                raise ValueError()
            body = os.read(stream.fileno(), 4096)
            self._total += len(body)
            if not body or self._total > 8192:
                raise ValueError()
            self._buffer.extend(body)
        line, _, rest = self._buffer.partition(b"\n")
        self._buffer = bytearray(rest)
        value = json.loads(line, object_pairs_hook=_object)
        if type(value) is not dict:
            raise ValueError()
        return value

    def preflight(self) -> None:
        deadline = time.monotonic() + 12
        hello = self.message(deadline)
        if (set(hello) != {"event", "process", "namespace"} or hello["event"] != "namespace"
                or any(type(hello[key]) is not list or len(hello[key]) != 2
                       or any(type(n) is not int or n <= 0 for n in hello[key])
                       for key in ("process", "namespace"))):
            raise ValueError()
        process, namespace = hello["process"], hello["namespace"]
        pidfd = os.pidfd_open(process[0])
        try:
            info = (Path("/proc") / str(process[0]) / "ns/pid").stat()
            own = Path("/proc/self/ns/pid").stat()
            if (_process_identity(process[0]) != process
                    or [info.st_dev, info.st_ino] != namespace
                    or (info.st_dev, info.st_ino) == (own.st_dev, own.st_ino)):
                raise ValueError()
        except BaseException:
            os.close(pidfd)
            raise
        self.pidfd, self.process, self.namespace = pidfd, process, namespace
        self.owner_namespace = [own.st_dev, own.st_ino]
        if self.message(deadline) != {"event": "qualified"}:
            raise ValueError()

    def send(self, byte: bytes) -> None:
        assert self.child.stdin is not None
        self.child.stdin.write(byte)
        self.child.stdin.flush()

    def close(self) -> None:
        # Bounded best effort on every path. The independent PID-1 timer and
        # bubblewrap parent-death protection also cover sudden owner loss.
        if self.pidfd is not None:
            with suppress(ProcessLookupError):
                signal.pidfd_send_signal(self.pidfd, signal.SIGKILL)
        if self.child.poll() is None:
            self.child.kill()
        try:
            self.child.wait(timeout=3)
            if self.pidfd is not None and not select.select([self.pidfd], [], [], 3)[0]:
                raise ValueError()  # Sending SIGKILL is not proof that PID 1 has exited.
        finally:
            if self.pidfd is not None:
                os.close(self.pidfd)
                self.pidfd = None
            for stream in (self.child.stdin, self.child.stdout):
                if stream is not None:
                    stream.close()


def run_browser_recovery(handoff: BrowserRecoveryHandoff, *, browser: Path,
                         bwrap: Path) -> BrowserHandoffAcknowledgement:
    """One bounded foreground handoff. No restore/guard release or automatic retries."""
    scope = None
    previous: dict[signal.Signals, Any] = {}
    try:
        if (threading.current_thread() is not threading.main_thread()
                or not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
                or not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal")):
            raise ValueError()
        bindings = _executable(browser), _executable(bwrap)
        command = recovery_browser_command(handoff, browser)

        def interrupted(signum: int, frame: object) -> None:
            raise KeyboardInterrupt

        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.getsignal(signum)
            signal.signal(signum, interrupted)
        scope = _Namespace(bwrap, command)
        scope.preflight()  # No host switch if containment/runtime qualification fails.
        if (_executable(browser), _executable(bwrap)) != bindings:
            raise ValueError()
        deadline = time.monotonic() + _TOTAL_SECONDS

        def supervise() -> None:
            assert scope is not None
            record, _, _ = handoff._checked(stopped=True, live=True)
            if (_executable(browser), _executable(bwrap)) != bindings:
                raise ValueError()
            _write_file(handoff._root, "supervisor.json", _json({"version": 1,
                "record_sha256": _digest(record), "process": scope.process,
                "namespace": scope.namespace, "expires_at": deadline,
                "owner_namespace": scope.owner_namespace,
                "command_sha256": _digest(_json(list(command)))}))
            scope.send(b"g")
            if scope.message(min(deadline, time.monotonic() + 5)) != {"event": "running"}:
                raise ValueError()
            startup = min(deadline, time.monotonic() + _START_SECONDS)
            ready = False
            while time.monotonic() < deadline:
                if scope.child.poll() is not None:
                    raise ValueError()  # A clean exit is not browser acknowledgement.
                acknowledged = False
                if not ready and (handoff._root / _LAUNCH).exists():
                    _matches(handoff._root / _LAUNCH, handoff._launch_body(record, live=True))
                    ready = True  # Readiness alone never causes shutdown or success.
                try:
                    # Native writers hold this directory exclusively through
                    # fsync and post-write checks. Never stop the browser merely
                    # because a partially published receipt is already visible.
                    # Do not contend with ordinary native requests while waiting
                    # for consent. Acknowledge only after its file exists and its
                    # publisher has released exclusive ownership.
                    if ready and (handoff._root / _ACK).exists():
                        with browser_profile_access(handoff._root, exclusive=False):
                            current, _, proof = handoff._checked(stopped=False, live=True)
                            handoff._ack(current, proof)
                            acknowledged = True
                except BrowserProfileAccessError:
                    pass  # Busy or changed context never becomes success; deadlines still apply.
                if not ready and time.monotonic() >= startup:
                    raise ValueError()
                if acknowledged:
                    scope.send(b"s")
                    if scope.message(time.monotonic() + _STOP_SECONDS + 2) != {"event": "stopped"}:
                        raise ValueError()
                    if scope.child.wait(timeout=3) != 0:
                        raise ValueError()
                    assert scope.pidfd is not None
                    if not select.select([scope.pidfd], [], [], 3)[0]:
                        raise ValueError()
                    return
                time.sleep(0.1)
            raise ValueError()

        return handoff.activate(supervise)
    except (Exception, KeyboardInterrupt):
        raise BrowserRecoveryLaunchError() from None
    finally:
        # Repeated termination signals cannot interrupt containment cleanup.
        for signum in previous:
            signal.signal(signum, signal.SIG_IGN)
        try:
            if scope is not None:
                scope.close()
        except Exception:
            raise BrowserRecoveryLaunchError() from None
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)


def _version(browser: str) -> None:
    child = subprocess.Popen([browser, "--version"], stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    assert child.stdout is not None
    deadline, body = time.monotonic() + 5, bytearray()
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([child.stdout], [], [], remaining)[0]:
                raise ValueError()
            part = os.read(child.stdout.fileno(), 1025)
            body.extend(part)
            if len(body) > 1024:
                raise ValueError()
            if not part:
                break
        remaining = deadline - time.monotonic()
        if remaining <= 0 or child.wait(timeout=remaining) != 0:
            raise ValueError()
        match = re.fullmatch(rb"Chromium ([0-9]{1,4})\.[^\r\n]{1,200}\r?\n?", body)
        if match is None or int(match[1]) < 120:
            raise ValueError()
    finally:
        child.stdout.close()
        # Any orphaned version descendants die when namespace PID 1 exits.
        if child.poll() is None:
            child.kill()
            child.wait(timeout=1)


def _child() -> None:
    """Fixed isolated PID-1 entry; stdout is only a bounded redacted status pipe."""
    browser: subprocess.Popen[bytes] | None = None
    try:
        if sys.platform != "linux" or os.getpid() != 1 or len(sys.argv) != 10:
            raise ValueError()

        def expired(signum: int, frame: object) -> None:
            raise SystemExit(75)

        signal.signal(signal.SIGALRM, expired)
        signal.signal(signal.SIGTERM, expired)
        signal.signal(signal.SIGINT, expired)
        signal.setitimer(signal.ITIMER_REAL, _NAMESPACE_SECONDS)
        # Host proc is intentionally visible: the native handoff owner lives in
        # the outer namespace. Kernel getpid()==1 independently checks isolation.
        pid = int(os.readlink("/proc/self"))
        ns = Path("/proc/self/ns/pid").stat()

        def report(value: dict[str, object]) -> None:
            body = json.dumps(value, separators=(",", ":")).encode("ascii")
            sys.stdout.buffer.write(body + b"\n")
            sys.stdout.buffer.flush()

        report({"event": "namespace", "process": _process_identity(pid),
                "namespace": [ns.st_dev, ns.st_ino]})
        _version(sys.argv[1])
        report({"event": "qualified"})
        if not select.select([sys.stdin], [], [], 15)[0] or os.read(0, 1) != b"g":
            raise ValueError()
        browser = subprocess.Popen(sys.argv[1:], stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        report({"event": "running"})
        while browser.poll() is None:
            if select.select([sys.stdin], [], [], 0.1)[0]:
                if os.read(0, 1) != b"s":
                    raise ValueError()
                browser.send_signal(signal.SIGINT)
                if browser.wait(timeout=_STOP_SECONDS) != 0:
                    raise ValueError()
                report({"event": "stopped"})
                # PID-1 exit destroys the namespace, including setsid descendants.
                os._exit(0)
        raise ValueError()
    except BaseException:
        # Never print Chromium, native or exception payloads. Exiting namespace
        # PID 1 is the kill boundary; no broad host signal or unbounded wait.
        os._exit(75)
