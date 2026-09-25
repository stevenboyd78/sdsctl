"""Authenticated local peer -> original baseline -> original service clock.

Real private sockets/processes/files/clock handles; all HAOS/Engine/cache paths
are the existing explicit synthetic fixtures. No installed source/confinement
qualification or permission to act on an App/scanner is implied.
"""

import os
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_service_permission as permission_tests
from . import test_supplemental_recording_startup_manifest as manifest_tests

m, startup = permission_tests.m, manifest_tests.m
(
    layout,
    tree,
    routing,
    projection,
    binding,
    directory,
    prepared,
    joined,
    before_handoff,
    service_case,
    manifest_case,
) = (
    manifest_tests.layout,
    manifest_tests.tree,
    manifest_tests.routing,
    manifest_tests.projection,
    manifest_tests.binding,
    manifest_tests.directory,
    manifest_tests.prepared,
    manifest_tests.joined,
    manifest_tests.before_handoff,
    manifest_tests.service_case,
    manifest_tests.manifest_case,
)
peer = permission_tests.peer


@pytest.fixture
def bound(manifest_case, peer):
    s = manifest_case
    permission = peer.make(
        template=s.startup.template,
        template_sha256=s.startup.expected,
        baseline_sha256=s.manifest_digest,
    )

    def prepare():
        return permission.prepare_service(s.startup, s.manifest_directory, s.docker)

    return SimpleNamespace(s=s, peer=peer, permission=permission, prepare=prepare)


def denied(bound):
    permission_tests.denied(bound.prepare)
    assert bound.permission.failed and bound.s.startup.failed and bound.s.startup.closed
    assert all(c.closed for c in bound.s.clocks if c is not bound.peer.timer)
    bound.peer.borrowed()


def test_no_baseline_or_host_read_without_permission(bound, monkeypatch):
    b, s = bound, bound.s
    reads = []

    def forbidden(*_args, **_kwargs):
        reads.append(True)
        pytest.fail("Unapproved preparation reached baseline/host/publication")

    monkeypatch.setattr(manifest_tests.protected, "load_baseline", forbidden)
    monkeypatch.setattr(manifest_tests.baseline_tests.launch.PreHandoffHost, "read", forbidden)
    monkeypatch.setattr(startup.publication.Publisher, "publish", forbidden)
    denied(b)
    assert not reads and not s.preflights and not s.cached_calls
    assert not list(s.root.iterdir()) and s.startup.clock is None


def test_gate_joins_original_baseline_without_accepting_or_running_service(bound, monkeypatch):
    b, s, obj = bound, bound.s, bound.permission
    initial_clocks = tuple(s.clocks)
    assert initial_clocks == (b.peer.timer,)
    observations = []
    original_load = manifest_tests.protected.load_baseline

    def loading(path, **kwargs):
        assert obj.approved and obj.used and obj.active
        assert path == s.manifest_directory and kwargs["expected_sha256"] == obj.baseline_sha256
        assert s.startup.clock is None and not s.cached_calls
        observations.append("baseline")
        return original_load(path, **kwargs)

    def host_read(sample):
        assert obj.active and s.startup.clock is None
        observations.append("host")
        return sample

    monkeypatch.setattr(manifest_tests.protected, "load_baseline", loading)
    s.after_read = host_read
    obj.wait()
    assert not s.preflights and not s.cached_calls and tuple(s.clocks) == initial_clocks
    original = b.prepare()
    assert observations == ["baseline", "host"]
    assert len(s.clocks) == 3 and s.clocks[1].closed
    assert s.startup.clock is s.clocks[2] and s.startup.clock is not b.peer.timer
    assert original.plan.original_clock is not b.peer.timer.original
    assert original.plan.original_clock.before_ns > b.peer.timer.original.after_ns
    assert s.startup.projected.host.manifest_sha256 == obj.baseline_sha256
    assert len(s.cached_calls) == 1 and not s.startup.accepted and not obj.active
    assert not (s.root / "journal").exists() and not (s.root / "inbox").exists()
    assert s.startup.poll() is None
    # Final-plan acceptance is a distinct existing action, not the permission reply.
    manifest_tests.baseline_tests.integration.startups.submit(s.startup)
    assert s.startup.poll() is original
    assert s.startup.accepted_input() is original
    assert s.manifest_path.read_bytes() == s.manifest_raw
    obj.close()
    b.peer.borrowed()
    assert not s.startup.clock.closed


def test_equal_but_recreated_template_is_not_original_declaration(bound):
    b, s = bound, bound.s
    # Independently valid bytes do not replace the original object selected for
    # this startup's custody. Construct a NEW local permission only for this test.
    other = b.peer.make(
        template=s.template, template_sha256=s.template.sha256, baseline_sha256=s.manifest_digest
    )
    assert s.template is not s.startup.template
    other.wait()
    permission_tests.denied(
        lambda: other.prepare_service(s.startup, s.manifest_directory, s.docker)
    )
    assert other.failed and s.startup.closed
    assert not s.preflights and not s.cached_calls and not list(s.root.iterdir())
    b.peer.borrowed()


def test_wrong_independent_baseline_pin_stops_before_host_read(bound):
    b, s = bound, bound.s
    obj = b.peer.make(
        template=s.startup.template, template_sha256=s.startup.expected, baseline_sha256="f" * 64
    )
    obj.wait()
    permission_tests.denied(lambda: obj.prepare_service(s.startup, s.manifest_directory, s.docker))
    assert obj.failed and s.startup.closed and s.startup.clock is None
    assert not s.preflights and not s.cached_calls and not list(s.root.iterdir())
    assert s.manifest_path.read_bytes() == s.manifest_raw
    b.peer.borrowed()


@pytest.mark.parametrize("phase", ["manifest", "host", "service_clock", "publication", "return"])
def test_permission_loss_at_each_boundary_preserves_case_without_later_phase(
    bound, monkeypatch, phase
):
    b, s, obj = bound, bound.s, bound.permission
    obj.wait()
    if phase == "manifest":
        original = manifest_tests.protected.load_baseline

        def changed(*args, **kwargs):
            value = original(*args, **kwargs)
            obj.close()
            return value

        monkeypatch.setattr(manifest_tests.protected, "load_baseline", changed)
    elif phase == "host":

        def changed(sample):
            obj.close()
            return sample

        s.after_read = changed
    elif phase == "service_clock":
        original = m.clock.ClockWitness.__init__

        def changed(clock, value):
            original(clock, value)
            if len(s.clocks) == 3:
                obj.close()

        monkeypatch.setattr(m.clock.ClockWitness, "__init__", changed)
    elif phase == "publication":
        original = startup.publication.Publisher.publish

        def changed(publisher):
            value = original(publisher)
            obj.close()
            return value

        monkeypatch.setattr(startup.publication.Publisher, "publish", changed)
    else:
        original = s.startup._prepare

        def changed(*args, **kwargs):
            value = original(*args, **kwargs)
            obj.close()
            return value

        monkeypatch.setattr(s.startup, "_prepare", changed)
    denied(b)
    assert not s.startup.accepted and obj.used
    if phase == "manifest":
        assert not s.preflights and not s.cached_calls
    if phase in ("manifest", "host"):
        assert s.startup.clock is None and len(s.clocks) == 2
    if phase in ("publication", "return"):
        assert {p.name for p in s.root.iterdir()} == {"startup-claim.json", "plan.json"}
    else:
        assert not list(s.root.iterdir())
    preserved = {p.name: p.read_bytes() for p in s.root.iterdir()}
    clocks, reads = len(s.clocks), len(s.cached_calls)
    denied(b)
    assert len(s.clocks) == clocks and len(s.cached_calls) == reads
    assert preserved == {p.name: p.read_bytes() for p in s.root.iterdir()}
    assert s.manifest_path.read_bytes() == s.manifest_raw


@pytest.mark.parametrize("error", [OSError("PRIVATE"), KeyboardInterrupt(), SystemExit(93)])
def test_host_read_failure_never_creates_continuing_clock_or_retries(bound, error):
    b, s = bound, bound.s
    b.permission.wait()

    def failed():
        raise error

    s.after_cached = failed
    if isinstance(error, Exception):
        denied(b)
    else:
        with pytest.raises(type(error)) as caught:
            b.prepare()
        assert caught.value is error
    assert b.permission.failed and s.startup.closed and s.startup.clock is None
    assert not list(s.root.iterdir()) and len(s.cached_calls) == 1
    b.peer.borrowed()
    os.fstat(b.peer.timer.fd)
