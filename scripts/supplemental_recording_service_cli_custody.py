#!/usr/bin/env python3
"""Independent fixed CLI evidence; no journal ownership or recovery dispatch.

An uninstalled observer records the original intent before exec/create and the
exact created execution before exec/start. It never opens a second Journal,
acquires/transfers a flock, emits replayed actions, starts an exec, or infers App
success from a CLI exit. The authenticated cross-process command and exclusive
recovery handoff remain separate, mandatory integration boundaries.
"""

from __future__ import annotations

import json
import os
import stat
import time
from dataclasses import asdict, dataclass
from threading import Lock, get_ident

import supplemental_recording_service_app_custody as apps

platform, engine, deadlines = apps.platform, apps.engine, apps.deadlines
plans = deadlines.links.plans
bootstrap, base = plans.bootstrap, plans.base
files = engine.files
MESSAGE = "Independent App command custody is unconfirmed; preserve the original case."


class UnconfirmedCliCustody(ValueError):
    """Unknown CLI evidence never permits retry, App success or ownership transfer."""


def require(value):
    if not value:
        raise UnconfirmedCliCustody(MESSAGE)


def _open_directory(path):
    fd = os.open("/", files.DIRECTORY)
    try:
        require(path.is_absolute() and ".." not in path.parts and len(path.parts) > 1)
        for part in path.parts[1:]:
            child = os.open(part, files.DIRECTORY, dir_fd=fd)
            os.close(fd)
            fd = child
        info = os.fstat(fd)
        require(info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o700)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _names(fd):
    names = []
    with os.scandir(fd) as entries:
        for item in entries:
            require(len(names) < bootstrap.Journal.max_events)
            names.append(item.name)
    names.sort()
    require(names and names == [base.Journal.name(i) for i in range(len(names))])
    return names


class _History:
    """Read-only original directory handle, explicitly NOT a Journal or lock.

    The original writer must pause publication during a boundary exchange.
    Concurrent/partial writes fail closed. Bounded repeated reads are not a
    filesystem transaction or protection against trusted root mutation.
    """

    def __init__(self, path):
        self.path, self.fd = path, _open_directory(path)
        try:
            self.identity = files.identity(os.fstat(self.fd))[:6]
        except BaseException:
            os.close(self.fd)
            self.fd = -1
            raise
        self._owned_fd = self.fd

    def check(self):
        require(self.fd == self._owned_fd and self.fd >= 0 and not os.get_inheritable(self.fd))
        require(files.identity(os.fstat(self.fd))[:6] == self.identity)
        fresh = _open_directory(self.path)
        try:
            require(files.identity(os.fstat(fresh))[:6] == self.identity)
        finally:
            os.close(fresh)

    def read(self, end):
        self.check()
        before = files.identity(os.fstat(self.fd))
        names, result = _names(self.fd), []
        for name in names:
            require(time.monotonic() < end)
            fd = os.open(
                name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=self.fd
            )
            try:
                info = os.fstat(fd)
                identity = files.identity(info)
                require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1)
                require(info.st_uid == os.geteuid() and info.st_mode & 0o7777 == 0o600)
                require(0 < info.st_size <= base.MAX_BYTES)
                raw = os.read(fd, base.MAX_BYTES + 1)
                require(len(raw) == info.st_size)
                require(identity == files.identity(os.fstat(fd)))
                require(
                    identity == files.identity(os.stat(name, dir_fd=self.fd, follow_symlinks=False))
                )
                result.append((identity, raw))
            finally:
                os.close(fd)
        require(names == _names(self.fd) and before == files.identity(os.fstat(self.fd)))
        self.check()
        require(time.monotonic() < end)
        return tuple(result)

    def close(self):
        if self._owned_fd >= 0:
            require(files.identity(os.fstat(self._owned_fd))[:2] == self.identity[:2])
            os.close(self._owned_fd)
            self._owned_fd = -1
            self.fd = -1


def _replay(snapshot, plan, projected, end):
    # An inert replay target as in Journal.replayed(): no open, lock, append,
    # replacement of the writer's state, or RecoverySession is constructed.
    replay = object.__new__(bootstrap.Journal)
    replay.machine = None
    previous, action = None, None
    for _, raw in snapshot:
        require(time.monotonic() < end)
        entry = json.loads(raw, object_pairs_hook=base.unique, parse_constant=base.reject_constant)
        require(type(entry) is dict and set(entry) == {"schema", "previous", "event"})
        require(type(entry["schema"]) is int and entry["schema"] == bootstrap.Journal.schema)
        require(entry["previous"] == base.checksum(previous) and base.encode(entry) == raw)
        action = bootstrap.Journal.apply(replay, entry["event"])
        previous = entry
    machine = replay.machine
    require(type(machine) is bootstrap.Machine)
    require((machine.case_id, machine.boot_id) == (plan.case, plan.boot))
    require(machine.created_at == plan.deadlines.issued_at)
    require(machine.hard_deadline == plan.deadlines.recover_by)
    require(machine.bootstrap == plan.bootstrap and machine.contract == plan.candidate.contract)
    require(
        base.encode(json.loads(snapshot[0][1])["event"])
        == base.encode(plan.preparation(machine.baseline, projected))
    )
    return machine, action  # Pure action is compared, never dispatched.


@dataclass(frozen=True)
class Binding:
    phase: str
    container_id: str
    execution_id: str
    intent_receipt: str
    created_receipt: str


@dataclass(frozen=True)
class Execution:
    binding: Binding
    state: str
    exit_code: int | None
    inspection_sha256: str


@dataclass(frozen=True)
class Status:
    apps: apps.Status
    executions: tuple[Execution, ...]
    pending_intent: tuple[str, str] | None  # phase/receipt; unknown create is NOT idle.
    capture_failed: bool
    inspection_failed: bool


class CliCustody:
    """One original observer before the first App action, never a dispatcher.

    observe() can be the receiver behind a separately authenticated command
    channel. Calling it directly is useful only in isolated composition tests;
    no callback/receipt by itself authenticates an independent process or user
    consent. The original TrackedDispatch remains the ONLY mutation owner.

    poll() may inspect already captured exec IDs after helper loss, through the
    same authenticated Engine. Confirmed terminal metadata is retained without
    rereading vanished IDs. Missing metadata is NOT completion; one failed read
    disables further Engine reads. Original absolute recovery expiry also ends
    reads, but retained factual status remains accessible. These elapsed bounds
    do not bound a blocked observer; independent execution supervision is needed.
    """

    def __init__(self, custody, projected):
        self.owner, self.lock = (os.getpid(), get_ident()), Lock()
        self.closed = self.capture_failed = self.inspection_failed = False
        self._history = None
        self._used, self._pending, self._executions = set(), None, ()
        self._pending_receipt = None
        self._original_executions, self._execution_bytes = self._executions, ()
        self._snapshot = ()
        acquired = False
        try:
            end = time.monotonic() + 2
            require(type(self) is CliCustody and type(custody) is apps.AppCustody)
            require(custody.lock.acquire(blocking=False))
            acquired = True
            require(custody.cli_custody_attempted is False)
            custody.cli_custody_attempted = True
            custody._capture_guard(end)
            require(len(custody._retained) == 1)
            require(not deadlines._readable(custody._retained[0][1]))
            self.custody, self.plan, self.projected = custody, custody.plan, projected
            self._originals = custody, self.plan, projected
            self.plan.check_projection(projected)
            self._history = _History(self.plan.root / "journal")
            self._original_history = self._history
            snapshot = self._history.read(end)
            machine, _ = _replay(snapshot, self.plan, projected, end)
            require(machine.state.phase == "prepared" and not machine.state.executions)
            require(snapshot == self._history.read(end))
            custody._capture_guard(end)
            self._snapshot = snapshot
        except BaseException as error:
            self.close()
            self._fail(error)
        finally:
            if acquired:
                custody.lock.release()

    def _guard(self):
        require(type(self) is CliCustody and not self.closed)
        require(self.owner == (os.getpid(), get_ident()))
        require(
            all(
                a is b
                for a, b in zip(
                    (self.custody, self.plan, self.projected), self._originals, strict=True
                )
            )
        )
        require(self._history is self._original_history)
        require((self._pending is None) == (self._pending_receipt is None))
        if self._pending is not None:
            require(self._pending.receipt == self._pending_receipt)
        require(self._executions is self._original_executions)
        require(tuple(base.encode(asdict(e)) for e in self._executions) == self._execution_bytes)
        require(self.custody.plan is self.plan and self.custody.cli_custody_attempted is True)
        return self.custody.poll()

    def _endpoint(self, end):
        status = self._guard()
        require(not status.deadline.recovery_deadline_expired and time.monotonic() < end)
        require(apps._endpoint(self.custody.endpoint) == self.custody.endpoint_pin)
        return status

    def _get(self, path, end):
        self._endpoint(end)
        value = engine._json_request(self.custody.endpoint, "GET", path, None, 200, deadline=end)
        self._endpoint(end)
        return value

    def _cli(self, cid, end):
        value = self._get("/containers/" + platform.CLI + "/json", end)
        require(value["Id"] == cid)
        require(
            platform.generation(value, name=platform.CLI, image=self.plan.cli_image)
            == self.plan.cli_generation
        )

    def _execution(self, binding, end):
        value = self._get("/exec/" + binding.execution_id + "/json", end)
        state = platform.execution_state(
            value,
            execution_id=binding.execution_id,
            container_id=binding.container_id,
            command=platform.CONTROL[binding.phase],
        )
        code = None
        if state == "not_running":
            require(value["Pid"] == 0 and 0 <= value["ExitCode"] <= 255)
            code = value["ExitCode"]
        elif state == "running":
            require(value["ExitCode"] in (None, 0))
        return Execution(binding, state, code, base.checksum(value))

    def observe(self, notice):
        acquired = False
        try:
            require(self.lock.acquire(blocking=False))
            acquired = True
            end = time.monotonic() + 2
            status = self._endpoint(end)
            require(not self.capture_failed and not self.inspection_failed)
            require(not status.deadline.helper_exited)
            require(type(notice) is platform.DispatchNotice)
            require(notice.stage in ("before_create", "before_start"))
            require(notice.phase in platform.CONTROL)
            require((notice.case_id, notice.boot_id) == (self.plan.case, self.plan.boot))
            base.digest(notice.container_id)
            receipt = notice.receipt
            snapshot = self._history.read(end)
            require(snapshot[: len(self._snapshot)] == self._snapshot)
            require(tuple(raw for _, raw in snapshot) == notice.history)
            machine, action = _replay(snapshot, self.plan, self.projected, end)
            require(machine.state.phase == notice.phase)
            cutoff = min(machine.state.deadline, self.plan.deadlines.recover_by)
            if notice.phase in ("stopping_normal", "starting_candidate"):
                cutoff = min(cutoff, self.plan.deadlines.ready_by)
            now = time.clock_gettime_ns(time.CLOCK_BOOTTIME) / 1_000_000_000
            require(0 <= now - machine.last_at <= 2 and now < cutoff)
            known = tuple(
                (e.binding.phase, e.binding.container_id, e.binding.execution_id)
                for e in self._executions
            )
            # Never adopt an earlier command the observer did not capture, or
            # trust the writer's terminal event in lieu of independent metadata.
            require(all(e.state == "not_running" for e in self._executions))
            require(
                machine.state.completed_executions
                == tuple((e.binding.execution_id, e.exit_code) for e in self._executions)
            )
            if notice.stage == "before_create":
                require(machine.state.executions == known)
                require(notice.execution_id is None and self._pending is None)
                require(notice.phase not in self._used)
                self._used.add(notice.phase)  # Consume before any further acquisition.
                require(type(action) is base.Action and action.case_id == self.plan.case)
                require(
                    ("ha", "apps", action.operation, action.slug, "--raw-json")
                    == platform.CONTROL[notice.phase]
                )
                require(not any(p == notice.phase for p, _, _ in machine.state.executions))
                self._cli(notice.container_id, end)
                pending = notice
                captured = None
            else:
                require(self._pending is not None and notice.phase == self._pending.phase)
                pending = self._pending
                self._pending = None  # A lost acknowledgement never allows another try.
                self._pending_receipt = None
                require(notice.container_id == pending.container_id)
                base.digest(notice.execution_id)
                require(notice.history[:-1] == pending.history and len(snapshot) > 1)
                require(json.loads(snapshot[-1][1])["event"]["kind"] == "bind_execution")
                require(
                    machine.state.executions[-1]
                    == (notice.phase, notice.container_id, notice.execution_id)
                )
                require(machine.state.executions[:-1] == known)
                require(
                    not any(e.binding.execution_id == notice.execution_id for e in self._executions)
                )
                binding = Binding(
                    notice.phase, notice.container_id, notice.execution_id, pending.receipt, receipt
                )
                self._cli(notice.container_id, end)
                captured = self._execution(binding, end)
                require(captured.state == "created")
                require(captured == self._execution(binding, end))
                pending = None
            self._cli(notice.container_id, end)
            require(snapshot == self._history.read(end))
            require(notice.receipt == receipt)
            status = self._endpoint(end)
            require(not self.capture_failed and not self.inspection_failed)
            require(not status.deadline.helper_exited)
            now = time.clock_gettime_ns(time.CLOCK_BOOTTIME) / 1_000_000_000
            require(0 <= now - machine.last_at <= 2 and now < cutoff)
            self._snapshot, self._pending = snapshot, pending
            self._pending_receipt = pending.receipt if pending is not None else None
            if captured is not None:
                self._retain((*self._executions, captured))
            return receipt
        except BaseException as error:
            self.capture_failed = True
            self._fail(error)
        finally:
            if acquired:
                self.lock.release()

    def poll(self):
        acquired = False
        try:
            require(self.lock.acquire(blocking=False))
            acquired = True
            end = time.monotonic() + 2
            status = self._guard()
            if not self.inspection_failed and not status.deadline.recovery_deadline_expired:
                for index, entry in enumerate(self._executions):
                    if entry.state == "not_running":
                        continue  # Retain confirmed terminal facts even after metadata expiry.
                    try:
                        current = self._execution(entry.binding, end)
                        require(not (entry.state == "running" and current.state == "created"))
                        self._retain(
                            (
                                *self._executions[:index],
                                current,
                                *self._executions[index + 1 :],
                            )
                        )
                    except Exception:
                        self.inspection_failed = True
                        break  # Unknown read is not exit; never automatically retry it.
            return Status(
                self._guard(),
                self._executions,
                (self._pending.phase, self._pending_receipt) if self._pending is not None else None,
                self.capture_failed,
                self.inspection_failed,
            )
        except BaseException as error:
            self.inspection_failed = True
            self._fail(error)
        finally:
            if acquired:
                self.lock.release()

    def _retain(self, values):
        self._executions = self._original_executions = values
        self._execution_bytes = tuple(base.encode(asdict(e)) for e in values)

    def _fail(self, error):
        if not isinstance(error, Exception):
            raise error
        raise UnconfirmedCliCustody(MESSAGE) from None

    def close(self):
        require(self.owner == (os.getpid(), get_ident()))
        if self.closed:
            return
        self.closed = True
        history = getattr(self, "_original_history", None)
        if history is not None:
            history.close()
