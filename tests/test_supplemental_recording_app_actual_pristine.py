"""Actual original service cancellation and pristine recovery, without recording.

Owned normal/candidate and native worker exits and file reads are real. Initial
CLI/App metadata and restored health are synthetic. Independent peer native
custody is supplied only by the explicit observer factory; installed supervision
is NOT supplied by this test. No live devices.
The shared helper also serves the explicitly abandoned-recording tests; that
route can also select its separately owned observer factory before the first action.
"""

import wave
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_app_actual_dispatch as dispatch

service, recovery = dispatch.service, dispatch.recovery
actual, driver, m, launch = service.actual, service.driver, service.m, service.launch
bwrap, staged, mapped = service.bwrap, service.staged, service.mapped
layout, image_umask, supervised = service.layout, service.image_umask, service.supervised
image, configured = service.image, service.configured
candidate, app, native = service.candidate, service.app, service.native
launch_case, driver_case, dispatched = (
    dispatch.launch_case,
    dispatch.driver_case,
    dispatch.dispatched,
)
pytestmark = service.pytestmark


@pytest.mark.parametrize("fault", [None, "live_init", "new_file"])
def test_original_service_recovers_pristine_files_after_actual_cancel_and_owned_exits(
    dispatched, mapped, staged, monkeypatch, fault
):
    run_closed(dispatched, mapped, staged, monkeypatch, fault=fault)


def run_closed(
    s, mapped, staged, monkeypatch, *, fault=None, preserved=False, observer_factory=None
):
    from . import test_supplemental_recording_app_driver_dispatch as initial

    actual.route_recordings(s, mapped, monkeypatch)
    with actual.actual.native_engine(
        s,
        mapped,
        staged,
        None,
        monkeypatch,
        controller=True,
        operator_capture=True,
        recording=preserved,
        fault="lost_completed" if preserved else None,
    ) as io:
        audit = launch.engine.Endpoint()
        s.endpoint = io.endpoint
        waits, attempts, errors = [], [], []

        def advance(seconds):
            assert seconds == 0.25 and len(waits) < 8
            phase = s.journal.machine.state.phase
            waits.append(phase)
            if attempts:
                if errors:
                    raise errors[0]
                if fault is not None:
                    assert not s.cycle.created and not s.cycle.native_calls
                    if fault == "live_init":
                        owner = s.driver.recording if preserved else s.driver.native
                        assert not s.witness.exited() and owner.reader is None
                        assert not owner.recovery_attempted
                    driver.expire(s, monkeypatch)
                    return
                owner = s.driver.recording if preserved else s.driver.native
                assert not owner.uncertain, "Original failure route lost exit evidence"
                s.cycle.wait(seconds)
                return
            if phase != "candidate_idle":
                return
            attempts.append(True)
            s.witness = s.session.processes.witnesses[m.base.CANDIDATE]
            service.start_native(s, mapped, io, audit)
            # Original failure readers require the caller-prepared private
            # progress directory even when no active sample was requested.
            (s.plan.root / "recording-progress").mkdir(mode=0o700)
            if preserved:
                assert s.driver.start_recording()
                for index in range(8):
                    mapped.rtsp.packets.sendto(
                        actual.actual.io.child.construction.make_rtp(
                            bytes(range(160)), sequence=100 + index, timestamp=1000 + index * 160
                        ),
                        mapped.rtsp.target,
                    )
                assert s.driver.abandon_recording()
                assert s.driver.recording.abandoned and not s.driver.recording.finish_attempted
                assert s.ledger.state.closed and s.ledger.state.acknowledgment is None
                read_preserved = s.driver.recording._preserved_reader

                def observed_preserved():
                    try:
                        return read_preserved()
                    except BaseException as error:
                        errors.append(error)
                        raise

                monkeypatch.setattr(s.driver.recording, "_preserved_reader", observed_preserved)
            else:
                assert s.driver.cancel_native()
                if observer_factory is not None:
                    assert s.observer.exchanges == ["dispatch"] * 4 + ["candidate", "native"]
            # Real owned transport EOF, not substituted worker-exit methods.
            io.worker.join(timeout=8)
            assert not io.worker.is_alive() and not io.errors
            assert io.operator.returncode == (0 if preserved else 70)
            assert io.begun is preserved
            if not preserved:
                assert s.driver.recording is None and not s.driver.recording_attempted
                assert s.ledger.state.count == 1 and not s.ledger.state.closed
            # Bounded terminal inspections; every request must be the same exec.
            io.handlers.extend([io.exited_metadata] * 24)
            native_phase = s.driver.native
            c = s.cycle = SimpleNamespace(s=s, host=None, operator=native_phase.operator)
            s.cleanup.enter_context(
                contextmanager(driver.platform.setup_cycling)(
                    c,
                    monkeypatch,
                    read_clock=s.service._now,
                    session=s.session,
                    cli_container=s.transfer_io.cli,
                )
            )
            monkeypatch.setattr(
                s.session.processes,
                "reconcile",
                m.begin.TrackedProcesses.reconcile.__get__(s.session.processes),
            )
            if fault != "live_init":
                s.child.stdin.close()
                assert s.child.wait(timeout=3) == 0 and s.witness.exited()
                assert native_phase.operator._actors() == frozenset(
                    ("init", "guardian", "native", "watchdog")
                )
                c.candidate = m.base.App(s.plan.candidate.pin, "stopped")
            if fault == "new_file":
                (mapped.recordings / "unexpected-fixture.wav").write_bytes(
                    b"preserve this evidence"
                )

        def wait(seconds):
            try:
                return advance(seconds)
            except BaseException as error:
                errors.append(error)
                raise

        resources = ExitStack()
        try:
            observer = None
            if observer_factory is not None:
                observer = resources.enter_context(observer_factory(s, io, monkeypatch))
            initial.request(s)
            try:
                result = s.driver.run(wait)
            except m.operator.UnconfirmedOperator:
                if errors:
                    raise errors[0] from None
                raise
            assert not errors and result.phase == ("complete" if fault is None else "review")
            assert attempts == [True]
            phase = s.driver.recording if preserved else s.driver.native
            if fault is None:
                assert type(phase.reader) is (
                    m.begin.worker_exit.reconcile.Preserved
                    if preserved
                    else m.begin.worker_exit.reconcile.NeverAuthorized
                )
                assert phase.reader.closed and phase.reader.collected.artifact is None
                assert s.cycle.created == [driver.platform.h.CONTROL["starting_normal"]]
                assert s.cycle.native_calls == ["e" * 64]
            else:
                assert not s.cycle.created and not s.cycle.native_calls
                assert s.journal.machine.state.reason != "restored"
                if fault == "new_file":
                    assert phase.reader.failed and phase.reader.closed
                    assert (
                        mapped.recordings / "unexpected-fixture.wav"
                    ).read_bytes() == b"preserve this evidence"
            assert phase.operator.closed and s.run.closed
            assert s.transfer_io.started == ["1" * 64, "2" * 64]
            assert s.journal.machine.state.recording_outcome == (
                "unconfirmed" if preserved else "not_attempted"
            )
            if preserved:
                assert s.journal.machine.state.authorization_generation == s.run.pins.generation
                assert s.ledger.state.closed and s.ledger.state.acknowledgment is None
                assert phase.completion is None and not phase.finish_attempted
                recordings = list(mapped.recordings.glob("sdsctl-acceptance-*.wav"))
                assert len(recordings) == 1
                with wave.open(str(recordings[0]), "rb") as saved:
                    assert saved.getnframes() == 1280
                if fault is None:
                    assert s.journal.machine.state.files_stage == "retained"
            else:
                assert s.journal.machine.state.files_stage == "pristine"
                assert s.journal.machine.state.authorization_generation is None
                assert s.driver.recording is s.run.begin_owner is None
                assert s.ledger.state.count == 1 and not s.ledger.state.closed
            assert 6 < len(io.requests) <= 30
            if not preserved:
                assert not tuple((s.case_root / "receipts").iterdir())
            assert not mapped.scanner.reads
            assert (mapped.recordings / "previous.wav").read_bytes() == b"old evidence unchanged"
            if fault != "new_file" and not preserved:
                assert list(mapped.recordings.iterdir()) == [mapped.recordings / "previous.wav"]
            if observer is not None:
                observer.verify(fault=fault)
            driver.assert_owners(s)
        finally:
            resources.close()
            audit.close()
