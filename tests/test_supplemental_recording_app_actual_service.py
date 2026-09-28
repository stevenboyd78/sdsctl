"""Original accepted service loop reaches actual native Ready/recording then expires.

Native launch, parser, source/profile/baseline checks, original service/session,
clock, journal and independent Operator handles are real. Initial App handoff,
idle provenance, Engine platform and host/probe observations are synthetic.
Only the recording variant writes its owned fixture media directory. Independent
Link/CLI observer exchange, recovery success or live installed acceptance is not
inferred. Only disposable local peers/processes run.
"""

import hashlib
import time
import wave
from contextlib import contextmanager

import pytest

from . import test_supplemental_recording_app_actual_startup as accepted
from ._supplemental_failure_diagnostics import failure_locations

actual = accepted.actual
startup = accepted.startup
driver = startup.driver
launch, m = driver.launch, driver.m
bwrap, staged, mapped = accepted.bwrap, accepted.staged, accepted.mapped
layout, image_umask, supervised = accepted.layout, accepted.image_umask, accepted.supervised
image, configured = accepted.image, accepted.configured
candidate, app, native, launch_case = (
    accepted.candidate,
    accepted.app,
    accepted.native,
    accepted.launch_case,
)
pytestmark = accepted.pytestmark


@pytest.fixture
def service_case(launch_case, tmp_path, monkeypatch, request):
    from supplemental_recording_app_begin import AppStart

    # RecordingPhase intentionally consumes a failed begin and preserves an
    # uncertain outcome. Observe only its failure path BEFORE that exception
    # loses its traceback; later cleanup must not obscure the first refusal.
    # No tracing, extra success-path reads, changed cutoffs, or private values.
    reported = set()

    def observed_failure(original):
        def fail(owner, error):
            locations = failure_locations(error)
            if locations and locations not in reported and len(reported) < 4:
                reported.add(locations)
                print("Original native recording refusal locations (no values):\n" + locations)
            return original(owner, error)

        return fail

    for policy in (AppStart, m.begin.relayed.Relay):
        monkeypatch.setattr(policy, "_fail", observed_failure(policy._fail))
    original = (
        (launch.engine, "Endpoint", launch.engine.Endpoint),
        (launch.engine, "Client", launch.engine.Client),
        (launch.received.Ready, "__init__", launch.received.Ready.__init__),
        (launch.received.Ready, "check_before_begin", launch.received.Ready.check_before_begin),
        (launch.received.Ready, "close", launch.received.Ready.close),
        (launch.engine.namespace.Witness, "refresh", launch.engine.namespace.Witness.refresh),
        (
            m.begin.worker_exit.reconcile.Operator,
            "__init__",
            m.begin.worker_exit.reconcile.Operator.__init__,
        ),
    )
    with contextmanager(driver.driver_case.__wrapped__)(
        launch_case, tmp_path, monkeypatch, request
    ) as s:
        for owner, name, value in original:
            monkeypatch.setattr(owner, name, value)
        yield s


def start_native(s, mapped, io, audit):
    profile = dict(
        published=s.published,
        bridge_sha256=hashlib.sha256(s.bridge_raw).hexdigest(),
        image_environment_sha256=launch.runtime.environment(s.image_env),
        timezone=driver.candidates.env.TIMEZONE,
        hostname=driver.candidates.env.HOSTNAME,
        architecture="amd64",
    )
    s.candidate_owner = s.driver.prepare_candidate(**profile)
    s.host = s.bootstrap_host = s.candidate_owner.reader
    binding = launch.binding.Binding(
        s.projected, s.plan.candidate_runtime.source, s.plan.sha256, s.plan.boot
    )
    path = s.plan.root / "recording-ledger"
    path.mkdir(mode=0o700)
    s.ledger = io.ledger = launch.binding.Ledger(path, binding, now=time.monotonic())
    assert s.driver.start_native(
        s.ledger, io.endpoint, audit, specification=s.spec, profile_sha256=mapped.profile
    )
    run = s.run = s.driver.native.run
    assert io.run is run and run.ready is run.qualify.ready
    assert run.journal is s.service.journal and run.ready.clock is s.plan.original_clock
    # The accepted declaration decodes its own equal immutable window;
    # require the same original value, not Python identity across decode.
    assert launch.plans._same_plan_value(run.ready.clock, s.startup.clock.original)
    assert s.driver.native.operator.endpoint is audit
    assert len(io.requests) == 6 and not run.client.attachment.begun
    assert s.ledger.state.count == 1 and not mapped.scanner.reads


def record_and_finalize(s, mapped, io, *, lost_completion=False):
    assert s.driver.start_recording()
    phase = s.driver.recording
    assert s.driver.native.retired and phase.started
    assert phase.start_attempt is s.run.begin_owner
    assert phase.relay.ready is s.run.ready
    for index in range(8):
        mapped.rtsp.packets.sendto(
            actual.actual.io.child.construction.make_rtp(
                bytes(range(160)),
                sequence=100 + index,
                timestamp=1000 + index * 160,
            ),
            mapped.rtsp.target,
        )
    finished = s.driver.finish_recording()
    if lost_completion:
        assert not finished and phase.uncertain and phase.finish_attempted
        assert phase.reader is None and not s.ledger.state.closed
        assert phase.relay.phase == "unconfirmed" and len(io.requests) == 6
        assert not phase.recovery_attempted and not phase.abandon_attempted
        return
    assert finished
    assert phase.reader.phase == "published" and s.ledger.state.closed
    assert phase.completion.collected.artifact.samples == 1280
    assert s.journal.machine.state.operator_exit_sha256 is not None
    assert not phase.operator.result.init_exited and len(io.requests) == 9
    assert phase.reader.journal is s.service.journal
    assert not phase.recovery_attempted


@pytest.mark.parametrize("recording", [False, True, "lost_completed"])
def test_original_service_reaches_native_ready_then_hard_expiry_refuses_further_work(
    service_case, mapped, staged, monkeypatch, recording
):
    s = service_case
    original = s.startup.original, s.startup.clock, s.service.session, s.journal, s.plan.raw
    if recording:
        actual.route_recordings(s, mapped, monkeypatch)
    with actual.actual.native_engine(
        s,
        mapped,
        staged,
        None,
        monkeypatch,
        controller=True,
        operator_capture=True,
        recording=bool(recording),
        fault=recording if type(recording) is str else None,
    ) as io:
        if recording:
            io.handlers.extend([io.exited_metadata, io.exited_metadata])
        audit = launch.engine.Endpoint()
        s.endpoint = io.endpoint
        attempts, callback_errors = [], []

        def advance(seconds):
            assert seconds == 0.25 and not attempts
            attempts.append(True)
            start_native(s, mapped, io, audit)
            if recording:
                record_and_finalize(s, mapped, io, lost_completion=recording == "lost_completed")
            # Advance only the fixture clock: exercise the original service's
            # hard-deadline failure path, without a second writer or new lease.
            driver.expire(s, monkeypatch, hard=True)

        def wait(seconds):
            try:
                return advance(seconds)
            except BaseException as error:
                callback_errors.append(error)
                raise

        try:
            # Hard recovery expiry raises rather than inventing a final policy
            # result. The original writer closes its own lifetime in finally.
            with pytest.raises(m.operator.UnconfirmedOperator):
                s.driver.run(wait)
            assert not callback_errors, callback_errors
            assert len(attempts) == 1 and s.service.failed
            assert original == (
                s.startup.original,
                s.startup.clock,
                s.service.session,
                s.journal,
                s.plan.raw,
            )
            assert s.service.closed and s.driver.native.run.closed
            assert s.driver.native.operator.closed and not s.startup.clock.closed
            if recording:
                assert s.journal.machine.state.recording_outcome == "unconfirmed"
                assert (
                    s.journal.machine.state.authorization_generation == s.original.idle.generation
                )
                assert s.driver.recording_attempted and not s.driver.recording.recovery_attempted
                recordings = list(mapped.recordings.glob("sdsctl-acceptance-*.wav"))
                assert len(recordings) == 1
                with wave.open(str(recordings[0]), "rb") as saved:
                    assert saved.getnframes() == 1280
            else:
                assert s.journal.machine.state.recording_outcome == "not_attempted"
                assert s.journal.machine.state.authorization_generation is None
                assert not s.driver.recording_attempted
                assert s.ledger.state.count == 1
                assert not tuple((s.case_root / "receipts").iterdir())
                assert not mapped.scanner.reads
            assert not s.driver.native.recovery_attempted
            assert (mapped.recordings / "previous.wav").read_bytes() == b"old evidence unchanged"
        finally:
            audit.close()
