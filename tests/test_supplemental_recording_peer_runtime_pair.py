"""Two real retained child pidfds and file/environment inventories.

Engine containers, cmdlines, image/root routing and kernel-privilege results
are synthetic. No active commands, installed provenance or outer termination.
"""

import builtins
import copy
import io
import json
import os
import subprocess
import sys
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_peer_runtime_qualification as original
from ._supplemental_fixture_budget import integer_budget

m = original.m
layout, image_umask, supervised = original.layout, original.image_umask, original.supervised
image, configured, helper = original.image, original.configured, original.helper


def read_peer_inputs(template, expectations):
    # Ordinary collector tests supply synthetic immutable values as before.
    # Separate integration tests can retain real private inputs BEFORE either
    # original collector is built, without retrofitting qualified objects.
    return template, expectations


@pytest.fixture
def pair(helper, image, configured, monkeypatch, request):
    handoff = getattr(request, "param", False)
    assert type(handoff) is bool
    root = helper.root
    graph = m.declarations.peer_source if handoff else m.declarations.source
    for name in graph.HELPER_FILES - m.launch.helper_source.HELPER_FILES:
        path = root / m.launch.HelperQualification.HELPER / name
        path.write_bytes(b"raise RuntimeError('PRIVATE_OBSERVED_CODE_MUST_NOT_RUN')\n")
        path.chmod(0o644)
    supplied = json.loads(helper.plan.raw)
    supplied["helper"]["source"] = (
        graph.Layout(
            root / m.launch.plans.fixed.PACKAGE, root / m.launch.HelperQualification.HELPER
        )
        .observe()
        .sha256
    )
    plan = m.launch.plans.decode(supplied)
    template = m.declarations.templates.decode(
        dict(
            schema=1,
            kind=m.declarations.templates.KIND,
            plan={
                key: item
                for key, item in supplied.items()
                if key not in {"original_clock", "deadlines"}
            },
            budget=integer_budget(supplied["deadlines"]),
        )
    )
    values = dict(entry.split("=", 1) for entry in configured)
    values.update(HOME="/root", HOSTNAME=helper.args["hostname"])
    child = subprocess.Popen(
        [sys.executable, "-I", "-B", "-c", "import sys; sys.stdin.read()"],
        stdin=subprocess.PIPE,
        env=values,
    )
    cid = "8" * 64
    processes = m.launch.engine.dispatch.process
    observer_identity = processes.process_identity(
        child.pid,
        cid,
        Path(f"/proc/{child.pid}/stat").read_text(),
        f"0::/system.slice/docker-{cid}.scope\n",
    )
    read_identity = processes.read_identity

    def identity(pid, container_id):
        if (pid, container_id) == (child.pid, cid):
            return processes.process_identity(
                pid,
                cid,
                Path(f"/proc/{pid}/stat").read_text(),
                f"0::/system.slice/docker-{cid}.scope\n",
            )
        return read_identity(pid, container_id)

    monkeypatch.setattr(processes, "read_identity", identity)
    witness = None
    try:
        witness = processes.ProcessWitness(observer_identity)
        witnesses = dict(writer=helper.witness, observer=witness)
        children = dict(writer=helper.child, observer=child)
        commands, containers, roles = {}, {}, {}
        counts = {role: dict(container=0, kernel=0, command=0) for role in witnesses}
        image_reads = []
        for role in witnesses:
            command = (
                "/usr/local/bin/python",
                "-I",
                "-B",
                "/opt/sdsctl-recording-host/" + graph.__name__ + ".py",
                template.sha256,
                role,
            )
            container = copy.deepcopy(helper.container)
            container.update(
                Id=witnesses[role].identity.container_id,
                Name="/sdsctl-recording-handoff-"
                + plan.case
                + ("-observer" if role == "observer" else ""),
                Path=command[0],
                Args=list(command[1:]),
            )
            container["State"]["Pid"] = children[role].pid
            container["Config"]["Entrypoint"] = list(command)
            container["GraphDriver"]["Data"]["ID"] = container["Id"]
            commands[role], containers[role] = command, container
            roles[role] = dict(
                runtime=asdict(plan.helper),
                command_sha256=m.declarations.command_digest(command),
                configuration_sha256=original.original.configuration_pin(
                    container, plan.helper.environment
                ),
                **{
                    key: helper.args[key]
                    for key in ("image_environment_sha256", "timezone", "hostname", "architecture")
                },
            )
        decode = m.declarations.decode_peer_handoff if handoff else m.declarations.decode
        expected = decode(
            dict(
                schema=1,
                kind=m.declarations.PEER_KIND if handoff else m.declarations.KIND,
                template_sha256=template.sha256,
                source_kind=graph.KIND,
                **roles,
            )
        )
        template, expected = read_peer_inputs(template, expected)
        role_by_cid = {item["Id"]: role for role, item in containers.items()}
        original_open, original_os_open, original_stat = builtins.open, os.open, os.stat

        def open_file(path, *args, **kwargs):
            for role, member in children.items():
                if path == f"/proc/{member.pid}/cmdline":
                    counts[role]["command"] += 1
                    return io.BytesIO(b"\0".join(item.encode() for item in commands[role]) + b"\0")
                if path == f"/proc/{member.pid}/root/proc/{member.pid}/cgroup":
                    return io.BytesIO(
                        f"0::/system.slice/docker-{containers[role]['Id']}.scope\n".encode()
                    )
            return original_open(path, *args, **kwargs)

        def routed(path):
            if path == f"/proc/{child.pid}/root":
                return root
            if path == f"/proc/{child.pid}/ns/mnt":
                return helper.namespace_route
            return path

        def os_open(path, flags, *args, **kwargs):
            return original_os_open(routed(path), flags, *args, **kwargs)

        def os_stat(path, *args, **kwargs):
            return original_stat(routed(path), *args, **kwargs)

        def inspect(docker, selected):
            assert docker is helper.docker
            role = role_by_cid[selected]
            counts[role]["container"] += 1
            return copy.deepcopy(containers[role])

        def inspect_image(docker, selected):
            assert docker is helper.docker and selected == plan.helper.image
            image_reads.append(selected)
            return dict(Id=selected, Os="linux", Architecture="amd64", Config=dict(Env=image))

        def kernel(selected, *, deadline):
            role = next(role for role, item in witnesses.items() if item is selected)
            counts[role]["kernel"] += 1
            return m.launch.runtime.HelperKernel(
                "6" * 64, selected.identity, m.launch.time.monotonic()
            )

        monkeypatch.setattr(builtins, "open", open_file)
        monkeypatch.setattr(os, "open", os_open)
        monkeypatch.setattr(os, "stat", os_stat)
        monkeypatch.setattr(type(helper.docker), "container", inspect)
        monkeypatch.setattr(type(helper.docker), "image", inspect_image)
        monkeypatch.setattr(m.launch.runtime, "collect_helper_kernel", kernel)
        qualifiers = {}
        for role in witnesses:
            container = containers[role]
            qualifiers[role] = m.PeerRuntimeQualification(
                plan,
                witnesses[role],
                helper.docker,
                template=template,
                expectations=expected,
                expectations_sha256=expected.sha256,
                role=role,
                generation=m.launch.plans.ordinary.generation(
                    container, name=container["Name"][1:], image=plan.helper.image
                ),
                command=commands[role],
                peer_handoff=handoff,
            )
        result = m.PeerRuntimePair(**qualifiers)
        yield SimpleNamespace(
            obj=result,
            plan=plan,
            template=template,
            expectations=expected,
            qualifiers=qualifiers,
            witnesses=witnesses,
            children=children,
            containers=containers,
            commands=commands,
            counts=counts,
            image_reads=image_reads,
        )
    finally:
        child.stdin.close()
        child.wait(timeout=3)
        if witness is not None:
            witness.close()


def denied(obj):
    original.original.launch.denied(obj)
    assert obj.failed and obj.elapsed_seconds is None
    original.original.launch.denied(obj)


def test_both_original_peers_get_full_fresh_collection_in_one_window(pair):
    before = pair.plan.raw, pair.expectations.raw
    assert pair.obj() is None
    assert 0 <= pair.obj.elapsed_seconds < 2
    for role in m.declarations.ROLES:
        assert pair.counts[role]["container"] == 2
        assert pair.counts[role]["kernel"] == pair.counts[role]["command"] == 2
        assert not pair.witnesses[role].exited()
        assert pair.qualifiers[role].plan is pair.plan
    assert before == (pair.plan.raw, pair.expectations.raw)
    assert pair.image_reads == [pair.plan.helper.image] * 4
    assert pair.obj() is None
    assert all(pair.counts[role]["container"] == 4 for role in m.declarations.ROLES)
    assert pair.image_reads == [pair.plan.helper.image] * 8


@pytest.mark.parametrize("role", m.declarations.ROLES)
def test_actual_loss_of_either_original_peer_refuses_without_replacement(pair, role):
    pair.children[role].terminate()
    pair.children[role].wait(timeout=3)
    denied(pair.obj)
    assert all(pair.counts[role]["container"] == 0 for role in m.declarations.ROLES)


def test_writer_cannot_fill_both_roles_even_when_runtime_images_match(pair):
    assert pair.qualifiers["writer"].runtime_pin == pair.qualifiers["observer"].runtime_pin
    original.original.launch.denied(lambda: m.PeerRuntimePair(pair.obj.writer, pair.obj.writer))
    original.original.launch.denied(lambda: m.PeerRuntimePair(pair.obj.observer, pair.obj.writer))


@pytest.mark.parametrize("role", m.declarations.ROLES)
def test_one_sided_failure_never_becomes_pair_success(pair, role):
    pair.containers[role]["Config"]["Env"].append("PRIVATE_EXTRA=value")
    denied(pair.obj)
    assert pair.qualifiers[role].failed
    if role == "writer":
        assert pair.counts["observer"]["container"] == 0


def test_loss_of_writer_during_observer_collection_is_not_hidden_by_two_results(pair, monkeypatch):
    collect = pair.obj.observer._collect_before

    def finish(deadline):
        collect(deadline)
        pair.children["writer"].terminate()
        pair.children["writer"].wait(timeout=3)

    monkeypatch.setattr(pair.obj.observer, "_collect_before", finish)
    denied(pair.obj)
    assert pair.counts["writer"]["container"] == pair.counts["observer"]["container"] == 2


def test_pair_passes_one_absolute_deadline_to_both_collectors(pair, monkeypatch):
    ends = []
    for qualifier in pair.qualifiers.values():
        original_collect = qualifier._collect_before

        def narrowed(deadline, collect=original_collect):
            ends.append(deadline)
            return collect(deadline)

        monkeypatch.setattr(qualifier, "_collect_before", narrowed)
    assert pair.obj() is None
    assert len(ends) == 2 and ends[0] == ends[1]
    assert ends[0] <= pair.plan.lease["ready_by"]


def test_writer_cannot_renew_pair_budget_before_observer_collection(pair, monkeypatch):
    collect = pair.obj.writer._collect_before

    def late(deadline):
        collect(deadline)
        monkeypatch.setattr(m.launch.time, "monotonic", lambda: deadline + 0.01)

    monkeypatch.setattr(pair.obj.writer, "_collect_before", late)
    denied(pair.obj)
    assert pair.counts["writer"]["container"] == 2
    assert pair.counts["observer"]["container"] == 0


def test_pair_requires_one_original_plan_not_two_equal_plans(pair):
    pair.obj.observer.plan = m.launch.plans.decode(json.loads(pair.plan.raw))
    original.original.launch.denied(lambda: m.PeerRuntimePair(pair.obj.writer, pair.obj.observer))
    denied(pair.obj)


@pytest.mark.parametrize("field", ["template", "expectations", "docker"])
def test_equal_but_separately_supplied_owner_objects_cannot_join(pair, field):
    value = getattr(pair.obj.observer, field)
    changed = m.launch.plans.ordinary.Docker() if field == "docker" else replace(value)
    setattr(pair.obj.observer, field, changed)
    original.original.launch.denied(lambda: m.PeerRuntimePair(pair.obj.writer, pair.obj.observer))
    denied(pair.obj)


def test_observer_loss_while_writer_is_being_qualified_stops_before_second_collection(
    pair, monkeypatch
):
    collect = pair.obj.writer._collect_before

    def finish(deadline):
        collect(deadline)
        pair.children["observer"].terminate()
        pair.children["observer"].wait(timeout=3)

    monkeypatch.setattr(pair.obj.writer, "_collect_before", finish)
    denied(pair.obj)
    assert pair.counts["writer"]["container"] == 2
    assert pair.counts["observer"]["container"] == 0


def test_held_pair_lock_refuses_without_taking_ownership_of_child_processes(pair):
    pair.obj.lock.acquire()
    try:
        denied(pair.obj)
    finally:
        pair.obj.lock.release()
    assert all(not witness.exited() for witness in pair.witnesses.values())
    assert all(pair.counts[role]["container"] == 0 for role in m.declarations.ROLES)


def test_pair_never_acquires_action_authority_or_closes_borrowed_peers(pair, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Pair comparison attempted Engine mutation")

    monkeypatch.setattr(m.launch.plans.ordinary.Docker, "_request", forbidden)
    monkeypatch.setattr(m.launch.plans.ordinary.Docker, "create_execution", forbidden)
    monkeypatch.setattr(m.launch.plans.ordinary.Docker, "start_execution", forbidden)
    assert pair.obj() is None
    assert all(not witness.exited() for witness in pair.witnesses.values())
