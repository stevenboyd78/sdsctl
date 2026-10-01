"""Real temporary inventories/native PCM files; no scanner or host operations."""

import hashlib
import importlib.util
import json
import os
import stat
import sys
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from sds200.daemon_recording import DaemonRecordingManager

from .test_daemon_recording import FakeClock, FakeRuntime, FakeWallClock
from .test_supplemental_recording_handoff import h
from .test_supplemental_recording_monitor import m
from .test_supplemental_recording_owner import o

NAME = "supplemental_recording_protected"
SPEC = importlib.util.spec_from_file_location(NAME, Path(h.__file__).with_name(NAME + ".py"))
p = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = p
SPEC.loader.exec_module(p)
e = p.evidence
CASE = "ab12345612344abc8abc123456789abc"
GENERATION = "a" * 64
ENDPOINT = hashlib.sha256(b"synthetic audio source").hexdigest()
START = "2026-09-23T08:00:00-06:00"


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "PRIVATE_RECORDINGS"
    root.mkdir()
    (root / "older").mkdir()
    (root / "older" / "old.wav").write_bytes(b"old evidence unchanged")
    (root / "old.json").write_bytes(b"PRIVATE_METADATA")
    baseline = e.capture_baseline(root, CASE)
    directory = tmp_path / "PRIVATE_BASELINE"
    directory.mkdir(mode=0o700)
    writer = m.Writer(os.geteuid(), os.getegid(), 0o600)
    stored = p.save_baseline(directory, baseline, writer, ENDPOINT)
    expected = e.RecordingExpectation(CASE, GENERATION, ENDPOINT, START)
    name = e.filename(CASE, START)
    return SimpleNamespace(
        root=root,
        baseline=baseline,
        directory=directory,
        writer=writer,
        stored=stored,
        collector=p.Collector(stored),
        expected=expected,
        wav=root / name,
        sidecar=root / (name + ".json"),
        temporary=root / ("." + name + ".json.abcdefgh.tmp"),
    )


def write(path, raw=b""):
    path.write_bytes(raw)
    path.chmod(0o600)


def refusal(call):
    with pytest.raises(p.UnconfirmedProtection) as error:
        call()
    assert str(error.value) == p.MESSAGE and error.value.__suppress_context__
    assert "PRIVATE" not in str(error.value)


def load(tree, **changes):
    return p.load_baseline(
        tree.directory,
        **(
            {
                "expected_contract": tree.stored.contract,
                "expected_sha256": tree.stored.manifest_sha256,
            }
            | changes
        ),
    )


def test_manifest_is_private_durable_and_matches_native_owner_hashes(tree, monkeypatch, tmp_path):
    target = tmp_path / "new-manifest"
    target.mkdir(mode=0o700)
    synced, original = [], os.fsync

    def sync(fd):
        synced.append(stat.S_IFMT(os.fstat(fd).st_mode))
        original(fd)

    monkeypatch.setattr(os, "fsync", sync)
    saved = p.save_baseline(target, tree.baseline, tree.writer, ENDPOINT)
    assert saved == tree.stored
    assert synced == [stat.S_IFREG, stat.S_IFDIR]
    assert stat.S_IMODE((target / "baseline.json").stat().st_mode) == 0o600
    expected = {
        "root_identity": tree.baseline.root_identity,
        "files": [(name, asdict(item)) for name, item in tree.baseline.files],
    }
    assert saved.contract.baseline_sha256 == hashlib.sha256(o.encoded(expected)).hexdigest()
    assert saved.contract.root_sha256 == hashlib.sha256(os.fsencode(tree.root)).hexdigest()
    assert load(tree) == saved
    assert tree.collector.pristine().files.stage == "pristine"


def test_restart_loads_original_inventory_not_new_recording_as_old_data(tree):
    write(tree.wav, b"partially buffered")
    stored = load(tree)
    assert stored.baseline == tree.baseline
    refusal(lambda: p.Collector(stored).pristine())
    current = p.Collector(stored).active(tree.expected)
    assert current.files.stage == "active" and current.progress.wav.size_after == 18
    assert current.progress.old_files == 2
    raw = tree.collector.dump_progress(current, tree.expected)
    recovered = p.Collector(load(tree))
    previous = recovered.load_progress(raw, expected_files=current.files, expected=tree.expected)
    with tree.wav.open("ab") as stream:
        stream.write(b"more")
    assert recovered.active(tree.expected, previous=previous).progress.wav.size_after == 22


@pytest.mark.parametrize(
    "fault",
    [
        "contract",
        "digest",
        "empty",
        "extra",
        "mode",
        "link",
        "symlink",
        "ancestor_symlink",
        "truncate",
        "contents",
    ],
)
def test_manifest_load_refuses_uncertain_or_unsealed_input(tree, fault):
    target = tree.directory / "baseline.json"
    changes = {}
    if fault == "contract":
        changes["expected_contract"] = replace(tree.stored.contract, writer_sha256="f" * 64)
    elif fault == "digest":
        changes["expected_sha256"] = "f" * 64
    elif fault == "empty":
        target.unlink()
    elif fault == "extra":
        write(tree.directory / "other")
    elif fault == "mode":
        target.chmod(0o644)
    elif fault == "link":
        (tree.directory.parent / "alias").hardlink_to(target)
    elif fault == "symlink":
        original = tree.directory.parent / "copied"
        target.rename(original)
        target.symlink_to(original)
    elif fault == "ancestor_symlink":
        original = tree.directory.with_name("moved")
        tree.directory.rename(original)
        tree.directory.symlink_to(original, target_is_directory=True)
    elif fault == "truncate":
        write(target, target.read_bytes()[:-1])
    else:
        write(target, target.read_bytes().replace(b"old.wav", b"new.wav"))
    refusal(lambda: load(tree, **changes))


@pytest.mark.parametrize(
    "fault",
    [
        "duplicate",
        "unknown",
        "schema_bool",
        "case",
        "root",
        "root_dot",
        "root_identity",
        "root_mode",
        "file_bool",
        "file_large",
        "file_mode",
        "file_duplicate",
        "file_unsorted",
        "file_parent",
        "file_empty",
        "file_control",
        "reserved",
        "hidden_reserved",
        "writer_bool",
        "duration_bool",
        "duration_large",
        "noncanonical",
        "nan",
    ],
)
def test_manifest_decoder_is_strict_even_with_matching_external_digest(tree, fault):
    original = (tree.directory / "baseline.json").read_bytes()
    value = json.loads(original)
    if fault == "duplicate":
        raw = original[:-1] + b',"schema":1}'
    elif fault == "unknown":
        value["extra"] = None
    elif fault == "schema_bool":
        value["schema"] = True
    elif fault == "case":
        value["case"] = "c" * 32
    elif fault == "root":
        value["root"] = "/"
    elif fault == "root_dot":
        value["root"] += "/../PRIVATE_RECORDINGS"
    elif fault == "root_identity":
        value["root_identity"][0] = True
    elif fault == "root_mode":
        value["root_identity"][2] = stat.S_IFREG | 0o700
    elif fault == "file_bool":
        value["files"][0][1]["size"] = False
    elif fault == "file_large":
        value["files"][0][1]["size"] = 16 * 1024 * 1024 + 1
    elif fault == "file_mode":
        value["files"][0][1]["mode"] = 0o10000
    elif fault == "file_duplicate":
        value["files"].append(value["files"][-1])
    elif fault == "file_unsorted":
        value["files"].reverse()
    elif fault in ("file_parent", "file_empty", "file_control"):
        value["files"][0][0] = {
            "file_parent": "../file",
            "file_empty": "",
            "file_control": "bad\nname",
        }[fault]
    elif fault in ("reserved", "hidden_reserved"):
        value["files"] = [
            [("." if fault == "hidden_reserved" else "") + tree.wav.name, value["files"][0][1]]
        ]
    elif fault == "writer_bool":
        value["writer"]["wav_mode"] = True
    elif fault == "duration_bool":
        value["maximum_recording_seconds"] = True
    elif fault == "duration_large":
        value["maximum_recording_seconds"] = 181
    elif fault == "noncanonical":
        raw = json.dumps(value, indent=2).encode()
    else:
        raw = original.replace(b'"schema":1', b'"schema":NaN')
    if fault not in ("duplicate", "noncanonical", "nan"):
        raw = p.encode(value)
    write(tree.directory / "baseline.json", raw)
    refusal(lambda: load(tree, expected_sha256=hashlib.sha256(raw).hexdigest()))


@pytest.mark.parametrize(
    "fault", ["existing", "old_changed", "overlap", "mode", "fsync_file", "fsync_directory"]
)
def test_baseline_publication_never_overwrites_or_retries_partial_evidence(
    tree, tmp_path, monkeypatch, fault
):
    target = tmp_path / "save"
    target.mkdir(mode=0o700)
    if fault == "existing":
        write(target / "baseline.json", b"retained old attempt")
    elif fault == "old_changed":
        (tree.root / "old.json").write_bytes(b"modified")
    elif fault == "overlap":
        target = tree.root / "private"
        target.mkdir(mode=0o700)
    elif fault == "mode":
        target.chmod(0o755)
    else:
        original = os.fsync

        def sync(fd):
            original(fd)
            selected = (
                stat.S_ISREG(os.fstat(fd).st_mode)
                if fault == "fsync_file"
                else stat.S_ISDIR(os.fstat(fd).st_mode)
            )
            if selected:
                raise OSError("PRIVATE failure")

        monkeypatch.setattr(os, "fsync", sync)
    refusal(lambda: p.save_baseline(target, tree.baseline, tree.writer, ENDPOINT))
    if fault in ("fsync_file", "fsync_directory", "existing"):
        before = (target / "baseline.json").read_bytes()
        monkeypatch.undo()
        refusal(lambda: p.save_baseline(target, tree.baseline, tree.writer, ENDPOINT))
        assert (target / "baseline.json").read_bytes() == before


@pytest.mark.parametrize("publication", ["none", "writing", "linked", "published"])
@pytest.mark.parametrize("wav", [b"", b"invalid partial RIFF", b"RIFF" + b"\0" * 2048])
def test_retained_hashes_empty_partial_and_interrupted_publication_without_success(
    tree, publication, wav
):
    write(tree.wav, wav)
    if publication != "none":
        write(tree.temporary, b"bad native metadata" if publication != "writing" else b"")
    if publication in ("linked", "published"):
        tree.sidecar.hardlink_to(tree.temporary)
    if publication == "published":
        tree.temporary.unlink()
    collected = tree.collector.retained(tree.expected)
    assert collected.files.stage == "retained" and collected.artifact is None
    assert collected.progress.publication == publication
    assert tree.collector.retained(tree.expected).files == collected.files
    assert tree.wav.read_bytes() == wav
    if publication == "linked":
        assert tree.sidecar.stat().st_nlink == tree.temporary.stat().st_nlink == 2


@pytest.mark.parametrize(
    "fault",
    [
        "old_contents",
        "old_removed",
        "extra",
        "other_case",
        "second_wav",
        "second_temp",
        "symlink",
        "hardlink",
        "wrong_mode",
        "wrong_writer",
        "root_replaced",
        "wav_directory",
    ],
)
def test_retained_never_becomes_a_root_exclusion(tree, fault):
    write(tree.wav, b"partial")
    if fault == "old_contents":
        (tree.root / "old.json").write_bytes(b"changed")
    elif fault == "old_removed":
        (tree.root / "old.json").unlink()
    elif fault == "extra":
        write(tree.root / "extra")
    elif fault == "other_case":
        write(tree.root / e.filename("d" * 32, START))
    elif fault == "second_wav":
        write(tree.root / (tree.wav.stem + "-1.wav"))
    elif fault == "second_temp":
        write(tree.temporary)
        write(tree.temporary.with_name(tree.temporary.name.replace("abcdefgh", "ijklmnop")))
    elif fault == "symlink":
        tree.wav.unlink()
        tree.wav.symlink_to(tree.root / "old.json")
    elif fault == "hardlink":
        (tree.root.parent / "alias").hardlink_to(tree.wav)
    elif fault == "wrong_mode":
        tree.wav.chmod(0o644)
    elif fault == "wrong_writer":
        original = tree.collector.stored
        refusal(
            lambda: p.Collector(
                replace(original, writer=replace(tree.writer, uid=tree.writer.uid + 1))
            )
        )
        return
    elif fault == "root_replaced":
        tree.root.rename(tree.root.with_name("retained-root"))
        tree.root.mkdir()
    else:
        tree.wav.unlink()
        tree.wav.mkdir()
    refusal(lambda: tree.collector.retained(tree.expected))


@pytest.mark.parametrize(
    "fault",
    [
        "contract",
        "generation",
        "bytes",
        "size_bool",
        "inode",
        "publication",
        "writer",
        "writer_float",
        "metadata",
        "extra",
        "duplicate",
    ],
)
def test_progress_checkpoint_requires_exact_host_digest_and_strict_shape(tree, fault):
    write(tree.wav, b"buffered")
    collected = tree.collector.active(tree.expected)
    raw = tree.collector.dump_progress(collected, tree.expected)
    value = json.loads(raw)
    pinned = collected.files
    if fault == "contract":
        pinned = replace(pinned, contract_sha256="f" * 64)
    elif fault == "generation":
        pinned = replace(pinned, generation="f" * 64)
    elif fault == "bytes":
        raw += b" "
    elif fault == "size_bool":
        value["progress"]["wav"]["size_after"] = True
    elif fault == "inode":
        value["progress"]["wav"]["identity"][1] = 0
    elif fault == "publication":
        value["progress"]["publication"] = "published"
    elif fault == "writer":
        value["progress"]["writer"]["uid"] += 1
    elif fault == "writer_float":
        value["progress"]["writer"]["uid"] = float(tree.writer.uid)
    elif fault == "metadata":
        value["progress"]["metadata_sha256"] = "f" * 64
    elif fault == "extra":
        value["progress"]["extra"] = None
    else:
        raw = raw[:-1] + b',"contract":"' + tree.stored.contract.sha256.encode() + b'"}'
    if fault not in ("contract", "generation", "bytes", "duplicate"):
        raw = p.encode(value)
        pinned = replace(pinned, evidence_sha256=hashlib.sha256(raw).hexdigest())
    refusal(
        lambda: tree.collector.load_progress(raw, expected_files=pinned, expected=tree.expected)
    )


@pytest.mark.parametrize("fault", ["shrink", "replace", "stage", "metadata"])
def test_loaded_progress_preserves_native_regression_checks(tree, fault):
    write(tree.wav, b"first bytes")
    if fault in ("stage", "metadata"):
        write(tree.sidecar, b"stable metadata")
    captured = tree.collector.active(tree.expected, finalizing=fault in ("stage", "metadata"))
    raw = tree.collector.dump_progress(captured, tree.expected)
    restored = p.Collector(load(tree))
    previous = restored.load_progress(raw, expected_files=captured.files, expected=tree.expected)
    if fault == "shrink":
        write(tree.wav, b"")
    elif fault == "replace":
        tree.wav.rename(tree.wav.with_suffix(".old"))
        write(tree.wav, b"first bytes")
        tree.wav.with_suffix(".old").unlink()
    elif fault == "metadata":
        write(tree.sidecar, b"changed metadata")
    refusal(
        lambda: restored.active(tree.expected, finalizing=fault == "metadata", previous=previous)
    )


@pytest.fixture
def native(tree):
    runtime = FakeRuntime()
    clock = FakeClock()
    wall = FakeWallClock(datetime(2026, 9, 23, 8, tzinfo=UTC))
    endpoint = hashlib.sha256(runtime.audio.stream.endpoint.encode()).hexdigest()
    # A separate new manifest reflects the actual synthetic runtime source/mode.
    probe = tree.root.parent / "mode-probe"
    probe.touch(mode=0o666)
    writer = m.Writer(os.geteuid(), os.getegid(), stat.S_IMODE(probe.stat().st_mode))
    directory = tree.directory.with_name("NATIVE_BASELINE")
    directory.mkdir(mode=0o700)
    stored = p.save_baseline(directory, tree.baseline, writer, endpoint)
    receipts = tree.directory.with_name("NATIVE_RECEIPTS")
    receipts.mkdir(mode=0o700)
    manager = DaemonRecordingManager(
        runtime, tree.root, template=e.template(CASE), clock=clock, now=wall
    )
    plan = o.Plan(CASE, GENERATION, endpoint, 100, 105, 164, 170)
    owner = o.FiniteRecordingOwner(manager, tree.baseline, plan, receipts, monotonic=clock)
    value = SimpleNamespace(
        tree=tree,
        runtime=runtime,
        clock=clock,
        wall=wall,
        manager=manager,
        owner=owner,
        collector=p.Collector(stored),
        receipts=receipts,
    )
    try:
        yield value
    finally:
        owner.close()
        manager.close()
        runtime.close()


def finish(native):
    expected = native.owner.start()
    active = native.collector.active(expected)
    native.runtime.router.submit_pcm(b"\x12\x34" * 160)
    native.clock.value = 164
    native.wall.value += timedelta(seconds=64)
    stopped = native.owner.stop()
    acknowledgment = p.Acknowledgment(
        CASE,
        GENERATION,
        native.collector.stored.contract.sha256,
        expected.started_at,
        p.checksum(stopped),
        p.checksum({"fixture_successful_return": stopped}),
    )
    return expected, active, stopped, acknowledgment


def test_native_pcm_owner_to_collector_finalization_and_retained_are_distinct(native):
    assert native.collector.pristine().files.stage == "pristine"
    expected, active, stopped, acknowledgment = finish(native)
    retained = native.collector.retained(expected, previous=active.progress)
    assert retained.artifact is None and retained.files.stage == "retained"
    final = native.collector.finalized(
        expected, stopped=stopped, acknowledgment=acknowledgment, previous=active.progress
    )
    assert final.files.stage == "finalized"
    assert final.artifact.samples == 160 and final.artifact.old_files == 2
    assert final.files != retained.files
    assert native.runtime.running  # File verification does not claim process exit.
    assert (native.tree.root / "old.json").read_bytes() == b"PRIVATE_METADATA"


@pytest.mark.parametrize(
    "fault",
    [
        "none",
        "receipt_only",
        "case",
        "generation",
        "contract",
        "timestamp",
        "snapshot",
        "corrupt_wav",
        "corrupt_metadata",
    ],
)
def test_good_receipt_is_not_native_success_acknowledgment(native, fault):
    expected, _, stopped, acknowledgment = finish(native)
    if fault == "none":
        acknowledgment = None
    elif fault == "receipt_only":
        acknowledgment = json.loads((native.receipts / "stopped.json").read_bytes())
    elif fault == "case":
        acknowledgment = replace(acknowledgment, case="ab12345612344abc8abc123456789abd")
    elif fault == "generation":
        acknowledgment = replace(acknowledgment, generation="f" * 64)
    elif fault == "contract":
        acknowledgment = replace(acknowledgment, contract_sha256="f" * 64)
    elif fault == "timestamp":
        acknowledgment = replace(acknowledgment, started_at=START)
    elif fault == "snapshot":
        stopped = dict(stopped, samples=161)
    elif fault == "corrupt_wav":
        with (native.tree.root / stopped["recording"]).open("r+b") as stream:
            stream.write(b"FAIL")
    else:
        (native.tree.root / stopped["metadata"]).write_bytes(b"corrupt")
    refusal(
        lambda: native.collector.finalized(expected, stopped=stopped, acknowledgment=acknowledgment)
    )


@pytest.mark.parametrize("mutation", ["during_hash", "between_hashes", "old_after_hash"])
def test_retained_detects_mutation_during_or_between_stable_reads(tree, monkeypatch, mutation):
    write(tree.wav, b"buffered")
    original = p._stable_file
    calls = 0

    def reading(*args, **kwargs):
        nonlocal calls
        calls += 1
        if mutation == "during_hash":
            original_read = os.read

            def changed(fd, size):
                raw = original_read(fd, size)
                write(tree.wav, b"changed contents")
                return raw

            with monkeypatch.context() as patch:
                patch.setattr(os, "read", changed)
                return original(*args, **kwargs)
        result = original(*args, **kwargs)
        if calls == 1:
            if mutation == "between_hashes":
                write(tree.wav, b"changed")
            else:
                (tree.root / "old.json").write_bytes(b"changed")
        return result

    monkeypatch.setattr(p, "_stable_file", reading)
    refusal(lambda: tree.collector.retained(tree.expected))


def test_repeated_bad_reads_do_not_leak_descriptors_or_mutate_evidence(tree):
    write(tree.wav, b"partial")
    write(tree.root / "unexpected")
    before = len(os.listdir("/proc/self/fd"))
    inventory = sorted(path.name for path in tree.root.iterdir())
    for _ in range(20):
        refusal(lambda: tree.collector.retained(tree.expected))
    assert len(os.listdir("/proc/self/fd")) == before
    assert sorted(path.name for path in tree.root.iterdir()) == inventory


@pytest.mark.parametrize("fault", ["mode", "extra", "replace", "root_replace"])
def test_manifest_rechecks_file_and_directory_after_read(tree, monkeypatch, fault):
    original = e.read_bytes

    def changed(*args, **kwargs):
        raw = original(*args, **kwargs)
        path = tree.directory / "baseline.json"
        if fault == "mode":
            path.chmod(0o644)
        elif fault == "extra":
            write(tree.directory / "unrelated")
        elif fault == "replace":
            path.rename(path.with_name("old"))
            write(path, raw)
            path.with_name("old").unlink()
        else:
            tree.directory.rename(tree.directory.with_name("preserved"))
            tree.directory.mkdir(mode=0o700)
            write(path, raw)
        return raw

    monkeypatch.setattr(e, "read_bytes", changed)
    refusal(lambda: load(tree))


@pytest.mark.parametrize("operation", ["save", "load", "retained", "finalized"])
def test_total_collection_budget_is_not_extended_by_inner_read_budgets(
    native, monkeypatch, operation
):
    tree = native.tree
    if operation == "finalized":
        expected, _, stopped, acknowledgment = finish(native)
    elif operation == "retained":
        write(tree.wav, b"partial")
    clock = [100.0]
    monkeypatch.setattr(p.time, "monotonic", lambda: clock[0])
    if operation == "save":
        target = tree.directory.with_name("late-save")
        target.mkdir(mode=0o700)
        original = os.fsync

        def late(fd):
            original(fd)
            clock[0] = 106.0

        monkeypatch.setattr(os, "fsync", late)
        refusal(lambda: p.save_baseline(target, tree.baseline, tree.writer, ENDPOINT))
        assert (target / "baseline.json").exists()  # Preserve late publication.
    elif operation == "load":
        original = p._read_manifest

        def late(*args):
            raw = original(*args)
            clock[0] = 106.0
            return raw

        monkeypatch.setattr(p, "_read_manifest", late)
        refusal(lambda: load(tree))
    else:
        original = p._stable_file

        def late(*args, **kwargs):
            raw = original(*args, **kwargs)
            clock[0] = 106.0
            return raw

        monkeypatch.setattr(p, "_stable_file", late)
        if operation == "retained":
            refusal(lambda: tree.collector.retained(tree.expected))
        else:
            refusal(
                lambda: native.collector.finalized(
                    expected, stopped=stopped, acknowledgment=acknowledgment
                )
            )


@pytest.mark.parametrize("fault", ["oversized_manifest", "oversized_wav", "oversized_metadata"])
def test_actual_collection_rejects_oversized_inputs_without_deleting_them(tree, fault):
    if fault == "oversized_manifest":
        path = tree.directory / "baseline.json"
        size = p.MAX_MANIFEST_BYTES + 1
    else:
        write(tree.wav)
        if fault == "oversized_wav":
            path, size = tree.wav, e.MAX_WAV_BYTES + 1
        else:
            path, size = tree.sidecar, e.MAX_METADATA_BYTES + 1
            write(path)
    with path.open("r+b") as stream:
        stream.truncate(size)
    if fault == "oversized_manifest":
        refusal(lambda: load(tree))
    else:
        refusal(lambda: tree.collector.retained(tree.expected))
    assert path.stat().st_size == size


@pytest.mark.parametrize(
    "fault", ["wav_after_verifier", "metadata_after_verifier", "wav_after_monitor"]
)
def test_finalized_never_uses_size_only_proof_after_content_validation(native, monkeypatch, fault):
    expected, _, stopped, acknowledgment = finish(native)
    if fault == "wav_after_monitor":
        original = native.collector.active
        calls = 0

        def changed(*args, **kwargs):
            nonlocal calls
            result = original(*args, **kwargs)
            calls += 1
            if calls == 2:
                path = native.tree.root / stopped["recording"]
                data = path.read_bytes()
                path.write_bytes(data[:-1] + bytes([data[-1] ^ 0xFF]))
            return result

        monkeypatch.setattr(native.collector, "active", changed)
    else:
        original = e.verify_finalized

        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            path = (
                native.tree.root
                / stopped["recording" if fault == "wav_after_verifier" else "metadata"]
            )
            data = path.read_bytes()
            path.write_bytes(data[:-1] + bytes([data[-1] ^ 0xFF]))
            return result

        monkeypatch.setattr(e, "verify_finalized", changed)
    refusal(
        lambda: native.collector.finalized(expected, stopped=stopped, acknowledgment=acknowledgment)
    )


def test_manifest_lock_contention_is_nonblocking_and_preserves_original(tree):
    raw = (tree.directory / "baseline.json").read_bytes()
    with p._private_directory(tree.directory, exclusive=True):
        refusal(lambda: load(tree))
    assert (tree.directory / "baseline.json").read_bytes() == raw
    assert load(tree) == tree.stored


def test_finalized_enforces_contract_elapsed_limit_even_with_little_audio(native):
    expected, _, stopped, acknowledgment = finish(native)
    stored = p._decode(
        p.manifest_bytes(
            native.collector.stored.baseline,
            native.collector.stored.writer,
            expected.audio_endpoint_sha256,
            maximum_recording_seconds=1,
        )
    )
    collector = p.Collector(stored)
    acknowledgment = replace(acknowledgment, contract_sha256=stored.contract.sha256)
    assert stopped["audio_duration_seconds"] < 1 < stopped["elapsed_seconds"]
    refusal(lambda: collector.finalized(expected, stopped=stopped, acknowledgment=acknowledgment))


@pytest.mark.parametrize("seconds", [1, 180])
def test_baseline_reserves_whole_recording_and_metadata_observation_budget(tree, seconds):
    available = p.MAX_TOTAL_BYTES - (44 + seconds * 8000 * 2 + 2 * e.MAX_METADATA_BYTES)
    unit = 16 * 1024 * 1024
    prototype = tree.baseline.files[0][1]
    files = tuple(
        (f"old-{index}.wav", replace(prototype, size=min(unit, available - index * unit)))
        for index in range(4)
    )
    assert sum(item.size for _, item in files) == available
    baseline = replace(tree.baseline, files=files)
    raw = p.manifest_bytes(baseline, tree.writer, ENDPOINT, maximum_recording_seconds=seconds)
    assert p._decode(raw).baseline == baseline
    extra = files[:-1] + ((files[-1][0], replace(files[-1][1], size=files[-1][1].size + 1)),)
    refusal(
        lambda: p.manifest_bytes(
            replace(baseline, files=tuple(extra)),
            tree.writer,
            ENDPOINT,
            maximum_recording_seconds=seconds,
        )
    )


def test_progress_checkpoint_roundtrips_each_metadata_publication(tree):
    write(tree.wav, b"partial")
    previous = None
    for publication in ("none", "writing", "linked", "published"):
        if publication == "writing":
            write(tree.temporary)
        elif publication == "linked":
            write(tree.temporary, b"frozen metadata")
            tree.sidecar.hardlink_to(tree.temporary)
        elif publication == "published":
            tree.temporary.unlink()
        result = tree.collector.active(tree.expected, finalizing=True, previous=previous)
        raw = tree.collector.dump_progress(result, tree.expected)
        previous = p.Collector(load(tree)).load_progress(
            raw, expected_files=result.files, expected=tree.expected
        )
        assert previous == result.progress and previous.publication == publication


@pytest.mark.parametrize("ending", ["verified", "lost_acknowledgment", "partial"])
@pytest.mark.parametrize("restart", [False, True])
@pytest.mark.parametrize("missing_exit", [False, True])
def test_real_collected_files_drive_recovery_without_substituting_for_exit(
    native, monkeypatch, ending, restart, missing_exit
):
    # The recorder/files/journal are real local implementations. Only App/CLI
    # lifecycle and init witnesses are fake: this is not platform qualification.
    from . import test_supplemental_recording_recovery as recovery
    from .test_supplemental_recording_checkpoints import c

    old = recovery.previous
    host = recovery.Host()
    host.contract = native.collector.stored.contract
    host.evidence = native.collector.pristine().files
    monkeypatch.setattr(old.r, "read_identity", host.identity)
    monkeypatch.setattr(old.r, "ProcessWitness", host.witness)
    path = native.tree.directory.with_name("HOST_JOURNAL")
    path.mkdir(mode=0o700)
    journal = h.Journal(path)
    run = owner = None
    try:
        journal.append(
            {
                "kind": "prepare_recording",
                "case_id": CASE,
                "boot_id": host.boot,
                "now": host.now,
                "observation": asdict(host.read().observation),
                "contract": asdict(host.contract),
            }
        )
        run = recovery.session(host, journal)
        recovery.candidate(host, journal, run)
        generation = journal.machine.state.candidate_generation
        # The fixture's unused prepared owner dispatches nothing. This distinct
        # native controller binds to the fake host's actual candidate generation.
        native.owner.close()
        receipt_dir = native.receipts.with_name("BOUND_RECEIPTS")
        receipt_dir.mkdir(mode=0o700)
        checkpoint_dir = native.receipts.with_name("BOUND_PROGRESS")
        checkpoint_dir.mkdir(mode=0o700)
        owner = o.FiniteRecordingOwner(
            native.manager,
            native.tree.baseline,
            o.Plan(CASE, generation, host.contract.audio_endpoint_sha256, 100, 105, 164, 170),
            receipt_dir,
            monotonic=native.clock,
        )
        expected = owner.start()
        progress = native.collector.active(expected)
        host.recording = native.manager.snapshot().as_dict()["active"]
        assert host.recording is True
        host.evidence = progress.files
        assert old.poll(host, run).phase == "candidate_running"
        # Serialize/reload through the original sealed manifest, not by treating
        # the current WAV as part of a freshly captured pristine inventory.
        tip = c.append_progress(
            checkpoint_dir, native.collector, expected, progress, previous_tip=None
        )
        if restart:
            run.close()
            journal.close()
            restored = p.load_baseline(
                native.tree.directory.with_name("NATIVE_BASELINE"),
                expected_contract=host.contract,
                expected_sha256=native.collector.stored.manifest_sha256,
            )
            native.collector = p.Collector(restored)
            journal = h.Journal(path)
            run = recovery.session(host, journal)
        previous = c.load_progress(checkpoint_dir, native.collector, expected, expected_tip=tip)
        native.runtime.router.submit_pcm(b"\x12\x34" * 160)
        native.clock.value = 164
        native.wall.value += timedelta(seconds=64)
        stopped = owner.stop()
        if ending == "partial":
            with (native.tree.root / stopped["recording"]).open("r+b") as stream:
                stream.truncate(12)
        host.evidence = native.collector.active(expected, finalizing=True, previous=previous).files
        host.recording = native.manager.snapshot().as_dict()["active"]
        assert host.recording is False
        host.omit_exit = missing_exit
        host.now = journal.machine.state.recording_deadline
        assert old.poll(host, run).outcome == "dispatch_submitted"
        if ending == "verified":
            acknowledgment = p.Acknowledgment(
                CASE,
                generation,
                host.contract.sha256,
                expected.started_at,
                p.checksum(stopped),
                p.checksum({"fixture_successful_return": stopped}),
            )
            final = native.collector.finalized(
                expected, stopped=stopped, acknowledgment=acknowledgment, previous=previous
            )
        else:
            final = native.collector.retained(expected, previous=previous)
        host.evidence = final.files
        count = len(host.sent)
        result = old.poll(host, run)
        if missing_exit:
            assert result.phase == "stopping_candidate" and len(host.sent) == count
            assert journal.machine.state.recording_outcome == "unconfirmed"
        else:
            assert result.outcome == "dispatch_submitted"
            assert old.poll(host, run).phase == "complete"
            assert journal.machine.state.recording_outcome == (
                "verified" if ending == "verified" else "unconfirmed"
            )
            assert host.sent[-1] == ("start", recovery.b.NORMAL)
        assert (native.tree.root / stopped["recording"]).exists()
        assert (native.tree.root / "old.json").read_bytes() == b"PRIVATE_METADATA"
    finally:
        if owner is not None:
            owner.close()
        if run is not None:
            run.close()
        journal.close()
