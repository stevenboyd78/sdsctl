"""Real three-process input authentication; synthetic runtime/cgroup provenance.

No baseline, Engine, service, scanner, recording or App action is permitted by
this exchange. The writer-channel tests separately join the same inputs and
peers to the actual original Startup and passive dispatcher retirement.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack, closing
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_writer_channel as writer

m, transport = writer.prep, writer.transport
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
    writer.layout,
    writer.tree,
    writer.routing,
    writer.projection,
    writer.binding,
    writer.directory,
    writer.prepared,
    writer.joined,
    writer.before_handoff,
    writer.service_case,
)

pytestmark = pytest.mark.parametrize("service_case", ["published-peer-inputs"], indirect=True)

SENDER = r"""
import json, os, socket, struct, sys, time
from pathlib import Path
from contextlib import ExitStack, closing
sys.path[:0] = [sys.argv[1], str(Path(sys.argv[1]).parent / "src")]
import supplemental_recording_peer_preparation as p
p.ROOT_UID = p.listeners.ROOT_UID = p.connections.ROOT_UID = os.geteuid()
p.inputs_module.ROOT_UID = p.domains.ROOT_UID = os.geteuid()
print("ready", flush=True)
config = json.loads(sys.stdin.buffer.readline())
table = {item["pid"]: item["container_id"] for item in config["identities"]}
def identity(pid, cid):
    assert table[pid] == cid
    return p.domains.process.process_identity(pid, cid, Path(f"/proc/{pid}/stat").read_text(),
        f"0::/system.slice/docker-{cid}.scope\n")
p.domains.process.read_identity = identity
p.inputs_module.declarations.declaration_root = lambda case: Path(config["template_root"])
p.inputs_module.inputs_root = lambda case: Path(config["input_root"])
p.preparation_root = lambda case, role: Path(config["preparation_root"])
original_send = p._Exchange.send
def send(exchange, value):
    fault = config["fault"]
    if value.get("phase") == "inputs":
        if fault == "pin": value = value | {"expectations_sha256": "0" * 64}
        elif fault == "schema": value = value | {"schema": True}
        elif fault == "scope": value = value | {"scope": "App-action"}
        elif fault == "kind": value = value | {"kind": p.bootstrap.KIND}
        elif fault == "nonce": value = value | {"nonce": "0" * 64}
        elif fault == "case": value = value | {"case": "0" * 32}
        elif fault == "role": value = value | {"role": "observer"}
        elif fault == "baseline": value = value | {"baseline_sha256": "0" * 64}
        elif fault == "counterpart": value = value | {"counterpart": value["requester"]}
        elif fault == "extra": value = value | {"action": "start"}
        elif fault in {"rights", "truncated-rights"}:
            raw = p.links.base.encode(value)
            exchange.wait(sending=True)
            exchange.channel.sendmsg([raw], [(socket.SOL_SOCKET, socket.SCM_RIGHTS,
                struct.pack("i", exchange.remote.fd) * (20 if fault == "truncated-rights" else 1))])
            return p.links.base.checksum(value)
        elif fault == "descendant":
            # SO_PEERCRED is still the original outer, but per-packet credentials
            # must reject this freshly forked, unapproved sender incarnation.
            child = os.fork()
            if child == 0:
                exchange.channel.send(p.links.base.encode(value))
                os._exit(0)
            assert os.waitpid(child, 0) == (child, 0)
            return p.links.base.checksum(value)
        elif fault == "overlong":
            exchange.wait(sending=True)
            exchange.channel.send(b"x" * (p.MAX_BYTES + 1))
            return "0" * 64
        elif fault == "deadline":
            # Exercise the original bound without extending either endpoint.
            time.sleep(max(0, exchange.end - time.monotonic()) + .01)
    return original_send(exchange, value)
p._Exchange.send = send
original_close = p._Exchange.close
def retire(exchange):
    was_open = exchange.domain is not None and not exchange.domain.closed
    original_close(exchange)
    if was_open and config["fault"] == "retire-input":
        (Path(config["input_root"]) / p.inputs_module.NAME).write_bytes(b"PRIVATE changed")
p._Exchange.close = retire
with ExitStack() as stack:
    original = stack.enter_context(p.inputs_module.declarations.Declaration(
        Path(config["template_root"]), config["template_sha256"]))
    inputs = stack.enter_context(closing(p.inputs_module.Inputs(
        original, Path(config["input_root"]), config["expectations_sha256"])))
    requester = stack.enter_context(p.domains.process.ProcessWitness(
        identity(config["requester"], table[config["requester"]])))
    counterpart = stack.enter_context(p.domains.process.ProcessWitness(
        identity(config["counterpart"], table[config["counterpart"]])))
    timer = stack.enter_context(closing(p.domains.clock.ClockWitness(p.domains.clock.read())))
    owned = stack.enter_context(closing(p.listeners.Listener(
        Path(config["preparation_root"]), requester, deadline=time.monotonic() + 2)))
    print("listening", flush=True)
    try:
        owned.accept()
        receipt = p.send_inputs(inputs, owned, timer, identity(os.getpid(), table[os.getpid()]),
            role=config["role"], baseline_sha256=config["baseline_sha256"], counterpart=counterpart)
        print(json.dumps(dict(prepared=receipt, deadline=owned.deadline)), flush=True)
    except p.UnconfirmedPreparation:
        print("refused", flush=True)
    # Do not manufacture receiver success by terminating the original sender.
    sys.stdin.buffer.readline()
"""


def refused(call):
    with pytest.raises(m.UnconfirmedPreparation) as error:
        call()
    assert str(error.value) == m.MESSAGE and error.value.__suppress_context__


@pytest.fixture
def exchange(service_case, monkeypatch):
    s = service_case
    for module in (m, m.listeners, m.connections, m.inputs_module, m.domains):
        monkeypatch.setattr(module, "ROOT_UID", os.geteuid())
    processes = []

    def spawn(code):
        child = subprocess.Popen(
            [sys.executable, "-I", "-B", "-c", code, str(Path(m.__file__).parent)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        processes.append(child)
        assert transport.line(child) == "ready"
        return child

    with ExitStack() as stack:
        try:
            outer = spawn(SENDER)
            other = spawn('import sys; print("ready", flush=True); sys.stdin.buffer.readline()')
            table = {os.getpid(): "a" * 64, outer.pid: "b" * 64, other.pid: "c" * 64}

            def identity(pid, cid):
                assert table[pid] == cid
                return m.domains.process.process_identity(
                    pid,
                    cid,
                    Path(f"/proc/{pid}/stat").read_text(),
                    f"0::/system.slice/docker-{cid}.scope\n",
                )

            monkeypatch.setattr(m.domains.process, "read_identity", identity)
            identities = {pid: identity(pid, cid) for pid, cid in table.items()}
            witness = stack.enter_context(m.domains.process.ProcessWitness(identities[outer.pid]))
            timer = stack.enter_context(
                closing(m.domains.clock.ClockWitness(m.domains.clock.read()))
            )
            root = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="sds-prep-")))
            monkeypatch.setattr(m, "preparation_root", lambda case, role: root)
            used, ends, inputs, peers = [], [], [], []
            exchange_init, inputs_init = m._Exchange.__init__, m.inputs_module.Inputs.__init__
            peer_init = m.domains.process.ProcessWitness.__init__

            def construction(value, *args, **kwargs):
                exchange_init(value, *args, **kwargs)
                used.append(value)
                ends.append(value.end)

            def input_construction(value, *args, **kwargs):
                inputs_init(value, *args, **kwargs)
                inputs.append(value)

            def peer_construction(value, *args, **kwargs):
                peer_init(value, *args, **kwargs)
                peers.append(value)

            monkeypatch.setattr(m._Exchange, "__init__", construction)
            monkeypatch.setattr(m.inputs_module.Inputs, "__init__", input_construction)
            monkeypatch.setattr(m.domains.process.ProcessWitness, "__init__", peer_construction)
            e = SimpleNamespace(
                s=s,
                outer=outer,
                other=other,
                witness=witness,
                timer=timer,
                root=root,
                identities=identities,
                used=used,
                ends=ends,
                inputs=inputs,
                peers=peers,
            )

            def start(*, fault="", role="writer", baseline_pin="d" * 64, **changes):
                assert not hasattr(e, "connection")  # One attempt per fixture/case.
                transport.command(
                    outer,
                    dict(
                        identities=[asdict(item) for item in identities.values()],
                        requester=os.getpid(),
                        counterpart=other.pid,
                        role=role,
                        fault=fault,
                        template_root=str(s.declaration.root),
                        template_sha256=s.template.sha256,
                        input_root=str(s.peer_input_root),
                        expectations_sha256=s.expected_input_digest,
                        preparation_root=str(root),
                        baseline_sha256=baseline_pin,
                    ),
                )
                assert transport.line(outer) == "listening"
                e.connection = stack.enter_context(
                    closing(m.connections.Connection(root, witness, deadline=time.monotonic() + 2))
                )
                options = dict(role=role, baseline_sha256=baseline_pin) | changes
                return m.receive_inputs(
                    s.declaration, e.connection, timer, identities[os.getpid()], **options
                )

            e.start = start
            yield e
        finally:
            for child in processes:
                if child.poll() is None:
                    child.kill()  # Only original disposable fixture children.
                child.wait(timeout=3)
                for stream in (child.stdin, child.stdout, child.stderr):
                    stream.close()


@pytest.mark.parametrize("role", ["writer", "observer"])
def test_authenticated_original_pin_is_retained_before_baseline_without_grant(exchange, role):
    e = exchange
    context = e.start(role=role)
    before = writer.connections.fds()
    originals = {
        path: path.read_bytes()
        for path in (
            e.s.declaration.root / m.inputs_module.declarations.NAME,
            e.s.peer_input_root / m.inputs_module.NAME,
        )
    }
    with context as (inputs, counterpart):
        receipt = json.loads(transport.line(e.outer))
        assert receipt["prepared"] and e.connection.preparation_attempted
        assert inputs.expected == e.s.expected_input_digest
        assert inputs.expectations.raw == e.s.expected_inputs.raw
        assert inputs.declaration is e.s.declaration and inputs.template is e.s.startup.template
        assert counterpart.identity == e.identities[e.other.pid] and not counterpart.exited()
        assert e.s.startup.clock is None and e.s.clocks == [e.timer]
        assert not e.s.preflights and not e.s.cached_calls and not list(e.s.root.iterdir())
        assert e.ends == [e.connection.deadline] and not e.used[0].domain.closed
    assert inputs.closed and counterpart.fd == -1 and e.used[0].domain.closed
    assert writer.connections.fds() == before
    assert not e.timer.closed and not e.connection.closed and not e.s.declaration.closed
    assert e.witness.fd >= 0 and not e.witness.exited()
    assert {path: path.read_bytes() for path in originals} == originals
    # A second object cannot consume this original channel's used slot.
    refused(
        lambda: m.receive_inputs(
            e.s.declaration,
            e.connection,
            e.timer,
            e.identities[os.getpid()],
            role=role,
            baseline_sha256="d" * 64,
        ).__enter__()
    )
    assert writer.connections.fds() == before and e.ends == [e.connection.deadline]


@pytest.mark.parametrize(
    "fault",
    [
        "pin",
        "schema",
        "scope",
        "kind",
        "nonce",
        "case",
        "role",
        "baseline",
        "counterpart",
        "extra",
        "rights",
        "truncated-rights",
        "descendant",
        "overlong",
        "deadline",
    ],
)
def test_bad_offer_never_yields_inputs_or_changes_baseline(exchange, fault):
    e = exchange
    context = e.start(fault=fault)
    before = writer.connections.fds()
    refused(context.__enter__)
    assert writer.connections.fds() == before
    assert e.connection.preparation_attempted and all(x.domain.closed for x in e.used)
    assert all(x.closed for x in e.inputs) and all(x.fd == -1 for x in e.peers)
    assert e.s.startup.clock is None and not e.s.cached_calls and not list(e.s.root.iterdir())
    assert not e.s.declaration.closed and not e.timer.closed and not e.witness.exited()
    assert (e.root / m.connections.NAME).is_socket()


@pytest.mark.parametrize("phase", ["request", "retained"])
def test_sender_rejects_boolean_schema_and_does_not_issue_receipt(exchange, monkeypatch, phase):
    e = exchange
    send = m._Exchange.send

    def changed(value, frame):
        return send(value, frame | {"schema": True} if frame["phase"] == phase else frame)

    monkeypatch.setattr(m._Exchange, "send", changed)
    context = e.start()
    if phase == "request":
        refused(context.__enter__)
    else:
        # Receiver acknowledgment is NOT proof that the original outer accepted.
        with context:
            assert transport.line(e.outer) == "refused"
        return
    assert transport.line(e.outer) == "refused"


@pytest.mark.parametrize("fault", ["inputs", "declaration", "clock", "interrupt"])
def test_final_ack_boundary_drift_or_interrupt_never_yields(exchange, monkeypatch, fault):
    e = exchange
    send = m._Exchange.send

    def changed(value, frame):
        result = send(value, frame)
        if frame["phase"] == "retained":
            if fault == "inputs":
                (e.s.peer_input_root / m.inputs_module.NAME).write_bytes(b"PRIVATE changed")
            elif fault == "declaration":
                (e.s.declaration.root / m.inputs_module.declarations.NAME).write_bytes(
                    b"PRIVATE changed"
                )
            elif fault == "clock":
                e.timer.close()
            else:
                raise KeyboardInterrupt
        return result

    monkeypatch.setattr(m._Exchange, "send", changed)
    context = e.start()
    if fault == "interrupt":
        with pytest.raises(KeyboardInterrupt):
            context.__enter__()
    else:
        refused(context.__enter__)
    assert all(value.closed for value in e.inputs)
    assert all(value.fd == -1 for value in e.peers)
    assert all(value.domain.closed for value in e.used)
    assert not e.s.cached_calls and e.s.startup.clock is None and not list(e.s.root.iterdir())


def test_cleanup_retires_only_original_counterpart_fd_on_replacement(exchange):
    e = exchange
    context = e.start()
    with closing(os.fdopen(os.open("/dev/null", os.O_RDONLY), "rb")) as replacement:

        def changed():
            with context as (_, counterpart):
                assert json.loads(transport.line(e.outer))["prepared"]
                e.original_peer_fd = counterpart.fd
                counterpart.fd = replacement.fileno()

        refused(changed)
        os.fstat(replacement.fileno())
        with pytest.raises(OSError):
            os.fstat(e.original_peer_fd)
    assert e.inputs[0].closed and e.used[0].domain.closed


@pytest.mark.parametrize("fault", ["lost-ack", "retire-input"])
def test_receiver_ack_does_not_replace_outer_final_retirement(exchange, monkeypatch, fault):
    e = exchange
    send = m._Exchange.send

    def changed(value, frame):
        if frame["phase"] == "retained" and fault == "lost-ack":
            return m.links.base.checksum(frame)  # Suppressed packet, not success evidence.
        return send(value, frame)

    monkeypatch.setattr(m._Exchange, "send", changed)
    context = e.start(fault=fault)
    # There is deliberately no bilateral success/permission claim at yield.
    # Retirement drift may race the receiver's last input recheck.
    try:
        with context:
            assert transport.line(e.outer) == "refused"
    except m.UnconfirmedPreparation:
        assert fault == "retire-input"
        assert transport.line(e.outer) == "refused"
    assert not e.s.cached_calls and e.s.startup.clock is None
    assert all(value.closed for value in e.inputs)
    assert all(value.fd == -1 for value in e.peers)
    assert e.connection.preparation_attempted


def test_preparation_is_not_implicitly_in_any_existing_source_profile(service_case):
    # Both existing closed inventories retain their exact older selections.
    assert Path(m.__file__).stem not in m.codec.source.MODULES
    assert Path(m.__file__).stem not in m.codec.peer_source.MODULES
    assert service_case.startup.clock is None


def test_legacy_expectations_do_not_enable_preparation(exchange):
    e = exchange
    value = json.loads(e.s.expected_inputs.raw)
    value["kind"], value["source_kind"] = m.codec.KIND, m.codec.source.KIND
    legacy = m.codec.decode(value)
    # An independently pinned OLD input still cannot select the newer exchange.
    (e.s.peer_input_root / m.inputs_module.NAME).write_bytes(legacy.raw)
    e.s.expected_input_digest = legacy.sha256
    context = e.start()
    refused(context.__enter__)
    assert transport.line(e.outer) == "refused"
    assert not e.s.cached_calls and e.s.startup.clock is None


@pytest.mark.parametrize("fault", ["digest", "contents"])
def test_authenticated_exchange_cannot_bypass_persisted_baseline_read(exchange, monkeypatch, fault):
    e = exchange
    stored = e.s.projected.host
    protected = writer.m.startup.plans.projection.recording
    raw = protected.manifest_bytes(
        stored.baseline,
        stored.writer,
        stored.contract.audio_endpoint_sha256,
        maximum_recording_seconds=stored.contract.maximum_recording_seconds,
    )
    root = e.s.root.parent / "preparation-baseline"
    root.mkdir(mode=0o700)
    path = root / "baseline.json"
    path.write_bytes(raw)
    path.chmod(0o600)
    pin = "0" * 64 if fault == "digest" else stored.manifest_sha256
    with e.start(baseline_pin=pin) as (inputs, counterpart):
        assert json.loads(transport.line(e.outer))["prepared"]
        assert inputs.expected == e.s.expected_input_digest and not counterpart.exited()
        e.connection.close()  # Preparation cannot be reused for the next phase.
        if fault == "contents":
            path.write_bytes(b"PRIVATE changed baseline")
        preserved = path.read_bytes()

        def forbidden(*_args):
            pytest.fail("Bad baseline reached a host read")

        monkeypatch.setattr(writer.assembly.baseline_tests.launch.PreHandoffHost, "read", forbidden)
        with pytest.raises(writer.m.startup.UnconfirmedStartup):
            e.s.startup.prepare_service_from_baseline(root, pin, e.s.docker)
        assert path.read_bytes() == preserved
        assert e.s.startup.clock is None and e.s.startup.closed and e.s.startup.failed
        assert not e.s.cached_calls and not e.s.preflights and not list(e.s.root.iterdir())
        assert len(e.s.clocks) == 2 and e.s.clocks[-1].closed
