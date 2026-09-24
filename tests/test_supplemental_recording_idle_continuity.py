"""Actual owned init pidfd/files; explicitly synthetic Retained and HA mappings.

Retained's actual Engine/Ready/private-transport authentication is separately
tested. This fixture cannot certify an installed namespace, source or service.
"""

import os
import time
from dataclasses import replace
from threading import Thread, get_ident
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_idle_observer as idle_tests

m, prepared = idle_tests.m, idle_tests.prepared


def denied(action):
    with pytest.raises(m.UnconfirmedPostBegin) as caught:
        action()
    assert str(caught.value) == m.POST_BEGIN_MESSAGE and caught.value.__suppress_context__


@pytest.fixture
def joined(prepared, monkeypatch):
    idle = prepared.observe()
    plan, projection = idle.plan, object()

    def check_projection(self, value):
        assert self is plan and value is projection  # Explicit synthetic projection.

    monkeypatch.setattr(m.host_plan.Plan, "check_projection", check_projection)

    class Retained:
        def __init__(self):
            self.owner = os.getpid(), get_ident()
            self.ready, self.clock = object(), plan.original_clock
            self.zero_domain, self.domain_sha256 = idle.zero_domain, idle.domain_sha256
            self.ready_by, self.finish_by = plan.lease["ready_by"], plan.lease["stop_by"]
            self.watch_deadline = self.ready_by + plan.candidate.contract.maximum_recording_seconds
            self.actors = (idle.actor,)
            self.pins = SimpleNamespace(
                init=idle.init,
                generation=idle.generation,
                host=SimpleNamespace(
                    plan_sha256=plan.sha256,
                    boot_id=plan.boot,
                    source_sha256=plan.candidate_runtime.source,
                    projection=projection,
                ),
                command=SimpleNamespace(
                    source_sha256=plan.candidate_runtime.source,
                    plan=str(plan.native_root / "launch/launch.json"),
                    ready_by=self.ready_by,
                ),
            )
            self.exits, self.failed, self.calls = frozenset(), False, 0

        def check(self):
            self.calls += 1
            assert not self.failed
            return self.exits

    monkeypatch.setattr(m.retained, "Retained", Retained)
    guard = Retained()
    post = m.PostBegin(idle, guard)
    try:
        yield SimpleNamespace(idle=idle, guard=guard, post=post, plan=plan, original=prepared)
    finally:
        post.close()


def test_original_owned_init_and_files_remain_only_continuity(joined):
    s = joined
    original = (s.plan.raw, s.idle.initial, s.post.ready_by, s.post.finish_by)
    values = {
        p: (p.read_bytes(), m.files.identity(p.stat()))
        for p in (s.original.lease, s.original.claim)
    }
    observed = s.post.read()
    assert type(observed) is m.Continuity and observed.original is s.idle.initial
    assert observed.sampled_at >= observed.original.sampled_at and not observed.exited
    assert not hasattr(observed, "native")
    assert not any(hasattr(s.post, name) for name in ("start", "begin", "dispatch", "restore"))
    assert original == (s.plan.raw, s.idle.initial, s.post.ready_by, s.post.finish_by)
    assert values == {p: (p.read_bytes(), m.files.identity(p.stat())) for p in values}
    s.post.close()
    s.post.close()
    assert not s.idle.closed and not s.original.witness.exited()
    os.fstat(s.idle.pidfd)
    denied(s.post.read)
    assert s.idle.read().init == s.original.identity


def advance(monkeypatch, seconds):
    # Explicit consistent elapsed-clock fixture, NOT actual time namespace proof.
    original_read, original_mono = m.host_plan.clock.read, time.monotonic

    def read():
        value = original_read()
        amount = int(seconds * m.host_plan.clock.NS)
        return replace(
            value,
            before_ns=value.before_ns + amount,
            after_ns=value.after_ns + amount,
            boottime_ns=value.boottime_ns + amount,
        )

    monkeypatch.setattr(m.host_plan.clock, "read", read)
    monkeypatch.setattr(time, "monotonic", lambda: original_mono() + seconds)


def test_expired_readiness_is_not_renewed_by_post_begin_observation(joined, monkeypatch):
    s = joined
    old = s.plan.raw, s.idle.initial, s.guard.ready_by, s.guard.finish_by
    with monkeypatch.context() as patch:
        advance(patch, 65)
        assert time.monotonic() > s.post.ready_by
        result = s.post.read()
        assert result.original.sampled_at < s.plan.deadlines.ready_by < result.sampled_at
        assert old == (s.plan.raw, s.idle.initial, s.guard.ready_by, s.guard.finish_by)
        idle_tests.denied(s.idle.read)  # Original API still refuses; no renewal.
        denied(s.post.read)  # A poisoned borrowed Idle cannot later be reused.


@pytest.mark.parametrize("fault", ["lease", "claim", "replacement", "extra", "mode", "directory"])
def test_changed_original_files_never_become_a_new_baseline(joined, fault):
    s = joined
    path = s.original.claim
    if fault in ("lease", "claim"):
        path = s.original.lease if fault == "lease" else path
        path.write_bytes(path.read_bytes() + b" ")
    elif fault == "replacement":
        raw = path.read_bytes()
        path.rename(path.with_name("retained-original.json"))
        path.write_bytes(raw)
        path.chmod(0o600)
    elif fault == "extra":
        (path.parent / "renew.json").write_bytes(b"{}")
    elif fault == "mode":
        path.chmod(0o644)
    else:
        path.parent.chmod(0o755)
    denied(s.post.read)
    assert s.post.failed and not s.idle.closed and not s.original.witness.exited()
    denied(s.post.read)


@pytest.mark.parametrize(
    "fault",
    [
        "owner",
        "guard_owner",
        "ready",
        "generation",
        "source",
        "boot",
        "plan_pin",
        "command_path",
        "command_source",
        "ready_by",
        "finish_by",
        "watch_deadline",
        "clock",
        "domain",
        "domain_digest",
        "actor",
        "projection",
        "idle_failed",
        "idle_closed",
        "initial",
        "plan_object",
        "plan_nested",
        "plan_raw",
    ],
)
def test_changed_original_bindings_refuse_without_signalling_or_closing(joined, fault):
    s = joined
    if fault in ("ready_by", "finish_by", "watch_deadline"):
        setattr(s.guard, fault, getattr(s.guard, fault) + 1)
    elif fault in ("source", "boot", "plan_pin"):
        setattr(
            s.guard.pins.host,
            {"source": "source_sha256", "boot": "boot_id", "plan_pin": "plan_sha256"}[fault],
            "f" * 64,
        )
    elif fault == "generation":
        s.guard.pins.generation = "0" * 64
    elif fault in ("owner", "guard_owner"):
        (s.post if fault == "owner" else s.guard).owner = (-1, -1)
    elif fault == "ready":
        s.guard.ready = object()
    elif fault == "clock":
        s.guard.clock = m.host_plan.clock.read()
    elif fault == "domain":
        s.guard.zero_domain = object()
    elif fault == "domain_digest":
        s.guard.domain_sha256 = "0" * 64
    elif fault == "actor":
        s.guard.actors = (replace(s.idle.actor, start_ticks=s.idle.actor.start_ticks + 1),)
    elif fault == "projection":
        s.guard.pins.host.projection = object()
    elif fault == "command_path":
        s.guard.pins.command.plan = "/data/PRIVATE"
    elif fault == "command_source":
        s.guard.pins.command.source_sha256 = "0" * 64
    elif fault in ("idle_failed", "idle_closed"):
        setattr(s.idle, fault.removeprefix("idle_"), True)
    elif fault == "initial":
        s.idle.initial = replace(s.idle.initial, claim_sha256="f" * 64)
    elif fault == "plan_nested":
        object.__setattr__(s.plan.helper, "source", "f" * 64)
    elif fault == "plan_raw":
        object.__setattr__(s.plan, "raw", s.plan.raw + b" ")
    else:
        s.idle.plan = m.host_plan.load_bytes(s.plan.raw, s.plan.sha256)
    denied(s.post.read)
    assert not s.original.witness.exited()
    os.fstat(s.idle.pidfd)
    s.post.owner = os.getpid(), get_ident()  # Only to close this fixture-owned facade.


def test_substituted_original_descriptor_is_not_accepted(joined):
    s = joined
    previous, duplicate = s.idle.pidfd, os.dup(s.idle.pidfd)
    try:
        s.idle.pidfd = duplicate
        denied(s.post.read)
    finally:
        s.idle.pidfd = previous
        os.close(duplicate)
    assert not s.original.witness.exited()


@pytest.mark.parametrize("fault", ["files", "process", "retained", "interrupt"])
def test_uncertainty_consumes_facade_and_preserves_caller_handles(joined, monkeypatch, fault):
    s = joined
    error = KeyboardInterrupt if fault == "interrupt" else OSError

    def fail(*args):
        raise error("PRIVATE")

    if fault == "retained":
        monkeypatch.setattr(s.guard, "check", fail)
    else:
        monkeypatch.setattr(s.idle, "_files" if fault == "files" else "_process", fail)
    if fault == "interrupt":
        with pytest.raises(KeyboardInterrupt):
            s.post.read()
    else:
        denied(s.post.read)
    assert s.post.failed and not s.idle.closed and not s.original.witness.exited()
    os.fstat(s.idle.pidfd)
    denied(s.post.read)


@pytest.mark.parametrize("fault", ["deadline", "suspend", "reboot", "namespace", "backward"])
def test_original_clock_and_stop_bound_never_renew(joined, monkeypatch, fault):
    with monkeypatch.context() as patch:
        if fault == "deadline":
            advance(patch, 301)
        else:
            original = m.host_plan.clock.read

            def changed():
                value = original()
                if fault == "suspend":
                    return replace(value, boottime_ns=value.boottime_ns + m.host_plan.clock.NS)
                if fault == "reboot":
                    return replace(value, boot="f" * 32)
                if fault == "namespace":
                    return replace(value, namespace=(0, 1))
                return joined.plan.original_clock

            patch.setattr(m.host_plan.clock, "read", changed)
        denied(joined.post.read)
    assert not joined.original.witness.exited()


def test_original_idle_and_facade_locks_are_not_bypassed(joined):
    s = joined
    s.idle.lock.acquire()
    try:
        denied(s.post.read)
        assert s.idle.lock.locked()
    finally:
        s.idle.lock.release()


def test_foreign_thread_refuses_before_any_observation(joined):
    s = joined
    calls, errors = s.guard.calls, []
    thread = Thread(
        target=lambda: errors.append(pytest.raises(m.UnconfirmedPostBegin, s.post.read))
    )
    thread.start()
    thread.join(2)
    assert not thread.is_alive() and len(errors) == 1 and s.guard.calls == calls
    assert not s.original.witness.exited()


@pytest.mark.parametrize("bad", [frozenset({"init"}), frozenset({"other"}), {"native"}, None])
def test_only_retained_worker_exits_can_be_reported(joined, bad):
    joined.guard.exits = bad
    denied(joined.post.read)


def test_worker_exit_is_not_recording_success_or_init_exit(joined):
    s = joined
    s.guard.exits = frozenset({"native"})
    result = s.post.read()
    assert result.exited == s.guard.exits and not s.original.witness.exited()
    assert not hasattr(result, "success") and not hasattr(result, "native")
    s.guard.exits = frozenset()
    denied(s.post.read)


def test_collection_completion_does_not_reset_freshness(joined, monkeypatch):
    s = joined
    original = s.idle._files
    earliest = m.host_plan.clock.read().boottime_ns / m.host_plan.clock.NS

    def delayed():
        time.sleep(0.01)
        return original()

    monkeypatch.setattr(s.idle, "_files", delayed)
    observed = s.post.read()
    latest = m.host_plan.clock.read().boottime_ns / m.host_plan.clock.NS
    assert earliest <= observed.sampled_at <= latest - 0.015


def test_two_second_collection_limit_is_not_extended(joined, monkeypatch):
    s = joined
    original = s.idle._files
    first = True

    def late():
        nonlocal first
        result = original()
        if first:
            first = False
            advance(monkeypatch, 3)
        return result

    monkeypatch.setattr(s.idle, "_files", late)
    denied(s.post.read)


def test_missing_retained_begin_capability_cannot_construct(prepared):
    idle = prepared.observe()
    denied(lambda: m.PostBegin(idle, object()))
    assert not prepared.witness.exited() and not idle.closed


def test_freshness_includes_initial_binding_and_descriptor_checks(joined, monkeypatch):
    s = joined
    original = s.post._guard
    starts = []

    def delayed(deadline):
        starts.append(m.host_plan.clock.read().boottime_ns / m.host_plan.clock.NS)
        time.sleep(0.01)
        return original(deadline)

    monkeypatch.setattr(s.post, "_guard", delayed)
    result = s.post.read()
    assert result.sampled_at <= starts[0] < starts[1]


def test_unconfirmed_retained_actor_during_collection_cannot_return_continuity(joined, monkeypatch):
    s = joined
    original = s.idle._files

    def changed():
        result = original()
        s.guard.failed = True
        return result

    monkeypatch.setattr(s.idle, "_files", changed)
    denied(s.post.read)
    assert not s.original.witness.exited()


def test_original_init_exit_never_becomes_a_continuity_result(joined):
    s = joined
    child = s.original.child
    child.stdin.close()
    child.wait(timeout=3)
    assert child.returncode == 0 and s.original.witness.exited()
    denied(s.post.read)
    os.fstat(s.idle.pidfd)  # Still available for the actual owner to reconcile.
