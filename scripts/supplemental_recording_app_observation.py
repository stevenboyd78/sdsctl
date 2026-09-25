#!/usr/bin/env python3
"""Explicit read-only App host/status composition after a returned begin.

Uninstalled. Direct-policy gates stay unchanged. Full source/runtime inventories
still bracket every observation; neither a native receipt file nor an earlier
successful read grants health, recording success or restoration authority.
"""

from __future__ import annotations

import os
import time
from contextlib import suppress
from threading import Lock, get_ident

import qualify_supplemental_recording_app_active as active
import supplemental_recording_app_begin as app_begin

begin, launch, require = app_begin.begin, app_begin.launch, app_begin.require


class AppRetainedHost(begin.RetainedHost):
    """Original App authorization plus fresh metadata and recording file reads.

    Only admission/identity/failure handling differ from the direct reader. Its
    fixed read-only worker, owner-thread file collection, two-second deadline
    and unknown-until-probed native flags are shared without a caller callback.
    """

    def __init__(self, start, continuity):
        self.owner, self.lock = (os.getpid(), get_ident()), Lock()
        self.failed = False
        self.pending = None
        self.start = self.original_start = None
        try:
            require(type(self) is AppRetainedHost and type(start) is app_begin.AppStart)
            require(type(continuity) is launch.idle_module.PostBegin)
            self.start = self.original_start = start
            self.continuity = continuity
            self.run, self.plan, self.relay = start.run, start.plan, start.relay
            self.docker = self.run.read.docker
            self.objects = self._objects()
            self._guard()
        except BaseException as error:
            self._fail(error)

    def _objects(self):
        return (
            self.start,
            self.run,
            self.plan,
            self.relay,
            self.continuity,
            self.docker,
            self.run.read,
            self.run.witness,
            self.run.qualify,
        )

    def _guard(self, *, require_live=False):
        require(type(self) is AppRetainedHost and type(require_live) is bool)
        require(not self.failed and self.owner == (os.getpid(), get_ident()))
        require(all(a is b for a, b in zip(self._objects(), self.objects, strict=True)))
        start, run, plan, relay, continued = (
            self.start,
            self.run,
            self.plan,
            self.relay,
            self.continuity,
        )
        require(type(start) is app_begin.AppStart and start is self.original_start)
        require(type(run) is app_begin.execution.AppLaunch)
        require(start.run is run and start.plan is plan and start.relay is relay)
        require(type(relay) is begin.relayed.Relay and relay.phase in ("completed", "closed"))
        require(type(continued) is launch.idle_module.PostBegin)
        require(continued.idle is run.idle and continued.guard is relay.guard)
        require(continued.plan is plan and continued.ready is run.ready)
        require(continued.finish_by == plan.lease["stop_by"])
        require(type(self.docker) is begin.plans.ordinary.Docker)
        require(self.docker.path == "/var/run/docker.sock")
        require(self.docker is run.read.docker is run.qualify.docker)
        start.retained_history(require_live=require_live)

    def _fail(self, error):
        self.failed = True
        if self.pending is not None:
            self.pending.cancelled.set()
        if type(self.original_start) is app_begin.AppStart:
            self.original_start._fail(error)
        if not isinstance(error, Exception):
            raise error
        raise begin.UnconfirmedHostBegin(begin.MESSAGE) from None


class AppActiveSample(begin.ActiveSample):
    """One cached native status read within full original App qualification.

    Exact AppRetainedHost/AppStart/NativeActiveQualification identities replace
    only the direct policy's admissions, not its observation algorithm. A new
    one-use sampler may borrow the same continuing inputs; a failed host or
    qualification cannot be repaired by constructing another sampler.
    """

    def __init__(self, host, qualify):
        self.owner, self.lock = (os.getpid(), get_ident()), Lock()
        self.used = self.failed = self.closed = False
        self.probe = self.host = self.original_host = None
        try:
            require(type(self) is AppActiveSample and type(host) is AppRetainedHost)
            require(type(qualify) is active.NativeActiveQualification)
            self.host = self.original_host = host
            self.qualify = qualify
            self.start, self.run, self.plan = host.start, host.run, host.plan
            self.relay, self.continuity = host.relay, host.continuity
            self.objects = self._objects()
            self.context = host._context()
            self._guard()
        except BaseException as error:
            self._fail(error)

    def _objects(self):
        host = self.host
        return (
            host,
            self.qualify,
            host.start,
            host.run,
            host.plan,
            host.relay,
            host.continuity,
            host.docker,
        )

    def _bindings(self):
        require(type(self) is AppActiveSample and not self.failed and not self.closed)
        require(self.owner == (os.getpid(), get_ident()))
        host, q = self.host, self.qualify
        require(type(host) is AppRetainedHost and host is self.original_host)
        require(type(q) is active.NativeActiveQualification)
        require(all(a is b for a, b in zip(self._objects(), self.objects, strict=True)))
        require(
            all(
                a is b
                for a, b in zip(
                    (self.start, self.run, self.plan, self.relay, self.continuity),
                    (host.start, host.run, host.plan, host.relay, host.continuity),
                    strict=True,
                )
            )
        )
        require(q.start is self.start and q.prebegin is self.run.ready_qualification)
        require(q.continuity is self.continuity and q.plan is self.plan)
        require(q.idle is self.run.idle and q.witness is self.run.witness)
        require(q.docker is host.docker is self.run.qualify.docker)
        require(not q.failed and host._context() == self.context)
        require(self.relay.phase == "completed" and self.relay.expected is not None)
        require(time.monotonic() < self.relay.plan.stop_at)

    def _fail(self, error):
        self.failed = True
        if self.probe is not None and self.probe.channel is not None:
            with suppress(Exception):
                self.probe.channel.close()
        if type(self.original_host) is AppRetainedHost:
            self.original_host._fail(error)
        if not isinstance(error, Exception):
            raise error
        raise begin.UnconfirmedHostBegin(begin.MESSAGE) from None


if __name__ == "__main__":
    raise SystemExit("Uninstalled App observation only; no polling loop enabled.")
