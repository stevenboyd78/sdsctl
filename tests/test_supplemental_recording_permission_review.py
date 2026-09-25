"""Read-only observer review with real owned child/local clock-domain samples.

Only Docker cgroup labels are synthetic. The fixture constructs a statement
from the child's clock and original identities; it does not attest installed
source, transport provenance or a permission sender. No response is written.
"""

import importlib.util
import json
import os
import sys
from copy import deepcopy
from dataclasses import asdict, replace
from pathlib import Path
from threading import Thread
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_service_clock_link as clock_tests  # noqa: F401
from . import test_supplemental_recording_service_permission as peer_tests

NAME = "supplemental_recording_permission_review"
SPEC = importlib.util.spec_from_file_location(
    NAME, Path(peer_tests.m.__file__).with_name(NAME + ".py")
)
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)
peer = peer_tests.peer
pytestmark = pytest.mark.parametrize("peer", ["clock"], indirect=True)


@pytest.fixture
def supplied(peer):
    child = json.loads(peer_tests.line(peer.child))
    remote = m.clock.Window(**(child | {"namespace": tuple(child["namespace"])}))
    reverse = m.domains.Evidence(
        peer.target,
        remote.namespace,
        peer.timer.original.namespace,
        peer.domain.evidence.user,
        remote,
    )
    end = remote.after_ns / m.clock.NS + m.permission.WAIT_SECONDS
    value = dict(
        schema=1,
        kind=m.permission.CHALLENGE_KIND,
        case=json.loads(peer.template.raw)["plan"]["case"],
        template_sha256=peer.template.sha256,
        baseline_sha256=peer.baseline_sha256,
        observer=asdict(peer.target),
        target=asdict(peer.observer.identity),
        original_clock=child,
        domain_sha256=reverse.sha256,
        deadline=end,
        wait_by=end - m.permission.IO_SECONDS,
        nonce="d" * 64,
    )
    raw = m.templates.plans.base.encode(value) + b"\n"
    args = dict(
        raw=raw,
        template=peer.template,
        template_sha256=peer.template.sha256,
        baseline_sha256=peer.baseline_sha256,
        observer=peer.target,
        target=peer.observer,
        timer=peer.timer,
        domain=peer.domain,
    )
    objects = []

    def make(**changes):
        obj = m.Review(**(args | changes))
        objects.append(obj)
        return obj

    try:
        yield SimpleNamespace(peer=peer, value=value, raw=raw, remote=remote, make=make)
    finally:
        for obj in objects:
            obj.close()


def denied(action):
    with pytest.raises(m.UnconfirmedReview) as caught:
        action()
    assert str(caught.value) == m.MESSAGE and caught.value.__suppress_context__


def encoded(value):
    return m.templates.plans.base.encode(value) + b"\n"


def test_real_child_clock_is_compared_not_adopted_or_relabelled(supplied):
    s, p = supplied, supplied.peer
    obj = s.make()
    before = p.timer.original
    observed = obj.read()
    assert observed.namespace == before.namespace and p.timer.original is before
    assert obj.plan.original_clock == s.remote and obj.plan.original_clock is not before
    assert obj.challenge_sha256 == m.templates.plans.base.checksum(s.value)
    assert obj.cutoff <= s.value["wait_by"]
    assert obj.cutoff <= before.after_ns / m.clock.NS + 13
    assert not hasattr(obj, "approve") and not hasattr(obj, "send")
    assert not hasattr(obj, "prepare_service")
    obj.close()
    obj.close()
    p.borrowed()
    denied(obj.read)


@pytest.mark.parametrize("field", sorted(m.FIELDS))
def test_every_protocol_field_is_required(supplied, field):
    value = deepcopy(supplied.value)
    del value[field]
    denied(lambda: supplied.make(raw=encoded(value)))
    supplied.peer.borrowed()


@pytest.mark.parametrize("field", sorted(m.FIELDS))
def test_every_protocol_field_has_closed_types_and_original_binding(supplied, field):
    value = deepcopy(supplied.value)
    value[field] = None
    denied(lambda: supplied.make(raw=encoded(value)))
    supplied.peer.borrowed()


@pytest.mark.parametrize("field", ["template", "observer", "target", "timer", "domain"])
def test_serialized_objects_cannot_replace_original_live_owners(supplied, field):
    denied(lambda: supplied.make(**{field: {}}))
    supplied.peer.borrowed()


@pytest.mark.parametrize("field", ["template_sha256", "baseline_sha256"])
def test_independent_pins_not_derived_from_received_bytes(supplied, field):
    denied(lambda: supplied.make(**{field: "f" * 64}))
    value = deepcopy(supplied.value)
    value[field] = "f" * 64
    denied(lambda: supplied.make(raw=encoded(value)))
    supplied.peer.borrowed()


@pytest.mark.parametrize(
    "fault",
    [
        "no_newline",
        "extra_newline",
        "duplicate",
        "spaces",
        "oversize",
        "unknown",
        "schema_bool",
        "reversed_peers",
    ],
)
def test_noncanonical_ambiguous_or_unknown_frame_never_qualifies(supplied, fault):
    value, raw = deepcopy(supplied.value), supplied.raw
    if fault == "no_newline":
        raw = raw[:-1]
    elif fault == "extra_newline":
        raw += b"\n"
    elif fault == "duplicate":
        raw = raw.replace(b'"schema":1', b'"schema":1,"schema":1')
    elif fault == "spaces":
        raw = json.dumps(value, indent=2).encode() + b"\n"
    elif fault == "oversize":
        raw = b"X" * (m.permission.MAX_BYTES + 1)
    else:
        if fault == "unknown":
            value["PRIVATE"] = "extra"
        elif fault == "schema_bool":
            value["schema"] = True
        else:
            value["observer"], value["target"] = value["target"], value["observer"]
        raw = encoded(value)
    denied(lambda: supplied.make(raw=raw))
    supplied.peer.borrowed()


@pytest.mark.parametrize("field", ["boot", "namespace", "before_ns", "boottime_ns", "after_ns"])
def test_clock_fields_cannot_be_silently_coerced(supplied, field):
    value = deepcopy(supplied.value)
    value["original_clock"][field] = True
    denied(lambda: supplied.make(raw=encoded(value)))


@pytest.mark.parametrize("field", ["deadline", "wait_by", "domain_sha256", "case"])
def test_self_consistent_json_does_not_override_original_case_domain_or_deadlines(supplied, field):
    value = deepcopy(supplied.value)
    value[field] = value[field] + 1 if field in ("deadline", "wait_by") else "f" * len(value[field])
    denied(lambda: supplied.make(raw=encoded(value)))


@pytest.mark.parametrize(
    "field", ["raw", "template", "observer", "target", "timer", "domain", "link"]
)
def test_borrowed_object_replacement_poisoned(supplied, field):
    obj = supplied.make()
    original = getattr(obj, field)
    setattr(obj, field, None)
    denied(obj.read)
    setattr(obj, field, original)
    assert obj.failed
    supplied.peer.borrowed()


@pytest.mark.parametrize(
    "target, field",
    [("timer", "closed"), ("domain", "closed"), ("link", "closed"), ("link", "target")],
)
def test_final_read_retirement_cannot_return_a_valid_review(supplied, monkeypatch, target, field):
    obj = supplied.make()
    real, owner = obj.link.read, getattr(obj, target)
    previous = getattr(owner, field)

    def retire():
        observed = real()
        setattr(owner, field, True if field == "closed" else None)
        return observed

    monkeypatch.setattr(obj.link, "read", retire)
    denied(obj.read)
    setattr(owner, field, previous)
    assert obj.failed
    supplied.peer.borrowed()


def test_original_clock_cutoff_is_not_renewed_by_new_review_object(supplied, monkeypatch):
    obj = supplied.make()
    cutoff = obj.cutoff
    monkeypatch.setattr(m.time, "monotonic", lambda: cutoff)
    denied(obj.read)
    denied(supplied.make)
    assert obj.cutoff == cutoff
    supplied.peer.borrowed()


def test_closed_original_target_blocks_review_without_closing_borrowed_handles(supplied):
    p, obj = supplied.peer, supplied.make()
    p.child.stdin.close()
    p.child.wait(timeout=3)
    assert p.observer.exited()
    denied(obj.read)
    assert not p.timer.closed and not p.domain.closed
    os.fstat(p.timer.fd)


def test_wrong_thread_is_sticky(supplied):
    obj = supplied.make()
    outcomes = []

    def other():
        try:
            obj.read()
        except m.UnconfirmedReview:
            outcomes.append(True)

    thread = Thread(target=other)
    thread.start()
    thread.join(timeout=3)
    assert not thread.is_alive() and outcomes == [True] and obj.failed
    supplied.peer.borrowed()


def test_busy_lock_is_not_released_by_a_refused_review(supplied):
    obj = supplied.make()
    assert obj.lock.acquire(blocking=False)
    try:
        denied(obj.read)
        assert obj.lock.locked() and obj.failed
    finally:
        obj.lock.release()
    supplied.peer.borrowed()


def test_review_never_writes_a_socket_or_selects_service_startup(supplied, monkeypatch):
    import socket

    import supplemental_recording_service_startup as startup

    def forbidden(*_args, **_kwargs):
        pytest.fail("Read-only review attempted permission or service action")

    monkeypatch.setattr(socket.socket, "send", forbidden)
    monkeypatch.setattr(socket.socket, "sendall", forbidden)
    monkeypatch.setattr(startup.Startup, "__init__", forbidden)
    monkeypatch.setattr(m.permission.Permission, "prepare_service", forbidden)
    obj = supplied.make()
    assert type(obj.read()) is m.clock.Window
    supplied.peer.borrowed()


def test_replaced_equal_proof_is_not_original_domain(supplied):
    obj = supplied.make()
    before = obj.domain.evidence
    obj.domain.evidence = replace(before)
    denied(obj.read)
    obj.domain.evidence = before
    supplied.peer.borrowed()


@pytest.mark.parametrize("error", [ValueError("PRIVATE"), KeyboardInterrupt(), SystemExit(92)])
def test_interrupt_and_failure_leave_original_resources_to_caller(supplied, monkeypatch, error):
    obj = supplied.make()

    def broken():
        raise error

    monkeypatch.setattr(obj.link, "read", broken)
    if isinstance(error, Exception):
        denied(obj.read)
    else:
        with pytest.raises(type(error)) as caught:
            obj.read()
        assert caught.value is error
    assert obj.failed and not obj.lock.locked()
    supplied.peer.borrowed()
