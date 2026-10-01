"""Closed web-input policy with explicitly synthetic immutable-input reads.

Original-file readers and actual actor/peer checks have separate integration
tests. This module is not an installed launch, listener or readiness witness.
"""

import copy
import hashlib
import importlib.util
import json
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from .test_supplemental_recording_probe_entry import fake_cache as fake_cache
from .test_supplemental_recording_probe_entry import family as family
from .test_supplemental_recording_probe_entry import plans, probe, wire

PATH = Path(probe.__file__).with_name("supplemental_recording_web_plan.py")
SPEC = importlib.util.spec_from_file_location("supplemental_recording_web_plan", PATH)
m = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = m
SPEC.loader.exec_module(m)


@pytest.fixture
def selected(family, fake_cache, monkeypatch):
    context = dict.fromkeys(m.CONTEXT - {"ready_by"}, "a" * 64)
    inputs = plans.m.ProbeInputs(
        *fake_cache.paths[:2],
        fake_cache.paths[2].with_name("api.sock"),
        "Version 1.26.01",
        context,
        15,
    )
    value = SimpleNamespace(inputs=inputs, calls=[], hook=None)

    def read(path, **kwargs):
        value.calls.append((path, kwargs))
        return value.inputs if value.hook is None else value.hook()

    monkeypatch.setattr(m.launch, "probe_inputs", read)
    request = {
        "schema": 1,
        "kind": m.KIND,
        "context": context | {"ready_by": family.expected.deadline - 15},
        "guardian": asdict(family.expected.guardian),
        "native": asdict(family.expected.native),
        "watchdog": asdict(family.expected.watchdog)
        | {"deadline": family.expected.deadline, "grace": 3},
    }
    value.request = request
    value.path = fake_cache.paths[0].with_name("launch.json")
    return value


def prepare(value):
    return m.prepare(value.request, value.path, plan_sha256="a" * 64, source_sha256="a" * 64)


def denied(callback):
    with pytest.raises(ValueError) as caught:
        callback()
    assert str(caught.value) == m.MESSAGE and caught.value.__suppress_context__


def test_binds_closed_original_request_without_client_or_launch_preflight(
    selected, family, monkeypatch
):
    def forbidden(*args, **kwargs):
        pytest.fail("Web input reader must not launch or contact a daemon")

    monkeypatch.setattr(m.launch, "load", forbidden)
    monkeypatch.setattr(m.probe, "collect", forbidden)
    before = copy.deepcopy(selected.request)
    result = prepare(selected)
    assert result.request == wire.encode(before) and selected.request == before
    assert result.expected == family.expected
    assert result.request_sha256 == hashlib.sha256(result.request).hexdigest()
    assert result.sockets == selected.inputs.daemon_socket.parent
    assert result.maximum_recording_seconds == 15
    assert result.ready_by + 15 == family.expected.deadline
    assert len(selected.calls) == 2 and selected.calls[0] == selected.calls[1]
    result.recheck()
    assert len(selected.calls) == 4
    assert not hasattr(result, "healthy") and not hasattr(result, "recording")


@pytest.mark.parametrize("key", sorted(m.CONTEXT - {"ready_by"}))
def test_changed_original_context_fingerprint_refuses(selected, key):
    selected.request["context"][key] = "b" * 64
    denied(lambda: prepare(selected))


@pytest.mark.parametrize(
    "fault",
    [
        "schema_bool",
        "kind_probe",
        "kind_old",
        "extra",
        "context_extra",
        "missing_actor",
        "listen",
        "command",
        "ready_bool",
        "ready_expired",
        "ready_too_long",
        "ready_nan",
        "watch_renewed",
        "watch_bool",
        "grace_bool",
        "grace_changed",
        "pid_bool",
        "pid_self",
        "duplicate_pid",
        "ticks_zero",
        "extra_actor",
        "wrong_uid",
        "changed_request",
    ],
)
def test_invalid_or_expanded_input_never_becomes_web_authority(selected, fault):
    request = selected.request
    if fault == "schema_bool":
        request["schema"] = True
    elif fault == "kind_probe":
        request["kind"] = "finite-recording-cached-probe"
    elif fault == "kind_old":
        request["kind"] = "idle-handoff"
    elif fault in ("extra", "listen", "command"):
        request[fault] = "PRIVATE"
    elif fault == "context_extra":
        request["context"]["unknown"] = "a" * 64
    elif fault == "missing_actor":
        del request["native"]
    elif fault == "ready_bool":
        request["context"]["ready_by"] = True
    elif fault == "ready_expired":
        request["context"]["ready_by"] = time.monotonic() - 1
    elif fault == "ready_too_long":
        request["context"]["ready_by"] = time.monotonic() + 900
    elif fault == "ready_nan":
        request["context"]["ready_by"] = float("nan")
    elif fault == "watch_renewed":
        request["watchdog"]["deadline"] += 1
    elif fault == "watch_bool":
        request["watchdog"]["deadline"] = True
    elif fault == "grace_bool":
        request["watchdog"]["grace"] = True
    elif fault == "grace_changed":
        request["watchdog"]["grace"] = 4
    elif fault == "pid_bool":
        request["native"]["pid"] = True
    elif fault == "pid_self":
        request["native"]["pid"] = m.probe.os.getpid()
    elif fault == "duplicate_pid":
        request["native"]["pid"] = request["guardian"]["pid"]
    elif fault == "ticks_zero":
        request["native"]["start_ticks"] = 0
    elif fault == "extra_actor":
        request["native"]["unexpected"] = "PRIVATE"
    elif fault == "wrong_uid":
        request["native"]["uid"] += 1
    else:

        def mutate():
            request["kind"] = "changed"
            return selected.inputs

        selected.hook = mutate
    denied(lambda: prepare(selected))


def test_changed_input_between_original_reads_refuses(selected):
    def read():
        if len(selected.calls) == 2:
            return replace(
                selected.inputs, context=selected.inputs.context | {"generation": "b" * 64}
            )
        return selected.inputs

    selected.hook = read
    denied(lambda: prepare(selected))


@pytest.mark.parametrize("field", ["sockets", "deployment", "recordings", "firmware"])
def test_recheck_does_not_accept_changed_derived_locations(selected, field):
    original = prepare(selected)
    replacement = "changed" if field == "firmware" else getattr(original, field).with_name("other")
    changed = replace(original, **{field: replacement})
    denied(changed.recheck)


def test_recheck_does_not_adopt_changed_source_or_extend_original_time(selected, monkeypatch):
    original = prepare(selected)
    selected.inputs = replace(
        selected.inputs, context=selected.inputs.context | {"source": "b" * 64}
    )
    denied(original.recheck)
    selected.inputs = replace(
        selected.inputs, context=selected.inputs.context | {"source": "a" * 64}
    )
    monkeypatch.setattr(m.time, "monotonic", lambda: original.ready_by + 0.001)
    denied(original.recheck)


@pytest.mark.parametrize("field", ["ready_by", "maximum_recording_seconds", "expected", "request"])
def test_frozen_request_fields_cannot_drift_while_retaining_original_bytes(selected, field):
    original = prepare(selected)
    value = {
        "ready_by": original.ready_by + 1,
        "maximum_recording_seconds": 16,
        "expected": replace(original.expected, deadline=original.expected.deadline + 1),
        "request": wire.encode(json.loads(original.request) | {"kind": "changed"}),
    }[field]
    with pytest.raises(ValueError):
        replace(original, **{field: value})


def test_boolean_actor_cannot_equal_an_integer_in_immutable_request(selected):
    original = prepare(selected)
    body = json.loads(original.request)
    body["native"]["uid"] = False
    with pytest.raises(ValueError):
        replace(original, request=wire.encode(body))


def test_wrong_api_socket_name_is_not_generalized_to_arbitrary_service(selected):
    selected.inputs = replace(
        selected.inputs, daemon_socket=selected.inputs.daemon_socket.with_name("other.sock")
    )
    denied(lambda: prepare(selected))
