#!/usr/bin/env python3
"""One passive Engine exec joined to the original live recording actors.

Uninstalled mechanism, not a source/image/runtime/container qualifier. The host
adapter must independently establish and recheck those properties around this
sample. No caller-selected PIDs, argv, request, generation or renewed recording
deadline. No scanner demand, recording begin, stop, signal or recovery action.
"""

from __future__ import annotations

import hashlib
import json
import os
import select
import time
from threading import get_ident

import supplemental_handoff_observer as observer
import supplemental_recording_retained as retained

engine = retained.engine
execution = engine.dispatch.execution
binding = engine.dispatch.binding
namespace = engine.namespace
MESSAGE = "Finite recording probe execution is unconfirmed; preserve evidence and do not retry."
SAMPLE_SECONDS, MAX_COLLECTION_SECONDS, MAX_AGE_SECONDS = 5, 2.5, 2


class UnconfirmedProbeExec(ValueError):
    """An uncertain passive read cannot establish healthy or recording-idle."""


def require(value):
    if not value:
        raise UnconfirmedProbeExec(MESSAGE)


class Sample:
    """One request through the original Engine peer, with an actual probe pidfd.

    Takes neither Ready/Retained nor Endpoint ownership. Owns only this new
    probe attachment and process descriptor. Failure consumes this sample and
    retains its execution ID/actor/pidfd until close(); no automatic retry or
    signal. Probe EOF and exit0 are separate requirements, neither native exit
    nor health. Result flags come only from the bound, fresh, closed reply.
    Source/container requalification and full recording-file checks remain the
    caller's prerequisite. No installed HostObserver selects this path yet.
    """

    def __init__(self, original):
        self.owner = os.getpid(), get_ident()
        self.original = original
        self.channel = self.actor = self.execution_id = None
        self.fd = -1
        self.used = self.closed = self.failed = False
        try:
            require(type(original) in (retained.received.Ready, retained.Retained))
            self.ready = original if type(original) is retained.received.Ready else original.ready
            self.client, self.processes = self.ready.client, self.ready.processes
            self.endpoint, self.pins = self.client.endpoint, self.client.claim.pins
            self.context_raw, self.ready_raw = self.ready.context_raw, self.ready.ready_raw
            self.clock, self.zero_domain = self.ready.clock, self.ready.zero_domain
            self.ready_by, self.watch_deadline = self.ready.ready_by, self.ready.watch_deadline
            self.actors = self.processes.actors
            self.handles = dict(self.processes.handles)
            self.original_end = (
                self.ready_by if type(original) is retained.received.Ready else self.watch_deadline
            )
            self.probe_by = min(time.monotonic() + SAMPLE_SECONDS, self.original_end)
            command = self.pins.command
            self.command = execution.ProbeCommand(
                command.plan, command.plan_sha256, command.source_sha256, self.probe_by
            )
            self._check()
            report = json.loads(self.ready_raw)
            request = {
                "schema": 1,
                "kind": "finite-recording-cached-probe",
                "context": json.loads(self.context_raw),
                **{role: report[role] for role in ("guardian", "native", "watchdog")},
            }
            self.request_raw = binding.encode(request)
            self.request_sha256 = hashlib.sha256(self.request_raw).hexdigest()
        except BaseException as error:
            self._fail(error)

    def _check(self):
        require(not self.closed and not self.failed and self.owner == (os.getpid(), get_ident()))
        require(time.monotonic() < self.probe_by <= self.original_end)
        ready = self.ready
        require(ready.client is self.client and ready.processes is self.processes)
        require(self.client.endpoint is self.endpoint and self.client.claim.pins == self.pins)
        require((ready.context_raw, ready.ready_raw) == (self.context_raw, self.ready_raw))
        require((ready.ready_by, ready.watch_deadline) == (self.ready_by, self.watch_deadline))
        require(ready.clock == self.clock and ready.zero_domain is self.zero_domain)
        require(self.processes.actors == self.actors and self.processes.handles == self.handles)
        if type(self.original) is retained.received.Ready:
            self.original.check_before_begin()
        else:
            require(type(self.original) is retained.Retained and self.original.ready is ready)
            require(self.original.check() == frozenset())  # Buffered exits are not live health.
        require(time.monotonic() < self.probe_by)

    def _inspect(self):
        self._check()
        value = engine._json_request(
            self.endpoint,
            "GET",
            f"/exec/{self.execution_id}/json",
            None,
            200,
            deadline=self.probe_by,
        )
        result = execution.inspect_probe(
            value,
            execution_id=self.execution_id,
            container_id=self.pins.init.container_id,
            command=self.command,
        )
        self._check()
        return result

    def _probe_live(self):
        engine._alive(self.fd)
        require(namespace.read(self.actor.host_pid, self.actor.container_id) == self.actor)
        engine._alive(self.fd)

    def _bind(self):
        first = self._inspect()
        require(first.phase == "running")
        require(first.pid not in {actor.host_pid for actor in self.actors})
        self.fd = os.pidfd_open(first.pid, 0)
        engine._alive(self.fd)
        actor = namespace.read(first.pid, self.pins.init.container_id)
        require(type(actor) is namespace.Actor and actor.host_pid == first.pid)
        require(actor.container_id == self.pins.init.container_id)
        require(actor.namespaces == self.actors[0].namespaces)
        require(actor.local_pid > 1 and actor.local_pid not in {a.local_pid for a in self.actors})
        require(actor.parent not in {a.host_pid for a in self.actors[1:]})
        self.actor = actor
        require(self._inspect() == first)  # Actual second Engine read, not caller metadata.
        self._probe_live()
        self._check()

    def _reply(self, value, sent_at, received_at):
        fields = binding.protected._mapping
        fields(value, {"schema", "kind", "request_sha256", "observed_after", "observed_at", "body"})
        require(type(value["schema"]) is int and value["schema"] == 1)
        require(value["kind"] == "finite-recording-cached-probe-result")
        require(value["request_sha256"] == self.request_sha256)
        start, end = value["observed_after"], value["observed_at"]
        binding.clock(start)
        binding.clock(end)
        require(sent_at <= start <= end <= received_at < self.probe_by)
        require(end - start <= MAX_COLLECTION_SECONDS)
        body = fields(
            value["body"],
            {
                "profile_sha256",
                "healthy",
                "recording",
                "supplemental_advertised",
                "peer_pid",
                "peer_start_ticks",
            },
        )
        require(body["profile_sha256"] == json.loads(self.context_raw)["profile"])
        require(body["supplemental_advertised"] is True)
        require(type(body["healthy"]) is bool and type(body["recording"]) is bool)
        native = self.actors[2]
        require(type(body["peer_pid"]) is int and body["peer_pid"] == native.local_pid)
        require(body["peer_start_ticks"] == str(native.start_ticks))
        return observer.NativeState(self.pins.generation, body["healthy"], body["recording"]), end

    def read(self):
        """Consume once; return truthful cached flags, never a success receipt."""
        try:
            self._check()
            require(not self.used)
            self.used = True  # Even a lost create return consumes this sample.
            value = engine._json_request(
                self.endpoint,
                "POST",
                f"/containers/{self.pins.init.container_id}/exec",
                self.command.create_body(),
                201,
                deadline=self.probe_by,
            )
            require(set(value) == {"Id"})
            execution._digest(value["Id"])
            require(value["Id"] != self.client.claim.state.execution_id)
            self.execution_id = value["Id"]
            require(self._inspect().phase == "created")
            sock = self.endpoint.connect(deadline=self.probe_by)
            self.channel = engine.attachment.ProbeAttachment(
                sock, self.execution_id, probe_by=self.probe_by, sender=self.endpoint.sender
            )
            self.channel.start(deadline=self.probe_by)
            self._bind()
            self._probe_live()
            sent_at = time.monotonic()
            self.channel.send_request(json.loads(self.request_raw), deadline=self.probe_by)
            value = self.channel.receive(deadline=self.probe_by)
            received_at = time.monotonic()
            result, sampled_at = self._reply(value, sent_at, received_at)
            self.channel.finish(deadline=self.probe_by)  # Only framing EOF.
            self._check()
            poller = select.poll()
            poller.register(self.fd, select.POLLIN)
            events = poller.poll(max(0, int((self.probe_by - time.monotonic()) * 1000)))
            require(len(events) == 1 and events[0][0] == self.fd)
            require(events[0][1] in (select.POLLIN, select.POLLIN | select.POLLHUP))
            final = self._inspect()  # Separate original process exit AND exact Engine status.
            require(final.phase == "not_running" and final.returncode == 0)
            require(final.pid in (0, self.actor.host_pid))
            self._check()
            require(0 <= time.monotonic() - sampled_at <= MAX_AGE_SECONDS)
            return result
        except BaseException as error:
            self._fail(error)

    def _fail(self, error):
        self.failed = True
        if self.channel is not None:
            self.channel.close()
        if not isinstance(error, Exception):
            raise error
        raise UnconfirmedProbeExec(MESSAGE) from None

    def close(self):
        require(self.owner == (os.getpid(), get_ident()))
        if self.closed:
            return
        self.closed = True
        try:
            if self.channel is not None:
                self.channel.close()
        finally:
            if self.fd >= 0:
                fd, self.fd = self.fd, -1
                os.close(fd)


if __name__ == "__main__":
    raise SystemExit("Private passive exec join only; no installed host action enabled.")
