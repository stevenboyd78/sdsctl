#!/usr/bin/env python3
"""Read-only, retained plan intake for the prospective recording service.

This is NOT a service entrypoint, restart permission or action authority. The
caller supplies an independently reviewed digest; it cannot be learned from
this file. Retain this object and recheck the original descriptors, bytes and
decoded object instead of reopening/adopting a replacement plan. No directory,
file, process, Engine request, journal or operator notice is created here.
Direct execution is restricted to a finite, read-only startup observation probe;
it does not run or enable the prospective recording service.
"""

from __future__ import annotations

import os
import stat
import sys
import time
from pathlib import Path
from threading import get_ident

PROBE_MESSAGE = "Read-only recording startup probe is unconfirmed; preserve this case."

# Direct execution is ONLY a finite read-only probe in the sealed helper image.
# Isolated Python omits the script directory; never import helpers from cwd,
# an environment override or an arbitrary directory supplied on the command line.
if __name__ == "__main__":
    try:
        allowed = (
            (len(sys.argv) == 3 or (len(sys.argv) == 4 and sys.argv[3] == "--zero-offset-probe"))
            and sys.flags.isolated == sys.flags.dont_write_bytecode == 1
            and os.geteuid() == os.getegid() == 0
            and os.getcwd() == "/"
            and Path(__file__)
            == Path("/opt/sdsctl-recording-host/supplemental_recording_service_input.py")
        )
    except Exception:
        allowed = False
    if not allowed:
        print(PROBE_MESSAGE, file=sys.stderr)
        raise SystemExit(64)
    sys.path.insert(0, "/opt/sdsctl-recording-host")

try:
    import supplemental_handoff_files as files
    import supplemental_recording_host_plan as plans
    import supplemental_recording_time_domain as time_domain
except Exception:
    if __name__ == "__main__":
        print(PROBE_MESSAGE, file=sys.stderr)
        raise SystemExit(75) from None
    raise

MAX_SECONDS = 2.0
MESSAGE = "Recording service input is unconfirmed; preserve the case and do not restart."


class UnconfirmedInput(ValueError):
    """Fixed failure text, never a private pathname or plan fragment."""


def require(value):
    if not value:
        raise UnconfirmedInput(MESSAGE)


class CasePlan:
    """Same-process/thread custody of one existing schema3 plan, read-only.

    Every ancestor is opened without following symlinks and retained until
    close. Ancestor identity/ownership/mode, the private case mode, and the exact
    single-link plan file are rechecked before and after each bounded read.
    Sibling entries may change as the separately owned journal grows; the plan
    may not. Failure is sticky, even if someone later puts the original back.

    File ownership is bound to the original effective uid/gid. Requiring the
    installed service to run as root, checking its source/runtime/confinement,
    authenticating the digest and checking current time remain separate gates.
    Expired plans can be inspected but never gain new deadlines from this read.
    """

    def __init__(self, root, expected_sha256):
        self._owner = (os.getpid(), get_ident(), os.geteuid(), os.getegid())
        self._anchor = self._file = -1
        self._directories = []
        self._failed = self._closed = False
        self._plan = self._pin = None
        try:
            require(type(root) is type(Path()) and root.is_absolute() and root != Path("/"))
            require(1 < len(root.parts) <= 32 and ".." not in root.parts)
            require(type(expected_sha256) is str)
            plans.base.digest(expected_sha256)
            self._root, self._expected = root, expected_sha256
            end = time.monotonic() + MAX_SECONDS
            self._anchor = os.open("/", files.DIRECTORY)
            self._anchor_identity = files.identity(os.fstat(self._anchor))[:5]
            parent = self._anchor
            for name in root.parts[1:]:
                require(time.monotonic() < end)
                child = os.open(name, files.DIRECTORY, dir_fd=parent)
                try:
                    original = files.identity(os.fstat(child))[:5]
                except BaseException:
                    os.close(child)
                    raise
                self._directories.append((parent, name, child, original))
                parent = child
            self._check_directories(end)
            before = os.stat("plan.json", dir_fd=parent, follow_symlinks=False)
            require(
                stat.S_ISREG(before.st_mode)
                and stat.S_IMODE(before.st_mode) == 0o600
                and (before.st_uid, before.st_gid) == self._owner[2:]
                and before.st_nlink == 1
                and 0 < before.st_size <= plans.MAX_BYTES
            )
            self._file_identity = files.identity(before)
            self._file = os.open(
                "plan.json",
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                dir_fd=parent,
            )
            raw = self._read(end)
            plan = plans.load_bytes(raw, expected_sha256)
            require(plan.root == root)
            pin = plans.PinnedPlan(plan)
            self._check_file(end)
            self._check_directories(end)
            self._plan, self._pin = plan, pin
        except BaseException as error:
            self._failed = True
            self.close()
            if not isinstance(error, Exception):
                raise
            raise UnconfirmedInput(MESSAGE) from None

    def _context(self, end):
        require(not self._closed and not self._failed)
        require(self._owner == (os.getpid(), get_ident(), os.geteuid(), os.getegid()))
        require(time.monotonic() < end)

    def _check_directories(self, end):
        self._context(end)
        require(files.identity(os.fstat(self._anchor))[:5] == self._anchor_identity)
        for parent, name, child, original in self._directories:
            self._context(end)
            require(files.identity(os.fstat(child))[:5] == original)
            require(
                files.identity(os.stat(name, dir_fd=parent, follow_symlinks=False))[:5] == original
            )
        info = os.fstat(self._directories[-1][2])
        require(
            stat.S_ISDIR(info.st_mode)
            and stat.S_IMODE(info.st_mode) == 0o700
            and (info.st_uid, info.st_gid) == self._owner[2:]
        )
        self._context(end)

    def _check_file(self, end):
        self._context(end)
        require(files.identity(os.fstat(self._file)) == self._file_identity)
        require(
            files.identity(
                os.stat("plan.json", dir_fd=self._directories[-1][2], follow_symlinks=False)
            )
            == self._file_identity
        )
        self._context(end)

    def _read(self, end):
        self._check_directories(end)
        self._check_file(end)
        size = self._file_identity[6]
        raw = bytearray()
        while len(raw) <= size:
            self._context(end)
            chunk = os.pread(self._file, size + 1 - len(raw), len(raw))
            if not chunk:
                break
            raw.extend(chunk)
        require(len(raw) == size)
        self._check_file(end)
        self._check_directories(end)
        return bytes(raw)

    @property
    def plan(self):
        """The original decoded object; obtaining it still performs a fresh read."""
        return self.recheck()

    def recheck(self):
        """Fresh read of the same original file, never a fresh plan or deadline."""
        try:
            end = time.monotonic() + MAX_SECONDS
            self._context(end)
            self._pin.check(self._plan)
            require(self._plan.root == self._root and self._plan.sha256 == self._expected)
            require(self._read(end) == self._plan.raw)
            self._pin.check(self._plan)
            self._context(end)
            return self._plan
        except BaseException as error:
            self._failed = True
            self.close()
            if not isinstance(error, Exception):
                raise
            raise UnconfirmedInput(MESSAGE) from None

    def close(self):
        """Release descriptors only; never remove evidence or certify anything."""
        if self._closed:
            return
        self._closed = True
        if self._file >= 0:
            os.close(self._file)
            self._file = -1
        for _, _, child, _ in reversed(self._directories):
            os.close(child)
        self._directories.clear()
        if self._anchor >= 0:
            os.close(self._anchor)
            self._anchor = -1

    def __enter__(self):
        self.recheck()
        return self

    def __exit__(self, *_):
        self.close()


def startup_probe(root, expected_sha256, *, zero_offset_probe=False):
    """Hold only original read-only plan/clock handles for at most 30 seconds.

    The bound starts at the PLAN's original clock, not this process's startup.
    There is no journal, notice, Engine connection, listener or service assembly.
    This process is an observation target for an independent trusted qualifier;
    keeping it alive or seeing exit 75 proves no readiness or restoration.
    Blocked kernel I/O still needs a separate outer process deadline. No signals
    are masked and no case can be re-armed by this function.

    The explicit zero-offset variant is ONLY a passive target for a continuing
    outside owner holding a live ZeroDomain witness. It checks its OWN actual
    zero offsets and clock custody; it never relabels a sample as the plan's
    domain or certifies that foreign domain. The outside owner must qualify both
    domains. An additional local 30-second cap limits even an unqualified lease.
    """
    require(type(zero_offset_probe) is bool)
    with CasePlan(root, expected_sha256) as original:
        plan = original.recheck()
        local = plans.clock.read() if zero_offset_probe else plan.original_clock
        clock = plans.clock.ClockWitness(local)
        try:
            end = min(plan.lease["ready_by"], plan.lease["issued_at"] + 30)
            if zero_offset_probe:
                end = min(end, local.after_ns / plans.clock.NS + 30)
                time_domain._offsets(os.getpid())
                require(local.boot == plan.boot)
            require(clock.read().after_ns / plans.clock.NS < end)
            for _ in range(301):
                require(original.recheck() is plan)
                if zero_offset_probe:
                    time_domain._offsets(os.getpid())
                observed = clock.read()
                if zero_offset_probe:
                    # ClockWitness checks current AND child namespace identity
                    # around the sample, so offsets describe this retained domain.
                    time_domain._offsets(os.getpid())
                    require(observed.boot == plan.boot)
                else:
                    plan.check_clock(observed)
                remaining = end - observed.after_ns / plans.clock.NS
                if remaining <= 0:
                    return 75
                time.sleep(min(0.1, remaining))
            require(False)
        finally:
            clock.close()


if __name__ == "__main__":
    try:
        root = Path(sys.argv[1])
        require(str(root) == sys.argv[1] and root.is_absolute() and ".." not in root.parts)
        code = startup_probe(root, sys.argv[2], zero_offset_probe=len(sys.argv) == 4)
    except Exception:
        print(PROBE_MESSAGE, file=sys.stderr)
        raise SystemExit(75) from None
    raise SystemExit(code)
