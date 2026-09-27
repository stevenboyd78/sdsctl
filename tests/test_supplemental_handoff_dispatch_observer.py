"""Optional dispatch observation never replaces fresh policy or one-use intent."""

import pytest

from . import test_supplemental_handoff_host as old

h, p = old.h, old.p
journal = old.journal


@pytest.mark.parametrize("stage", ["before_create", "before_start"])
@pytest.mark.parametrize("fault", ["bool", "none", "wrong", "late", "generation", "callback"])
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
