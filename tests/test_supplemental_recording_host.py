"""Recording-aware join with synthetic Docker; no installed or live host access."""

import importlib.util
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from . import test_supplemental_handoff_audio_network as audio
from . import test_supplemental_handoff_protected as fixed
from . import test_supplemental_recording_protected as protected
from . import test_supplemental_recording_recovery as recovery

NAME = "supplemental_recording_host"
SPEC = importlib.util.spec_from_file_location(
    NAME, Path(protected.p.__file__).with_name(NAME + ".py")
)
r = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = r
SPEC.loader.exec_module(r)
o, f, p, h = r.ordinary, r.static, audio.p, audio.h
c, e = protected.p, protected.e
CONTRACT = recovery.policy_tests.CONTRACT
STATIC = f.StaticFiles(*("a" * 64,) * 3)
layout, disk, tree = fixed.layout, fixed.disk, protected.tree


def kwargs(previous, **changes):
    return {
        "seals": tuple(previous.seals.values()),
        "installed_versions": previous.versions,
        "other_scanner_apps": previous.other,
        "core_image": previous.core_image,
        "core_generation": previous.core_generation,
        "core_version": previous.core_version,
        "read_clock": previous.read_clock,
        "collect_files": previous.collect_files,
        "read_native": previous.read_native,
        "network": previous.network,
    } | changes


def recording_files(stage="pristine", generation=None, proof="5" * 64, contract=CONTRACT):
    return c.Collected(r.Files(contract.sha256, stage, proof, generation))


def make_host(monkeypatch, *, running=False):
    host, old, idle = audio.audio_host(monkeypatch, running=running)
    original = old.seals[p.CANDIDATE]
    seal = r.CandidateSeal(original.version, original.image, original.settings, STATIC, CONTRACT)
    host.files[p.CANDIDATE] = r.CandidateEvidence(STATIC, recording_files())
    observer = r.HostObserver(host, host, **kwargs(old, seals=(old.seals[p.NORMAL], seal)))
    return host, observer, idle


def active_files(host, *, stage="active", proof="6" * 64):
    generation = h.generation(
        host.container("app_" + p.CANDIDATE), name="app_" + p.CANDIDATE, image=audio.ot.IMAGE
    )
    return recording_files(stage, generation, proof)


@pytest.mark.parametrize("running", [False, True])
def test_new_observation_retains_all_base_gates_and_does_not_invent_idle(monkeypatch, running):
    host, observer, idle = make_host(monkeypatch, running=running)
    host.native_state = (True, True)
    sample = observer.read()
    assert type(sample) is recovery.r.Sample
    assert type(sample.observation) is r.Observation
    assert sample.observation.files == host.files[p.CANDIDATE].recording.files
    assert sample.observation.candidate.pin == observer.seals[p.CANDIDATE].pin
    assert sample.observation.normal.pin == observer.seals[p.NORMAL].pin
    native = sample.observation.candidate if running else sample.observation.normal
    assert native.recording is True and native.healthy is True
    assert len(idle) == (0 if running else 2)
    assert observer._files is None


@pytest.mark.parametrize("stage", ["active", "finalizing", "retained", "finalized"])
def test_changing_recording_proofs_do_not_change_candidate_static_pin(monkeypatch, stage):
    host, observer, _ = make_host(monkeypatch, running=True)
    host.native_state = (True, stage in ("active", "finalizing"))
    host.files[p.CANDIDATE] = r.CandidateEvidence(STATIC, active_files(host, stage=stage))
    before = observer.read().observation
    host.files[p.CANDIDATE] = r.CandidateEvidence(
        STATIC, active_files(host, stage=stage, proof="7" * 64)
    )
    after = observer.read().observation
    assert after.candidate.pin == before.candidate.pin == observer.seals[p.CANDIDATE].pin
    assert after.files.evidence_sha256 != before.files.evidence_sha256
    assert after.files.stage == stage
    assert before.candidate.recording == after.candidate.recording == host.native_state[1]


@pytest.mark.parametrize("part", ["context", "package", "profile", "options"])
def test_changed_candidate_source_or_settings_never_executes_native_probe(monkeypatch, part):
    host, observer, _ = make_host(monkeypatch, running=True)
    if part == "options":
        host.private_options["candidate"] = "changed"
    else:
        host.files[p.CANDIDATE] = replace(
            host.files[p.CANDIDATE], static=replace(STATIC, **{part: "b" * 64})
        )

    def forbidden(*_):
        pytest.fail("Changed candidate code must not be executed")

    observer.read_native = forbidden
    sample = observer.read()
    assert sample.observation.candidate.pin != observer.seals[p.CANDIDATE].pin
    assert sample.observation.candidate.healthy is None
    assert sample.observation.candidate.recording is None


@pytest.mark.parametrize("part", ["context", "package", "profile", "recordings"])
def test_normal_app_keeps_entire_original_pin(monkeypatch, part):
    host, observer, _ = make_host(monkeypatch)
    host.files[p.NORMAL] = replace(host.files[p.NORMAL], **{part: "b" * 64})
    observed = observer.read().observation.normal
    assert observed.pin != observer.seals[p.NORMAL].pin
    assert observed.healthy is None and observed.recording is None


@pytest.mark.parametrize("state", [(False, True), (True, False), (None, None)])
def test_native_state_is_kept_separate_from_file_stage(monkeypatch, state):
    host, observer, _ = make_host(monkeypatch, running=True)
    host.native_state = state
    host.files[p.CANDIDATE] = r.CandidateEvidence(STATIC, active_files(host))
    observed = observer.read().observation
    assert observed.files.stage == "active"
    assert (observed.candidate.healthy, observed.candidate.recording) == state


def test_lost_native_reply_cannot_be_replaced_by_file_success(monkeypatch):
    host, observer, _ = make_host(monkeypatch, running=True)
    host.files[p.CANDIDATE] = r.CandidateEvidence(STATIC, active_files(host, stage="finalized"))
    calls = []

    def lost(*args):
        calls.append(args)
        raise TimeoutError("private native reply lost")

    observer.read_native = lost
    observed = observer.read().observation
    assert len(calls) == 1
    assert observed.files.stage == "finalized"
    assert observed.candidate.healthy is None and observed.candidate.recording is None


@pytest.mark.parametrize("fault", ["contract", "generation", "old_proof", "missing"])
def test_wrong_or_missing_recording_evidence_fails_without_stale_sample(monkeypatch, fault):
    host, observer, _ = make_host(monkeypatch, running=True)
    valid = r.CandidateEvidence(STATIC, active_files(host))
    host.files[p.CANDIDATE] = valid
    observer.read()
    if fault == "old_proof":
        host.files[p.CANDIDATE] = o.ProtectedFiles(*("a" * 64,) * 4)
    elif fault == "missing":
        host.files[p.CANDIDATE] = None
    else:
        fields = {"contract_sha256" if fault == "contract" else "generation": "f" * 64}
        host.files[p.CANDIDATE] = replace(
            valid,
            recording=replace(valid.recording, files=replace(valid.recording.files, **fields)),
        )
    with pytest.raises(p.UnsafeHandoff):
        observer.read()
    assert observer._files is None and not observer._lock.locked()
    host.files[p.CANDIDATE] = valid
    assert observer.read().observation.files == valid.recording.files


def test_parallel_read_is_refused_and_never_consumes_outer_evidence(monkeypatch):
    host, observer, _ = make_host(monkeypatch)
    base = observer.collect_files
    seen = []

    def collect(slug, container):
        with pytest.raises(p.UnsafeHandoff):
            observer.read()
        seen.append(slug)
        return base(slug, container)

    observer.collect_files = collect
    assert observer.read().observation.files.stage == "pristine"
    assert seen == [p.NORMAL, p.CANDIDATE]
    assert not observer._lock.locked()


@pytest.mark.parametrize("point", ["normal", "candidate", "final_clock"])
def test_collection_failure_always_clears_candidate_evidence(monkeypatch, point):
    host, observer, _ = make_host(monkeypatch)
    original = observer.collect_files

    def collect(slug, container):
        if slug == (p.NORMAL if point == "normal" else p.CANDIDATE):
            if point == "final_clock":
                observer.read_clock = lambda: (host.boot, host.now + 3)
            else:
                raise p.UnsafeHandoff("unconfirmed")
        return original(slug, container)

    observer.collect_files = collect
    with pytest.raises(p.UnsafeHandoff):
        observer.read()
    assert observer._files is None and not observer._lock.locked()


@pytest.mark.parametrize("network", [h.READER_NETWORK, "other", None])
def test_recording_observer_cannot_downgrade_audio_ownership_policy(monkeypatch, network):
    host, observer, _ = make_host(monkeypatch)
    with pytest.raises(p.UnsafeHandoff):
        r.HostObserver(host, host, **kwargs(observer, network=network))


@pytest.mark.parametrize("fault", ["network", "jobs", "other", "core", "inventory", "boot"])
def test_original_host_checks_remain_enforced(monkeypatch, fault):
    host, observer, _ = make_host(monkeypatch, running=True)
    if fault == "network":
        host.values["app_" + p.CANDIDATE]["HostConfig"]["NetworkMode"] = "host"
    elif fault == "jobs":
        host.jobs = False
        assert observer.read().observation.jobs_idle is False
        return
    elif fault == "other":
        host.values["app_" + audio.ot.OTHER] = audio.ot.recovery_tests.container(
            "app_" + audio.ot.OTHER, 5
        )
        assert observer.read().observation.other_owners_stopped is False
        return
    elif fault == "core":
        host.values[h.CORE]["Id"] = "f" * 64
    elif fault == "inventory":
        host.versions["new_scanner_app"] = "1"
    else:
        calls = iter(((host.boot, host.now), ("f" * 32, host.now)))
        observer.read_clock = lambda: next(calls)
    with pytest.raises(p.UnsafeHandoff):
        observer.read()


def test_old_and_new_seal_types_are_deliberately_incompatible(monkeypatch):
    host, observer, _ = make_host(monkeypatch)
    with pytest.raises(p.UnsafeHandoff):
        o.HostObserver(host, host, **kwargs(observer))
    old_host, old_observer, _ = audio.audio_host(monkeypatch)
    with pytest.raises(p.UnsafeHandoff):
        r.HostObserver(old_host, old_host, **kwargs(old_observer))


@pytest.mark.parametrize("fault", ["duplicate", "reversed_roles", "list", "only_candidate"])
def test_exact_roles_and_seal_collection_required(monkeypatch, fault):
    host, observer, _ = make_host(monkeypatch)
    normal, candidate = tuple(observer.seals.values())
    values = {
        "duplicate": (candidate, candidate),
        "reversed_roles": (replace(normal, slug=p.CANDIDATE), candidate),
        "list": [normal, candidate],
        "only_candidate": (candidate,),
    }
    with pytest.raises(p.UnsafeHandoff):
        r.HostObserver(host, host, **kwargs(observer, seals=values[fault]))


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", "bad\nversion"),
        ("version", 1),
        ("image", "a" * 64),
        ("settings", "bad"),
        ("files", o.ProtectedFiles(*("a" * 64,) * 4)),
        ("contract", None),
    ],
)
def test_candidate_seal_uses_same_identity_bounds(monkeypatch, field, value):
    _, observer, _ = make_host(monkeypatch)
    with pytest.raises(p.UnsafeHandoff):
        replace(observer.seals[p.CANDIDATE], **{field: value})


def test_contract_is_in_static_pin_even_before_recording(monkeypatch):
    _, observer, _ = make_host(monkeypatch)
    seal = observer.seals[p.CANDIDATE]
    assert seal.pin != replace(seal, contract=replace(CONTRACT, writer_sha256="f" * 64)).pin


@pytest.fixture
def routing(layout, tree, monkeypatch):
    media = layout.media
    data = Path("/mnt/data/supervisor/apps/data") / p.CANDIDATE
    candidate = f.ProtectedLayout(
        p.CANDIDATE,
        Path("/mnt/data/supervisor/apps/local") / p.CANDIDATE.removeprefix("local_"),
        data,
        media,
        media / "candidate/recordings",
        data / "deployment.toml",
        data / "configuration.toml",
        data / "accepted/accepted-profile.json",
        media / "candidate/profile/profile.cfg",
        "a" * 64,
    )
    # Serialized fixture ONLY: no /mnt/data path is created, opened or written.
    baseline = replace(tree.baseline, root=candidate.recordings)
    collector = c.Collector(c._decode(c.manifest_bytes(baseline, tree.writer, protected.ENDPOINT)))
    calls = []
    for stage in ("pristine", "active", "retained", "finalized"):

        def capture(*args, stage=stage, **kwargs):
            calls.append((stage, args, kwargs))
            effective = "finalizing" if kwargs.get("finalizing") else stage
            return recording_files(
                effective,
                None if effective == "pristine" else tree.expected.generation,
                contract=collector.stored.contract,
            )

        monkeypatch.setattr(collector, stage, capture)
    return candidate, collector, calls


@pytest.mark.parametrize("stage", ["pristine", "active", "finalizing", "retained", "finalized"])
def test_routes_each_stage_to_complete_recording_collector(layout, disk, routing, tree, stage):
    candidate, collector, calls = routing
    arguments = {} if stage == "pristine" else {"expected": tree.expected}
    if stage == "finalized":
        arguments.update(
            stopped={},
            acknowledgment=c.Acknowledgment(
                tree.expected.case,
                tree.expected.generation,
                collector.stored.contract.sha256,
                tree.expected.started_at,
                "b" * 64,
                "c" * 64,
            ),
        )
    request = r.Capture(stage, **arguments)
    collect = r.FilesCollector(layout, candidate, collector, lambda: request)
    result = collect(p.CANDIDATE, None)
    assert type(result) is r.CandidateEvidence
    assert result.recording.files.stage == stage
    assert len(calls) == 1
    assert calls[0][0] == ("active" if stage == "finalizing" else stage)
    assert {path for path, _ in disk} == {candidate.context, *candidate.profile_paths}
    if stage == "finalized":
        assert calls[0][2]["acknowledgment"] is arguments["acknowledgment"]
        assert calls[0][2]["stopped"] is arguments["stopped"]


def test_normal_route_still_hashes_entire_recording_tree(layout, disk, routing):
    candidate, collector, calls = routing

    def forbidden():
        pytest.fail("Normal collection must not use candidate exceptions")

    result = r.FilesCollector(layout, candidate, collector, forbidden)(p.NORMAL, None)
    assert type(result) is o.ProtectedFiles
    assert not calls
    assert disk[-1] == (layout.recordings, {"max_file_bytes": 16 * 1024 * 1024})


@pytest.mark.parametrize(
    "fault", ["same_root", "parent_root", "child_root", "normal_profile", "wrong_baseline"]
)
def test_recording_root_cannot_overlap_normal_protection(layout, routing, fault):
    candidate, collector, _ = routing
    if fault == "same_root":
        layout = replace(layout, recordings=candidate.recordings)
    elif fault == "parent_root":
        layout = replace(layout, recordings=candidate.recordings.parent)
    elif fault == "child_root":
        layout = replace(layout, recordings=candidate.recordings / "child")
    elif fault == "normal_profile":
        layout = replace(layout, source=candidate.recordings / "profile.cfg")
    else:
        candidate = replace(candidate, recordings=candidate.recordings.with_name("different"))
    with pytest.raises(p.UnsafeHandoff):
        r.FilesCollector(layout, candidate, collector, lambda: r.Capture("pristine"))


@pytest.mark.parametrize(
    "fault", ["capture", "collector_result", "recording_failure", "static_failure"]
)
def test_collection_does_not_return_partial_or_old_style_evidence(
    layout, routing, disk, monkeypatch, fault
):
    candidate, collector, calls = routing

    def capture():
        return r.Capture("pristine")

    if fault == "capture":

        def capture():
            return {"stage": "pristine"}
    elif fault == "collector_result":
        monkeypatch.setattr(collector, "pristine", lambda: o.ProtectedFiles(*("a" * 64,) * 4))
    else:

        def fail(*_):
            raise p.UnsafeHandoff("unconfirmed")

        monkeypatch.setattr(
            f if fault == "static_failure" else collector,
            "_collect_static" if fault == "static_failure" else "pristine",
            fail,
        )
    with pytest.raises(p.UnsafeHandoff):
        r.FilesCollector(layout, candidate, collector, capture)(p.CANDIDATE, None)
    if fault == "static_failure":
        assert not calls


@pytest.mark.parametrize("stage", ["unknown", "recording", "", None, 1, []])
def test_unknown_capture_stage_is_not_idle(stage):
    with pytest.raises(p.UnsafeHandoff):
        r.Capture(stage)


@pytest.mark.parametrize("stage", ["pristine", "active", "finalizing", "retained", "finalized"])
def test_capture_requires_stage_specific_authority(tree, stage):
    with pytest.raises(p.UnsafeHandoff):
        r.Capture(
            stage, **({"expected": tree.expected} if stage in ("pristine", "finalized") else {})
        )
    with pytest.raises(p.UnsafeHandoff):
        r.Capture(stage, expected=tree.expected, previous={})


def test_observer_consumes_actual_growing_and_retained_file_proofs(monkeypatch, tree):
    host, observer, _ = make_host(monkeypatch, running=True)
    original = observer.seals[p.CANDIDATE]
    observer.seals[p.CANDIDATE] = replace(original, contract=tree.stored.contract)
    generation = h.generation(
        host.container("app_" + p.CANDIDATE), name="app_" + p.CANDIDATE, image=audio.ot.IMAGE
    )
    expected = replace(tree.expected, generation=generation)
    protected.write(tree.wav, b"partial native buffer")
    collected = tree.collector.active(expected)
    host.files[p.CANDIDATE] = r.CandidateEvidence(STATIC, collected)
    host.native_state = (True, True)
    first = observer.read().observation
    with tree.wav.open("ab") as output:
        output.write(b"more bytes")
    host.files[p.CANDIDATE] = r.CandidateEvidence(
        STATIC, tree.collector.active(expected, previous=collected.progress)
    )
    second = observer.read().observation
    assert first.files.evidence_sha256 != second.files.evidence_sha256
    assert first.candidate.pin == second.candidate.pin
    assert second.candidate.recording is True
    retained = tree.collector.retained(expected)
    host.files[p.CANDIDATE] = r.CandidateEvidence(STATIC, retained)
    host.values.pop("app_" + p.CANDIDATE)
    final = observer.read().observation
    assert final.candidate.state == "stopped" and final.candidate.generation is None
    assert final.files.stage == "retained" and final.files.generation == generation
    assert retained.artifact is None  # Still no independent exit or acknowledgment proof.
    assert (tree.root / "older/old.wav").read_bytes() == b"old evidence unchanged"
