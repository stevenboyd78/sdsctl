"""Bounded intermediate-file observations; temporary fixtures, no live services."""

import hashlib
import importlib.util
import json
import os
import stat
import sys
from contextlib import contextmanager
from dataclasses import FrozenInstanceError, asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from .test_supplemental_recording_evidence import CASE, ENDPOINT, GENERATION, START, r, wav_bytes
from .test_supplemental_recording_owner import due
from .test_supplemental_recording_owner import native as native

NAME = "supplemental_recording_monitor"
SPEC = importlib.util.spec_from_file_location(NAME, Path(r.__file__).with_name(NAME + ".py"))
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "PRIVATE_RECORDINGS"
    root.mkdir()
    (root / "older").mkdir()
    (root / "older" / "old.wav").write_bytes(wav_bytes())
    (root / "old.json").write_bytes(b"PRIVATE_OLD_METADATA")
    baseline = r.capture_baseline(root, CASE)
    expected = r.RecordingExpectation(
        CASE, GENERATION, hashlib.sha256(ENDPOINT.encode()).hexdigest(), START
    )
    name = r.filename(CASE, START)
    wav = root / name
    wav.write_bytes(b"")
    wav.chmod(0o600)
    return SimpleNamespace(
        root=root,
        baseline=baseline,
        expected=expected,
        wav=wav,
        metadata=root / (name + ".json"),
        temporary=root / ("." + name + ".json.abcdefgh.tmp"),
        writer=m.Writer(os.geteuid(), os.getegid(), 0o600),
    )


def observe(tree, **kwargs):
    return m.observe(
        tree.baseline,
        tree.expected,
        generation=GENERATION,
        writer=tree.writer,
        stage=kwargs.pop("stage", "recording"),
        **kwargs,
    )


def refuse(tree, **kwargs):
    with pytest.raises(m.UnconfirmedObservation) as caught:
        observe(tree, **kwargs)
    assert str(caught.value) == m.MESSAGE and caught.value.__suppress_context__


def write_private(path, data=b'{"private":"current metadata"}'):
    path.write_bytes(data)
    path.chmod(0o600)


def test_empty_buffered_wav_and_growth_are_not_final_artifact_proof(tree):
    first = observe(tree)
    assert first.wav.size_before == first.wav.size_after == 0
    assert first.publication == "none" and first.old_files == 2
    tree.wav.write_bytes(wav_bytes())
    second = observe(tree, previous=first)
    assert second.wav.size_after == len(wav_bytes()) and second.metadata is None
    assert "PRIVATE" not in json.dumps(asdict(second))
    assert "sha256" not in asdict(second.wav)
    with pytest.raises(FrozenInstanceError):
        second.publication = "published"


def test_all_exact_native_metadata_publication_states(tree):
    first = observe(tree)
    waiting = observe(tree, stage="finalizing", previous=first)
    write_private(tree.temporary, b"")
    writing = observe(tree, stage="finalizing", previous=waiting)
    assert writing.publication == "writing" and writing.metadata.size_after == 0
    write_private(tree.temporary)
    grown = observe(tree, stage="finalizing", previous=writing)
    assert grown.metadata.size_after > 0 and grown.metadata_sha256 is None
    tree.metadata.hardlink_to(tree.temporary)
    linked = observe(tree, stage="finalizing", previous=grown)
    assert linked.publication == "linked" and tree.metadata.stat().st_nlink == 2
    tree.temporary.unlink()
    published = observe(tree, stage="finalizing", previous=linked)
    assert published.publication == "published" and published.temporary_name is None
    assert published.metadata_sha256 == linked.metadata_sha256
    assert observe(tree, stage="finalizing", previous=published) == published
    # Opaque contents here are intentionally NOT a valid recorder metadata proof.
    assert published.metadata_sha256 == hashlib.sha256(tree.metadata.read_bytes()).hexdigest()


@pytest.mark.parametrize("state", ["writing", "linked", "published"])
def test_metadata_is_forbidden_before_authorized_finalization_stage(tree, state):
    write_private(tree.temporary)
    if state != "writing":
        tree.metadata.hardlink_to(tree.temporary)
    if state == "published":
        tree.temporary.unlink()
    refuse(tree)


@pytest.mark.parametrize("state", ["linked", "published"])
def test_sampler_may_miss_intermediate_metadata_states_without_inventing_them(tree, state):
    first = observe(tree)
    write_private(tree.temporary)
    tree.metadata.hardlink_to(tree.temporary)
    if state == "published":
        tree.temporary.unlink()
    assert observe(tree, stage="finalizing", previous=first).publication == state


@pytest.mark.parametrize(
    "fault", ["content", "mode", "remove", "rename", "link", "symlink", "extra", "nested_extra"]
)
def test_every_old_file_remains_protected(tree, fault):
    path = tree.root / "old.json"
    if fault == "content":
        path.write_bytes(b"PRIVATE_CHANGED")
    elif fault == "mode":
        path.chmod(0o400)
    elif fault == "remove":
        path.unlink()
    elif fault == "rename":
        path.rename(tree.root / "renamed")
    elif fault == "link":
        (tree.root.parent / "alias").hardlink_to(path)
    elif fault == "symlink":
        path.unlink()
        path.symlink_to(tree.wav)
    else:
        (tree.root / ("extra" if fault == "extra" else "older/extra")).write_bytes(b"new")
    refuse(tree, stage="finalizing")


@pytest.mark.parametrize(
    "fault",
    [
        "second_temp",
        "wrong_case",
        "nested_temp",
        "short_token",
        "uppercase_token",
        "slash",
        "unrelated",
        "second_wav",
    ],
)
def test_only_one_exact_adjacent_native_temporary_name_is_allowed(tree, fault):
    write_private(tree.temporary)
    target = {
        "second_temp": tree.root / tree.temporary.name.replace("abcdefgh", "ijklmnop"),
        "wrong_case": tree.root / tree.temporary.name.replace(CASE, "d" * 32),
        "nested_temp": tree.root / "older" / tree.temporary.name,
        "short_token": tree.root / tree.temporary.name.replace("abcdefgh", "a"),
        "uppercase_token": tree.root / tree.temporary.name.replace("abcdefgh", "ABCDEFGH"),
        "slash": tree.root / "older" / tree.metadata.name,
        "unrelated": tree.root / ".unrelated.tmp",
        "second_wav": tree.root / r.filename("d" * 32, START),
    }[fault]
    write_private(target)
    refuse(tree, stage="finalizing")


@pytest.mark.parametrize("target", ["wav", "temporary", "metadata"])
@pytest.mark.parametrize(
    "fault", ["symlink", "hardlink", "fifo", "directory", "mode", "owner", "oversized"]
)
def test_mutable_files_still_require_exact_safe_types_permissions_and_bounds(
    tree, monkeypatch, target, fault
):
    path = getattr(tree, target)
    write_private(path)
    if fault in ("symlink", "fifo", "directory"):
        path.unlink()
        if fault == "symlink":
            path.symlink_to(tree.root / "old.json")
        elif fault == "fifo":
            os.mkfifo(path)
        else:
            path.mkdir()
    elif fault == "hardlink":
        (tree.root.parent / "alias").hardlink_to(path)
    elif fault == "mode":
        path.chmod(0o666)
    elif fault == "owner":
        tree.writer = replace(tree.writer, uid=tree.writer.uid + 1)
    else:
        monkeypatch.setattr(m, "MAX_WAV_BYTES" if target == "wav" else "MAX_METADATA_BYTES", 0)
    refuse(tree, stage="finalizing")


def test_two_metadata_names_must_be_links_to_the_same_inode(tree):
    write_private(tree.temporary)
    write_private(tree.metadata)
    refuse(tree, stage="finalizing")


@pytest.mark.parametrize(
    "fault",
    [
        "disappear",
        "shrink",
        "replace",
        "new_temp",
        "regress",
        "metadata_change",
        "metadata_replace",
    ],
)
def test_progress_cannot_hide_regression_or_replacement(tree, fault):
    tree.wav.write_bytes(wav_bytes())
    write_private(tree.temporary)
    if fault in ("regress", "metadata_change", "metadata_replace"):
        tree.metadata.hardlink_to(tree.temporary)
        tree.temporary.unlink()
    before = observe(tree, stage="finalizing")
    if fault == "disappear":
        tree.wav.unlink()
    elif fault == "shrink":
        tree.wav.write_bytes(b"")
    elif fault == "replace":
        tree.wav.rename(tree.root.parent / "preserved.wav")
        write_private(tree.wav, wav_bytes())
    elif fault == "new_temp":
        tree.temporary.rename(tree.root / tree.temporary.name.replace("abcdefgh", "ijklmnop"))
    elif fault == "regress":
        tree.metadata.unlink()
    elif fault == "metadata_change":
        write_private(tree.metadata, tree.metadata.read_bytes().replace(b"private", b"changed"))
    else:
        tree.metadata.rename(tree.root.parent / "preserved.json")
        write_private(tree.metadata)
    refuse(tree, stage="finalizing", previous=before)


@pytest.mark.parametrize(
    "field,value",
    [
        ("case", "d" * 32),
        ("generation", "b" * 64),
        ("binding_sha256", "0" * 64),
        ("old_files", 100),
        ("stage", "wrong"),
        ("publication", "wrong"),
    ],
)
def test_previous_observation_cannot_cross_binding(tree, field, value):
    refuse(tree, previous=replace(observe(tree), **{field: value}))


def test_finalizing_does_not_return_to_recording_even_without_metadata(tree):
    before = observe(tree, stage="finalizing")
    refuse(tree, previous=before)


def test_start_receipt_binding_includes_subsecond_timestamp(tree):
    before = observe(tree)
    tree.expected = replace(tree.expected, started_at=START.replace("00-06", "00.1-06"))
    refuse(tree, previous=before)


@pytest.mark.parametrize(
    "field,value", [("uid", True), ("gid", -1), ("wav_mode", 0o666), ("wav_mode", False)]
)
def test_writer_identity_is_explicit_and_strict(field, value):
    values = dict(uid=os.geteuid(), gid=os.getegid(), wav_mode=0o600)
    values[field] = value
    with pytest.raises(m.UnconfirmedObservation):
        m.Writer(**values)


@pytest.mark.parametrize(
    "limit,value",
    [
        ("MAX_SECONDS", -1),
        ("MAX_ENTRIES", 1),
        ("MAX_FILES", 2),
        ("MAX_TOTAL_BYTES", 1),
        ("MAX_DEPTH", 0),
    ],
)
def test_bound_exhaustion_never_returns_partial_evidence(tree, monkeypatch, limit, value):
    monkeypatch.setattr(m, limit, value)
    refuse(tree)


def test_growth_during_observation_allowed_without_claiming_payload_stability(tree, monkeypatch):
    original = os.fstat
    inode = tree.wav.stat().st_ino
    count = 0

    def growing(fd):
        nonlocal count
        info = original(fd)
        if info.st_ino == inode:
            count += 1
            if count == 2:
                tree.wav.write_bytes(wav_bytes())
                info = original(fd)
        return info

    monkeypatch.setattr(os, "fstat", growing)
    observation = observe(tree)
    assert observation.wav.size_before == 0 and observation.wav.size_after == len(wav_bytes())


@pytest.mark.parametrize("target", ["wav", "temporary"])
def test_growth_cannot_exceed_aggregate_inventory_bound(tree, monkeypatch, target):
    path = getattr(tree, target)
    if target == "temporary":
        write_private(path, b"")
    original = os.fstat
    inode = path.stat().st_ino
    count = 0
    monkeypatch.setattr(m, "MAX_TOTAL_BYTES", sum(item.size for _, item in tree.baseline.files))

    def growing(fd):
        nonlocal count
        info = original(fd)
        if info.st_ino == inode:
            count += 1
            if count == 2:
                path.write_bytes(b"x")
                info = original(fd)
        return info

    monkeypatch.setattr(os, "fstat", growing)
    refuse(tree, stage="finalizing")
    assert count == 2


@pytest.mark.parametrize("fault", ["old_mutation", "new_file", "wav_replaced", "metadata_unlinked"])
def test_racing_observation_is_unconfirmed_and_leaks_no_descriptors(tree, monkeypatch, fault):
    write_private(tree.metadata)
    original = os.read
    changed = False
    before = len(list(Path("/proc/self/fd").iterdir()))

    def reading(fd, size):
        nonlocal changed
        data = original(fd, size)
        if not changed:
            changed = True
            if fault == "old_mutation":
                (tree.root / "old.json").write_bytes(b"PRIVATE_CHANGED")
            elif fault == "new_file":
                (tree.root / "extra").write_bytes(b"PRIVATE")
            elif fault == "wav_replaced":
                tree.wav.rename(tree.root.parent / "preserved.wav")
                write_private(tree.wav)
            else:
                tree.metadata.unlink()
        return data

    monkeypatch.setattr(os, "read", reading)
    refuse(tree, stage="finalizing")
    assert changed and len(list(Path("/proc/self/fd").iterdir())) == before


@pytest.mark.parametrize("target", ["read", "scandir", "fstat", "open"])
def test_private_io_errors_are_sanitized_and_descriptors_closed(tree, monkeypatch, target):
    before = len(list(Path("/proc/self/fd").iterdir()))

    def fail(*_args, **_kwargs):
        raise OSError("PRIVATE_SYSTEM_ERROR")

    with monkeypatch.context() as patch:
        patch.setattr(os, target, fail)
        refuse(tree)
    assert len(list(Path("/proc/self/fd").iterdir())) == before


def test_native_owner_and_actual_metadata_writer_follow_observed_states(native, monkeypatch):
    import sds200.recording_metadata as module

    owner = native.build()
    expected = owner.start()
    info = native.manager.recording_path.stat()
    writer = m.Writer(info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode))
    samples = [
        m.observe(
            native.baseline,
            expected,
            generation=native.plan.generation,
            writer=writer,
            stage="recording",
        )
    ]

    def sample():
        # Native stop has durably consumed its intent before metadata publication.
        assert (native.journal / "stop-intent.json").exists()
        samples.append(
            m.observe(
                native.baseline,
                expected,
                generation=native.plan.generation,
                writer=writer,
                stage="finalizing",
                previous=samples[-1],
            )
        )

    original_temp, original_link = module.NamedTemporaryFile, os.link

    @contextmanager
    def temporary_file(**kwargs):
        with original_temp(**kwargs) as handle:
            sample()
            yield handle
        sample()

    def linking(*args, **kwargs):
        original_link(*args, **kwargs)
        sample()

    native.runtime.router.submit_pcm(b"\x12\x34" * 8000)
    due(native)
    with monkeypatch.context() as patch:
        patch.setattr(module, "NamedTemporaryFile", temporary_file)
        patch.setattr(os, "link", linking)
        stopped = owner.stop()
    sample()
    assert [s.publication for s in samples] == ["none", "writing", "writing", "linked", "published"]
    assert samples[-1].wav.size_after == 44 + 16000
    assert (
        r.verify_finalized(
            native.baseline, expected, generation=native.plan.generation, stopped=stopped
        ).samples
        == 8000
    )
    assert (
        native.runtime.running and native.runtime.attach_calls == native.runtime.detach_calls == 1
    )


def test_component_is_not_a_live_adapter_or_mutating_file_tool():
    source = Path(m.__file__).read_text()
    assert (
        "from sds200" not in source and "subprocess" not in source and "import socket" not in source
    )
    assert "os.unlink(" not in source and "os.rename(" not in source
    assert len(list(Path(m.__file__).parent.glob("supplemental_handoff_*.py"))) == 14
