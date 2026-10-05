"""Optional dispatch observation never replaces fresh policy or one-use intent."""

from dataclasses import replace

import pytest

from . import test_supplemental_handoff_host as old

h, p = old.h, old.p
journal = old.journal


@pytest.mark.parametrize(
    "field,value",
    [
        ("plan_sha256", "bad"),
        ("generation", True),
        ("process", object()),
        ("history", ()),
        ("history", [b"x"]),
        ("history", ("x",)),
        ("history", (b"",)),
        ("history", (b"x",) * 65),
        ("history", (b"x" * (h.MAX_BYTES + 1),)),
    ],
)
def test_candidate_notice_rejects_unbounded_or_wrong_evidence(field, value):
    notice = h.CandidateNotice(
        "a" * 64, "b" * 64, h.ProcessIdentity(123, 456, "c" * 64), (b"fixture",)
    )
    with pytest.raises(p.UnsafeHandoff):
        replace(notice, **{field: value})


def test_candidate_receipt_binds_every_field_without_becoming_an_action():
    notice = h.CandidateNotice(
        "a" * 64, "b" * 64, h.ProcessIdentity(123, 456, "c" * 64), (b"fixture",)
    )
    alternatives = (
        replace(notice, plan_sha256="d" * 64),
        replace(notice, generation="d" * 64),
        replace(notice, process=replace(notice.process, start_ticks=457)),
        replace(notice, history=(b"other",)),
    )
    assert all(other.receipt != notice.receipt for other in alternatives)
    assert not isinstance(notice, p.Action)


@pytest.mark.parametrize("stage", ["before_create", "before_start"])
@pytest.mark.parametrize(
    "fault", ["bool", "none", "wrong", "late", "generation", "callback", "callback_pair"]
)
def test_receipt_or_post_ack_fault_never_repeats_or_starts(journal, stage, fault):
    docker = old.FakeDocker()
    stamp = [12.2]
    notices = []

    def observe(notice):
        notices.append(notice)
        if notice.stage == stage:
            if fault in ("bool", "none", "wrong"):
                return {"bool": True, "none": None, "wrong": "e" * 64}[fault]
            if fault == "late":
                stamp[0] = 14.01
            if fault == "generation":
                docker.cli["State"]["Pid"] += 1
            if fault == "callback":
                send.observe = None
            if fault == "callback_pair":
                send.observe = send._original_observe = None
        return notice.receipt

    send = h.TrackedDispatch(
        journal,
        docker,
        cli_image=old.IMAGE,
        cli_generation=h.generation(old.container(), name=h.CLI, image=old.IMAGE),
        now=lambda: stamp[0],
        observe=observe,
    )
    old.intent(journal)
    with pytest.raises(p.UnsafeHandoff):
        send(old.COMMAND, old.CASE)
    assert len(docker.created) == (stage == "before_start")
    assert not docker.started
    with pytest.raises(p.UnsafeHandoff):
        send(old.COMMAND, old.CASE)
    assert not docker.started


@pytest.mark.parametrize("fault", ["entries", "machine", "clock", "docker", "execution"])
def test_same_phase_post_ack_substitution_is_not_accepted(journal, fault):
    docker = old.FakeDocker()

    def observe(notice):
        if notice.stage == "before_start":
            if fault == "entries":
                journal.entries[-1]["event"]["now"] += 0.01
            elif fault == "machine":
                from copy import deepcopy

                journal.machine = deepcopy(journal.machine)
            elif fault == "clock":
                send.now = lambda: 12.2
            elif fault == "docker":
                send.docker = old.FakeDocker()
            else:
                docker.exec.update(Running=True, Pid=123)
        return notice.receipt

    send = h.TrackedDispatch(
        journal,
        docker,
        cli_image=old.IMAGE,
        cli_generation=h.generation(old.container(), name=h.CLI, image=old.IMAGE),
        now=lambda: 12.2,
        observe=observe,
    )
    old.intent(journal)
    with pytest.raises(p.UnsafeHandoff):
        send(old.COMMAND, old.CASE)
    assert len(docker.created) == 1 and not docker.started


@pytest.mark.parametrize("boundary", ["initial_inspect", "create_return", "final_inspect"])
def test_callback_pair_is_pinned_across_engine_boundaries(journal, monkeypatch, boundary):
    docker, notices = old.FakeDocker(), []
    original_container = docker.container
    original_create = docker.create_execution
    original_inspect = docker.inspect_execution
    inspections = []

    def changed():
        send.observe = send._original_observe = None

    def container(name):
        result = original_container(name)
        if boundary == "initial_inspect":
            changed()
        return result

    def create(cid, command):
        result = original_create(cid, command)
        if boundary == "create_return":
            changed()
        return result

    def inspect(eid):
        result = original_inspect(eid)
        inspections.append(eid)
        if boundary == "final_inspect" and len(inspections) == 2:
            changed()
        return result

    def observe(notice):
        notices.append(notice)
        return notice.receipt

    send = h.TrackedDispatch(
        journal,
        docker,
        cli_image=old.IMAGE,
        cli_generation=h.generation(old.container(), name=h.CLI, image=old.IMAGE),
        now=lambda: 12.2,
        observe=observe,
    )
    monkeypatch.setattr(docker, "container", container)
    monkeypatch.setattr(docker, "create_execution", create)
    monkeypatch.setattr(docker, "inspect_execution", inspect)
    old.intent(journal)
    with pytest.raises(p.UnsafeHandoff):
        send(old.COMMAND, old.CASE)
    assert len(docker.created) == (boundary != "initial_inspect") and not docker.started
    assert len(notices) == {"initial_inspect": 0, "create_return": 1, "final_inspect": 2}[boundary]
    before = tuple(journal.entries)
    with pytest.raises(p.UnsafeHandoff):
        send(old.COMMAND, old.CASE)
    assert tuple(journal.entries) == before and not docker.started
