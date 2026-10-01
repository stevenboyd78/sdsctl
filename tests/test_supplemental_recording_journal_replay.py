"""Pure policy replay with real private journals, not installed authority."""

import copy
import time
from dataclasses import replace

import pytest

from . import test_supplemental_recording_bootstrap as policy

m, b = policy.m, policy.b
directory, journal, idle, launched = (
    policy.directory,
    policy.journal,
    policy.idle,
    policy.launched,
)


def refused(journal, end=None):
    cached, fd = journal.machine, journal.fd
    originals = {path: path.read_bytes() for path in journal.path.iterdir()}
    with pytest.raises(b.UnsafeHandoff, match="Recording journal replay is unconfirmed"):
        journal.replayed(time.monotonic() + 1 if end is None else end)
    assert journal.machine is cached and journal.fd == fd
    assert {path: path.read_bytes() for path in journal.path.iterdir()} == originals


@pytest.mark.parametrize("stage", ["launched", "ready", "authorized", "exited"])
def test_original_policy_is_checked_without_reopening_or_reemitting_actions(
    launched, monkeypatch, stage
):
    j = launched
    if stage != "launched":
        j.append(policy.ready(j))
    if stage in ("authorized", "exited"):
        j.append(policy.recording.authorization(20))
    if stage == "exited":
        j.append(policy.worker_exit(j, 21))
    cached, state, entries = j.machine, j.machine.state, copy.deepcopy(j.entries)
    originals = {path: path.read_bytes() for path in j.path.iterdir()}

    def forbidden(*_, **__):
        pytest.fail("Read-only replay may not open, publish, repair or dispatch")

    with monkeypatch.context() as patch:
        patch.setattr(b.os, "open", forbidden)
        patch.setattr(b.os, "fsync", forbidden)
        patch.setattr(m.Journal, "append", forbidden)
        assert j.replayed(time.monotonic() + 1) is cached
        assert j.replayed(time.monotonic() + 1) is cached
    assert j.machine.state is state and j.entries == entries
    assert {path: path.read_bytes() for path in j.path.iterdir()} == originals


@pytest.mark.parametrize(
    "field,value",
    [
        ("phase", "candidate_running"),
        ("deadline", 999),
        ("trial_deadline", 999),
        ("recording_outcome", "verified"),
        ("files_stage", "finalized"),
        ("operator_exit_sha256", "7" * 64),
        ("reason", "PRIVATE changed cache"),
        ("finish_requested", 0),  # == False is not the same canonical value.
    ],
)
def test_cached_state_must_match_replay_including_types(launched, field, value):
    launched.machine.state = replace(launched.machine.state, **{field: value})
    refused(launched)


@pytest.mark.parametrize(
    "field",
    ["last_at", "created_at", "hard_deadline", "case_id", "baseline", "contract", "bootstrap"],
)
def test_all_original_policy_inputs_are_checked_not_only_phase(launched, field):
    machine = launched.machine
    if field in ("last_at", "created_at", "hard_deadline"):
        setattr(machine, field, getattr(machine, field) + 1)
    elif field == "case_id":
        machine.case_id = "f" * 32
    elif field == "baseline":
        machine.baseline = replace(machine.baseline, jobs_idle=False)
    elif field == "contract":
        machine.contract = replace(machine.contract, maximum_recording_seconds=1)
    else:
        machine.bootstrap = replace(machine.bootstrap, stop_by=601)
    refused(launched)


@pytest.mark.parametrize(
    "fault",
    ["schema", "schema_bool", "chain", "extra", "missing", "event", "order", "empty", "large"],
)
def test_malformed_in_memory_chain_cannot_become_authority(launched, fault):
    j = launched
    if fault == "schema":
        j.entries[-1]["schema"] = 2
    elif fault == "schema_bool":
        j.entries[-1]["schema"] = True
    elif fault == "chain":
        j.entries[-1]["previous"] = "f" * 64
    elif fault == "extra":
        j.entries[-1]["extra"] = "PRIVATE"
    elif fault == "missing":
        del j.entries[-1]["previous"]
    elif fault == "event":
        j.entries[-1]["event"]["kind"] = "invalid"
    elif fault == "order":
        j.entries[-2:] = reversed(j.entries[-2:])
    elif fault == "empty":
        j.entries.clear()
    else:
        j.entries[-1]["event"]["extra"] = "x" * (b.MAX_BYTES + 1)
    refused(j)


@pytest.mark.parametrize("fault", ["state", "machine", "entries", "late"])
def test_changes_during_replay_are_not_adopted(launched, monkeypatch, fault):
    j = launched
    apply, calls = m.Journal.apply, []

    def changed(target, event):
        result = apply(target, event)
        if not calls:
            calls.append(True)
            if fault == "state":
                j.machine.state = replace(j.machine.state, recording_outcome="verified")
            elif fault == "machine":
                j.machine = copy.deepcopy(j.machine)
            elif fault == "entries":
                j.entries[-1]["previous"] = "f" * 64
            else:
                now = time.monotonic()
                monkeypatch.setattr(m.time, "monotonic", lambda: now + 3)
        return result

    monkeypatch.setattr(m.Journal, "apply", changed)
    original = {path: path.read_bytes() for path in j.path.iterdir()}
    with pytest.raises(b.UnsafeHandoff, match="Recording journal replay is unconfirmed"):
        j.replayed(time.monotonic() + 1)
    assert len(calls) == 1 and j.fd >= 0
    assert {path: path.read_bytes() for path in j.path.iterdir()} == original


@pytest.mark.parametrize("end", [0, True, float("inf"), float("nan"), "PRIVATE", None])
def test_invalid_bounds_refuse_without_touching_journal(launched, end):
    with pytest.raises(b.UnsafeHandoff):
        launched.replayed(end)
    assert launched.fd >= 0


def test_no_new_longer_window_or_replayed_deadline(launched):
    refused(launched, time.monotonic() + 3)


def test_corrupt_replay_never_repairs_original_cache(launched):
    j = launched
    object.__setattr__(j.machine.state, "recording_outcome", "verified")
    refused(j)
    assert j.machine.state.recording_outcome == "verified"
