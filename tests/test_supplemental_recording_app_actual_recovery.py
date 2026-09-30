"""Original App service recovery after real native recording and owned init exit.

Native bytes, WAV, worker pidfds, candidate-init exit, original service/session
and journal are real. Recovery App/CLI metadata and restored health are explicitly
synthetic; this does not qualify installed recovery or independent Link custody.
Only disposable namespace paths and owned loopback peers/processes are used.
"""

from contextlib import ExitStack, contextmanager
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_app_actual_service as service

actual, driver, launch, m = service.actual, service.driver, service.launch, service.m
bwrap, staged, mapped = service.bwrap, service.staged, service.mapped
layout, image_umask, supervised = service.layout, service.image_umask, service.supervised
image, configured = service.image, service.configured
candidate, app, native, launch_case, service_case = (
    service.candidate,
    service.app,
    service.native,
    service.launch_case,
    service.service_case,
)
pytestmark = service.pytestmark


@pytest.mark.parametrize("fault", [None, "running_metadata", "new_file"])
def test_original_service_recovers_after_actual_recording_and_owned_init_exit(
    service_case, mapped, staged, monkeypatch, fault
):
    run_recovery(service_case, mapped, staged, monkeypatch, fault=fault)


def run_recovery(
    s,
    mapped,
    staged,
    monkeypatch,
    *,
    initial=False,
    fault=None,
    observer_factory=None,
    native_fault=None,
):
    actual.route_recordings(s, mapped, monkeypatch)
    with actual.actual.native_engine(
        s,
        mapped,
        staged,
        None,
        monkeypatch,
        controller=True,
        operator_capture=True,
        recording=True,
        fault=native_fault,
    ) as io:
        io.handlers.extend([io.exited_metadata, io.exited_metadata])
        audit = launch.engine.Endpoint()
        s.endpoint = io.endpoint
        actions, waits, failures, observations = [], [], [], []
        host_read = m.begin.FinalizedHost.read

        def observed_read(reader):
            try:
                return host_read(reader)
            except BaseException as error:
                observations.append(error)
                raise

        monkeypatch.setattr(m.begin.FinalizedHost, "read", observed_read)

        def advance(seconds):
            assert seconds == 0.25
            phase = s.journal.machine.state.phase
            waits.append(phase)
            assert len(waits) < 8, "Owned local recovery must settle without real waiting"
            if actions:
                if fault is not None:
                    assert not s.cycle.created and not s.cycle.native_calls
                    driver.expire(s, monkeypatch)
                    return
                recent = next(
                    item["event"]["observation"]
                    for item in reversed(s.journal.entries)
                    if item["event"]["kind"] == "observe"
                )
                assert recent["normal"]["state"] != "unknown", "Original normal exit uncertain"
                assert recent["candidate"]["state"] != "unknown", (
                    "Original candidate exit uncertain"
                )
                assert recent["jobs_idle"], "Original CLI executions uncertain"
                s.cycle.wait(seconds)
                return
            if initial and phase != "candidate_idle":
                return
            if initial:
                s.witness = s.session.processes.witnesses[m.base.CANDIDATE]
                assert s.session.processes.exit_confirmed(m.base.NORMAL)
            actions.append("record")
            service.start_native(s, mapped, io, audit)
            service.record_and_finalize(s, mapped, io)
            # Three original samples (including the pre-dispatch recheck)
            # bracket immutable files with inspections of the same terminal exec.
            io.handlers.extend(
                [io.exited_metadata]
                * {None: 6, "new_file": 1, "running_metadata": 2, "observer_exit": 4}[fault]
            )
            phase = s.driver.recording
            c = s.cycle = SimpleNamespace(s=s, host=phase.reader, operator=phase.operator)
            s.cleanup.enter_context(
                contextmanager(driver.platform.setup_cycling)(
                    c,
                    monkeypatch,
                    read_clock=s.service._now,
                    session=s.session,
                    cli_container=s.transfer_io.cli if initial else None,
                )
            )
            c.reader = phase.reader
            # Replace only the synthetic exit adapter with the original process
            # tracker over its already-held witness, never reopen numeric PIDs.
            monkeypatch.setattr(
                s.session.processes,
                "reconcile",
                m.begin.TrackedProcesses.reconcile.__get__(s.session.processes),
            )
            assert not phase.operator._exited("init")
            s.child.stdin.close()  # EOF only to this fixture's harmless owned init.
            assert s.child.wait(timeout=3) == 0 and s.witness.exited()
            assert phase.operator._exited("init")
            # Metadata must agree with the separately retained real init exit.
            # A running App plus an exited original init is NOT safe to stop.
            if fault != "running_metadata":
                c.candidate = m.base.App(s.plan.candidate.pin, "stopped")
            if fault == "new_file":
                (mapped.recordings / "unexpected-fixture.wav").write_bytes(b"unexpected output")

        def wait(seconds):
            try:
                return advance(seconds)
            except BaseException as error:
                failures.append(error)
                raise

        resources = ExitStack()
        try:
            observer = None
            if observer_factory is not None:
                assert initial
                observer = resources.enter_context(observer_factory(s, io, monkeypatch))
            if initial:
                from . import test_supplemental_recording_app_driver_dispatch as dispatch

                dispatch.request(s)
            try:
                result = s.driver.run(wait)
            except m.operator.UnconfirmedOperator:
                if observations:
                    raise observations[0] from None
                if failures:
                    raise failures[0] from None
                raise
            assert result.phase == ("complete" if fault is None else "review")
            assert actions == ["record"]
            if fault is None:
                assert s.cycle.created == [driver.platform.h.CONTROL["starting_normal"]]
                assert len(s.cycle.started) == len(set(s.cycle.started)) == 1
                assert s.cycle.native_calls == ["e" * 64]
                assert s.journal.machine.state.recording_outcome == "verified"
                assert not observations
            else:
                assert not s.cycle.created and not s.cycle.started and not s.cycle.native_calls
                assert s.journal.machine.state.reason != "restored"
                assert bool(observations) == (fault == "new_file")
            assert s.driver.recording.recovery_attempted
            assert s.service.closed and s.driver.recording.reader.closed
            if observer is not None:
                observer.verify(fault=fault)
            if initial:
                assert waits[:3] == ["stopping_normal", "starting_candidate", "candidate_idle"]
                assert s.transfer_io.started == ["1" * 64, "2" * 64]
                assert len(s.journal.machine.state.completed_executions) == (
                    3 if fault is None else 2
                )
            driver.assert_owners(s)
            assert (
                len(io.requests)
                == {
                    None: 15,
                    "new_file": 10,
                    "running_metadata": 11,
                    "observer_exit": 13,
                }[fault]
            )
            assert (mapped.recordings / "previous.wav").read_bytes() == b"old evidence unchanged"
        finally:
            resources.close()
            audit.close()


def test_native_start_refusal_preserves_child_source_locations(
    service_case, mapped, staged, monkeypatch
):
    with pytest.raises(AssertionError) as caught:
        run_recovery(
            service_case,
            mapped,
            staged,
            monkeypatch,
            native_fault="changed_baseline",
        )
    notes = getattr(caught.value, "__notes__", [])
    report = "\n".join(note for note in notes if note.startswith("Native relay fixture outcome:"))
    assert "source_locations" in report
    assert "scripts/supplemental_recording_owner.py:" in report
    assert str(mapped.recordings) not in report and "changed before" not in report
