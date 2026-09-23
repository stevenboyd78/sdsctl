"""Actual private host ledger/checkpoints; synthetic namespace and native replies."""

import importlib.util
import json
import os
import stat
import sys
from dataclasses import asdict, replace
from functools import partial
from pathlib import Path

import pytest

from . import test_supplemental_recording_checkpoints as progress_tests
from . import test_supplemental_recording_projection as projection_tests

NAME = "supplemental_recording_binding"
SPEC = importlib.util.spec_from_file_location(
    NAME, Path(projection_tests.m.__file__).with_name(NAME + ".py")
)
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)
p, c = m.protected, m.checkpoints
layout, tree, routing, projection = (
    projection_tests.layout,
    projection_tests.tree,
    projection_tests.routing,
    projection_tests.projection,
)
BOOT = "ab12345612344abc8abc123456789abc"


@pytest.fixture
def binding(projection):
    return m.Binding(projection, "1" * 64, "2" * 64, BOOT)


@pytest.fixture
def directory(tmp_path):
    path = tmp_path / "PRIVATE_HOST_LEDGER"
    path.mkdir(mode=0o700)
    return path


@pytest.fixture
def ledger(directory, binding):
    return m.Ledger(directory, binding, now=10)


def intent(ledger, tree, **changes):
    return ledger.start_intent(
        **(
            {
                "now": 20,
                "generation": tree.expected.generation,
                "authorization_sha256": "3" * 64,
                "start_by": 25,
                "finish_by": 120,
            }
            | changes
        )
    )


def started(ledger, tree):
    intent(ledger, tree)
    return ledger.started(tree.expected, now=21, success_sha256="4" * 64)


def acknowledged(ledger, tree, **changes):
    return p.Acknowledgment(
        **(
            {
                "case": tree.expected.case,
                "generation": tree.expected.generation,
                "contract_sha256": ledger.binding.projection.host.contract.sha256,
                "started_at": tree.expected.started_at,
                "stopped_sha256": "5" * 64,
                "completion_sha256": "6" * 64,
            }
            | changes
        )
    )


def refusal(call):
    with pytest.raises(m.UnconfirmedBinding) as error:
        call()
    assert str(error.value) == m.MESSAGE and "PRIVATE" not in str(error.value)


def checkpoint(tree, ledger, path, previous=None):
    # Real local file monitor + explicit synthetic host namespace contract. This
    # is not installed mount qualification or native successful-return evidence.
    progress_tests.write(tree.wav, b"some pcm")
    actual = tree.collector.active(tree.expected)
    collector = p.Collector(ledger.binding.projection.host)
    raw = p.encode(
        {
            "expected": asdict(tree.expected),
            "progress": asdict(actual.progress),
            "contract": collector.stored.contract.sha256,
        }
    )
    value = p.Collected(
        p.Files(
            collector.stored.contract.sha256,
            "active",
            m.hashlib.sha256(raw).hexdigest(),
            tree.expected.generation,
        ),
        actual.progress,
    )
    tip = c.append_progress(path, collector, tree.expected, value, previous_tip=previous)
    return collector, tip


def test_host_pins_original_manifests_and_progress_across_read_only_reload(ledger, tree, tmp_path):
    path = tmp_path / "PRIVATE_PROGRESS"
    path.mkdir(mode=0o700)
    started(ledger, tree)
    collector, tip = checkpoint(tree, ledger, path)
    state = ledger.progress(path, collector, tip, now=22)
    assert m.load(ledger.directory, ledger.binding) == state
    assert state.tip == tip and state.expected == tree.expected and state.count == 4
    previous = c.load_progress(path, collector, state.expected, expected_tip=state.tip)
    assert previous.wav.size_after == len(b"some pcm")
    # A later unpinned complete checkpoint must not be silently adopted.
    _, unpinned = checkpoint(tree, ledger, path, tip)
    assert unpinned.count == 2
    assert m.load(ledger.directory, ledger.binding).tip == tip
    with pytest.raises(p.UnconfirmedProtection):
        c.load_progress(path, collector, state.expected, expected_tip=state.tip)
    # Failed replay poisons this live ledger; no future operation is attempted.
    refusal(lambda: ledger.progress(path, collector, tip, now=23))
    refusal(lambda: ledger.abandon(now=24))


def test_completion_and_restoration_are_not_inferred_from_native_disk(ledger, tree):
    started(ledger, tree)
    # The fixture supplies an already-authenticated assertion. No native receipt
    # file is consulted and this ledger itself does not authenticate that claim.
    ack = acknowledged(ledger, tree)
    state = ledger.completed(ack, now=110)
    assert state.closed and state.acknowledgment == ack
    loaded = m.load(ledger.directory, ledger.binding)
    assert loaded == state and not hasattr(loaded, "start_intent")
    assert not hasattr(loaded, "exited_processes")
    refusal(lambda: intent(ledger, tree, now=111))


def test_rehashed_checkpoint_history_cannot_replace_the_host_pinned_prefix(ledger, tree, tmp_path):
    path = tmp_path / "PRIVATE_PROGRESS"
    path.mkdir(mode=0o700)
    started(ledger, tree)
    collector, first = checkpoint(tree, ledger, path)
    ledger.progress(path, collector, first, now=22)
    _, second = checkpoint(tree, ledger, path, first)
    # Individually valid earlier progress with a smaller WAV, then rehash every
    # dependent field/link. The previous independently pinned host tip remains.
    raw = json.loads((path / "0000.json").read_bytes())
    raw["progress"]["progress"]["wav"]["size_before"] = 1
    raw["progress"]["progress"]["wav"]["size_after"] = 1
    raw["files"]["evidence_sha256"] = p.checksum(raw["progress"])
    first_bytes = p.encode(raw)
    progress_tests.write(path / "0000.json", first_bytes)
    later = json.loads((path / "0001.json").read_bytes())
    later["previous"] = m.hashlib.sha256(first_bytes).hexdigest()
    second_bytes = p.encode(later)
    progress_tests.write(path / "0001.json", second_bytes)
    forged = replace(second, sha256=m.hashlib.sha256(second_bytes).hexdigest())
    assert c.load_progress(path, collector, tree.expected, expected_tip=forged)
    refusal(lambda: ledger.progress(path, collector, forged, now=23))
    assert m.load(ledger.directory, ledger.binding).tip == first


def test_successive_checkpoint_extension_keeps_the_host_pin(ledger, tree, tmp_path):
    path = tmp_path / "PRIVATE_PROGRESS"
    path.mkdir(mode=0o700)
    started(ledger, tree)
    collector, first = checkpoint(tree, ledger, path)
    ledger.progress(path, collector, first, now=22)
    _, second = checkpoint(tree, ledger, path, first)
    result = ledger.progress(path, collector, second, now=23)
    assert result.tip == second and m.load(ledger.directory, ledger.binding) == result


@pytest.mark.parametrize("phase", ["prepared", "intent", "started"])
def test_restart_never_reopens_an_actionable_writer(ledger, tree, phase):
    if phase == "intent":
        intent(ledger, tree)
    elif phase == "started":
        started(ledger, tree)
    prior = m.load(ledger.directory, ledger.binding)
    refusal(lambda: m.Ledger(ledger.directory, ledger.binding, now=22))
    assert m.load(ledger.directory, ledger.binding) == prior
    if phase != "prepared":
        assert prior.generation == tree.expected.generation


@pytest.mark.parametrize("field", ["source_sha256", "plan_sha256", "boot_id", "projection"])
def test_reload_requires_original_independent_binding(ledger, field):
    changed = "f" * (32 if field == "boot_id" else 64)
    if field == "projection":
        other = projection_tests.reencode(ledger.binding.projection.host, endpoint="f" * 64)
        changed = m.projection.project(ledger.binding.projection.layout, other)
    binding = replace(ledger.binding, **{field: changed})
    refusal(lambda: m.load(ledger.directory, binding))


@pytest.mark.parametrize(
    "fault",
    [
        "before_intent",
        "twice",
        "late_start",
        "late_finish",
        "wrong_generation",
        "wrong_case",
        "wrong_endpoint",
        "wrong_contract",
        "clock_back",
        "clock_bool",
        "window",
        "start_window",
    ],
)
def test_invalid_sequence_or_binding_is_consumed_without_dispatch(ledger, tree, fault):
    if fault == "before_intent":
        call = partial(ledger.started, tree.expected, now=21, success_sha256="4" * 64)
    elif fault in ("window", "start_window", "clock_bool", "clock_back"):
        updates = {
            "window": {"finish_by": 201},
            "start_window": {"start_by": 31},
            "clock_bool": {"now": True},
            "clock_back": {"now": 9},
        }[fault]
        call = partial(intent, ledger, tree, **updates)
    elif fault == "twice":
        intent(ledger, tree)
        call = partial(intent, ledger, tree)
    elif fault in ("late_finish", "wrong_contract"):
        started(ledger, tree)
        ack = acknowledged(
            ledger,
            tree,
            **(
                {"contract_sha256": ledger.binding.projection.native.contract.sha256}
                if fault == "wrong_contract"
                else {}
            ),
        )
        call = partial(ledger.completed, ack, now=120 if fault == "late_finish" else 110)
    else:
        intent(ledger, tree)
        expected = tree.expected
        if fault != "late_start":
            key = {
                "wrong_generation": "generation",
                "wrong_case": "case",
                "wrong_endpoint": "audio_endpoint_sha256",
            }[fault]
            expected = replace(
                expected, **{key: "bb12345612344abc8abc123456789abc" if key == "case" else "f" * 64}
            )
        call = partial(
            ledger.started,
            expected,
            now=25 if fault == "late_start" else 21,
            success_sha256="4" * 64,
        )
    refusal(call)
    refusal(lambda: ledger.abandon(now=200))


@pytest.mark.parametrize(
    "fault",
    [
        "empty",
        "truncated",
        "mode",
        "symlink",
        "hardlink",
        "fifo",
        "oversized",
        "extra",
        "missing_first",
        "changed_first",
        "noncanonical",
        "duplicate_key",
        "schema",
        "unexpected_event",
        "bad_chain",
    ],
)
def test_corrupt_ledger_is_never_repaired(ledger, tree, fault):
    started(ledger, tree)
    path = ledger.directory / "0000.json"
    raw = path.read_bytes()
    if fault == "empty":
        progress_tests.write(path)
    elif fault == "truncated":
        progress_tests.write(path, raw[:-1])
    elif fault == "mode":
        path.chmod(0o644)
    elif fault in ("symlink", "hardlink"):
        backup = ledger.directory.parent / "PRIVATE_COPY"
        if fault == "symlink":
            path.rename(backup)
            path.symlink_to(backup)
        else:
            backup.hardlink_to(path)
    elif fault == "fifo":
        path.unlink()
        os.mkfifo(path, 0o600)
    elif fault == "oversized":
        progress_tests.write(path, b"x" * (m.MAX_BYTES + 1))
    elif fault == "extra":
        progress_tests.write(ledger.directory / "unexpected")
    elif fault == "missing_first":
        path.rename(ledger.directory.parent / "PRIVATE_MOVED")
    elif fault == "noncanonical":
        progress_tests.write(path, json.dumps(json.loads(raw), indent=2).encode())
    elif fault == "duplicate_key":
        progress_tests.write(path, raw[:-1] + b',"schema":1}')
    else:
        value = json.loads(raw)
        if fault == "schema":
            value["schema"] = True
        elif fault == "unexpected_event":
            value["event"]["kind"] = "start_recording"
        elif fault == "bad_chain":
            value["previous"] = "f" * 64
        else:
            value["event"]["binding"]["host_manifest"] = "f" * 64
        progress_tests.write(path, p.encode(value))
    names = sorted(p.name for p in ledger.directory.iterdir())
    refusal(lambda: m.load(ledger.directory, ledger.binding))
    refusal(lambda: ledger.abandon(now=30))
    assert sorted(p.name for p in ledger.directory.iterdir()) == names


@pytest.mark.parametrize("failure", ["file_fsync", "directory_fsync", "lost_return", "partial"])
def test_uncertain_intent_publication_preserves_case_and_never_retries(
    ledger, tree, monkeypatch, failure
):
    sync = os.fsync
    read = m._read
    with monkeypatch.context() as patch:
        if failure in ("file_fsync", "directory_fsync"):

            def broken(fd):
                info = os.fstat(fd)
                if (stat.S_ISDIR(info.st_mode)) == (failure == "directory_fsync"):
                    raise OSError("PRIVATE_FAILURE")
                sync(fd)

            patch.setattr(os, "fsync", broken)
        elif failure == "lost_return":

            def lost(*args):
                result = read(*args)
                if result and result.generation:
                    raise TimeoutError("PRIVATE_LOST_RETURN")
                return result

            patch.setattr(m, "_read", lost)
        else:

            def partial(fd, mode):
                os.write(fd, b'{"partial":')
                os.close(fd)
                raise OSError("PRIVATE_PARTIAL")

            patch.setattr(os, "fdopen", partial)
        refusal(lambda: intent(ledger, tree))
    assert (ledger.directory / "0001.json").exists()
    refusal(lambda: intent(ledger, tree))
    refusal(lambda: m.Ledger(ledger.directory, ledger.binding, now=22))
    if failure == "partial":
        refusal(lambda: m.load(ledger.directory, ledger.binding))
    else:
        # Even when final fsync was uncertain, any complete observed intent is
        # consumed for recovery, not a claim that dispatch succeeded.
        loaded = m.load(ledger.directory, ledger.binding)
        assert loaded.generation and loaded.expected is None


def test_file_and_directory_fsync_precede_return(directory, binding, monkeypatch):
    events, sync = [], os.fsync

    def watched(fd):
        events.append(stat.S_IFMT(os.fstat(fd).st_mode))
        sync(fd)

    monkeypatch.setattr(os, "fsync", watched)
    ledger = m.Ledger(directory, binding, now=10)
    assert events == [stat.S_IFREG, stat.S_IFDIR]
    assert stat.S_IMODE((directory / "0000.json").stat().st_mode) == 0o600
    assert m.load(directory, binding) == ledger.state


@pytest.mark.parametrize(
    "path",
    [
        "/mnt",
        "/mnt/data",
        "/mnt/data/supervisor",
        "/mnt/data/supervisor/media/private",
        "/mnt/data/supervisor/apps/data/private",
    ],
)
def test_ledger_refuses_app_visible_location_without_opening_it(binding, monkeypatch, path):
    def forbidden(*args, **kwargs):
        pytest.fail("Must not open an App-visible ledger directory")

    monkeypatch.setattr(os, "open", forbidden)
    refusal(lambda: m.Ledger(Path(path), binding, now=10))


def test_abandoned_case_never_claims_success_or_process_exit(ledger, tree):
    started(ledger, tree)
    state = ledger.abandon(now=130)
    assert state.closed and state.acknowledgment is None
    assert m.load(ledger.directory, ledger.binding) == state
    refusal(lambda: ledger.completed(acknowledged(ledger, tree), now=131))


def test_cancellation_poisoning_before_intent_publication(ledger, tree, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(m, "_read", lambda *args: (_ for _ in ()).throw(KeyboardInterrupt()))
        with pytest.raises(KeyboardInterrupt):
            intent(ledger, tree)
    assert m.load(ledger.directory, ledger.binding).generation is None
    refusal(lambda: intent(ledger, tree))


def test_replaced_directory_with_identical_contents_cannot_continue_live_ledger(ledger, tree):
    old = ledger.directory.with_name("PRIVATE_OLD_LEDGER")
    ledger.directory.rename(old)
    ledger.directory.mkdir(mode=0o700)
    for path in old.iterdir():
        progress_tests.write(ledger.directory / path.name, path.read_bytes())
    refusal(lambda: intent(ledger, tree))


@pytest.mark.parametrize("fault", ["entry_limit", "time_limit", "locked", "unsafe_directory"])
def test_bounded_or_locked_ledger_is_not_bypassed(ledger, tree, monkeypatch, fault):
    if fault == "entry_limit":
        monkeypatch.setattr(m, "MAX_ENTRIES", 1)
    elif fault == "time_limit":
        monkeypatch.setattr(m, "MAX_SECONDS", -1)
    elif fault == "unsafe_directory":
        ledger.directory.chmod(0o755)
    else:
        ledger._lock.acquire()
    try:
        refusal(lambda: intent(ledger, tree))
    finally:
        if fault == "locked":
            ledger._lock.release()
    assert not (ledger.directory / "0001.json").exists()
