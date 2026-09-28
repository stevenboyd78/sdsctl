"""Pre-exec private staging joined to actual authenticated input delivery.

No stdin launch barrier, future PID, retry, renewed cutoff or active operation.
Process/stat/pidfd/socket/path evidence is real; cgroup/root/source/runtime and
installation provenance are explicit offline fixtures, not host qualification.
"""

import json
import os
import select
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack, closing
from dataclasses import asdict
from pathlib import Path

import pytest

from . import test_supplemental_recording_peer_preparation as preparation

p, m, transport = preparation.m, preparation.m.listeners, preparation.transport
(
    layout,
    tree,
    routing,
    projection,
    binding,
    directory,
    prepared,
    joined,
    before_handoff,
    service_case,
) = (
    preparation.layout,
    preparation.tree,
    preparation.routing,
    preparation.projection,
    preparation.binding,
    preparation.directory,
    preparation.prepared,
    preparation.joined,
    preparation.before_handoff,
    preparation.service_case,
)

pytestmark = pytest.mark.parametrize("service_case", ["published-peer-inputs"], indirect=True)

CLIENT = r"""
import json, os, select, sys, time
from pathlib import Path
from contextlib import ExitStack, closing
sys.path[:0] = [sys.argv[1], str(Path(sys.argv[1]).parent / "src")]
import supplemental_recording_peer_preparation as p
config = json.loads(Path(sys.argv[2]).read_bytes())  # Synthetic fixture paths only.
for module in (p, p.connections, p.inputs_module, p.domains):
    module.ROOT_UID = os.geteuid()
table = {item["pid"]: item["container_id"] for item in config["identities"]}
table[os.getpid()] = "b" * 64
def identity(pid, cid):
    assert table[pid] == cid
    return p.domains.process.process_identity(pid, cid, Path(f"/proc/{pid}/stat").read_text(),
        f"0::/system.slice/docker-{cid}.scope\n")
p.domains.process.read_identity = identity
p.inputs_module.declarations.declaration_root = lambda case: Path(config["template_root"])
p.inputs_module.inputs_root = lambda case: Path(config["input_root"])
p.preparation_root = lambda case, role: Path(config["root"])
before = len(os.listdir("/proc/self/fd"))
with ExitStack() as stack:
    declaration = stack.enter_context(p.inputs_module.declarations.Declaration(
        Path(config["template_root"]), config["template_sha256"]))
    local = identity(os.getpid(), "b" * 64)
    timer = stack.enter_context(closing(p.domains.clock.ClockWitness(p.domains.clock.read())))
    origin = timer.original
    outer = stack.enter_context(p.domains.process.ProcessWitness(
        identity(config["outer"], table[config["outer"]])))
    # Enter immediately after exec/setup: no stdin read or parent startup signal.
    connection = stack.enter_context(closing(p.connections.Connection(
        Path(config["root"]), outer, deadline=config["deadline"])))
    with p.receive_inputs(declaration, connection, timer, local, role=config["role"],
            baseline_sha256=config["baseline_sha256"], preparation=True) as (inputs, other):
        assert timer.original is origin and inputs.declaration is declaration
        result = dict(input_pin=inputs.expected, counterpart=other.identity.pid,
            deadline=connection.deadline, clock_preserved=True)
        # Post-exchange fixture retirement only, never a startup/action grant.
        assert select.select([connection.channel], [], [],
            max(0, config["deadline"] - time.monotonic()))[0]
        assert connection.channel.recv(128) == b"fixture-retire"
        assert time.monotonic() < config["deadline"]
result["fd_delta"] = len(os.listdir("/proc/self/fd")) - before
print(json.dumps(result), flush=True)
raise SystemExit(75)
"""


@pytest.fixture
def staged(service_case, monkeypatch, tmp_path):
    for module in (m, p, p.inputs_module, p.domains):
        monkeypatch.setattr(module, "ROOT_UID", os.geteuid())

    def identity(pid, cid):
        return p.domains.process.process_identity(
            pid,
            cid,
            Path(f"/proc/{pid}/stat").read_text(),
            f"0::/system.slice/docker-{cid}.scope\n",
        )

    monkeypatch.setattr(p.domains.process, "read_identity", identity)
    with ExitStack() as stack:
        short = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="sds-stage-")))
        root = short / "input"
        root.mkdir(mode=0o700)
        monkeypatch.setattr(p, "preparation_root", lambda *_: root)
        other = subprocess.Popen(
            [sys.executable, "-I", "-B", "-c", "import sys; sys.stdin.buffer.read()"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        def stop(child):
            if child.poll() is None:
                child.kill()  # Only this fixture's original disposable child.
            child.wait(timeout=3)
            for stream in (child.stdin, child.stdout, child.stderr):
                if stream is not None:
                    stream.close()

        stack.callback(stop, other)
        counterpart = stack.enter_context(
            p.domains.process.ProcessWitness(identity(other.pid, "c" * 64))
        )
        timer = stack.enter_context(closing(p.domains.clock.ClockWitness(p.domains.clock.read())))
        local = identity(os.getpid(), "a" * 64)
        values = json.loads(service_case.expected_inputs.raw)
        values.update(
            kind=p.codec.PREPARATION_KIND,
            source_kind=p.codec.peer_source.PreparationProfile.KIND,
        )
        expected = p.codec.decode_peer_preparation(values)
        (service_case.peer_input_root / p.inputs_module.NAME).write_bytes(expected.raw)
        inputs = stack.enter_context(
            closing(
                p.inputs_module.Inputs(
                    service_case.declaration,
                    service_case.peer_input_root,
                    expected.sha256,
                )
            )
        )
        listener = stack.enter_context(
            closing(m.Listener.stage(root, deadline=time.monotonic() + 2))
        )
        original = (
            listener.deadline,
            tuple(listener.handles),
            listener.listener,
            listener.node_pin,
        )

        def spawn(role="writer"):
            config = short / "fixture.json"
            config.write_text(
                json.dumps(
                    dict(
                        root=str(root),
                        deadline=listener.deadline,
                        outer=local.pid,
                        identities=[asdict(local), asdict(counterpart.identity)],
                        template_root=str(service_case.declaration.root),
                        template_sha256=service_case.template.sha256,
                        input_root=str(service_case.peer_input_root),
                        role=role,
                        baseline_sha256="d" * 64,
                    )
                )
            )
            config.chmod(0o600)
            child = subprocess.Popen(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    "-c",
                    CLIENT,
                    str(Path(p.__file__).parent),
                    str(config),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
            stack.callback(stop, child)
            witness = stack.enter_context(
                p.domains.process.ProcessWitness(identity(child.pid, "b" * 64))
            )
            return child, witness

        yield listener, original, spawn, inputs, timer, local, counterpart


def refuse(call):
    with pytest.raises(m.UnconfirmedListener) as error:
        call()
    assert str(error.value) == m.MESSAGE and error.value.__suppress_context__


@pytest.mark.parametrize("role", ["writer", "observer"])
def test_socket_precedes_exec_then_same_owner_authenticates_original_inputs(staged, role):
    listener, original, spawn, inputs, timer, local, counterpart = staged
    assert listener.peer is None and not listener.bound and not listener.binding_attempted
    assert listener.recheck() is None and not listener.accepted
    child, witness = spawn(role)
    assert child.stdin is None
    queued = select.poll()
    queued.register(listener.listener.fileno(), select.POLLIN)
    assert queued.poll(max(0, int((original[0] - time.monotonic()) * 1000))) == [
        (listener.listener.fileno(), select.POLLIN)
    ]
    assert listener.peer is None  # Real connection arrived BEFORE witness binding.
    listener.bind_peer(witness)
    assert listener.peer is witness and listener.bound and listener.binding_attempted
    assert original == (
        listener.deadline,
        tuple(listener.handles),
        listener.listener,
        listener.node_pin,
    )
    listener.accept()
    receipt = p.send_inputs(
        inputs,
        listener,
        timer,
        local,
        role=role,
        baseline_sha256="d" * 64,
        counterpart=counterpart,
        preparation=True,
    )
    assert receipt and listener.preparation_attempted and inputs.recheck() is inputs.expectations
    listener.channel.send(b"fixture-retire")
    observed = json.loads(transport.line(child))
    assert observed == dict(
        input_pin=inputs.expected,
        counterpart=counterpart.identity.pid,
        deadline=original[0],
        clock_preserved=True,
        fd_delta=0,
    )
    assert child.wait(timeout=3) == 75
    pin = (listener.root / m.NAME).stat()
    listener.close()
    assert (listener.root / m.NAME).stat() == pin


@pytest.mark.parametrize(
    "fault",
    [
        "unbound-accept",
        "invalid-peer",
        "expiry",
        "second-bind",
        "substituted-peer",
        "unselected-bind",
        "changed-path",
        "changed-cutoff",
    ],
)
def test_staging_refuses_without_new_time_or_adopting_any_peer(staged, fault):
    listener, original, spawn, _, _, _, counterpart = staged
    if fault == "unbound-accept":
        refuse(listener.accept)
    elif fault == "invalid-peer":
        refuse(lambda: listener.bind_peer(None))
    elif fault == "expiry":
        select.select([], [], [], max(0, listener.deadline - time.monotonic()) + 0.01)
        refuse(lambda: listener.bind_peer(counterpart))
    elif fault == "changed-path":
        (listener.root / "unexpected").touch()
        refuse(lambda: listener.bind_peer(counterpart))
    elif fault == "changed-cutoff":
        listener.deadline += 1
        refuse(lambda: listener.bind_peer(counterpart))
    elif fault == "unselected-bind":
        listener.staged = False
        refuse(lambda: listener.bind_peer(counterpart))
    else:
        child, witness = spawn()
        listener.bind_peer(witness)
        if fault == "second-bind":
            refuse(lambda: listener.bind_peer(witness))
        else:
            listener.peer = counterpart
            refuse(listener.recheck)
        assert child.pid == witness.identity.pid
    assert listener.closed and listener.failed and not listener.accepted
    assert not counterpart.exited() and (listener.root / m.NAME).is_socket()
    assert original[0] == listener.initial[1]  # No new deadline even on refusal.
    refuse(lambda: m.Listener.stage(listener.root, deadline=time.monotonic() + 2))


def test_queued_wrong_incarnation_is_not_discovered_or_accepted(staged):
    listener, _, spawn, _, _, _, counterpart = staged
    child, _ = spawn()
    listener.bind_peer(
        counterpart
    )  # Independently expected other original, never socket discovery.
    refuse(listener.accept)
    assert listener.closed and listener.failed and not counterpart.exited()
    assert child.poll() is None or child.returncode != 0


def test_legacy_constructor_still_requires_original_peer_before_any_path_write(staged, monkeypatch):
    listener, _, _, _, _, _, _ = staged
    with monkeypatch.context() as patch:
        patch.setattr(m.os, "open", lambda *a, **kw: pytest.fail("Must not open"))
        refuse(lambda: m.Listener(listener.root, None, deadline=time.monotonic() + 2))
    assert listener.recheck() is None and not listener.bound


def test_staged_selection_cannot_be_reused_as_a_new_binding_slot(staged):
    listener, _, _, _, _, _, counterpart = staged
    listener.bind_peer(counterpart)
    listener.bound = False
    listener.binding_attempted = False
    refuse(lambda: listener.bind_peer(counterpart))
    assert listener.closed and listener.failed and not counterpart.exited()
