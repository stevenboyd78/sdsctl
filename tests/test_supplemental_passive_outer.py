"""Reusable passive outer with actual original owners, never installed proof."""

import json
import select
import signal
import time
from types import SimpleNamespace

import pytest

from . import test_supplemental_observer_pipeline as pipeline

command, native = pipeline.command, pipeline.native
layout, image_umask, supervised = pipeline.layout, pipeline.image_umask, pipeline.supervised
image, configured, helper, joined = (
    pipeline.image,
    pipeline.configured,
    pipeline.helper,
    pipeline.joined,
)
binary, direct_launcher = pipeline.binary, pipeline.direct_launcher
reviewed_binary, pytestmark = pipeline.reviewed_binary, pipeline.pytestmark


@pytest.mark.parametrize("joined", [pipeline.SELECTION], indirect=True)
@pytest.mark.parametrize("after", ["plan", "writer"])
def test_late_accept_refuses_before_next_phase_without_renewing_original_cutoff(
    binary, direct_launcher, reviewed_binary, joined, monkeypatch, after
):
    s = joined
    accept, send = command.p.listeners.Listener.accept, command.p.send_observer_plan
    events, expired = [], []

    def accepted(listener):
        role = {
            s.plan_socket: "plan",
            s.writer_handoff: "writer",
            s.observer_handoff: "observer",
        }.get(listener.root)
        result = accept(listener)
        if role is not None:
            events.append(role + "_accept")
        if role == after:
            expired.append(True)
        return result

    def sent(*args, **kwargs):
        events.append("plan_send")
        return send(*args, **kwargs)

    def arm():
        command.arm_original_watch(
            s,
            native.selected_watch(
                "sealed-direct-owner-ingress", binary, None, direct_launcher, reviewed_binary
            ),
        )

    # Only this new outer's cooperative time view changes. Every peer, retained
    # clock, native timer and independent actual-exit cutoff remains original.
    monkeypatch.setattr(
        command.outer,
        "time",
        SimpleNamespace(monotonic=lambda: s.pipeline_end if expired else time.monotonic()),
    )
    monkeypatch.setattr(command.p.listeners.Listener, "accept", accepted)
    monkeypatch.setattr(command.p, "send_observer_plan", sent)
    assert s.sender.send() is None
    with pytest.raises(command.delivery_tests.m.UnconfirmedDelivery):
        command.finish(s, monkeypatch, arm)
    expected = ["plan_accept"]
    if after == "writer":
        expected += ["plan_send", "writer_accept"]
    assert events == expected
    assert s.watch.finished and s.watch.closed
    assert s.pair.passive_completion_attempted and not s.pair.channel_delivery_attempted
    files = {path.name: path.read_bytes() for path in s.case_root.iterdir() if path.is_file()}
    for fd in (s.h.witness.fd, s.counterpart.fd):
        assert time.monotonic() < s.pipeline_end
        assert select.select([fd], [], [], s.pipeline_end - time.monotonic())[0] == [fd]
    assert s.h.child.wait(timeout=0) == -signal.SIGKILL
    assert s.observer.wait(timeout=0) in (-signal.SIGKILL, 75)
    assert files == {
        path.name: path.read_bytes() for path in s.case_root.iterdir() if path.is_file()
    }


@pytest.mark.parametrize("joined", [pipeline.SELECTION], indirect=True)
def test_plan_exchange_rechecks_original_bytes_without_redecoding_the_relation(
    binary, direct_launcher, reviewed_binary, joined, monkeypatch
):
    checks, guards = [], []
    check = command.p.codec.Expectations.check_plan
    make_guard = command.p._plan_file_guard

    def observed(*args):
        checks.append(True)
        return check(*args)

    def retained(*args):
        before = len(checks)
        plan, domain, guard = make_guard(*args)
        try:
            assert len(checks) == before + 1  # Initial FULL structural validation.
        except BaseException:
            domain.close()  # Caller has not received ownership yet.
            raise

        def recheck():
            count = len(checks)
            guard()  # All original file/clock/domain checks, no structural parse.
            guards.append(True)
            assert len(checks) == count

        return plan, domain, recheck

    monkeypatch.setattr(command.p.codec.Expectations, "check_plan", observed)
    monkeypatch.setattr(command.p, "_plan_file_guard", retained)
    pipeline.exercise_original_observer_pipeline(
        binary, direct_launcher, reviewed_binary, joined, monkeypatch
    )
    assert len(guards) == 3


@pytest.mark.parametrize("joined", [pipeline.SELECTION], indirect=True)
@pytest.mark.parametrize("fault", ["template", "expectations", "expectations_bytes", "plan_field"])
def test_plan_relation_guard_refuses_replacement_or_changed_records(
    binary, direct_launcher, reviewed_binary, joined, monkeypatch, fault
):
    s = joined
    make_guard, confirmed = command.p._plan_file_guard, []
    before = {}

    def changed(exchange, inputs, counterpart, original):
        plan, domain, guard = make_guard(exchange, inputs, counterpart, original)
        try:
            if fault == "template":
                inputs.template = type(inputs.template)(inputs.template.raw)
            elif fault == "expectations":
                inputs.expectations = type(inputs.expectations)(inputs.expectations.raw)
            elif fault == "expectations_bytes":
                value = json.loads(inputs.expectations.raw)
                value["observer"]["hostname"] = "another-host"
                object.__setattr__(
                    inputs.expectations, "raw", command.p.codec.decode_peer_preparation(value).raw
                )
            else:
                object.__setattr__(plan, "case", "0" * 32)
            with pytest.raises(
                (command.p.UnconfirmedPreparation, command.outer.submission.intake.UnconfirmedInput)
            ):
                guard()
            confirmed.append(True)
        finally:
            domain.close()  # This test intercepted the return before caller ownership.
        raise command.p.UnconfirmedPreparation(command.p.MESSAGE)

    def arm():
        command.arm_original_watch(
            s,
            native.selected_watch(
                "sealed-direct-owner-ingress", binary, None, direct_launcher, reviewed_binary
            ),
        )
        before.update(
            (path.name, path.read_bytes()) for path in s.case_root.iterdir() if path.is_file()
        )

    monkeypatch.setattr(command.p, "_plan_file_guard", changed)
    assert s.sender.send() is None
    with pytest.raises(command.delivery_tests.m.UnconfirmedDelivery):
        command.finish(s, monkeypatch, arm)
    assert confirmed == [True]
    assert s.pair.passive_completion_attempted and not s.pair.channel_delivery_attempted
    assert s.watch.closed and s.watch.finished
    for fd in (s.h.witness.fd, s.counterpart.fd):
        assert time.monotonic() < s.pipeline_end
        assert select.select([fd], [], [], s.pipeline_end - time.monotonic())[0] == [fd]
    assert s.h.child.wait(timeout=0) == -signal.SIGKILL
    assert s.observer.wait(timeout=0) in (-signal.SIGKILL, 75)
    assert before == {
        path.name: path.read_bytes() for path in s.case_root.iterdir() if path.is_file()
    }


@pytest.mark.parametrize("joined", [pipeline.SELECTION], indirect=True)
@pytest.mark.parametrize(
    "fault",
    [
        "inputs",
        "plan",
        "local",
        "listener_roles",
        "duplicate_listener",
        "deadline",
        "expired",
        "shorter_listener",
    ],
)
def test_bound_outer_refusal_consumes_once_and_stops_originals_before_any_publication(
    binary, direct_launcher, reviewed_binary, joined, monkeypatch, fault
):
    s = joined
    end = s.pipeline_end
    complete = command.outer.complete
    calls, before, received = [], {}, {}
    construct = command.p.listeners.Listener.__init__

    def listener(owner, root, peer, *, deadline):
        if fault == "shorter_listener" and root == s.writer_handoff:
            deadline -= 0.01
        construct(owner, root, peer, deadline=deadline)

    def arm():
        command.arm_original_watch(
            s,
            native.selected_watch(
                "sealed-direct-owner-ingress", binary, None, direct_launcher, reviewed_binary
            ),
        )
        before.update(
            (path.name, path.read_bytes()) for path in s.case_root.iterdir() if path.is_file()
        )

    def refused(*args, **kwargs):
        received.update(args=args, kwargs=kwargs)
        values = list(args)
        if fault == "inputs":
            values[3] = "PRIVATE_UNAUTHENTICATED_INPUTS"
        elif fault == "plan":
            values[4] = None
        elif fault == "local":
            values[2] = s.counterpart.identity
        elif fault == "listener_roles":
            values[5], values[6] = values[6], values[5]
        elif fault == "duplicate_listener":
            values[7] = values[5]
        elif fault == "deadline":
            kwargs = {"deadline": False}
        elif fault == "expired":
            kwargs = {"deadline": 0}
        return complete(*values, **kwargs)

    def forbidden(*args, **kwargs):
        calls.append(True)
        pytest.fail("Refused outer reached a publication or handoff")

    monkeypatch.setattr(command.outer, "complete", refused)
    monkeypatch.setattr(command.p.listeners.Listener, "__init__", listener)
    monkeypatch.setattr(command.p, "send_observer_plan", forbidden)
    monkeypatch.setattr(command.outer.submission.Submission, "submit_before", forbidden)
    monkeypatch.setattr(command.p.bootstrap.Endpoint, "deliver", forbidden)
    assert s.sender.send() is None
    with pytest.raises(command.delivery_tests.m.UnconfirmedDelivery) as failure:
        command.finish(s, monkeypatch, arm)
    assert str(failure.value) == command.delivery_tests.m.MESSAGE
    assert "PRIVATE_UNAUTHENTICATED_INPUTS" not in repr(getattr(failure.value, "__notes__", []))
    assert s.pair.passive_completion_attempted and not s.pair.channel_delivery_attempted
    assert s.custody.armed_watch is s.watch and s.watch.closed and s.watch.finished
    assert s.staged_plan.deadline == s.staged_handoff.deadline == s.pipeline_end == end
    assert not s.plan_listener.accepted and not s.writer_listener.accepted
    assert not s.observer_listener.accepted and not calls
    for fd in (s.h.witness.fd, s.counterpart.fd):
        assert time.monotonic() < end
        assert select.select([fd], [], [], end - time.monotonic())[0] == [fd]
    assert s.h.child.wait(timeout=0) == -signal.SIGKILL
    assert s.observer.wait(timeout=0) in (-signal.SIGKILL, 75)
    assert set(before) == {"startup-claim.json", "plan.json"}
    # Even corrected arguments cannot reuse the consumed original pair/watch.
    with pytest.raises(command.delivery_tests.m.UnconfirmedDelivery):
        complete(*received["args"], **received["kwargs"])
    assert not calls
    assert before == {
        path.name: path.read_bytes() for path in s.case_root.iterdir() if path.is_file()
    }
