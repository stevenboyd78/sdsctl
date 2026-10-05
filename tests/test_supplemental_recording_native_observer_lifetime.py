"""Original observer custody across an actual isolated native recording.

Native Ready, recorder/PCM, return bytes, files, clocks and process handles are
real. Engine/container/root metadata is synthetic, as in relay_native. These
tests retain a distinct observer endpoint and duplicate handles in the same
process or a separate actual observer child. Private fixture messages are NOT
the service Link; no qualified AppService/recovery lifetime or installed
scanner acceptance is claimed.
"""

import json
import os
import select
import signal
import subprocess
import sys
import time
import wave
from contextlib import ExitStack, contextmanager
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_relay_native as native
from . import test_supplemental_recording_service_native_custody as custody_tests

m, apps = custody_tests.m, custody_tests.app_tests
plans = custody_tests.plan_tests.m
tree, configured, cached, prepared, staged = (
    native.tree,
    native.configured,
    native.cached,
    native.prepared,
    native.staged,
)
pytestmark = apps.pytestmark


def fixture_deadlines(original, maximum):
    issued = original.boottime_ns / plans.clock.NS
    return dict(
        issued_at=issued,
        ready_by=issued + 8,
        # Each host deadline is conservatively converted independently. Exact
        # equality at ready + maximum + 3 can lose one float ULP when the two
        # values straddle an exponent boundary. Reserve margin in the ORIGINAL
        # fixture plan, not by extending an accepted lease or relaxing Ready.
        stop_by=issued + 8 + maximum + 4,
        recover_by=issued + 1500,
    )


def test_fixture_original_budget_retains_native_grace_across_float_boundary():
    original = plans.clock.Window("a" * 32, (4, 100), 3915106974238, 3915106974619, 3915106974819)
    limits = fixture_deadlines(original, 180)
    ready = original.native_deadline(limits["ready_by"])
    # Deterministically reproduce the CI-only former zero-margin fixture.
    exact_host_stop = limits["ready_by"] + 180 + 3
    assert ready + 180 + 3 > original.native_deadline(exact_host_stop)
    assert ready + 180 + 3 < original.native_deadline(limits["stop_by"])
    assert limits["stop_by"] == exact_host_stop + 1
    assert limits["ready_by"] == limits["issued_at"] + 8
    assert limits["recover_by"] == limits["issued_at"] + 1500


def record(value):
    if type(value) is dict:
        return SimpleNamespace(**{key: record(item) for key, item in value.items()})
    return value


class ProcessObserver:
    """Test-only control for a distinct process; never a production transport."""

    def __init__(self, state):
        self.child = subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-B",
                str(Path(__file__).with_name("_native_observer_process_fixture.py")),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        self.binding = None
        try:
            projection = state.recording.pins.host.projection
            value = self.exchange(
                dict(
                    plan=json.loads(state.plan.raw),
                    root=str(state.plan.root),
                    socket=str(m.engine.SOCKET),
                    identities=state.identities,
                    helper=asdict(state.target),
                    native_media=str(projection.native.baseline.root.parent),
                    host_manifest=m.dispatch.binding.projection._validated(projection.host).hex(),
                    native_manifest=projection.native_manifest.hex(),
                    container_namespaces=native.engine.namespaces.NS[:3],
                )
            )
            assert value["owner"] == self.child.pid != os.getpid()
        except BaseException:
            self.shutdown()
            raise

    def exchange(self, value):
        self.child.stdin.write(json.dumps(value, separators=(",", ":")).encode() + b"\n")
        result, end = bytearray(), time.monotonic() + 4
        while not result.endswith(b"\n"):
            assert select.select([self.child.stdout], [], [], max(0, end - time.monotonic()))[0]
            data = os.read(self.child.stdout.fileno(), 65536)
            if not data:
                self.child.wait(timeout=1)
                raise AssertionError(self.child.stderr.read(8192).decode())
            result.extend(data)
            assert len(result) <= 262144
        return json.loads(result)

    def capture_candidate(self, generation):
        return self.exchange(dict(operation="candidate", generation=generation))

    def capture_native(self, pins, reports):
        result = self.exchange(
            dict(operation="native", launch_sha256=pins.command.plan_sha256, reports=reports)
        )
        binding = record(result["binding"])
        binding.actors = tuple(
            m.engine.namespace.Actor(
                **(actor | {"namespaces": tuple(tuple(pair) for pair in actor["namespaces"])})
            )
            for actor in result["binding"]["actors"]
        )
        self.binding, self._retained = binding, tuple(result["retained"])
        return self

    def poll(self):
        status = record(self.exchange(dict(operation="poll")))
        if self.binding is not None:
            status.exited = frozenset(status.exited)
        return status

    def close(self):
        assert self.exchange(dict(operation="close_endpoint")) == {"closed": True}

    def shutdown(self):
        if self.child.poll() is None:
            self.child.stdin.close()
            try:
                self.child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.child.kill()  # Only this exact owned, unreaped test process.
                self.child.wait(timeout=3)
        self.child.stdout.close()
        self.child.stderr.close()
        self.child.stdin.close()

    def finish(self):
        try:
            assert self.exchange(dict(operation="close")) == {"closed": True}
            assert self.child.wait(timeout=3) == 0
        finally:
            self.shutdown()


@contextmanager
def joined(staged, prepared, tmp_path, monkeypatch, fault, *, separate=False):
    """Create every original clock/plan/init binding before native publication."""
    with apps.child() as helper, apps.child() as normal, ExitStack() as cleanup:
        state = SimpleNamespace(helper=helper, normal=normal)

        def configure(projection, original, init, candidate):
            _, raw = custody_tests.plan_tests.projected_plan(projection)
            values = {
                m.apps.NORMAL: apps.metadata(
                    normal, m.apps.NORMAL, raw["normal"]["image"], "b" * 64
                ),
                m.apps.CANDIDATE: apps.metadata(
                    candidate, m.apps.CANDIDATE, raw["candidate"]["image"], init.container_id
                ),
            }
            generations = {
                slug: m.apps.platform.generation(value, name="app_" + slug, image=value["Image"])
                for slug, value in values.items()
            }
            raw.update(
                boot=original.boot,
                original_clock=asdict(original) | {"namespace": list(original.namespace)},
                normal_generation=generations[m.apps.NORMAL],
                deadlines=fixture_deadlines(
                    original, projection.native.contract.maximum_recording_seconds
                ),
            )
            state.plan = plans.decode(raw)
            monkeypatch.setattr(plans.Plan, "root", property(lambda self: tmp_path))
            identities = {
                normal.pid: "b" * 64,
                candidate.pid: init.container_id,
                helper.pid: "d" * 64,
            }

            def identity(pid, cid):
                assert identities[pid] == cid
                return m.apps.processes.process_identity(
                    pid,
                    cid,
                    Path(f"/proc/{pid}/stat").read_text(),
                    f"0::/system.slice/docker-{cid}.scope\n",
                )

            monkeypatch.setattr(m.apps.processes, "read_identity", identity)
            target = identity(helper.pid, identities[helper.pid])
            state.target, state.identities = target, identities
            witness = m.apps.processes.ProcessWitness(target)
            cleanup.callback(witness.close)
            original_helper = SimpleNamespace(plan=state.plan, identity=target, witness=witness)
            state.link = cleanup.enter_context(
                apps.deadline_tests.original_link(original_helper, monkeypatch)
            )
            state.watch = m.deadlines.DeadlineWatch(state.link)
            cleanup.callback(state.watch.close)
            state.values, state.generations = values, generations
            return state.plan, generations[m.apps.CANDIDATE]

        with native.setup(
            staged, prepared, tmp_path, monkeypatch, configure=configure
        ) as recording:
            state.recording = recording
            original_handlers = native.handlers(recording, staged, prepared, monkeypatch, fault)

            def native_inspection(peer, request):
                assert request[0] == f"GET /v1.47/exec/{native.engine.attached.EXEC}/json HTTP/1.1"
                assert request[2] is None
                value = native.engine.intents.metadata(recording)
                value.update(Running=True, Pid=recording.operator.pid)
                peer.sendall(native.engine.reply(value))

            handlers = [
                *[native.engine.reply(state.values[m.apps.NORMAL])] * 2,
                *[native.engine.reply(state.values[m.apps.CANDIDATE])] * 2,
                *original_handlers[:5],
                native_inspection,
                native_inspection,
                *original_handlers[5:],
            ]
            with (
                native.engine.engine(recording, monkeypatch, handlers) as (client, requests),
                ExitStack() as observers,
            ):
                if separate:
                    custody = endpoint = ProcessObserver(state)
                    observers.callback(custody.finish)
                else:
                    endpoint = m.engine.Endpoint()
                    observers.callback(endpoint.close)
                    endpoint.connect(deadline=time.monotonic() + 1).close()
                    custody = m.apps.AppCustody(state.watch, endpoint)
                    observers.callback(custody.close)
                normal.stdin.close()
                assert normal.wait(timeout=3) == 0
                custody.capture_candidate(state.generations[m.apps.CANDIDATE])
                state.custody, state.endpoint = custody, endpoint
                state.client, state.requests, state.observers = client, requests, observers
                yield state


@pytest.mark.parametrize("fault", [None, "lost_completed", "lost_exited", "engine_exit"])
@pytest.mark.parametrize("separate", [False, True])
def test_actual_ready_workers_remain_independently_observed_through_recording_exit(
    staged, prepared, tmp_path, monkeypatch, fault, separate
):
    with joined(staged, prepared, tmp_path, monkeypatch, fault, separate=separate) as case:
        state, client = case.recording, case.client
        assert case.endpoint is not client.endpoint
        assert case.custody.poll().normal_exited and not case.custody.poll().candidate_exited
        client.create()
        client.attach(finish_by=case.plan.lease["stop_by"])
        state.ready = native.ready_module.Ready(
            client, profile_sha256=state.profile, original_clock=state.original_clock
        )
        reports = {
            role: {key: state.envelopes[0][role][key] for key in ("pid", "start_ticks")}
            | {"uid": 0, "gid": 0}
            for role in m.ROLES[1:]
        }
        if separate:
            observer = case.custody.capture_native(state.pins, reports)
        else:
            observer = m.NativeCustody(
                case.custody, state.pins, reports, deadline=state.ready.ready_by
            )
            case.observers.callback(observer.close)
        assert observer.binding.actors == state.ready.processes.actors
        assert observer.poll().exited == frozenset()
        assert state.ledger.state.count == 1 and state.relay is None
        assert [item["phase"] for item in state.envelopes] == ["ready"]
        assert len(case.requests) == 11
        original = tuple(observer._retained)
        if not separate:
            assert not {fd for _, fd, _ in original}.intersection(
                state.ready.processes.handles.values()
            )
        now = time.monotonic()
        state.ledger.start_intent(
            now=now,
            generation=state.pins.generation,
            authorization_sha256="1" * 64,
            start_by=state.ready.ready_by,
            finish_by=min(now + 20, case.plan.lease["stop_by"]),
        )
        state.relay = native.returned.Relay(state.ledger, state.ready)
        state.relay.started()
        for index in range(8):
            state.rtsp.packets.sendto(
                native.child.construction.make_rtp(
                    bytes(range(160)), sequence=100 + index, timestamp=1000 + index * 160
                ),
                state.rtsp.target,
            )
        if fault == "lost_completed":
            with pytest.raises(native.returned.UnconfirmedRelay):
                state.relay.completed()
            assert not state.ledger.state.closed
        else:
            result = state.relay.completed()
            assert result.collected.artifact.samples == 1280
            if fault in ("lost_exited", "engine_exit"):
                with pytest.raises(native.m.UnconfirmedExit):
                    native.m.collect(state.relay)
                assert state.relay.phase == "unconfirmed"
            else:
                native.m.collect(state.relay)
                assert state.relay.phase == "exited"
        assert state.operator.wait(timeout=5) == 0
        state.worker.join(5)
        assert not state.worker.is_alive()
        # Original observer handles outlive both native return transport and
        # Ready ownership. Actual exits do NOT repair a missing success return.
        state.ready.close()
        case.endpoint.close()
        before = len(case.requests)
        status = observer.poll()
        assert status.exited == frozenset(m.ROLES[1:])
        assert not status.apps.candidate_exited and not status.apps.deadline.helper_exited
        case.helper.stdin.close()
        assert case.helper.wait(timeout=3) == 0
        assert observer.poll().apps.deadline.helper_exited
        assert observer.poll().exited == frozenset(m.ROLES[1:])
        assert len(case.requests) == before and tuple(observer._retained) == original
        assert [item["phase"] for item in state.envelopes] == [
            "ready",
            "started",
            "completed",
            "exited",
        ]
        recordings = list(state.stored.baseline.root.glob("sdsctl-acceptance-*.wav"))
        assert len(recordings) == 1
        with wave.open(str(recordings[0]), "rb") as saved:
            assert saved.getnframes() == 1280
        assert (
            state.stored.baseline.root / "older/old.wav"
        ).read_bytes() == b"old evidence unchanged"
        if fault == "lost_completed":
            assert state.ledger.state.acknowledgment is None
        assert not hasattr(observer, "publish") and not hasattr(observer, "begin")


@pytest.mark.parametrize("separate", [False, True])
def test_helper_loss_before_begin_keeps_native_exit_separate_and_creates_no_recording(
    staged, prepared, tmp_path, monkeypatch, separate
):
    with joined(staged, prepared, tmp_path, monkeypatch, "no_begin", separate=separate) as case:
        state, client = case.recording, case.client
        client.create()
        client.attach(finish_by=case.plan.lease["stop_by"])
        state.ready = native.ready_module.Ready(
            client, profile_sha256=state.profile, original_clock=state.original_clock
        )
        reports = {
            role: {key: state.envelopes[0][role][key] for key in ("pid", "start_ticks")}
            | {"uid": 0, "gid": 0}
            for role in m.ROLES[1:]
        }
        if separate:
            observer = case.custody.capture_native(state.pins, reports)
        else:
            observer = m.NativeCustody(case.custody, state.pins, reports)
            case.observers.callback(observer.close)
        assert observer.binding.actors == state.ready.processes.actors
        # A stopped actual recorder is still alive, never an exit inferred
        # from lack of progress. Signal only this retained, owned test worker.
        native_fd = state.ready.processes.handles["native"]
        native_pid = observer.binding.actors[2].host_pid
        signal.pidfd_send_signal(native_fd, signal.SIGSTOP)
        try:
            limit = time.monotonic() + 1
            while (
                Path(f"/proc/{native_pid}/stat").read_text().rpartition(") ")[2].split()[0] != "T"
            ):
                assert time.monotonic() < limit
                time.sleep(0.005)
            assert observer.poll().exited == frozenset()
        finally:
            signal.pidfd_send_signal(native_fd, signal.SIGCONT)
        case.helper.stdin.close()
        assert case.helper.wait(timeout=3) == 0
        status = observer.poll()
        assert status.apps.deadline.helper_exited and status.exited == frozenset()
        assert not status.apps.candidate_exited
        # Test owner explicitly withdraws; custody itself never sends a begin,
        # closes the writer's transport or claims a worker exit from helper death.
        state.ready.close()
        assert state.operator.wait(timeout=5) == 70
        state.worker.join(5)
        assert not state.worker.is_alive()
        case.endpoint.close()
        before = len(case.requests)
        status = observer.poll()
        assert status.exited == frozenset(m.ROLES[1:])
        assert status.apps.deadline.helper_exited and not status.apps.candidate_exited
        assert len(case.requests) == before == 11
        assert state.ledger.state.count == 1 and state.ledger.state.expected is None
        assert state.ledger.state.acknowledgment is None
        assert [item["phase"] for item in state.envelopes] == ["ready"]
        assert not list(state.stored.baseline.root.glob("sdsctl-acceptance-*.wav"))
        assert (
            state.stored.baseline.root / "older/old.wav"
        ).read_bytes() == b"old evidence unchanged"
