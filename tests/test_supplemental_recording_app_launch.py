"""Real file write/read and pidfd; synthetic original Startup/Engine/idle.

No installed provenance, native action, scanner or recording is claimed. The
real accepted Startup publication and native plan parser have separate suites.
"""

import fcntl
import hashlib
import importlib.util
import json
import os
import socket
import sys
import time
from dataclasses import replace
from pathlib import Path
from threading import Lock, Thread
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_app_native_qualification as native_tests
from . import test_supplemental_recording_launch_plan as plan_tests  # noqa: F401

candidate, app, native = native_tests.candidate, native_tests.app, native_tests.native
layout, image_umask, supervised = (
    native_tests.layout,
    native_tests.image_umask,
    native_tests.supervised,
)
image, configured = native_tests.image, native_tests.configured
pytestmark = pytest.mark.parametrize("candidate", ["app_native"], indirect=True)

NAME = "supplemental_recording_app_launch"
SPEC = importlib.util.spec_from_file_location(
    NAME, Path(native_tests.m.__file__).with_name(NAME + ".py")
)
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)


@pytest.fixture
def launch_case(native, monkeypatch):
    yield from setup_launch_case(native, monkeypatch)


def setup_launch_case(native, monkeypatch, *, owner=None):
    """Use synthetic acceptance by default; an actual retained owner is optional."""
    s = native
    s.original = s.make_native()
    s.owner_reads = 0
    if owner is None:
        s.startup = object.__new__(m.publication.startup.Startup)
        s.startup.original = SimpleNamespace(recheck=lambda: s.plan)
        s.startup.clock = m.plans.clock.ClockWitness(s.plan.original_clock)
        s.startup.projected = s.projected
        s.startup.accepted = s.startup.app_idle_publication_used = True
        s.startup._service_inputs = object()
        s.startup.closed = False
        s.startup.service_used = s.startup._service_active = True
        s.startup.lock = Lock()
        s.startup.lock.acquire()
        s.startup._service_invalidate = lambda: None

        def original_guard(owner):
            assert owner is s.startup
            s.owner_reads += 1
            m.require(not owner.closed)

        monkeypatch.setattr(m.publication.startup.Startup, "_input", original_guard)
        monkeypatch.setattr(m.publication.startup.Startup, "_binding", original_guard)
    else:
        s.startup = owner
    s.spec = m.native.construction.Specification(
        "192.0.2.25",
        50536,
        554,
        "0.0.0.0",
        50000,
        s.plan.native_root / "sockets",
        s.plan.native_root / "receipts",
        s.plan.firmware,
        64,
        60,
        60,
    )
    s.publish = lambda: m.publish_launch(
        s.startup,
        s.original,
        specification=s.spec,
        profile_sha256="a" * 64,
    )
    original_clock = s.startup.clock
    try:
        yield s
    finally:
        if owner is None:
            original_clock.close()
            s.startup.lock.release()


def denied(callback):
    native_tests.candidates.launch.denied(callback)


def test_publication_prepares_original_empty_guardian_directory(launch_case):
    s = launch_case
    q = s.publish()
    directory = s.case_root / "launch/guardian"
    assert directory.is_dir()
    assert directory.stat().st_mode & 0o7777 == 0o700
    assert not tuple(directory.iterdir())
    assert q.launch_inputs.guardian_identity == m.files.identity(directory.stat())
    assert q() is None


@pytest.mark.parametrize("fault", ["mode", "replaced", "symlink", "early_claim", "extra"])
def test_prepared_guardian_directory_cannot_be_replaced_or_consumed_early(launch_case, fault):
    s = launch_case
    q = s.publish()
    directory = s.case_root / "launch/guardian"
    if fault == "mode":
        directory.chmod(0o755)
    elif fault in ("replaced", "symlink"):
        preserved = directory.with_name("preserved-guardian")
        directory.rename(preserved)
        if fault == "replaced":
            directory.mkdir(mode=0o700)
        else:
            directory.symlink_to(preserved, target_is_directory=True)
    else:
        (directory / ("launch-claimed.json" if fault == "early_claim" else "extra")).write_bytes(
            b"PRIVATE"
        )
    descriptors = len(os.listdir("/proc/self/fd"))
    denied(q)
    assert q.failed and q.elapsed_seconds is None
    assert len(os.listdir("/proc/self/fd")) == descriptors
    denied(s.publish)


def test_guardian_creation_failure_preserves_consumed_publication(launch_case, monkeypatch):
    s, mkdir = launch_case, os.mkdir

    def uncertain(path, *args, **kwargs):
        mkdir(path, *args, **kwargs)
        if path == "guardian":
            raise OSError("PRIVATE lost directory acknowledgment")

    monkeypatch.setattr(os, "mkdir", uncertain)
    descriptors = len(os.listdir("/proc/self/fd"))
    denied(s.publish)
    assert s.original.failed and s.original.native_launch_used
    assert (s.case_root / "launch/guardian").is_dir()
    assert not (s.case_root / "launch/launch.json").exists()
    assert len(os.listdir("/proc/self/fd")) == descriptors
    denied(s.publish)


def test_one_native_input_write_preserves_all_original_pins_without_actions(
    launch_case, monkeypatch
):
    s = launch_case

    def forbidden(*_, **__):
        pytest.fail("Input handoff must not construct runtime, resolve DNS or create sockets")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(m.native.construction, "construct", forbidden)
    original = s.plan.raw, s.plan.lease, s.published, s.startup.clock
    fds = len(os.listdir("/proc/self/fd"))
    q = s.publish()
    assert type(q) is m.NativeLaunchQualification and q.elapsed_seconds < 2
    assert len(os.listdir("/proc/self/fd")) == fds
    assert original == (s.plan.raw, s.plan.lease, s.published, s.startup.clock)
    assert s.original.native_launch_publication is q.launch_inputs
    assert q.launch_inputs.sha256 == hashlib.sha256(q.launch_inputs.raw).hexdigest()
    document = json.loads(q.launch_inputs.raw)
    assert document["generation"] == s.original.generation
    assert document["host_plan_sha256"] == s.plan.sha256
    assert document["projection_sha256"] == s.plan.projection_sha256
    assert document["baseline"]["sha256"] == s.plan.native_baseline_sha256
    assert document["profile"]["deployment"].startswith("/data/")
    assert q() is None and q.during(lambda: 123) == 123
    assert s.owner_reads > 0 and not s.witness.exited()
    assert not any(hasattr(q, name) for name in ("start", "begin", "restore"))
    # The one-attempt flag refuses a duplicate without invalidating the
    # successful successor; it never replaces the already written bytes.
    denied(s.publish)
    assert q() is None
    for name in ("sockets", "receipts"):
        assert not list((s.case_root / name).iterdir())


@pytest.mark.parametrize(
    "fault",
    ["firmware", "sockets", "receipts", "endpoint", "duration", "profile", "owner", "wrong_thread"],
)
def test_invalid_native_binding_consumes_attempt_without_writing(launch_case, fault):
    s = launch_case
    changes = dict(
        firmware={"firmware": "Version 1.23.07"},
        sockets={"sockets": Path("/data/other-sockets")},
        receipts={"receipts": Path("/data/other-receipts")},
        endpoint={"host": "192.0.2.26"},
        duration={"read_window_seconds": 170},
    )
    if fault in changes:
        # Deliberately bypass dataclass construction to exercise the handoff's
        # own revalidation of even forcibly changed input objects.
        for name, value in changes[fault].items():
            object.__setattr__(s.spec, name, value)
    if fault == "profile":
        s.publish = lambda: m.publish_launch(
            s.startup, s.original, specification=s.spec, profile_sha256="PRIVATE"
        )
    elif fault == "owner":
        s.startup.original = SimpleNamespace(recheck=lambda: object())
    if fault == "wrong_thread":
        errors = []

        def run():
            try:
                s.publish()
            except Exception as error:
                errors.append(error)

        worker = Thread(target=run)
        worker.start()
        worker.join(timeout=5)
        assert not worker.is_alive() and len(errors) == 1
        assert type(errors[0]) is m.qualification.launch.UnconfirmedHostLaunch
    else:
        denied(s.publish)
    assert not list((s.case_root / "launch").iterdir())
    assert s.original.native_launch_used and s.original.failed
    denied(s.publish)
    os.fstat(s.witness.fd)


@pytest.mark.parametrize(
    "fault", ["partial", "fsync", "close", "substitute", "source", "deadline", "owner_closed"]
)
def test_uncertain_write_preserved_and_original_reader_consumed(launch_case, monkeypatch, fault):
    s, real_write, real_fsync = launch_case, os.write, os.fsync
    real_close = m.publication._close
    triggered = False

    def writing(fd, raw):
        nonlocal triggered
        triggered = True
        count = real_write(fd, raw[:10] if fault == "partial" else raw)
        if fault == "source":
            s.container["Config"]["Cmd"][-1] = "0" * 32
        elif fault == "deadline":
            real_time = time.monotonic
            monkeypatch.setattr(m.time, "monotonic", lambda: real_time() + 3)
        elif fault == "owner_closed":
            s.startup.closed = True
        return count

    def syncing(fd):
        if triggered and fault == "fsync":
            raise OSError("PRIVATE")
        real_fsync(fd)
        if triggered and fault == "substitute":
            p = s.case_root / "launch/launch.json"
            if p.exists():
                p.rename(p.with_name("preserved.json"))
                p.write_bytes(b"PRIVATE")

    def closing(fds):
        real_close(fds)
        if triggered and fault == "close":
            raise OSError("PRIVATE lost close acknowledgment")

    monkeypatch.setattr(os, "write", writing)
    monkeypatch.setattr(os, "fsync", syncing)
    monkeypatch.setattr(m.publication, "_close", closing)
    denied(s.publish)
    assert triggered and s.original.failed
    assert (s.case_root / "launch/launch.json").exists()
    assert not s.original.lock.locked()
    denied(s.publish)
    os.fstat(s.witness.fd)


@pytest.mark.parametrize(
    "target",
    ["launch/launch.json", "baseline/baseline.json", "idle/lease.json", "app-start/consumed.json"],
)
def test_post_publication_replacement_is_never_a_new_baseline(launch_case, target):
    s = launch_case
    q = s.publish()
    p = s.case_root / target
    replacement = p.with_name("replacement")
    replacement.write_bytes(p.read_bytes())
    replacement.chmod(0o600)
    replacement.replace(p)
    native_tests.candidates.denied(q)


@pytest.mark.parametrize(
    "fault", ["receipt", "original", "clock", "projection", "outputs", "stale_reader"]
)
def test_successor_requires_original_custody_and_still_empty_outputs(launch_case, fault):
    s = launch_case
    q = s.publish()
    if fault == "receipt":
        s.original.native_launch_publication = replace(q.launch_inputs)
    elif fault == "original":
        s.original.published = replace(s.published)
    elif fault == "clock":
        s.startup.clock = object()
    elif fault == "projection":
        s.startup.projected = replace(s.projected)
    elif fault == "outputs":
        (s.case_root / "receipts/not-yet-authorized").write_bytes(b"PRIVATE")
    else:
        # The old empty-tree reader is deliberately not reusable after writing.
        denied(s.original)
    native_tests.candidates.denied(q)


@pytest.mark.parametrize(
    "fault", ["existing", "symlink", "unsafe_directory", "busy", "unqualified_source"]
)
def test_existing_or_unqualified_inputs_are_preserved_before_write(launch_case, fault):
    s = launch_case
    path = s.case_root / "launch/launch.json"
    fd = None
    if fault == "existing":
        path.write_bytes(b"PRIVATE existing input")
    elif fault == "symlink":
        path.symlink_to(s.case_root / "idle/lease.json")
    elif fault == "unsafe_directory":
        path.parent.chmod(0o755)
    elif fault == "busy":
        fd = os.open(path.parent, m.files.DIRECTORY)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    else:
        s.container["Config"]["Cmd"][-1] = "0" * 32
    before = path.read_bytes() if path.exists() else None
    try:
        denied(s.publish)
        assert s.original.failed and s.original.native_launch_used
        assert (path.read_bytes() if path.exists() else None) == before
        if fault == "symlink":
            assert path.is_symlink()
        os.fstat(s.witness.fd)
    finally:
        if fd is not None:
            os.close(fd)


def test_callback_cannot_change_new_launch_input_and_return_a_verified_value(launch_case):
    s = launch_case
    q = s.publish()

    def changed():
        (s.case_root / "launch/launch.json").write_bytes(b"PRIVATE substituted input")
        return "must not escape"

    denied(lambda: q.during(changed))
    assert q.failed and q.elapsed_seconds is None
    denied(q)


@pytest.mark.parametrize(
    "fault", ["not_assembled", "retired", "invalidate_missing", "clock_replaced"]
)
def test_publisher_requires_continuing_original_service_custody(launch_case, fault):
    s = launch_case
    if fault == "not_assembled":
        s.startup.service_used = False
    elif fault == "retired":
        s.startup._service_active = False
    elif fault == "invalidate_missing":
        s.startup._service_invalidate = None
    else:
        s.startup.clock = object()
    denied(s.publish)
    assert not tuple((s.case_root / "launch").iterdir())
    assert s.original.failed
