"""Real disposable worker tree/pidfds/dispatch; synthetic Docker/namespaces.

These tests neither launch the fixed native command nor authenticate Ready.
They qualify observer-owned handle capture/retention, not installed recovery.
"""

import importlib.util
import json
import os
import select
import signal
import sys
import time
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from threading import Thread

import pytest

from . import test_supplemental_recording_execution as execution_tests
from . import test_supplemental_recording_host_plan as plan_tests
from . import test_supplemental_recording_probe as probe_tests
from . import test_supplemental_recording_projection as projection_tests
from . import test_supplemental_recording_service_app_custody as app_tests

NAME = "supplemental_recording_service_native_custody"
SPEC = importlib.util.spec_from_file_location(
    NAME, Path(app_tests.m.__file__).with_name(NAME + ".py")
)
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)
prepared, family = app_tests.prepared, probe_tests.family
layout, tree, routing, projection = (
    projection_tests.layout,
    projection_tests.tree,
    projection_tests.routing,
    projection_tests.projection,
)
REAL_OPEN = os.open
pytestmark = app_tests.pytestmark


@contextmanager
def setup(prepared, projection, family, tmp_path, monkeypatch, *, sender=False, short=False):
    # Retain the existing real clock but join the actual manifest projection.
    if short:
        projection = projection_tests.m.project(
            projection.layout, projection_tests.reencode(projection.host, maximum=1)
        )
    _, raw = plan_tests.projected_plan(projection)
    supplied = json.loads(prepared.plan.raw)
    raw.update({key: supplied[key] for key in ("boot", "original_clock", "deadlines")})
    prepared.plan = plan_tests.m.decode(raw)
    # The reused helper fixture maps '/' for idle-path tests. This test uses
    # explicitly private real paths for the actual durable dispatch chain.
    monkeypatch.setattr(os, "open", REAL_OPEN)
    root = tmp_path / "dispatch-host"
    root.mkdir(mode=0o700)
    directory = root / "operator-exec"
    directory.mkdir(mode=0o700)
    monkeypatch.setattr(plan_tests.m.Plan, "root", property(lambda _: root))
    executions = {}
    with app_tests.setup(
        prepared, tmp_path, monkeypatch, sender=sender, short=short, executions=executions
    ) as case:
        custody = case.create()
        app_tests.exit_normal(case)
        candidate = custody.capture_candidate(case.generations[m.apps.CANDIDATE])
        plan = custody.plan
        host = m.dispatch.binding.Binding(
            projection, plan.candidate_runtime.source, plan.sha256, plan.boot
        )
        command = m.dispatch.execution.Command(
            str(plan.native_root / "launch/launch.json"),
            "e" * 64,
            host.source_sha256,
            plan.deadlines.ready_by,
        )
        pins = m.dispatch.Pins(host, command, candidate.generation, candidate.process)
        value = execution_tests.metadata()
        argv = command.argv()
        value["ProcessConfig"].update(entrypoint=argv[0], arguments=list(argv[1:]))
        with m.apps.processes.ProcessWitness(
            candidate.process, retained_fd=custody._retained[1][1]
        ) as witness:
            claim = m.dispatch.Claim(directory, pins, witness)
            claim.created(value["ID"])
            claim.attach_intent(value)
        value.update(Running=True, Pid=family.process.pid)
        executions[value["ID"]] = value
        domains = tuple(
            (info.st_dev, info.st_ino)
            for info in (os.stat(f"/proc/self/ns/{name}") for name in m.engine.namespace.NAMESPACES)
        )
        points = [
            candidate.process,
            family.expected.guardian,
            family.expected.native,
            family.expected.watchdog,
        ]
        actors = tuple(
            m.engine.namespace.Actor(
                item.pid,
                index + 1,
                os.getpid() if index < 2 else family.process.pid,
                item.start_ticks,
                candidate.process.container_id,
                domains,
            )
            for index, item in enumerate(points)
        )
        lookup = {actor.host_pid: actor for actor in actors}

        def read(pid, cid):
            actor = lookup[pid]
            assert cid == actor.container_id
            fields = Path(f"/proc/{pid}/stat").read_text().rpartition(") ")[2].split()
            m.engine.namespace.require(
                fields[0] in ("R", "S", "I") and int(fields[19]) == actor.start_ticks
            )
            assert int(fields[1]) == actor.parent
            return actor

        monkeypatch.setattr(m.engine.namespace, "read", read)
        reported = {
            role: dict(pid=actor.local_pid, start_ticks=actor.start_ticks, uid=0, gid=0)
            for role, actor in zip(m.ROLES[1:], actors[1:], strict=True)
        }
        case.custody, case.pins, case.reported = custody, pins, reported
        case.actors, case.value, case.directory, case.family = actors, value, directory, family
        objects = []

        def capture(**kwargs):
            result = m.NativeCustody(custody, case.pins, case.reported, **kwargs)
            objects.append(result)
            return result

        case.capture = capture
        try:
            yield case
        finally:
            for observer in objects:
                observer.close()


@pytest.fixture
def case(projection, prepared, family, tmp_path, monkeypatch):
    with setup(prepared, projection, family, tmp_path, monkeypatch) as result:
        yield result


def denied(call):
    with pytest.raises(m.UnconfirmedNativeCustody) as caught:
        call()
    assert str(caught.value) == m.MESSAGE and caught.value.__suppress_context__


def finish(family):
    family.process.stdin.close()
    assert family.process.wait(timeout=3) == 0


def test_actual_capture_matches_fixed_exec_and_retains_four_distinct_original_handles(case):
    original = {path.name: path.read_bytes() for path in case.directory.iterdir()}
    observer = case.capture()
    assert len(case.state.requests) == 6
    assert all(request[0].startswith("GET ") for request in case.state.requests)
    status = observer.poll()
    assert status.exited == frozenset() and status.apps.normal_exited
    assert status.apps.candidate_exited is False and not status.apps.deadline.helper_exited
    assert status.binding.actors == case.actors
    assert status.binding.execution_id == case.value["ID"]
    assert status.binding.dispatch_sha256 == m.dispatch.load(case.directory, case.pins).sha256
    assert case.custody.native_custody_attempted
    handles = tuple(fd for _, fd, _ in observer._retained)
    assert len(set(handles)) == 4 and all(not os.get_inheritable(fd) for fd in handles)
    assert handles[0] != case.custody._retained[1][1]
    assert not set(handles).intersection(case.family.handles)
    assert original == {path.name: path.read_bytes() for path in case.directory.iterdir()}
    assert not hasattr(observer, "begin") and not hasattr(observer, "publish")
    denied(case.capture)
    assert observer.poll().exited == frozenset()  # Retry refusal does not discard evidence.
    observer.close()
    observer.close()
    assert all(os.fstat(fd) for fd in case.family.handles)
    assert case.custody.poll().candidate_exited is False
    for fd in handles:
        with pytest.raises(OSError):
            os.fstat(fd)


@pytest.mark.parametrize("deadline", [False, True, "later", float("nan"), float("inf"), -1])
def test_invalid_native_deadline_consumes_attempt_without_engine_reads(case, deadline):
    before = app_tests.count_fds(case)
    denied(lambda: case.capture(deadline=deadline))
    assert len(case.state.requests) == 4 and app_tests.count_fds(case) == before
    assert case.custody.native_custody_attempted
    denied(case.capture)
    assert case.custody.poll().candidate_exited is False


def test_expired_native_exchange_cannot_start_a_fresh_capture(case):
    before = app_tests.count_fds(case)
    denied(lambda: case.capture(deadline=time.monotonic() - 1))
    assert len(case.state.requests) == 4 and app_tests.count_fds(case) == before
    assert case.custody.native_custody_attempted
    denied(case.capture)
    assert not case.custody.failed and not case.custody.capture_failed
    assert case.custody.poll().candidate_exited is False


@pytest.mark.parametrize("seconds", [1, 100])
def test_native_capture_preserves_shorter_outer_budget_through_both_engine_reads(
    case, monkeypatch, seconds
):
    original, limits = m.engine._json_request, []
    began = time.monotonic()
    deadline = began + seconds

    def bounded(*args, **kwargs):
        end = kwargs["deadline"]
        assert end <= deadline and end < began + m.MAX_SECONDS + 0.1
        limits.append(end)
        return original(*args, **kwargs)

    monkeypatch.setattr(m.engine, "_json_request", bounded)
    observer = case.capture(deadline=deadline)
    assert len(limits) == 2 and limits[0] == limits[1]
    assert observer.poll().binding.actors == case.actors
    assert observer.poll().exited == frozenset()


@pytest.mark.parametrize("when", [5, 6])
def test_outer_native_expiry_during_inspection_drops_partial_handles_without_retry(
    case, monkeypatch, when
):
    original, offset = m.time.monotonic, [0]
    deadline = original() + 1
    monkeypatch.setattr(m.time, "monotonic", lambda: original() + offset[0])

    def expire(value, count):
        if count == when:
            offset[0] = 1.1
        return value

    case.state.hook = expire
    before = app_tests.count_fds(case)
    denied(lambda: case.capture(deadline=deadline))
    offset[0] = 0
    assert len(case.state.requests) == when and app_tests.count_fds(case) == before
    assert case.custody.native_custody_attempted
    denied(case.capture)
    assert case.custody.poll().candidate_exited is False
    assert all(os.fstat(fd) for fd in case.family.handles)


def test_poll_survives_helper_init_engine_and_history_loss_without_worker_exit(case, monkeypatch):
    observer = case.capture()
    case.prepared.child.stdin.close()
    assert case.prepared.child.wait(timeout=3) == 0
    case.link.close()
    case.link.domain.close()
    case.link.observer.close()
    case.prepared.witness.close()
    case.endpoint.close()
    case.candidate.stdin.close()
    assert case.candidate.wait(timeout=3) == 0
    for path in case.directory.iterdir():
        path.unlink()  # Test-owned files only: polling must not replay the ledger.
    for module, name in (
        (m.engine, "_json_request"),
        (m.engine.namespace, "read"),
        (m.apps.processes, "read_identity"),
        (m.dispatch, "_read"),
        (os, "pidfd_open"),
    ):
        monkeypatch.setattr(
            module, name, lambda *_, **__: pytest.fail("lost process/history reopened")
        )
    status = observer.poll()
    assert status.exited == frozenset({"init"})
    assert status.apps.deadline.helper_exited and status.apps.candidate_exited
    finish(case.family)
    assert observer.poll().exited == frozenset(m.ROLES)
    assert observer.poll().exited == frozenset(m.ROLES)
    assert len(case.state.requests) == 6


@pytest.mark.parametrize("role", m.ROLES[1:])
def test_frozen_original_worker_is_not_exit(case, role):
    observer = case.capture()
    fd = dict((role, fd) for role, fd, _ in observer._retained)[role]
    signal.pidfd_send_signal(fd, signal.SIGSTOP)
    try:
        assert role not in observer.poll().exited
    finally:
        signal.pidfd_send_signal(fd, signal.SIGCONT)


@pytest.mark.parametrize("role", ("native", "watchdog"))
def test_individual_fatal_worker_exit_is_retained_not_native_success(case, role):
    observer = case.capture()
    fd = dict((role, fd) for role, fd, _ in observer._retained)[role]
    signal.pidfd_send_signal(fd, signal.SIGKILL)
    assert select.select([fd], [], [], 2)[0]
    assert observer.poll().exited == frozenset({role})
    assert not hasattr(observer.poll(), "returncode")


@pytest.mark.parametrize("role", m.ROLES[1:])
@pytest.mark.parametrize("field", ("pid", "start_ticks", "uid", "gid"))
def test_untrusted_identity_hint_requires_actual_kernel_match(case, role, field):
    case.reported[role][field] += 10
    before = app_tests.count_fds(case)
    denied(case.capture)
    assert app_tests.count_fds(case) == before
    assert case.custody.native_custody_attempted and case.custody.poll().candidate_exited is False
    denied(case.capture)


@pytest.mark.parametrize("when", (5, 6))
@pytest.mark.parametrize(
    "fault", ("pid", "command", "container", "execution", "exited", "starting")
)
def test_wrong_or_changed_exec_inspection_never_returns_custody(case, when, fault):
    def change(value, count):
        if count == when:
            if fault == "pid":
                value["Pid"] = case.candidate.pid
            elif fault == "command":
                value["ProcessConfig"]["arguments"].append("PRIVATE")
            elif fault == "container":
                value["ContainerID"] = "9" * 64
            elif fault == "execution":
                value["ID"] = "9" * 64
            elif fault == "exited":
                value.update(Running=False, Pid=0, ExitCode=0)
            else:
                value["Pid"] = 0
        return value

    case.state.hook = change
    before = app_tests.count_fds(case)
    denied(case.capture)
    assert app_tests.count_fds(case) == before
    assert case.custody.poll().candidate_exited is False


@pytest.mark.parametrize(
    "fault", ("generation", "init", "host_plan", "projection", "source", "path", "ready_by", "boot")
)
def test_wrong_original_plan_or_process_pins_refuse_before_engine(case, fault):
    pins = case.pins
    if fault == "generation":
        pins = replace(pins, generation="9" * 64)
    elif fault == "init":
        pins = replace(pins, init=replace(pins.init, start_ticks=pins.init.start_ticks + 1))
    elif fault in ("host_plan", "boot", "source", "projection"):
        changes = {
            "host_plan": dict(plan_sha256="9" * 64),
            "boot": dict(boot_id="9" * 32),
            "source": dict(source_sha256="9" * 64),
            "projection": dict(projection=None),
        }
        pins = replace(pins, host=replace(pins.host, **changes[fault]))
    else:
        changes = {
            "path": dict(plan="/data/other/launch.json"),
            "ready_by": dict(ready_by=pins.command.ready_by + 1),
        }
        pins = replace(pins, command=replace(pins.command, **changes[fault]))
    case.pins = pins
    denied(case.capture)
    assert len(case.state.requests) == 4


@pytest.mark.parametrize("stage", ("before", "during"))
@pytest.mark.parametrize("target", ("helper", "candidate"))
def test_original_exit_during_capture_preserves_app_custody_no_retry(case, stage, target):
    process = case.prepared.child if target == "helper" else case.candidate

    def exit_owned():
        process.stdin.close()
        assert process.wait(timeout=3) == 0

    if stage == "before":
        exit_owned()
    else:

        def hook(value, count):
            if count == 6:
                exit_owned()
            return value

        case.state.hook = hook
    before = app_tests.count_fds(case)
    denied(case.capture)
    # The test itself closes exactly one stdin to induce the mid-capture exit.
    assert app_tests.count_fds(case) == before - (stage == "during")
    assert case.custody.poll().normal_exited
    denied(case.capture)


@pytest.mark.parametrize("fault", ("missing", "extra", "changed"))
def test_dispatch_changed_during_capture_is_not_adopted(case, fault):
    def hook(value, count):
        if count == 6:
            path = case.directory / "0002.json"
            if fault == "missing":
                path.unlink()
            elif fault == "extra":
                (case.directory / "extra").write_bytes(b"PRIVATE")
            else:
                path.write_bytes(path.read_bytes() + b"\n")
        return value

    case.state.hook = hook
    before = app_tests.count_fds(case)
    denied(case.capture)
    assert app_tests.count_fds(case) == before
    assert case.custody.poll().candidate_exited is False


@pytest.mark.parametrize(
    "fault", ("receipt", "binding", "handles", "owner", "closed_apps", "inheritable")
)
def test_tampered_retained_evidence_is_never_exit_or_recaptured(case, fault):
    observer = case.capture()
    if fault == "receipt":
        object.__setattr__(observer.binding, "execution_id", "9" * 64)
    elif fault == "binding":
        observer.binding = replace(observer.binding)
    elif fault == "handles":
        observer._retained = tuple(list(observer._retained))
    elif fault == "owner":
        observer.owner = (os.getpid(), -1)
    elif fault == "closed_apps":
        case.custody.close()
    else:
        os.set_inheritable(observer._retained[0][1], True)
    try:
        denied(observer.poll)
        assert observer.failed and not observer.closed
        assert all(os.fstat(fd) for _, fd, _ in observer._original_retained)
    finally:
        if fault == "owner":
            from threading import get_ident

            observer.owner = os.getpid(), get_ident()


def test_other_thread_cannot_take_over_original_observer(case):
    observer = case.capture()
    errors = []

    def wrong_owner():
        try:
            observer.poll()
        except m.UnconfirmedNativeCustody as error:
            errors.append(error)

    worker = Thread(target=wrong_owner)
    worker.start()
    worker.join(timeout=3)
    assert not worker.is_alive() and len(errors) == 1 and observer.failed


def test_existing_source_profiles_and_commands_do_not_select_new_custody():
    scripts = Path(m.__file__).parent
    for name in (
        "supplemental_recording_service_command.py",
        "supplemental_recording_host_source.py",
        "supplemental_recording_app_host_source.py",
    ):
        assert NAME not in (scripts / name).read_text()


def test_sender_authenticated_endpoint_retains_the_same_original_actors(
    projection, prepared, family, tmp_path, monkeypatch
):
    with setup(prepared, projection, family, tmp_path, monkeypatch, sender=True) as case:
        observer = case.capture()
        assert case.endpoint.sender.peer is not None
        assert observer.poll().binding.actors == case.actors
        assert len(case.state.requests) == 6


def test_untrusted_hints_never_select_or_create_an_authenticated_ready_witness(case, monkeypatch):
    original, observed = m.engine.namespace.Observation.__init__, []

    def capture(self, *args):
        original(self, *args)
        assert type(self) is m.engine.namespace.Observation
        assert not isinstance(self, m.engine.namespace.Witness)
        observed.append(self.actors)

    def forbidden(*_, **__):
        pytest.fail("Untrusted hints cannot use the authenticated Ready witness API")

    monkeypatch.setattr(m.engine.namespace.Observation, "__init__", capture)
    monkeypatch.setattr(m.engine.namespace.Witness, "__init__", forbidden)
    observer = case.capture()
    assert observed == [case.actors] and observer.poll().binding.actors == case.actors


def test_actual_kernel_deadline_still_reports_individual_workers_with_frozen_helper(
    projection, prepared, family, tmp_path, monkeypatch
):
    with setup(prepared, projection, family, tmp_path, monkeypatch, short=True) as case:
        observer = case.capture()
        original = case.custody.plan.raw, case.watch.deadline_ns
        signal.pidfd_send_signal(case.watch.helper_fd, signal.SIGSTOP)
        try:
            case.endpoint.close()
            assert select.select([case.watch.timer_fd], [], [], 7)[0] == [case.watch.timer_fd]
            for _ in range(2):
                status = observer.poll()
                assert status.apps.deadline == m.deadlines.Status(False, True)
                assert status.exited == frozenset() and not status.apps.candidate_exited
            assert (case.custody.plan.raw, case.watch.deadline_ns) == original
            assert len(case.state.requests) == 6
        finally:
            signal.pidfd_send_signal(case.watch.helper_fd, signal.SIGCONT)


@pytest.mark.parametrize("role", m.ROLES[1:])
def test_frozen_worker_before_capture_is_not_qualified_as_live(case, role):
    index = m.ROLES.index(role)
    pid, fd = case.actors[index].host_pid, case.family.handles[index - 1]
    signal.pidfd_send_signal(fd, signal.SIGSTOP)
    try:
        end = time.monotonic() + 1
        while Path(f"/proc/{pid}/stat").read_text().rpartition(") ")[2].split()[0] not in (
            "T",
            "t",
        ):
            assert time.monotonic() < end
        before = app_tests.count_fds(case)
        denied(case.capture)
        assert app_tests.count_fds(case) == before
    finally:
        signal.pidfd_send_signal(fd, signal.SIGCONT)


@pytest.mark.parametrize("fault", ("command_sha", "source_pair"))
def test_self_consistent_new_pins_cannot_replace_original_dispatch_or_plan(case, fault):
    pins = case.pins
    if fault == "command_sha":
        case.pins = replace(pins, command=replace(pins.command, plan_sha256="9" * 64))
    else:
        case.pins = replace(
            pins,
            host=replace(pins.host, source_sha256="9" * 64),
            command=replace(pins.command, source_sha256="9" * 64),
        )
    denied(case.capture)
    assert len(case.state.requests) == 4


@pytest.mark.parametrize("role", m.ROLES[1:])
@pytest.mark.parametrize("fault", ("parent", "time", "user", "container", "ticks"))
def test_namespace_ancestry_and_incarnation_cannot_be_inferred_from_reports(
    case, monkeypatch, role, fault
):
    index = m.ROLES.index(role)
    real = m.engine.namespace.read

    def changed(pid, cid):
        actor = real(pid, cid)
        if actor.host_pid != case.actors[index].host_pid:
            return actor
        if fault == "parent":
            # Guardian may have an independent parent but may not be parented
            # by either of its own children. Other actors must be direct kids.
            return replace(actor, parent=case.actors[2].host_pid)
        if fault in ("time", "user"):
            namespaces = list(actor.namespaces)
            number = m.engine.namespace.NAMESPACES.index(fault)
            namespaces[number] = (namespaces[number][0], namespaces[number][1] + 1)
            return replace(actor, namespaces=tuple(namespaces))
        if fault == "container":
            return replace(actor, container_id="9" * 64)
        return replace(actor, start_ticks=actor.start_ticks + 1)

    monkeypatch.setattr(m.engine.namespace, "read", changed)
    before = app_tests.count_fds(case)
    denied(case.capture)
    assert app_tests.count_fds(case) == before


@pytest.mark.parametrize("role", m.ROLES[1:])
def test_kernel_descriptor_must_reference_the_actual_mapped_process(case, monkeypatch, role):
    real = os.pidfd_open
    expected = case.actors[m.ROLES.index(role)].host_pid

    def wrong(pid, *args):
        return real(case.candidate.pid if pid == expected else pid, *args)

    monkeypatch.setattr(os, "pidfd_open", wrong)
    before = app_tests.count_fds(case)
    denied(case.capture)
    assert app_tests.count_fds(case) == before
    assert case.custody.poll().candidate_exited is False


@pytest.mark.parametrize("after", (1, 2, 3, 4))
@pytest.mark.parametrize("error", (OSError, KeyboardInterrupt))
def test_interrupted_partial_native_capture_releases_own_fds_not_app_evidence(
    case, monkeypatch, after, error
):
    original = m.NativeCustody._duplicate
    calls = []

    def fail(self, *args):
        original(self, *args)
        calls.append(True)
        if len(calls) == after:
            raise error("PRIVATE")

    monkeypatch.setattr(m.NativeCustody, "_duplicate", fail)
    before = app_tests.count_fds(case)
    with pytest.raises(
        KeyboardInterrupt if error is KeyboardInterrupt else m.UnconfirmedNativeCustody
    ):
        case.capture()
    assert app_tests.count_fds(case) == before
    assert case.custody.poll().candidate_exited is False
    denied(case.capture)


def test_first_metadata_failure_after_native_dup_does_not_leak(case, monkeypatch):
    real_duplicate, real_identity = m.NativeCustody._duplicate, m.deadlines._identity
    armed = [False]

    def identity(fd):
        if armed[0]:
            armed[0] = False
            raise OSError("PRIVATE")
        return real_identity(fd)

    def duplicate(self, *args):
        armed[0] = True
        return real_duplicate(self, *args)

    monkeypatch.setattr(m.NativeCustody, "_duplicate", duplicate)
    monkeypatch.setattr(m.deadlines, "_identity", identity)
    before = app_tests.count_fds(case)
    denied(case.capture)
    assert app_tests.count_fds(case) == before


def test_capture_refuses_after_original_readiness_cutoff_even_with_live_actors(case, monkeypatch):
    # Inject a later BOOTTIME read without changing the retained plan, kernel
    # timer or real host clock. This is a capture-refusal test, not timer proof.
    real = time.clock_gettime_ns
    ready = int(case.custody.plan.deadlines.ready_by * 1_000_000_000) + 1
    monkeypatch.setattr(
        time,
        "clock_gettime_ns",
        lambda clock: ready if clock == time.CLOCK_BOOTTIME else real(clock),
    )
    denied(case.capture)
    assert len(case.state.requests) == 4


@pytest.mark.parametrize("fault", ("append", "truncated"))
def test_no_completed_attach_history_means_no_native_capture(case, fault):
    path = case.directory / "0002.json"
    if fault == "append":
        (case.directory / "0003.json").write_bytes(b"PRIVATE")
    else:
        path.unlink()
    before = app_tests.count_fds(case)
    denied(case.capture)
    assert app_tests.count_fds(case) == before and len(case.state.requests) == 4
