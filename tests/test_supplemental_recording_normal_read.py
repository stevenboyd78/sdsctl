"""Real fixed probe/closed plan join, explicit synthetic Engine and clocks.

No installed metadata, normal restoration or physical scanner result is claimed.
"""

import ast
import importlib.util
import sys
from dataclasses import asdict, replace
from threading import Thread
from types import SimpleNamespace

import pytest

from . import test_supplemental_handoff_app_read as legacy
from . import test_supplemental_recording_host as hosts
from . import test_supplemental_recording_host_plan as plans

NAME = "supplemental_recording_normal_read"
SPEC = importlib.util.spec_from_file_location(
    NAME, plans.Path(plans.m.__file__).with_name(NAME + ".py")
)
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)
b, h = m.plans.base, legacy.h


@pytest.fixture
def joined(monkeypatch):
    plan = m.plans.decode(plans.value())
    docker, fake = m.plans.ordinary.Docker(), legacy.Docker()
    for name in ("container", "_request", "inspect_execution", "start_execution"):
        monkeypatch.setattr(docker, name, getattr(fake, name))
    state = SimpleNamespace(tick=0, elapsed=0, plan=plan, docker=docker, fake=fake)

    def clock():
        state.tick += 1
        delta = state.tick * 1000
        return replace(
            plan.original_clock,
            before_ns=plan.original_clock.before_ns + delta,
            boottime_ns=plan.original_clock.boottime_ns + delta,
            after_ns=plan.original_clock.after_ns + delta,
        )

    monkeypatch.setattr(m.plans.clock, "read", clock)
    monkeypatch.setattr(m.time, "monotonic", lambda: 100 + state.elapsed)
    state.generation = h.generation(fake.cli, name="app_" + b.NORMAL, image=plan.normal.image)
    state.sample = m.Sample(plan, docker)
    assert not fake.calls and state.tick == 0  # Construction never executes or samples health.
    return state


def denied(callback):
    with pytest.raises(m.UnconfirmedNormalRead) as caught:
        callback()
    assert str(caught.value) == m.MESSAGE and caught.value.__suppress_context__


def test_original_plan_derives_only_normal_paths_and_fixed_cached_program(joined):
    s = joined
    assert s.sample.paths == m.cached.ProbePaths(
        "/data/deployment.toml", "/media/" + b.NORMAL + "/recordings"
    )
    assert s.sample.command[:4] == ("/usr/local/bin/python", "-I", "-B", "-c")
    tree = ast.parse(s.sample.command[-1])
    role = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Tuple)
        and [n.id for n in node.targets[0].elts] == ["candidate", "case", "source", "firmware"]
    )
    assert ast.literal_eval(role.value) == (False, s.plan.case, s.plan.source, s.plan.firmware)
    assert s.sample.read(b.NORMAL, s.generation) == m.plans.ordinary.NativeState(
        s.generation, True, False
    )
    assert s.fake.calls == ["create", "start"] and s.sample.used and not s.sample.failed
    assert s.tick == 2


def test_no_second_read_even_after_success(joined):
    s = joined
    s.sample.read(b.NORMAL, s.generation)
    denied(lambda: s.sample.read(b.NORMAL, s.generation))
    assert s.fake.calls == ["create", "start"]


@pytest.mark.parametrize("healthy,recording", [(None, False), (False, False), (True, True)])
def test_uncertain_unhealthy_or_recording_status_is_not_replaced_with_idle(
    joined, healthy, recording
):
    s = joined
    s.fake.evidence.update(healthy=healthy, recording=recording)
    assert s.sample.read(b.NORMAL, s.generation) == m.plans.ordinary.NativeState(
        s.generation, healthy, recording
    )


def test_restored_generation_may_differ_but_must_match_actual_current_engine(joined):
    s = joined
    s.fake.cli["State"]["Pid"] += 1
    s.fake.cli["State"]["StartedAt"] = "2026-09-23T00:00:00.123456789Z"
    current = h.generation(s.fake.cli, name="app_" + b.NORMAL, image=s.plan.normal.image)
    assert current not in (s.generation, s.plan.normal_generation)
    assert s.sample.read(b.NORMAL, current).generation == current
    # This is current metadata, not a claim that original workers or init exited.


@pytest.mark.parametrize("slug", [b.CANDIDATE, "homeassistant", None, "app_" + b.NORMAL])
def test_candidate_and_other_targets_never_execute(joined, slug):
    s = joined
    denied(lambda: s.sample.read(slug, s.generation))
    assert not s.fake.calls


@pytest.mark.parametrize("incarnation", [None, True, "bad", "f" * 64])
def test_unknown_generation_never_executes(joined, incarnation):
    s = joined
    denied(lambda: s.sample.read(b.NORMAL, incarnation))
    assert not s.fake.calls


@pytest.mark.parametrize(
    "key,value",
    [
        ("profile", "f" * 64),
        ("schema", True),
        ("healthy", "yes"),
        ("recording", 0),
        ("supplemental", True),
        ("extra", "PRIVATE"),
    ],
)
def test_malformed_or_wrong_role_reply_cannot_become_health(joined, key, value):
    s = joined
    s.fake.evidence[key] = value
    denied(lambda: s.sample.read(b.NORMAL, s.generation))
    assert s.fake.calls == ["create", "start"]
    denied(lambda: s.sample.read(b.NORMAL, s.generation))
    assert s.fake.calls == ["create", "start"]


@pytest.mark.parametrize("phase", ["before", "after_create", "after_start"])
def test_replaced_normal_generation_never_yields_state(joined, monkeypatch, phase):
    s = joined
    if phase == "before":
        s.fake.cli["State"]["Pid"] += 1
    else:
        name = "_request" if phase == "after_create" else "start_execution"
        original = getattr(s.docker, name)

        def changed(*args, **kwargs):
            value = original(*args, **kwargs)
            s.fake.cli["State"]["Pid"] += 1
            return value

        monkeypatch.setattr(s.docker, name, changed)
    denied(lambda: s.sample.read(b.NORMAL, s.generation))
    assert (
        s.fake.calls
        == {"before": [], "after_create": ["create"], "after_start": ["create", "start"]}[phase]
    )


@pytest.mark.parametrize("failure", [TimeoutError, OSError, KeyboardInterrupt])
def test_lost_return_is_consumed_and_sanitized_without_replay(joined, monkeypatch, failure):
    s = joined

    def lost(*args, **kwargs):
        s.fake.calls.append("start")
        raise failure("PRIVATE")

    monkeypatch.setattr(s.docker, "start_execution", lost)
    if failure is KeyboardInterrupt:
        with pytest.raises(KeyboardInterrupt):
            s.sample.read(b.NORMAL, s.generation)
    else:
        denied(lambda: s.sample.read(b.NORMAL, s.generation))
    assert s.sample.used and s.sample.failed
    denied(lambda: s.sample.read(b.NORMAL, s.generation))
    assert s.fake.calls == ["create", "start"]


@pytest.mark.parametrize("fault", ["plan", "command", "paths", "docker", "route"])
def test_mutated_original_context_refused_before_exec(joined, fault):
    s = joined
    if fault == "plan":
        value = plans.value()
        value["source"] = "f" * 40
        s.sample.plan = m.plans.decode(value)
    elif fault == "command":
        s.sample.command = ("/bin/sh", "-c", "PRIVATE")
    elif fault == "paths":
        s.sample.paths = m.cached.ProbePaths("/data/other.toml", "/media/other")
    elif fault == "docker":
        s.sample.docker = m.plans.ordinary.Docker()
    else:
        s.docker.path = "/PRIVATE"
    denied(lambda: s.sample.read(b.NORMAL, s.generation))
    assert not s.fake.calls


@pytest.mark.parametrize("fault", ["boot", "namespace", "suspend", "expired", "reversed", "late"])
def test_original_clock_and_two_second_budget_preserved(joined, monkeypatch, fault):
    s = joined
    original = m.plans.clock.read
    calls = []

    def changed():
        observed = original()
        calls.append(True)
        if fault == "boot":
            return replace(observed, boot="a" * 32)
        if fault == "namespace":
            return replace(observed, namespace=(1, 999))
        if fault == "suspend":
            return replace(observed, boottime_ns=observed.boottime_ns + m.plans.clock.NS)
        if fault in ("expired", "late"):
            if fault == "late" and len(calls) == 1:
                return observed
            delta = (1501 if fault == "expired" else 3) * m.plans.clock.NS
            return replace(
                observed,
                before_ns=observed.before_ns + delta,
                after_ns=observed.after_ns + delta,
                boottime_ns=observed.boottime_ns + delta,
            )
        if fault == "reversed" and len(calls) == 2:
            return replace(
                observed,
                before_ns=observed.before_ns - 1001,
                after_ns=observed.after_ns - 1001,
                boottime_ns=observed.boottime_ns - 1001,
            )
        return observed

    monkeypatch.setattr(m.plans.clock, "read", changed)
    denied(lambda: s.sample.read(b.NORMAL, s.generation))
    assert s.fake.calls == (["create", "start"] if fault in ("reversed", "late") else [])


@pytest.mark.parametrize("duration", [-1, 2.001])
def test_wall_collection_budget_includes_engine_work(joined, monkeypatch, duration):
    s = joined
    original = s.docker.start_execution

    def slow(*args, **kwargs):
        value = original(*args, **kwargs)
        s.elapsed += duration
        return value

    monkeypatch.setattr(s.docker, "start_execution", slow)
    denied(lambda: s.sample.read(b.NORMAL, s.generation))


def test_foreign_thread_and_concurrent_use_never_execute(joined):
    s = joined
    errors = []

    def other():
        try:
            s.sample.read(b.NORMAL, s.generation)
        except m.UnconfirmedNormalRead:
            errors.append(True)

    worker = Thread(target=other)
    worker.start()
    worker.join(1)
    assert not worker.is_alive() and errors == [True] and not s.fake.calls
    fresh = m.Sample(s.plan, s.docker)
    with fresh.lock:
        denied(lambda: fresh.read(b.NORMAL, s.generation))
    assert not s.fake.calls


@pytest.mark.parametrize("fault", ["legacy", "fake_docker", "route"])
def test_wrong_plan_or_docker_cannot_construct(joined, fault):
    s = joined
    plan, docker = s.plan, s.docker
    if fault == "legacy":
        plan = plans.legacy.s.decode_plan(plans.legacy.plan_value())
    elif fault == "fake_docker":
        docker = s.fake
    else:
        docker.path = "/PRIVATE"
    denied(lambda: m.Sample(plan, docker))
    assert not s.fake.calls


@pytest.mark.parametrize(
    "fault", [None, "reply", "context", "package", "profile", "recordings", "options", "busy"]
)
def test_full_recording_observer_keeps_all_normal_protections(joined, monkeypatch, fault):
    s = joined
    host, observer, _ = hosts.make_host(monkeypatch, running=False)
    normal = host.values["app_" + b.NORMAL]
    # Explicit synthetic Engine routing, not real container metadata.
    normal["Id"] = legacy.host_tests.CID
    s.fake.cli = normal
    value = plans.value()
    value["normal"] = asdict(observer.seals[b.NORMAL])
    plan = m.plans.decode(value)
    samples = []

    def read_native(slug, incarnation):
        assert slug == b.NORMAL
        sample = m.Sample(plan, s.docker)
        samples.append(sample)
        return sample.read(slug, incarnation)

    observer.read_native = read_native
    if fault == "reply":
        s.fake.reply_lost = True
    elif fault == "options":
        host.private_options["normal"] = "changed"
    elif fault == "busy":
        host.jobs = False
    elif fault is not None:
        host.files[b.NORMAL] = replace(host.files[b.NORMAL], **{fault: "f" * 64})
    observed = observer.read().observation
    assert observed.candidate.state == "stopped"
    assert observed.files == host.files[b.CANDIDATE].recording.files
    assert observed.core_running and observed.other_owners_stopped
    assert observed.jobs_idle is (fault != "busy")
    changed_pin = fault in ("context", "package", "profile", "recordings", "options")
    assert (observed.normal.pin != plan.normal.pin) is changed_pin
    if changed_pin:
        assert not samples and not s.fake.calls
    else:
        assert len(samples) == 1 and s.fake.calls == ["create", "start"]
    unknown = changed_pin or fault == "reply"
    assert observed.normal.healthy is (None if unknown else True)
    assert observed.normal.recording is (None if unknown else False)
    # This sample never authorizes restoration or replaces exit receipts.
