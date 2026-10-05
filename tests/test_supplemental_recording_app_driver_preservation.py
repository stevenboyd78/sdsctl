"""Actual App driver/abandon/Preserved/recovery composition; synthetic exit/file I/O.

Original service/plan/session and real ledger, progress directory and journal
checks are retained. The shared fixture labels the synthetic Startup, platform,
idle and native edges. No installed/native file or audible acceptance is claimed.
"""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_app_driver_composition as driver

m, r, base, launch, platform = driver.m, driver.r, driver.base, driver.launch, driver.platform
candidate, app, native, launch_case, driver_case = (
    driver.candidate,
    driver.app,
    driver.native,
    driver.launch_case,
    driver.driver_case,
)
layout, image_umask, supervised, image, configured = (
    driver.layout,
    driver.image_umask,
    driver.supervised,
    driver.image,
    driver.configured,
)
pytestmark = pytest.mark.parametrize("candidate", ["app_native"], indirect=True)


def prepare_preservation(s, monkeypatch, *, close_lost=False):
    phase = s.driver.recording
    s.transitions.append("abandon")
    s.close_error = close_lost
    assert s.driver.abandon_recording() is not close_lost
    assert phase.abandoned and not phase.uncertain and not phase.finish_attempted
    assert s.run.client.closed and s.ledger.state.closed
    assert s.ledger.state.acknowledgment is None
    return setup_exit_platform(s, monkeypatch, phase, preserved=True)


def setup_exit_platform(s, monkeypatch, phase, *, preserved):
    """Only exit and file I/O facts are synthetic; keep real reader policy."""
    observer = s.driver.native.operator
    c = SimpleNamespace(s=s, operator=observer, worker_exited=True, init_exited=True)
    s.cycle = c
    s.cleanup.enter_context(
        contextmanager(platform.setup_cycling)(
            c, monkeypatch, read_clock=s.service._now, session=s.session
        )
    )
    c.candidate = base.App(s.plan.candidate.pin, "stopped")
    (s.plan.root / "recording-progress").mkdir(mode=0o700)
    reconcile = r.begin.worker_exit.reconcile
    receipt = SimpleNamespace(sha256="d" * 64, returncode=75)

    def poll():
        assert phase.reader is None
        if not c.worker_exited:
            return None
        observer.done = True
        return reconcile.Evidence(
            *("a" * 64 for _ in range(8)), 75, c.init_exited, observer._clock()
        )

    def recheck():
        assert observer.done and observer.publish_attempted
        return receipt

    def publish(journal):
        assert journal is s.journal and observer.done and not observer.publish_attempted
        observer.publish_attempted = True
        assert (
            journal.append(
                dict(
                    kind="operator_exited",
                    boot_id=s.plan.boot,
                    now=observer._clock(),
                    generation=observer.pins.generation,
                    intent_sha256=journal.machine.state.launch_intent_sha256,
                    exit_evidence_sha256=receipt.sha256,
                )
            )
            is None
        )
        observer.result_sha256 = receipt.sha256
        return receipt.sha256

    monkeypatch.setattr(observer, "poll", poll)
    monkeypatch.setattr(observer, "recheck", recheck)
    monkeypatch.setattr(observer, "publish", publish)
    monkeypatch.setattr(observer, "_receipt", recheck)
    monkeypatch.setattr(observer, "_exited", lambda role: role == "init" and c.init_exited)
    monkeypatch.setattr(observer, "_actors", lambda: frozenset(reconcile.ROLES))
    # Original journal/ledger/progress and preservation checks remain real.
    # Only file collection is a boundary substitute; no WAV evidence is claimed.
    c.file_bad = False
    c.file_reads = []
    collected = launch.binding.protected.Collected(
        launch.binding.protected.Files(
            s.plan.candidate.contract.sha256,
            "retained" if preserved else "pristine",
            "9" * 64 if preserved else s.plan.candidate.contract.baseline_sha256,
            s.run.pins.generation if preserved else None,
        )
    )

    def collect(collector):
        assert collector.stored is s.projected.host
        c.file_reads.append(True)
        if c.file_bad:
            raise ValueError("PRIVATE changed retained files")
        return collected

    if preserved:

        def retained(collector, expected, *, previous):
            assert expected is s.ledger.state.expected and previous is None
            return collect(collector)

        monkeypatch.setattr(launch.binding.protected.Collector, "retained", retained)
    else:
        monkeypatch.setattr(launch.binding.protected.Collector, "pristine", collect)
    return c


@pytest.mark.parametrize("close_lost", [False, True])
def test_original_driver_preserves_abandoned_recording_and_restores_without_success(
    driver_case, monkeypatch, close_lost
):
    s = driver_case
    assert (
        driver.run(
            s, monkeypatch, lambda: prepare_preservation(s, monkeypatch, close_lost=close_lost)
        ).phase
        == "complete"
    )
    p = s.driver.recording
    assert type(p.reader) is r.begin.worker_exit.reconcile.Preserved
    assert p.abandoned and p.reader.recovery_attempted and p.reader.closed
    assert s.cycle.created == [platform.h.CONTROL["starting_normal"]]
    assert s.cycle.native_calls == ["e" * 64] and s.cycle.file_reads
    assert s.journal.machine.state.recording_outcome == "unconfirmed"
    assert s.journal.machine.state.files_stage == "retained"
    assert p.reader.collected.artifact is None and s.ledger.state.acknowledgment is None
    assert not p.finish_attempted and not s.finish_trace.count("completion")
    assert s.service.closed and s.run.closed and p.operator.closed
    driver.assert_owners(s)


@pytest.mark.parametrize("role", ["worker", "init"])
def test_preserved_driver_requires_both_exit_boundaries_before_recovery(
    driver_case, monkeypatch, role
):
    s = driver_case

    def action():
        c = prepare_preservation(s, monkeypatch)
        setattr(c, role + "_exited", False)

    def later(seconds):
        assert not s.cycle.created and s.driver.recording.reader is None
        assert not s.driver.recording.recovery_attempted
        driver.expire(s, monkeypatch)

    assert driver.run(s, monkeypatch, action, later=later).phase == "review"
    assert not s.cycle.created and not s.cycle.native_calls and not s.cycle.file_reads
    assert s.journal.machine.state.recording_outcome == "unconfirmed"
    assert s.ledger.state.closed and s.ledger.state.acknowledgment is None
    driver.assert_owners(s)


@pytest.mark.parametrize("after_start", [False, True])
def test_preserved_driver_changed_files_fail_closed_without_finalized_fallback(
    driver_case, monkeypatch, after_start
):
    s = driver_case

    def action():
        prepare_preservation(s, monkeypatch).file_bad = not after_start

    def later(seconds):
        if after_start and not s.cycle.waits:
            s.cycle.file_bad = True
            s.cycle.wait(seconds)
        else:
            driver.expire(s, monkeypatch)

    assert driver.run(s, monkeypatch, action, later=later).phase == "review"
    assert len(s.cycle.created) == int(after_start)
    # The restored-host bracket may probe normal health before collecting
    # retained files. A healthy probe cannot override the failed file result.
    assert s.cycle.native_calls == (["e" * 64] if after_start else [])
    assert s.driver.recording.reader.failed and not s.driver.recording.finish_attempted
    assert s.journal.machine.state.recording_outcome == "unconfirmed"
    assert s.journal.machine.state.reason != "restored"
    assert s.ledger.state.acknowledgment is None
    driver.assert_owners(s)


def test_preserved_driver_cannot_adopt_an_unacknowledged_progress_file(driver_case, monkeypatch):
    s = driver_case

    def action():
        prepare_preservation(s, monkeypatch)
        path = s.plan.root / "recording-progress/0000.json"
        path.write_bytes(b"PRIVATE unacknowledged progress")
        path.chmod(0o600)

    assert (
        driver.run(s, monkeypatch, action, later=lambda _: driver.expire(s, monkeypatch)).phase
        == "review"
    )
    assert s.driver.recording.uncertain and s.driver.recording.reader is None
    assert not s.cycle.created and not s.cycle.file_reads
    assert s.ledger.state.tip is None and s.ledger.state.acknowledgment is None
    assert (s.plan.root / "recording-progress/0000.json").exists()
    driver.assert_owners(s)
