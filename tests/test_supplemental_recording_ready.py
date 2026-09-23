"""Actual private framing and pidfds, synthetic Engine/namespace/clock metadata.

No real scanner or Docker endpoint. Kernel clock sampling is qualified separately;
this suite makes domain failures deterministic while retaining actual transport,
original intent files, owned processes, and real monotonic elapsed deadlines.
"""

import importlib.util
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_clock as clocks
from . import test_supplemental_recording_engine_binding as joined

NAME = "supplemental_recording_ready"
SPEC = importlib.util.spec_from_file_location(NAME, Path(joined.m.__file__).with_name(NAME + ".py"))
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)
layout, tree, routing, projection, binding, directory, prepared, family, actors = (
    joined.layout,
    joined.tree,
    joined.routing,
    joined.projection,
    joined.binding,
    joined.directory,
    joined.prepared,
    joined.family,
    joined.actors,
)
PROFILE = "f" * 64


@pytest.fixture
def calibration(prepared, monkeypatch):
    result = SimpleNamespace(
        boot=prepared.pins.host.boot_id, namespace=joined.ns.NS[4], offset=10, calls=0, after=None
    )

    def read():
        result.calls += 1
        if result.after is not None:
            result.after(result.calls)
        now = time.monotonic_ns()
        return clocks.m.Window(
            result.boot, result.namespace, now, now + result.offset * clocks.m.NS, now
        )

    monkeypatch.setattr(m.clock, "read", read)
    result.original = read()
    return result


def envelope(prepared, actors):
    context = m._context(prepared.pins, PROFILE)
    at = time.monotonic()
    raw = m.engine.dispatch.binding.encode(
        {
            "schema": 1,
            "kind": "finite-recording-control",
            "phase": "ready",
            "context": context,
            "body": {},
            "at": at,
        }
    ).decode("ascii")
    native = dict(actors.report["native"], raw=raw, received_at=at)
    return {
        "schema": 1,
        "kind": "finite-recording-operator",
        "phase": "ready",
        "context": context,
        "guardian": dict(actors.report["guardian"]),
        "native": dict(actors.report["native"]),
        "watchdog": dict(
            actors.report["watchdog"],
            deadline=prepared.pins.command.ready_by
            + prepared.pins.host.projection.native.contract.maximum_recording_seconds,
            grace=3,
        ),
        "body": {"received": native},
    }


def attached(prepared, actors, monkeypatch, *, change=None, tap=None):
    def message():
        value = envelope(prepared, actors)
        if change is not None:
            change(value)
        return value

    return joined.ready(
        prepared,
        actors,
        monkeypatch,
        consume=False,
        message=message,
        finish_extra=prepared.pins.host.projection.native.contract.maximum_recording_seconds + 3,
        tap=tap,
    )


def capture(client, calibration):
    return m.Ready(client, profile_sha256=PROFILE, original_clock=calibration.original)


def denied(callback, client):
    with pytest.raises(m.UnconfirmedReady) as error:
        callback()
    assert str(error.value) == m.MESSAGE and "PRIVATE" not in str(error.value)
    assert client.closed and client.attachment.closed
    assert not client.attachment.begun


def test_only_actual_ready_is_joined_to_original_context_and_live_witness(
    prepared, actors, calibration, monkeypatch
):
    with attached(prepared, actors, monkeypatch) as (client, requests):
        result = capture(client, calibration)
        try:
            assert len(requests) == 5 and client.attachment.reads == 1
            assert not client.attachment.begun and result.processes.refresh() == actors.values
            assert json.loads(result.context_raw) == m._context(prepared.pins, PROFILE)
            report = json.loads(result.ready_raw)
            assert report["body"]["received"]["raw"]
            original = (result.ready_by, result.watch_deadline, result.clock)
            result.check_before_begin()
            assert (result.ready_by, result.watch_deadline, result.clock) == original
            assert all(
                not hasattr(result, method) for method in ("begin", "start", "resume", "success")
            )
        finally:
            result.close()
        assert result.closed and not prepared.witness.exited()


@pytest.mark.parametrize(
    "field",
    [
        "launch",
        "source",
        "projection",
        "host_plan",
        "profile",
        "manifest",
        "contract",
        "generation",
        "ready_by",
    ],
)
@pytest.mark.parametrize("where", ["outer", "embedded"])
def test_every_original_context_field_must_match(
    prepared, actors, calibration, monkeypatch, field, where
):
    def change(value):
        if where == "outer":
            value["context"][field] = "PRIVATE"
        else:
            received = value["body"]["received"]
            native = json.loads(received["raw"])
            native["context"][field] = "PRIVATE"
            received["raw"] = m.engine.dispatch.binding.encode(native).decode()

    with attached(prepared, actors, monkeypatch, change=change) as (client, requests):
        denied(lambda: capture(client, calibration), client)
        assert len(requests) == 3  # No live-exec request after a bad envelope.


@pytest.mark.parametrize(
    "fault",
    [
        "schema",
        "kind",
        "extra",
        "body_extra",
        "actor_extra",
        "actor_uid",
        "watch_deadline",
        "watch_grace",
        "receipt_pid",
        "receipt_bool",
        "receipt_future",
        "raw_unicode",
        "raw_duplicate",
        "raw_noncanonical",
        "native_kind",
        "native_phase",
        "native_body",
        "native_past",
        "native_future",
    ],
)
def test_unusable_ready_never_becomes_native_authorization(
    prepared, actors, calibration, monkeypatch, fault
):
    def change(value):
        received = value["body"]["received"]
        if fault == "schema":
            value["schema"] = True
        elif fault == "kind":
            value["kind"] = "PRIVATE"
        elif fault == "extra":
            value["PRIVATE"] = True
        elif fault == "body_extra":
            value["body"]["PRIVATE"] = True
        elif fault == "actor_extra":
            value["guardian"]["PRIVATE"] = True
        elif fault == "actor_uid":
            value["guardian"]["uid"] = 1
        elif fault == "watch_deadline":
            value["watchdog"]["deadline"] += 1
        elif fault == "watch_grace":
            value["watchdog"]["grace"] = True
        elif fault == "receipt_pid":
            received["pid"] += 1
        elif fault == "receipt_bool":
            received["uid"] = False
        elif fault == "receipt_future":
            received["received_at"] += 60
        elif fault == "raw_unicode":
            received["raw"] = "PRIVATE\u2603"
        elif fault == "raw_duplicate":
            received["raw"] = '{"schema":1,' + received["raw"][1:]
        elif fault == "raw_noncanonical":
            received["raw"] += " "
        else:
            native = json.loads(received["raw"])
            if fault == "native_kind":
                native["kind"] = "PRIVATE"
            elif fault == "native_phase":
                native["phase"] = "started"
            elif fault == "native_body":
                native["body"] = {"PRIVATE": True}
            elif fault == "native_past":
                native["at"] = 0
            else:
                native["at"] += 60
            received["raw"] = m.engine.dispatch.binding.encode(native).decode()

    with attached(prepared, actors, monkeypatch, change=change) as (client, requests):
        denied(lambda: capture(client, calibration), client)
        assert len(requests) <= 4 and not prepared.witness.exited()


@pytest.mark.parametrize("fault", ["boot", "time_namespace", "suspend"])
@pytest.mark.parametrize("when", ["before", "during", "after"])
def test_clock_changes_never_rearm_readiness(
    prepared, actors, calibration, monkeypatch, fault, when
):
    def change():
        if fault == "boot":
            calibration.boot = "e" * 32
        elif fault == "time_namespace":
            calibration.namespace = (4, 999)
        else:
            calibration.offset += 5

    with attached(prepared, actors, monkeypatch) as (client, requests):
        if when == "before":
            change()
        elif when == "during":
            calibration.after = lambda count: change() if count == 3 else None
        if when != "after":
            denied(lambda: capture(client, calibration), client)
            assert len(requests) == (3 if when == "before" else 5)
        else:
            result = capture(client, calibration)
            handles = tuple(result.processes.handles.values())
            try:
                change()
                denied(result.check_before_begin, client)
                assert result.failed and not result.processes.closed
                assert tuple(result.processes.handles.values()) == handles
                assert all(not result.processes.exited(role) for role in result.processes.handles)
                denied(result.check_before_begin, client)
            finally:
                result.close()


def test_cannot_substitute_a_received_dictionary_for_owned_client(prepared, calibration):
    with pytest.raises(m.UnconfirmedReady, match=m.MESSAGE):
        m.Ready({"phase": "ready"}, profile_sha256=PROFILE, original_clock=calibration.original)


def test_no_second_ready_capture_or_reconstruction(prepared, actors, calibration, monkeypatch):
    with attached(prepared, actors, monkeypatch) as (client, requests):
        result = capture(client, calibration)
        try:
            denied(lambda: capture(client, calibration), client)
            assert len(requests) == 5 and result.processes.refresh() == actors.values
        finally:
            result.close()


def test_partial_constructor_binding_cleanup_closes_owned_handles(
    prepared, actors, calibration, monkeypatch
):
    def changed(count):
        if count == 3:
            calibration.namespace = (4, 999)

    calibration.after = changed
    with attached(prepared, actors, monkeypatch) as (client, _requests):
        descriptors = len(os.listdir("/proc/self/fd"))
        denied(lambda: capture(client, calibration), client)
        assert len(os.listdir("/proc/self/fd")) < descriptors
        assert not prepared.witness.exited()
