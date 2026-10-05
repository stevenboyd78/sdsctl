#!/usr/bin/env python3
"""Observer-owned kernel deadline and helper exit watch; not a supervisor.

Capture once from the original LIVE ObserverClock before any App mutation.
Retained namespace/pidfd handles and an absolute CLOCK_BOOTTIME timer survive
helper exit, a frozen helper, or closure of the startup comparison. No signal,
App action, journal publication, process discovery, launch or restoration is
provided. A separate joint inventory names this library; no command selects it.
The separate observer must itself have independently qualified provenance.
"""

from __future__ import annotations

import os
import re
import select
import stat
import time
from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal
from threading import Lock, get_ident

import supplemental_recording_service_clock_link as links

MESSAGE = "Independent recording deadline is unconfirmed; preserve the original case."
MAX_SECONDS = 1


class UnconfirmedDeadline(ValueError):
    """Neither a helper exit nor timer expiry is restoration or native exit."""


def require(value):
    if not value:
        raise UnconfirmedDeadline(MESSAGE)


def timerfd_available():
    """API presence only, not runtime qualification or a usable kernel timer.

    Python exposes these Linux APIs starting in 3.13. Other product runtimes
    remain supported; this uninstalled observer must refuse without a timer,
    not replace BOOTTIME with sleeps or a suspend-insensitive clock.
    """
    return (
        all(
            callable(getattr(os, name, None))
            for name in (
                "timerfd_create",
                "timerfd_settime_ns",
                "timerfd_gettime_ns",
            )
        )
        and all(
            type(getattr(os, name, None)) is int
            for name in (
                "TFD_CLOEXEC",
                "TFD_NONBLOCK",
                "TFD_TIMER_ABSTIME",
            )
        )
        and type(getattr(time, "CLOCK_BOOTTIME", None)) is int
    )


def _identity(fd):
    value = os.fstat(fd)
    return value.st_dev, value.st_ino, value.st_mode


def _readable(fd):
    poller = select.poll()
    poller.register(fd, select.POLLIN)
    events = poller.poll(0)
    if not events:
        return False
    require(len(events) == 1 and events[0][0] == fd)
    require(events[0][1] in (select.POLLIN, select.POLLIN | select.POLLHUP))
    return True


def _fdinfo(fd):
    # Bounded kernel metadata for our own retained descriptor only.
    with open(f"/proc/self/fdinfo/{fd}", "rb", buffering=0) as stream:
        raw = stream.read(1025)
    require(0 < len(raw) <= 1024 and raw.endswith(b"\n"))
    values = {}
    for line in raw.splitlines():
        key, separator, value = line.partition(b":")
        require(separator and key not in values)
        values[key] = value.strip(b" \t")
    return values


def _timer_metadata(fd):
    # Timerfds share an anonymous inode, so fstat alone cannot distinguish a
    # substituted MONOTONIC timer (which would stop counting during suspend).
    values = _fdinfo(fd)
    require(
        set(values)
        == {
            b"pos",
            b"flags",
            b"mnt_id",
            b"ino",
            b"clockid",
            b"ticks",
            b"settime flags",
            b"it_value",
            b"it_interval",
        }
    )
    require(values[b"clockid"] == str(time.CLOCK_BOOTTIME).encode("ascii"))
    require(re.fullmatch(rb"0*1", values[b"settime flags"]) is not None)
    require(values[b"it_interval"] == b"(0, 0)")


@dataclass(frozen=True)
class Status:
    helper_exited: bool
    recovery_deadline_expired: bool


class DeadlineWatch:
    """One read-only watch owned by the independent OBSERVER process.

    The original startup link must still prove live, zero-offset domains at
    capture. Its one capture attempt is consumed even if acquisition fails.
    Duplicates are taken BEFORE helper loss, never by reopening its numeric PID.
    Kernel time-namespace offsets were frozen by the original live member;
    retaining both namespace descriptors prevents their identity recycling.

    poll() does not call the helper, Docker, a clock callback, or an expired
    startup verifier. It keeps the observer's original namespace and plan and
    checks a one-shot kernel timer against the original absolute BOOTTIME limit.
    Suspend counts toward that limit. Timer readiness is not consumed or reset.
    A dead helper's original pidfd can be reported without reading /proc/PID.

    This watches only the helper, not normal/candidate/native processes. It
    neither bounds a blocked observer nor implements failover/recovery. Those
    responsibilities and independently retained App/native custody remain
    mandatory before an active command may use this prerequisite.
    """

    def __init__(self, link):
        self.owner, self.lock = (os.getpid(), get_ident()), Lock()
        self.failed = self.closed = False
        # The separately selected observer may retain the original App init
        # processes once. This is not action permission or recovery authority.
        self.app_custody_attempted = False
        self._owned = []
        self._original_owned = self._owned
        self._resources = None
        self._pins = None
        try:
            end = time.monotonic() + MAX_SECONDS
            require(type(self) is DeadlineWatch and type(link) is links.ObserverClock)
            link.read()
            require(link.deadline_capture_attempted is False)
            link.deadline_capture_attempted = True
            require(timerfd_available())
            require(time.monotonic() < end)
            self.plan, self.target = link.plan, link.target
            self._target_identity = (
                self.target.pid,
                self.target.start_ticks,
                self.target.container_id,
            )
            self.pin = links.plans.PinnedPlan(self.plan)
            proof, domain = link.proof, link.domain
            require(domain.evidence is proof and proof.init == self.target)
            require(self.target.pid != self.owner[0])
            self.host_namespace, self.target_namespace = proof.host_time, proof.native_time
            self.user_namespace = proof.user
            require(self.host_namespace == link.local_original.namespace)
            require(self.target_namespace == self.plan.original_clock.namespace)
            self.deadline_ns = int(
                (Decimal(self.plan.deadlines.recover_by) * links.plans.clock.NS).to_integral_value(
                    rounding=ROUND_FLOOR
                )
            )
            self.host_fd = self._duplicate(domain.handles[self.owner[0]][0])
            self.target_fd = self._duplicate(domain.handles[self.target.pid][0])
            self.helper_fd = self._duplicate(domain.pidfd)
            self.user_fd = self._own(os.open("/proc/self/ns/user", os.O_RDONLY | os.O_CLOEXEC))
            for fd, expected in (
                (self.host_fd, self.host_namespace),
                (self.target_fd, self.target_namespace),
                (self.user_fd, self.user_namespace),
            ):
                info = os.fstat(fd)
                require(stat.S_ISREG(info.st_mode) and (info.st_dev, info.st_ino) == expected)
            require(not _readable(self.helper_fd))
            require(_fdinfo(self.helper_fd).get(b"Pid") == str(self.target.pid).encode("ascii"))
            self.timer_fd = self._own(
                os.timerfd_create(time.CLOCK_BOOTTIME, flags=os.TFD_CLOEXEC | os.TFD_NONBLOCK)
            )
            require(time.clock_gettime_ns(time.CLOCK_BOOTTIME) < self.deadline_ns)
            os.timerfd_settime_ns(
                self.timer_fd, flags=os.TFD_TIMER_ABSTIME, initial=self.deadline_ns, interval=0
            )
            # This final live comparison is essential. An exit during capture
            # cannot be laundered into a successfully armed independent watch.
            link.read()
            require(link.plan is self.plan and link.target is self.target)
            require(link.proof is proof and link.domain is domain)
            require(link.deadline_capture_attempted is True and not _readable(self.helper_fd))
            require(_fdinfo(self.helper_fd).get(b"Pid") == str(self.target.pid).encode("ascii"))
            require(time.monotonic() < end)
            self._resources = tuple(self._owned)
            self._pins = self._values()
            self.poll()
            require(time.monotonic() < end)
        except BaseException as error:
            self.close()
            self._fail(error)

    def _own(self, fd):
        try:
            identity = _identity(fd)
        except BaseException:
            os.close(fd)
            raise
        self._owned.append((fd, identity))
        require(not os.get_inheritable(fd))
        return fd

    def _duplicate(self, fd):
        return self._own(os.dup(fd))

    def _values(self):
        return (
            self.plan,
            self.target,
            self.pin,
            self.host_namespace,
            self.target_namespace,
            self.user_namespace,
            self.deadline_ns,
            self.host_fd,
            self.target_fd,
            self.helper_fd,
            self.user_fd,
            self.timer_fd,
        )

    def _guard(self):
        require(type(self) is DeadlineWatch and not self.closed and not self.failed)
        require(self.owner == (os.getpid(), get_ident()))
        require(self._pins is not None)
        require(all(a is b for a, b in zip(self._values(), self._pins, strict=True)))
        require(self._owned is self._original_owned and len(self._owned) == 5)
        require(tuple(self._owned) == self._resources)
        require(tuple(fd for fd, _ in self._owned) == self._pins[7:])
        self.pin.check(self.plan)
        self.target.__post_init__()
        require(
            (self.target.pid, self.target.start_ticks, self.target.container_id)
            == self._target_identity
        )
        for fd, original in self._owned:
            require(_identity(fd) == original and not os.get_inheritable(fd))
        for name, expected in (
            ("time", self.host_namespace),
            ("time_for_children", self.host_namespace),
            ("user", self.user_namespace),
        ):
            info = os.stat(f"/proc/self/ns/{name}")
            require((info.st_dev, info.st_ino) == expected)
        _timer_metadata(self.timer_fd)

    def poll(self):
        """Read only original kernel handles, including AFTER the hard deadline.

        A late observation reports expiry, not a renewed action/recovery lease.
        Changed bindings, missing handles and disarmed/changed timers fail closed.
        There is deliberately no API accepting a PID, deadline or timer reset.
        """
        acquired = False
        try:
            end = time.monotonic() + MAX_SECONDS
            require(self.lock.acquire(blocking=False))
            acquired = True
            self._guard()
            require(time.monotonic() < end)
            before = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
            remaining, interval = os.timerfd_gettime_ns(self.timer_fd)
            after = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
            require(before <= after and interval == 0)
            if remaining:
                # A positive kernel remainder names precisely the same fixed
                # deadline between the two bracketing BOOTTIME observations.
                require(before <= self.deadline_ns - remaining <= after)
            else:
                require(after >= self.deadline_ns)
            expired = _readable(self.timer_fd)
            if not remaining:
                require(expired)  # Never consume an externally cleared timer.
            exited = _readable(self.helper_fd)
            self._guard()
            require(time.monotonic() < end)
            return Status(exited, expired)
        except BaseException as error:
            self._fail(error)
        finally:
            if acquired:
                self.lock.release()

    def _fail(self, error):
        self.failed = True
        if not isinstance(error, Exception):
            raise error
        raise UnconfirmedDeadline(MESSAGE) from None

    def close(self):
        """Release only owned duplicates; never close the original link/clock."""
        require(self.owner == (os.getpid(), get_ident()))
        if self.closed:
            return
        self.closed = True
        owned = self._resources if self._resources is not None else tuple(self._original_owned)
        self._owned = []
        problem = None
        for fd, expected in reversed(owned):
            try:
                # Do not close a foreign fd which reused a lost descriptor.
                require(_identity(fd) == expected)
                os.close(fd)
            except Exception as error:
                problem = error
        if problem is not None:
            self._fail(problem)


if __name__ == "__main__":
    raise SystemExit("Read-only observer deadline library; no active controller is enabled.")
