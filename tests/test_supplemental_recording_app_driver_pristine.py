"""Real no-recording App-driver cancellation/recovery with synthetic I/O facts.

Original NativePhase, NeverAuthorized reader, session, ledger and journal remain
real. The imported composition fixture documents Startup/platform/native seams.
Neither pristine files nor a lost transport-close reply substitute for exits.
"""

import pytest

from . import test_supplemental_recording_app_driver_preservation as failure

driver, r, platform = failure.driver, failure.r, failure.platform
candidate, app, native, launch_case, driver_case = (
    failure.candidate,
    failure.app,
    failure.native,
    failure.launch_case,
    failure.driver_case,
)
layout, image_umask, supervised, image, configured = (
    failure.layout,
    failure.image_umask,
    failure.supervised,
    failure.image,
    failure.configured,
)
pytestmark = pytest.mark.parametrize("candidate", ["app_native"], indirect=True)


def prepare(s, monkeypatch, *, close_lost=False):
    s.transitions.append("cancel")
    s.close_error = close_lost
    assert s.driver.cancel_native() is not close_lost
    assert s.driver.recording is None and not s.driver.recording_attempted
    assert s.ledger.state.count == 1 and not s.ledger.state.closed
    return failure.setup_exit_platform(s, monkeypatch, s.driver.native, preserved=False)


@pytest.mark.parametrize("close_lost", [False, True])
def test_original_driver_cancel_restores_without_ever_authorizing_recording(
    driver_case, monkeypatch, close_lost
):
    s = driver_case
    assert (
        driver.run(
            s, monkeypatch, lambda: prepare(s, monkeypatch, close_lost=close_lost), record=False
        ).phase
        == "complete"
    )
    p = s.driver.native
    assert type(p.reader) is r.begin.worker_exit.reconcile.NeverAuthorized
    assert p.reader.closed and p.operator.closed and s.run.closed
    assert s.cycle.created == [platform.h.CONTROL["starting_normal"]]
    assert s.cycle.native_calls == ["e" * 64] and s.cycle.file_reads
    assert s.journal.machine.state.recording_outcome == "not_attempted"
    assert s.journal.machine.state.files_stage == "pristine"
    assert s.journal.machine.state.authorization_generation is None
    assert p.reader.collected.artifact is p.reader.collected.files.generation is None
    assert s.driver.recording is s.run.begin_owner is None
    assert s.ledger.state.count == 1 and not s.ledger.state.closed
    driver.assert_owners(s)


@pytest.mark.parametrize("role", ["worker", "init"])
def test_cancel_driver_cannot_infer_exits_from_pristine_files(driver_case, monkeypatch, role):
    s = driver_case

    def action():
        c = prepare(s, monkeypatch)
        setattr(c, role + "_exited", False)

    def later(seconds):
        assert not s.cycle.created and s.driver.native.reader is None
        assert not s.driver.native.recovery_attempted
        driver.expire(s, monkeypatch)

    assert driver.run(s, monkeypatch, action, later=later, record=False).phase == "review"
    assert not s.cycle.file_reads and not s.cycle.native_calls
    assert s.journal.machine.state.recording_outcome == "not_attempted"
    assert s.ledger.state.count == 1
    driver.assert_owners(s)


def test_cancel_driver_pristine_read_failure_is_sticky_without_abandonment(
    driver_case, monkeypatch
):
    s = driver_case

    def action():
        prepare(s, monkeypatch).file_bad = True

    assert (
        driver.run(
            s, monkeypatch, action, later=lambda _: driver.expire(s, monkeypatch), record=False
        ).phase
        == "review"
    )
    assert not s.cycle.created and not s.cycle.native_calls
    assert s.driver.native.reader.failed and s.driver.recording is None
    assert s.journal.machine.state.recording_outcome == "not_attempted"
    assert s.ledger.state.count == 1 and not s.ledger.state.closed
    driver.assert_owners(s)
