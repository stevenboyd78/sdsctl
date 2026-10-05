"""Actual local sockets/files/pidfd; explicitly synthetic native Ready/Startup.

No listening/connecting socket, scanner, native process or begin is created.
Actual Ready's authenticated transport/process semantics have separate tests.
"""

import importlib.util
import json
import os
import socket
import sys
import time
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_app_launch as launches

candidate, app, native, launch_case = (
    launches.candidate,
    launches.app,
    launches.native,
    launches.launch_case,
)
layout, image_umask, supervised = launches.layout, launches.image_umask, launches.supervised
image, configured = launches.image, launches.configured
pytestmark = pytest.mark.parametrize("candidate", ["app_native"], indirect=True)

NAME = "qualify_supplemental_recording_app_ready"
SPEC = importlib.util.spec_from_file_location(
    NAME, Path(launches.m.__file__).with_name(NAME + ".py")
)
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)


def synthetic_actors(ready, init):
    ready.processes = object.__new__(m.launch.engine.namespace.Witness)
    ready.processes.closed = False
    domains = tuple((1, index + 1) for index in range(len(m.launch.engine.namespace.NAMESPACES)))
    ready.processes.actors = tuple(
        m.launch.engine.namespace.Actor(
            init.pid if index == 0 else 1_000_000 + index,
            index + 1,
            0 if index == 0 else init.pid,
            init.start_ticks + index,
            init.container_id,
            domains,
        )
        for index in range(4)
    )


def synthetic_guardian_claim(s, ready):
    """Explicit fixture transport/actor boundary; actual source, private inode."""
    static = m.launch.plans.host.candidate_static
    evidence = static.source.Layout(
        s.root / m.launch.plans.fixed.PACKAGE, s.root / static.NATIVE
    ).observe()
    actor = ready.processes.actors[1]
    raw = m.inputs.base.encode(
        dict(
            schema=1,
            kind="finite-recording-guardian-claim",
            context=json.loads(ready.context_raw),
            source=asdict(evidence),
            guardian_pid=actor.local_pid,
            guardian_start_ticks=actor.start_ticks,
            hard_deadline=ready.watch_deadline,
            grace_seconds=m.launch.received.GRACE_SECONDS,
        )
    )
    target = s.case_root / "launch/guardian/launch-claimed.json"
    with target.open("xb") as stream:
        stream.write(raw)
    target.chmod(0o600)


@pytest.fixture
def ready_case(launch_case, monkeypatch):
    s = launch_case
    s.prelaunch = s.publish()
    # A second passive reader must not provide another ready-acquisition slot.
    s.prelaunch_copy = m.inputs.NativeLaunchQualification(
        s.startup,
        s.original,
        s.prelaunch.launch_inputs,
    )
    assert s.prelaunch_copy() is None
    p = s.plan
    s.expected = m.launch.engine.dispatch.Pins(
        m.launch.binding.Binding(s.projected, p.candidate_runtime.source, p.sha256, p.boot),
        m.launch.execution.Command(
            str(p.native_root / "launch/launch.json"),
            s.prelaunch.launch_inputs.sha256,
            p.candidate_runtime.source,
            p.lease["ready_by"],
        ),
        s.prelaunch.generation,
        s.prelaunch.init,
    )
    s.ready = object.__new__(m.launch.received.Ready)
    s.ready.client = SimpleNamespace(claim=SimpleNamespace(pins=s.expected), begun=False)
    synthetic_actors(s.ready, s.prelaunch.init)
    s.ready.zero_domain = object()
    s.ready.clock = p.original_clock
    s.ready.ready_by = p.lease["ready_by"]
    s.ready.watch_deadline = s.ready.ready_by + p.candidate.contract.maximum_recording_seconds
    s.ready.received_at = time.monotonic()
    s.ready.context_raw = m.inputs.base.encode(m.launch.received._context(s.expected, "a" * 64))
    s.ready.ready_raw = b"explicitly synthetic Ready, not native evidence"
    s.ready.closed = s.ready.failed = False
    synthetic_guardian_claim(s, s.ready)
    s.ready_checks = 0

    def checking(ready):
        assert ready is s.ready
        s.ready_checks += 1
        m.require(not ready.closed and not ready.failed and not ready.client.begun)
        m.require(time.monotonic() < ready.ready_by)

    monkeypatch.setattr(m.launch.received.Ready, "check_before_begin", checking)
    sockets = []
    directory = os.open(s.case_root / "sockets", m.files.DIRECTORY)

    def bind(name):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sockets.append(sock)
        # Real local socket inode, avoiding pytest's long path and AF_UNIX's
        # pathname limit. No listener or connect; proc fd only aliases this dir.
        sock.bind(f"/proc/self/fd/{directory}/{name}")
        os.chmod(name, 0o600, dir_fd=directory)

    s.bind = bind
    try:
        for name in m.SOCKETS:
            bind(name)
        s.make_ready = lambda: m.NativeReadyQualification(s.prelaunch, s.ready)
        yield s
    finally:
        for sock in sockets:
            sock.close()
        os.close(directory)


def test_explicit_ready_allows_only_native_sockets_before_begin(ready_case):
    s = ready_case
    before = s.plan.raw, s.plan.lease, s.published
    q = s.make_ready()
    assert s.ready_checks == 1
    fds = len(os.listdir("/proc/self/fd"))
    assert q() is None
    assert s.ready_checks == 3
    assert q.during(lambda: 42) == 42
    assert s.ready_checks == 5  # Fresh readiness before/after, not per file guard.
    assert len(os.listdir("/proc/self/fd")) == fds
    assert q.elapsed_seconds < 2 and s.ready_checks > 0
    assert before == (s.plan.raw, s.plan.lease, s.published)
    assert not any(hasattr(q, name) for name in ("begin", "start", "restore"))
    assert type(q) is not m.launch.CandidateQualification
    assert type(q) is not m.inputs.NativeLaunchQualification
    assert not tuple((s.case_root / "receipts").iterdir())
    assert not s.witness.exited()


@pytest.mark.parametrize(
    "acquired,fault",
    [
        (acquired, fault)
        for acquired in (False, True)
        for fault in (
            "missing",
            "mode",
            "extra",
            "replacement",
            "symlink",
            "directory",
            "duplicate_json",
            "context",
            "pid",
            "ticks",
            "deadline",
            "grace",
            "source",
            "count",
            "extra_field",
        )
        if acquired or fault != "replacement"
    ],
)
def test_guardian_claim_requires_original_ready_and_immutable_custody(ready_case, acquired, fault):
    s = ready_case
    q = s.make_ready()
    if acquired:
        assert q() is None
    target = s.case_root / "launch/guardian/launch-claimed.json"
    if fault == "missing":
        target.unlink()
    elif fault == "mode":
        target.chmod(0o644)
    elif fault == "extra":
        (target.parent / "extra").write_bytes(b"PRIVATE")
    elif fault == "directory":
        target.parent.chmod(0o755)
    elif fault in ("replacement", "symlink"):
        original = target.read_bytes()
        preserved = s.case_root.parent / "preserved-claim"
        target.rename(preserved)
        if fault == "replacement":
            target.write_bytes(original)
            target.chmod(0o600)
        else:
            target.symlink_to(preserved)
    elif fault == "duplicate_json":
        target.write_bytes(target.read_bytes().replace(b'"schema":1', b'"schema":1,"schema":1'))
    else:
        value = json.loads(target.read_bytes())
        if fault == "context":
            value["context"]["generation"] = "0" * 64
        elif fault == "source":
            value["source"]["sha256"] = "0" * 64
        elif fault == "count":
            value["source"]["file_count"] = True
        elif fault == "extra_field":
            value["extra"] = 1
        else:
            field = {
                "pid": "guardian_pid",
                "ticks": "guardian_start_ticks",
                "deadline": "hard_deadline",
                "grace": "grace_seconds",
            }[fault]
            value[field] += 1
        target.write_bytes(m.inputs.base.encode(value))
    descriptors = len(os.listdir("/proc/self/fd"))
    launches.denied(q)
    assert q.failed and q.elapsed_seconds is None
    assert len(os.listdir("/proc/self/fd")) == descriptors


def test_claim_change_during_original_ready_collection_is_refused(ready_case):
    s = ready_case
    q = s.make_ready()

    def change():
        target = s.case_root / "launch/guardian/launch-claimed.json"
        value = json.loads(target.read_bytes())
        value["source"]["total_bytes"] += 1
        target.write_bytes(m.inputs.base.encode(value))
        return "not verified"

    launches.denied(lambda: q.during(change))
    assert q.failed and q.elapsed_seconds is None


@pytest.mark.parametrize(
    "fault",
    [
        "absent",
        "extra",
        "regular",
        "symlink",
        "mode",
        "replaced",
        "receipt",
        "directory",
        "before_begin_reader",
    ],
)
def test_unknown_or_replaced_output_is_not_adopted(ready_case, fault):
    s = ready_case
    q = s.make_ready()
    q()
    target = s.case_root / "sockets/api.sock"
    if fault in ("absent", "regular", "symlink", "replaced"):
        target.unlink()
    if fault == "regular":
        target.write_bytes(b"PRIVATE")
    elif fault == "symlink":
        target.symlink_to(s.case_root / "sockets/events.sock")
    elif fault == "replaced":
        s.bind("api.sock")
    elif fault == "mode":
        target.chmod(0o666)
    elif fault == "extra":
        s.bind("not-declared.sock")
    elif fault == "receipt":
        (s.case_root / "receipts/prepared.json").write_bytes(b"PRIVATE")
    elif fault == "directory":
        target.parent.chmod(0o755)
    elif fault == "before_begin_reader":
        launches.denied(s.prelaunch)
    launches.native_tests.candidates.denied(q)


@pytest.mark.parametrize(
    "fault",
    [
        "closed",
        "failed",
        "begun",
        "replacement",
        "context",
        "processes",
        "launch",
        "clock",
        "pins",
        "late",
    ],
)
def test_actual_original_readiness_cannot_be_replaced_or_renewed(ready_case, monkeypatch, fault):
    s = ready_case
    q = s.make_ready()
    q()
    if fault in ("closed", "failed"):
        setattr(s.ready, fault, True)
    elif fault == "begun":
        s.ready.client.begun = True
    elif fault == "replacement":
        q.ready = object()
    elif fault == "context":
        s.ready.context_raw = b"PRIVATE"
    elif fault == "processes":
        s.ready.processes = object()
    elif fault == "launch":
        (s.case_root / "launch/launch.json").write_bytes(b"PRIVATE")
    elif fault == "clock":
        s.ready.clock = object()
    elif fault == "pins":
        s.ready.client.claim.pins = object()
    else:
        original = time.monotonic
        monkeypatch.setattr(time, "monotonic", lambda: original() + 125)
    launches.native_tests.candidates.denied(q)


def test_mid_read_socket_replacement_does_not_return_verified_observation(ready_case):
    s = ready_case
    q = s.make_ready()

    def change():
        (s.case_root / "sockets/api.sock").unlink()
        s.bind("api.sock")
        return "must not escape"

    launches.denied(lambda: q.during(change))
    assert q.failed and q.elapsed_seconds is None


@pytest.mark.parametrize("fault", ["begin", "failed", "init_exit"])
def test_mid_read_lifecycle_change_refuses_the_result(ready_case, fault):
    s = ready_case
    q = s.make_ready()

    def changed():
        if fault == "begin":
            s.ready.client.begun = True
        elif fault == "failed":
            s.ready.failed = True
        else:
            s.child.stdin.close()  # This fixture's harmless stdin-waiting child only.
            s.child.wait(timeout=1)
        return "not independently verified"

    launches.denied(lambda: q.during(changed))
    assert q.failed and q.elapsed_seconds is None
    launches.denied(q)


def test_ready_policy_does_not_enter_any_old_launch_or_post_begin_gate(ready_case):
    s = ready_case
    q = s.make_ready()
    q()
    launches.denied(lambda: m.q.AppRetainedQualification(q, object()))
    launches.denied(
        lambda: m.inputs.publish_launch(
            s.startup,
            q,
            specification=s.spec,
            profile_sha256="a" * 64,
        )
    )
    # An inadmissible unrelated action request does not rewrite the phase.
    assert q() is None


@pytest.mark.parametrize("copied", [False, True])
def test_original_publication_allows_only_one_socket_identity_acquisition(ready_case, copied):
    s = ready_case
    q = s.make_ready()
    q()
    other = s.prelaunch_copy if copied else s.prelaunch
    launches.denied(lambda: m.NativeReadyQualification(other, s.ready))
    assert s.original.native_ready_owner is q and s.original.native_ready_used
    assert q() is None  # Rejected duplicate cannot steal or poison its owner.


def test_failed_initial_readiness_cannot_be_repaired_into_a_new_reader(ready_case):
    s = ready_case
    s.ready.closed = True
    launches.denied(s.make_ready)
    assert s.original.native_ready_used and s.original.failed
    s.ready.closed = False  # A false local repair is not a fresh native readiness.
    launches.denied(s.make_ready)
    os.fstat(s.witness.fd)


def test_ready_owner_replacement_is_refused(ready_case):
    s = ready_case
    q = s.make_ready()
    q()
    s.original.native_ready_owner = object()
    launches.native_tests.candidates.denied(q)
