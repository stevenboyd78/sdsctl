"""Pinned persisted baseline to startup; synthetic host metadata/cache only.

The manifest contains the existing fixture's original inventory with an explicit
host-path alias. No real /mnt/data path, App, scanner or recording is touched.
"""

import hashlib
import os

import pytest

from . import test_supplemental_recording_startup_baseline as baseline_tests

m = baseline_tests.m
protected = m.plans.projection.recording
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
) = (
    baseline_tests.layout,
    baseline_tests.tree,
    baseline_tests.routing,
    baseline_tests.projection,
    baseline_tests.binding,
    baseline_tests.directory,
    baseline_tests.prepared,
    baseline_tests.joined,
    baseline_tests.before_handoff,
    baseline_tests.service_case,
)


@pytest.fixture
def manifest_case(service_case, tmp_path):
    s = service_case
    s.manifest_directory = tmp_path / "original-baseline"
    s.manifest_directory.mkdir(mode=0o700)
    original = s.projected.host
    s.manifest_raw = protected.manifest_bytes(
        original.baseline,
        original.writer,
        original.contract.audio_endpoint_sha256,
        maximum_recording_seconds=original.contract.maximum_recording_seconds,
    )
    s.manifest_digest = original.manifest_sha256
    s.manifest_path = s.manifest_directory / "baseline.json"
    s.manifest_path.write_bytes(s.manifest_raw)
    s.manifest_path.chmod(0o600)
    return s


def prepare(s, *, digest=None):
    return s.startup.prepare_service_from_baseline(
        s.manifest_directory, digest or s.manifest_digest, s.docker
    )


def denied(s, *, digest=None):
    with pytest.raises(m.UnconfirmedStartup) as error:
        prepare(s, digest=digest)
    assert str(error.value) == m.MESSAGE and error.value.__suppress_context__
    assert s.startup.failed and s.startup.closed
    assert all(clock.closed for clock in s.clocks)


def test_original_manifest_projects_once_without_recapture_or_resealing(manifest_case, monkeypatch):
    s = manifest_case
    reads, comparisons = [], []
    load = protected.load_baseline
    capture = protected.evidence.capture_baseline

    def forbidden(*_args, **_kwargs):
        pytest.fail("Startup must never recapture or rewrite the original baseline")

    def loading(path, **kwargs):
        reads.append((path, kwargs))
        assert not list(s.root.iterdir()) and s.startup.clock is None
        return load(path, **kwargs)

    def current_comparison(*args, **kwargs):
        # Loading/projection must not capture a new original inventory. The
        # later host reader MUST still compare actual current temporary files.
        assert len(reads) == len(s.preflights) == 1 and s.startup.clock is None
        comparisons.append(True)
        return capture(*args, **kwargs)

    monkeypatch.setattr(protected, "load_baseline", loading)
    monkeypatch.setattr(protected, "save_baseline", forbidden)
    monkeypatch.setattr(protected.evidence, "capture_baseline", current_comparison)
    original = prepare(s)
    owner = s.startup
    assert len(reads) == len(s.preflights) == len(s.cached_calls) == 1
    assert comparisons
    assert reads == [
        (
            s.manifest_directory,
            dict(
                expected_contract=original.plan.candidate.contract,
                expected_sha256=s.manifest_digest,
            ),
        )
    ]
    assert owner.projected is not s.projected and owner.projected == s.projected
    assert owner.projected.host.manifest_sha256 == s.manifest_digest
    assert owner.projected.native.baseline.files == s.projected.host.baseline.files
    assert len(s.clocks) == 2 and s.clocks[0].closed and owner.clock is s.clocks[1]
    assert s.manifest_path.read_bytes() == s.manifest_raw
    baseline_tests.integration.startups.submit(owner)
    assert owner.poll() is original
    with owner.idle_service(s.docker) as service:
        assert service.projected is owner.projected and not service.used
        assert service.journal.machine.baseline == owner.baseline
    assert not owner.clock.closed and len(reads) == 1


def test_old_manifest_never_adopts_changed_current_recording_files(manifest_case):
    s = manifest_case
    changed = s.recording_root / "unexpected-current-file"
    changed.write_bytes(b"PRIVATE changed recordings")
    denied(s)
    assert len(s.preflights) == 1 and not list(s.root.iterdir())
    assert s.startup.clock is None
    assert s.manifest_path.read_bytes() == s.manifest_raw
    assert changed.read_bytes() == b"PRIVATE changed recordings"


@pytest.mark.parametrize(
    "fault",
    ["missing", "bytes", "digest", "symlink", "hardlink", "mode", "directory_mode", "extra"],
)
def test_bad_manifest_never_reaches_host_read_or_publication(manifest_case, fault):
    s = manifest_case
    digest = s.manifest_digest
    if fault == "missing":
        s.manifest_path.rename(s.manifest_directory / "preserved.json")
    elif fault == "bytes":
        s.manifest_path.write_bytes(b"PRIVATE invalid manifest")
    elif fault == "digest":
        digest = "f" * 64
    elif fault == "symlink":
        target = s.manifest_directory.parent / "preserved-baseline.json"
        s.manifest_path.rename(target)
        s.manifest_path.symlink_to(target)
    elif fault == "hardlink":
        os.link(s.manifest_path, s.manifest_directory.parent / "preserved-baseline.json")
    elif fault == "mode":
        s.manifest_path.chmod(0o644)
    elif fault == "directory_mode":
        s.manifest_directory.chmod(0o755)
    else:
        (s.manifest_directory / "unexpected").write_bytes(b"PRIVATE residue")
    denied(s, digest=digest)
    assert not s.preflights and not s.cached_calls and not list(s.root.iterdir())
    assert s.startup.clock is None
    denied(s, digest=digest)  # One attempt; no second clock or read.
    assert len(s.clocks) == 1


@pytest.mark.parametrize("field", ["writer", "root", "case", "endpoint", "files"])
def test_even_self_consistent_new_manifest_cannot_replace_template_inventory(manifest_case, field):
    from dataclasses import replace

    s = manifest_case
    original = s.projected.host
    baseline, writer = original.baseline, original.writer
    endpoint = original.contract.audio_endpoint_sha256
    if field == "writer":
        writer = replace(writer, uid=writer.uid + 1)
    elif field == "root":
        baseline = replace(baseline, root=baseline.root.parent / "other")
    elif field == "case":
        baseline = replace(baseline, case="bb12345612344abc8abc123456789abc")
    elif field == "endpoint":
        endpoint = "e" * 64
    else:
        baseline = replace(baseline, files=())
    raw = protected.manifest_bytes(
        baseline,
        writer,
        endpoint,
        maximum_recording_seconds=original.contract.maximum_recording_seconds,
    )
    protected._decode(raw)  # Valid format, but not this independently pinned case.
    s.manifest_path.write_bytes(raw)
    denied(s, digest=hashlib.sha256(raw).hexdigest())
    assert s.manifest_path.read_bytes() == raw
    assert not s.preflights and not s.cached_calls and not list(s.root.iterdir())


def test_projection_pin_is_checked_before_cached_host_command(manifest_case, monkeypatch):
    s = manifest_case
    project = m.plans.projection.project

    def changed(*args):
        result = project(*args)
        object.__setattr__(result.host, "manifest_sha256", "f" * 64)
        return result

    monkeypatch.setattr(m.plans.projection, "project", changed)
    denied(s)
    assert not s.preflights and not s.cached_calls and not list(s.root.iterdir())


@pytest.mark.parametrize("problem", [OSError, KeyboardInterrupt, SystemExit])
def test_lost_manifest_read_closes_preflight_without_capturing_service_clock(
    manifest_case, monkeypatch, problem
):
    s = manifest_case

    def lost(*_args, **_kwargs):
        raise problem("PRIVATE manifest acknowledgment")

    monkeypatch.setattr(protected, "load_baseline", lost)
    if issubclass(problem, Exception):
        denied(s)
    else:
        with pytest.raises(problem):
            prepare(s)
    assert s.startup.closed and s.startup.clock is None
    assert len(s.clocks) == 1 and s.clocks[0].closed
    assert not s.preflights and not s.cached_calls and not list(s.root.iterdir())


def test_acceptance_does_not_reload_or_adopt_a_replaced_manifest(manifest_case, monkeypatch):
    s = manifest_case
    original = prepare(s)
    projected, baseline = s.startup.projected, s.startup.baseline
    s.manifest_path.write_bytes(b"PRIVATE later replacement")

    def forbidden(*_args, **_kwargs):
        pytest.fail("Acceptance must keep the original baseline, not reopen it")

    monkeypatch.setattr(protected, "load_baseline", forbidden)
    baseline_tests.integration.startups.submit(s.startup)
    assert s.startup.poll() is original and s.startup.accepted_input() is original
    assert s.startup.projected is projected and s.startup.baseline is baseline
    assert projected.host.manifest_sha256 == s.manifest_digest
    assert s.manifest_path.read_bytes() == b"PRIVATE later replacement"


@pytest.mark.parametrize("first", ["probe", "in_memory", "manifest"])
def test_manifest_path_shares_the_same_consumed_prepare_attempt(manifest_case, monkeypatch, first):
    s = manifest_case
    if first == "probe":
        s.startup.prepare()
    elif first == "in_memory":
        s.startup.prepare_service(s.projected, s.docker)
    else:
        prepare(s)
    original_files = {p.name: p.read_bytes() for p in s.root.iterdir()}
    clocks, reads = len(s.clocks), len(s.cached_calls)

    def forbidden(*_args, **_kwargs):
        pytest.fail("A second preparation path must not load any manifest")

    monkeypatch.setattr(protected, "load_baseline", forbidden)
    denied(s)
    assert len(s.clocks) == clocks and len(s.cached_calls) == reads
    assert {p.name: p.read_bytes() for p in s.root.iterdir()} == original_files
