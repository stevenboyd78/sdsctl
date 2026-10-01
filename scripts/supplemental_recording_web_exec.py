#!/usr/bin/env python3
"""One original-Ready-bound finite dashboard exec, not a host adapter.

Image/source/interpreter/environment, fixed host paths and independent recovery
must already be qualified. No caller-selected command, request, actor or new
deadline. No recording begin, stop, success or ownership restoration. The web
process stays separate from the guardian's exact two-child native tree.
"""

from __future__ import annotations

import hashlib
import json
import os
import select
import time
from dataclasses import dataclass
from threading import get_ident

import supplemental_recording_retained as retained

engine = retained.engine
dispatch = engine.dispatch
execution = dispatch.execution
binding = dispatch.binding
namespace = engine.namespace
MESSAGE = "Finite dashboard execution is unconfirmed; preserve the case and do not retry."


class UnconfirmedWebExec(ValueError):
    """A lost web return consumes launch, never authorizes another listener."""


def require(value):
    if not value:
        raise UnconfirmedWebExec(MESSAGE)


@dataclass(frozen=True)
class Exited:
    execution_id: str
    host_pid: int
    start_ticks: int
    inspection_sha256: str
    observed_at: float


class Launch:
    """One durable web dispatch derived only from the actual original Ready.

    Borrows Ready/Endpoint/original actor handles, owns only its web attachment
    and new retained pidfd. The fixed sibling `web-exec` directory must have
    been precreated before idle continuity begins; construction never creates
    directories. Reopening any consumed web intent refuses a second launch.

    After the independent one-begin path, retain() accepts only a Retained of
    this same Ready. No deadline is extended. End/close never mean process exit;
    observe_exit separately needs the original pidfd and exact Engine status.
    It must precede the operator Exit collector which closes the shared Endpoint.
    Independent host/container recovery still covers blocked or frozen code.
    """

    def __init__(self, ready):
        self.owner = os.getpid(), get_ident()
        self.ready = self.original = ready
        self.claim = self.channel = self.actor = self.execution_id = None
        self.fd = -1
        self.used = self.listening = self.ended = self.closed = self.failed = False
        self.tip = self.journal_identity = None
        try:
            require(type(ready) is retained.received.Ready)
            ready.check_before_begin()
            self.client, self.processes = ready.client, ready.processes
            self.endpoint, self.operator = self.client.endpoint, self.client.claim
            self.pins = self.operator.pins
            self.context_raw, self.ready_raw = ready.context_raw, ready.ready_raw
            self.clock, self.zero_domain = ready.clock, ready.zero_domain
            self.ready_by, self.watch_deadline = ready.ready_by, ready.watch_deadline
            self.finish_by = self.watch_deadline + retained.received.GRACE_SECONDS
            require(self.finish_by <= self.client.attachment.finish_by)
            self.actors, self.handles = self.processes.actors, dict(self.processes.handles)
            report = json.loads(self.ready_raw)
            request = {
                "schema": 1,
                "kind": "finite-recording-web-startup",
                "context": json.loads(self.context_raw),
                **{role: report[role] for role in ("guardian", "native", "watchdog")},
            }
            self.request_raw = binding.encode(request)
            self.request_sha256 = hashlib.sha256(self.request_raw).hexdigest()
            command = self.pins.command
            self.command = execution.WebCommand(
                command.plan,
                command.plan_sha256,
                command.source_sha256,
                self.ready_by,
                self.request_sha256,
            )
            self.web_pins = dispatch.WebPins(
                self.pins, self.command, hashlib.sha256(self.ready_raw).hexdigest()
            )
            self.directory = self.operator.directory.with_name("web-exec")
            require(self.directory != self.operator.directory)
            self._original_live()
            self.claim = dispatch.WebClaim(self.directory, self.web_pins, self.operator.witness)
            self.claim.check()
            self._original_live()
        except BaseException as error:
            self._fail(error)

    def _same(self):
        require(not self.closed and self.owner == (os.getpid(), get_ident()))
        ready = self.ready
        require(type(ready) is retained.received.Ready)
        require(ready.client is self.client and ready.processes is self.processes)
        require(self.client.endpoint is self.endpoint and self.client.claim is self.operator)
        require(self.operator.pins == self.pins)
        require((ready.context_raw, ready.ready_raw) == (self.context_raw, self.ready_raw))
        require((ready.ready_by, ready.watch_deadline) == (self.ready_by, self.watch_deadline))
        require(ready.clock == self.clock and ready.zero_domain is self.zero_domain)
        require(self.processes.actors == self.actors and self.processes.handles == self.handles)
        require(
            self.web_pins
            == dispatch.WebPins(self.pins, self.command, hashlib.sha256(self.ready_raw).hexdigest())
        )
        require(self.command.request_sha256 == hashlib.sha256(self.request_raw).hexdigest())
        require(self.command.ready_by == self.ready_by)
        self.web_pins.payload()
        require(self.directory == self.operator.directory.with_name("web-exec"))
        require(self.finish_by == self.watch_deadline + retained.received.GRACE_SECONDS)

    def _original_live(self):
        self._same()
        require(not self.failed and not self.ended)
        if self.original is self.ready:
            self.ready.check_before_begin()
        else:
            require(type(self.original) is retained.Retained and self.original.ready is self.ready)
            require(self.original.check() == frozenset())
        require(time.monotonic() < self.watch_deadline)

    def _startup(self):
        self._original_live()
        require(self.original is self.ready and time.monotonic() < self.ready_by)
        require(type(self.claim) is dispatch.WebClaim and self.claim.pins == self.web_pins)
        require(
            self.claim.directory == self.directory and self.claim.witness is self.operator.witness
        )
        self.claim.check()

    def _inspect(self, end):
        value = engine._json_request(
            self.endpoint, "GET", f"/exec/{self.execution_id}/json", None, 200, deadline=end
        )
        state = execution.inspect_web(
            value,
            execution_id=self.execution_id,
            container_id=self.pins.init.container_id,
            command=self.command,
        )
        return value, state

    def _live_web(self):
        engine._alive(self.fd)
        require(namespace.read(self.actor.host_pid, self.actor.container_id) == self.actor)
        engine._alive(self.fd)

    def _bind(self):
        self._startup()
        _, first = self._inspect(self.ready_by)
        require(first.phase == "running")
        require(first.pid not in {a.host_pid for a in self.actors})
        self.fd = os.pidfd_open(first.pid, 0)
        engine._alive(self.fd)
        actor = namespace.read(first.pid, self.pins.init.container_id)
        require(type(actor) is namespace.Actor and actor.host_pid == first.pid)
        require(actor.container_id == self.pins.init.container_id)
        require(actor.namespaces == self.actors[0].namespaces)
        require(actor.local_pid > 1 and actor.local_pid not in {a.local_pid for a in self.actors})
        require(actor.parent not in {a.host_pid for a in self.actors[1:]})
        self.actor = actor
        require(self._inspect(self.ready_by)[1] == first)
        self._live_web()
        self._startup()

    def start(self):
        """One attempt, including lost create/attach/listening returns; no retry."""
        try:
            self._startup()
            require(not self.used and self.claim.state.phase == "create_intent")
            self.used = True
            value = engine._json_request(
                self.endpoint,
                "POST",
                f"/containers/{self.pins.init.container_id}/exec",
                self.command.create_body(),
                201,
                deadline=self.ready_by,
            )
            require(set(value) == {"Id"})
            execution._digest(value["Id"])
            require(value["Id"] != self.operator.state.execution_id)
            self.execution_id = value["Id"]
            self.claim.created(self.execution_id)
            self._startup()
            inspected, state = self._inspect(self.ready_by)
            require(state.phase == "created")
            self.claim.attach_intent(inspected)
            self.tip, self.journal_identity = self.claim.state, self.claim.identity
            self._startup()
            channel = self.endpoint.connect(deadline=self.ready_by)
            self.channel = engine.attachment.WebAttachment(
                channel,
                self.execution_id,
                ready_by=self.ready_by,
                finish_by=self.finish_by,
                sender=self.endpoint.sender,
            )
            self.channel.start(deadline=self.ready_by)
            self._bind()
            sent_at = time.monotonic()
            self.channel.send_request(json.loads(self.request_raw), deadline=self.ready_by)
            value = self.channel.receive(deadline=self.ready_by)
            received_at = time.monotonic()
            binding.protected._mapping(
                value,
                {
                    "schema",
                    "kind",
                    "request_sha256",
                    "pid",
                    "observed_at",
                    "deadline",
                },
            )
            require(type(value["schema"]) is int and value["schema"] == 1)
            require(value["kind"] == "finite-recording-web-listening")
            require(value["request_sha256"] == self.request_sha256)
            require(type(value["pid"]) is int and value["pid"] == self.actor.local_pid)
            binding.clock(value["observed_at"])
            binding.clock(value["deadline"])
            require(sent_at <= value["observed_at"] <= received_at < self.ready_by)
            require(value["deadline"] == self.watch_deadline)
            self._startup()
            self._live_web()
            self.channel.check_quiet(deadline=self.watch_deadline)
            self.listening = True
        except BaseException as error:
            self._fail(error)

    def _history(self, end):
        require(type(self.claim) is dispatch.WebClaim and self.claim.owner == self.owner)
        require(self.claim.directory == self.directory and self.claim.pins == self.web_pins)
        require(self.claim.witness is self.operator.witness)
        require(self.tip is not None and self.tip.phase == "attach_intent" and self.tip.count == 3)
        require(self.claim.state == self.tip and self.claim.identity == self.journal_identity)
        dispatch._web_location(self.directory, self.web_pins)
        with binding.protected._private_directory(self.directory, exclusive=False) as fd:
            require(binding.identity(os.fstat(fd))[:6] == self.journal_identity)
            require(dispatch._read_web(fd, self.web_pins, end) == self.tip)
        require(time.monotonic() < end)

    def check(self):
        """Continuing web identity/quiet channel, not daemon-health evidence."""
        try:
            self._original_live()
            require(self.listening and not self.claim.poisoned)
            end = min(time.monotonic() + 2, self.watch_deadline)
            self._history(end)
            self._live_web()
            self.channel.check_quiet(deadline=end)
            self._original_live()
            require(time.monotonic() < end)
        except BaseException as error:
            self._fail(error)

    def retain(self, original):
        """Bind the same Ready after its separately authorized single begin."""
        try:
            self._same()
            require(not self.failed and self.listening and not self.ended)
            require(self.original is self.ready)
            require(type(original) is retained.Retained and original.ready is self.ready)
            require(original.check() == frozenset())
            self.original = original
            self.check()
        except BaseException as error:
            self._fail(error)

    def end(self):
        """Close only the web attachment; preserve its original exit handle."""
        require(not self.closed and self.owner == (os.getpid(), get_ident()))
        self.ended = True
        if self.channel is not None:
            self.channel.close()

    def observe_exit(self):
        """Original web pidfd plus exact terminal70; not recording success.

        Read-only reconciliation may follow a failed live check. It cannot
        recreate, reattach, signal, or restore ownership, and it never releases
        the retained handle. Original init must still be independently alive.
        """
        try:
            self._same()
            require(self.ended and self.actor is not None and self.fd >= 0)
            end = min(time.monotonic() + 2, self.finish_by)
            require(time.monotonic() < end)
            self.clock.check_later(retained.received.clock.read())
            self._history(end)
            initial = self.operator.witness
            require(initial.identity == self.pins.init and not initial.exited())
            require(
                dispatch.process.read_identity(self.pins.init.pid, self.pins.init.container_id)
                == self.pins.init
            )
            poll = select.poll()
            poll.register(self.fd, select.POLLIN)
            events = poll.poll(max(0, int((end - time.monotonic()) * 1000)))
            require(len(events) == 1 and events[0][0] == self.fd)
            require(events[0][1] in (select.POLLIN, select.POLLIN | select.POLLHUP))
            inspected, state = self._inspect(end)
            require(state.phase == "not_running" and state.returncode == 70)
            require(state.pid in (0, self.actor.host_pid))
            self._history(end)
            require(not initial.exited())
            require(
                dispatch.process.read_identity(self.pins.init.pid, self.pins.init.container_id)
                == self.pins.init
            )
            self.clock.check_later(retained.received.clock.read())
            require(time.monotonic() < end)
            return Exited(
                self.execution_id,
                self.actor.host_pid,
                self.actor.start_ticks,
                binding.checksum(inspected),
                time.monotonic(),
            )
        except BaseException as error:
            self._fail(error)

    def _fail(self, error):
        self.failed = True
        if self.claim is not None:
            self.claim.poisoned = True
        if self.channel is not None:
            self.channel.close()
        if not isinstance(error, Exception):
            raise error
        raise UnconfirmedWebExec(MESSAGE) from None

    def close(self):
        require(self.owner == (os.getpid(), get_ident()))
        if self.closed:
            return
        self.end()
        self.closed = True
        if self.fd >= 0:
            fd, self.fd = self.fd, -1
            os.close(fd)


if __name__ == "__main__":
    raise SystemExit("Private original-Ready web join only; no installed host action enabled.")
