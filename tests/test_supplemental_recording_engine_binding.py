"""Actual Engine transport / owned pidfd join, synthetic Engine and namespaces.

No Docker access or scanner commands. The server replies and proc namespace
mapping are explicit fixtures; process creation, pidfds and signals are real.
"""

import os
import select
import signal
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_engine as engine
from . import test_supplemental_recording_probe as probe

m, ns = engine.m, engine.namespaces
layout, tree, routing, projection, binding, directory, prepared = (
    engine.layout,
    engine.tree,
    engine.routing,
    engine.projection,
    engine.binding,
    engine.directory,
    engine.prepared,
)
family = probe.family


@pytest.fixture
def actors(prepared, family, monkeypatch):
    initial = prepared.pins.init
    points = (family.expected.guardian, family.expected.native, family.expected.watchdog)
    values = (
        ns.m.Actor(initial.pid, 1, os.getpid(), initial.start_ticks, initial.container_id, ns.NS),
        *(
            ns.m.Actor(
                item.pid,
                index + 2,
                os.getpid() if index == 0 else points[0].pid,
                item.start_ticks,
                initial.container_id,
                ns.NS,
            )
            for index, item in enumerate(points)
        ),
    )
    lookup = {actor.host_pid: actor for actor in values}

    def read(pid, cid):
        assert cid == initial.container_id and pid in lookup
        fields = Path(f"/proc/{pid}/stat").read_text().rpartition(") ")[2].split()
        ns.m.require(fields[0] in ("R", "S", "I") and int(fields[19]) == lookup[pid].start_ticks)
        return lookup[pid]

    monkeypatch.setattr(ns.m, "read", read)
    monkeypatch.setattr(ns.m.Witness, "_host_domains", staticmethod(lambda: ns.NS[3:]))
    return SimpleNamespace(values=values, lookup=lookup, report=ns.reports(values))


def live(prepared, actors):
    result = engine.intents.metadata(prepared)
    result.update(Running=True, Pid=actors.values[1].host_pid)
    return result


@contextmanager
def ready(
    prepared,
    actors,
    monkeypatch,
    *,
    responses=None,
    consume=True,
    message=None,
    finish_extra=5,
    tap=None,
):
    retained = []

    def upgrade(peer, _request):
        # Keep the upgraded stream open while the same real server accepts the
        # two independent inspection connections. No recording begin is sent.
        retained.append(peer.dup())
        if tap is not None:
            tap.append(retained[-1])
        value = message() if callable(message) else message
        peer.sendall(
            engine.attached.stream.UPGRADE
            + engine.attached.stream.segment(
                engine.attached.stream.app({"phase": "ready"} if value is None else value)
            )
        )

    handlers = [
        engine.reply({"Id": engine.attached.EXEC}, 201),
        engine.reply(engine.intents.metadata(prepared)),
        upgrade,
        *(responses if responses is not None else [engine.reply(live(prepared, actors))] * 2),
    ]
    try:
        with engine.engine(prepared, monkeypatch, handlers) as (client, requests):
            client.create()
            channel = client.attach(finish_by=prepared.pins.command.ready_by + finish_extra)
            if consume:
                assert channel.receive(deadline=channel.ready_by) == {"phase": "ready"}
            yield client, requests
    finally:
        for channel in retained:
            channel.close()


def test_two_authenticated_inspections_surround_retained_exact_actor_mapping(
    prepared, actors, monkeypatch
):
    with ready(prepared, actors, monkeypatch) as (client, requests):
        witness = client.bind_processes(actors.report)
        try:
            assert witness.refresh() == actors.values and len(witness.handles) == 4
            assert client.binding_attempted and not client.attachment.begun
            assert len(requests) == 5
            assert all(
                request[0] == f"GET /v1.47/exec/{engine.attached.EXEC}/json HTTP/1.1"
                and request[2] is None
                for request in requests[3:]
            )
            assert len(list(prepared.directory.iterdir())) == 3
            client.close()
            assert all(not witness.exited(role) for role in witness.handles)
            assert witness.refresh() == actors.values  # Independent of transport lifetime.
        finally:
            witness.close()
        assert not prepared.witness.exited()


@pytest.mark.parametrize("position", [0, 1])
@pytest.mark.parametrize(
    "fault", ["created", "starting", "stopped", "pid", "id", "container", "argv", "lost"]
)
def test_changed_or_lost_exact_exec_never_binds_or_retries(
    prepared, actors, monkeypatch, position, fault
):
    changed = live(prepared, actors)
    if fault == "created":
        changed.update(Running=False, Pid=0)
    elif fault == "starting":
        changed["Pid"] = 0
    elif fault == "stopped":
        changed.update(Running=False, ExitCode=0)
    elif fault == "pid":
        changed["Pid"] = actors.values[0].host_pid
    elif fault == "id":
        changed["ID"] = "f" * 64
    elif fault == "container":
        changed["ContainerID"] = "f" * 64
    elif fault == "argv":
        changed["ProcessConfig"]["arguments"].append("PRIVATE")
    responses = [engine.reply(live(prepared, actors))] * position
    responses.append(b"" if fault == "lost" else engine.reply(changed))
    with ready(prepared, actors, monkeypatch, responses=responses) as (client, requests):
        descriptors = len(os.listdir("/proc/self/fd"))
        engine.refused(lambda: client.bind_processes(actors.report), client)
        assert len(requests) == 4 + position and not prepared.witness.exited()
        assert len(os.listdir("/proc/self/fd")) < descriptors  # All partial witness handles closed.
        assert engine.intents.m.load(prepared.directory, prepared.pins).phase == "attach_intent"


@pytest.mark.parametrize("fault", ["unread", "begun", "duplicate", "closed"])
def test_only_once_between_actual_ready_read_and_begin(prepared, actors, monkeypatch, fault):
    witness = None
    with ready(prepared, actors, monkeypatch, consume=fault != "unread") as (client, requests):
        try:
            if fault == "begun":
                # Only the existing framing mechanism; this fixture message is
                # not a host-authorized recording request and no daemon exists.
                client.attachment.send_begin(
                    engine.attached.BEGIN, deadline=client.attachment.ready_by
                )
            elif fault == "duplicate":
                witness = client.bind_processes(actors.report)
            elif fault == "closed":
                client.attachment.close()
            count = len(requests)
            engine.refused(lambda: client.bind_processes(actors.report), client)
            assert len(requests) == count
            if witness is not None:
                assert witness.refresh() == actors.values
        finally:
            if witness is not None:
                witness.close()


@pytest.mark.parametrize("role", ["guardian", "native", "watchdog"])
def test_wrong_reported_identity_refuses_without_second_inspection(
    prepared, actors, monkeypatch, role
):
    actors.report[role]["start_ticks"] += 1
    with ready(prepared, actors, monkeypatch, responses=[engine.reply(live(prepared, actors))]) as (
        client,
        requests,
    ):
        engine.refused(lambda: client.bind_processes(actors.report), client)
        assert len(requests) == 4


@pytest.mark.parametrize("fault", ["native_exit", "namespace_change", "history_change"])
def test_change_during_second_inspection_invalidates_partial_binding(
    prepared, actors, family, monkeypatch, fault
):
    def changed(peer, _request):
        if fault == "native_exit":
            signal.pidfd_send_signal(family.handles[1], signal.SIGKILL)
            assert select.select([family.handles[1]], [], [], 2)[0]
        elif fault == "namespace_change":
            actor = actors.values[2]
            actors.lookup[actor.host_pid] = replace(actor, local_pid=99)
        else:
            (prepared.directory / "0002.json").write_bytes(b"PRIVATE")
        peer.sendall(engine.reply(live(prepared, actors)))

    responses = [engine.reply(live(prepared, actors)), changed]
    with ready(prepared, actors, monkeypatch, responses=responses) as (client, requests):
        engine.refused(lambda: client.bind_processes(actors.report), client)
        assert len(requests) == 5 and not prepared.witness.exited()
