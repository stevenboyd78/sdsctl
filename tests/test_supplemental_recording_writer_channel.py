"""Real original Startup, plan files and 3-process descriptor/Link handoff.

Host/Engine reads, cgroups and runtime declaration provenance are synthetic.
The parent is the original writer; the outer child creates/delivers both channel
halves and a separate observer child uses Link. Unlike a reconstructed writer
fixture, the writer keeps the exact clock created AFTER its baseline read.
No installed qualification, App consent, scanner I/O or live services.
"""

import importlib.util
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_peer_bootstrap as transport
from . import test_supplemental_recording_peer_connection as connections
from . import test_supplemental_recording_peer_inputs as retained_inputs
from . import test_supplemental_recording_peer_listener as preparation_listeners
from . import test_supplemental_recording_permission_probe as preflight_probe
from . import test_supplemental_recording_service_runtime_expectations as declared
from . import test_supplemental_recording_startup_assembly as assembly

NAME = "supplemental_recording_writer_channel"
SPEC = importlib.util.spec_from_file_location(
    NAME, Path(transport.m.__file__).with_name(NAME + ".py")
)
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)

PREPARATION = "supplemental_recording_peer_preparation"
PREP_SPEC = importlib.util.spec_from_file_location(
    PREPARATION, Path(m.__file__).with_name(PREPARATION + ".py")
)
prep = importlib.util.module_from_spec(PREP_SPEC)
sys.modules[PREPARATION] = prep
PREP_SPEC.loader.exec_module(prep)
assert prep.listeners is preparation_listeners.m

assert m.startup is assembly.m and m.expectations is declared.m
assert m.input_files is retained_inputs.m and m.connections is connections.m
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
    assembly.layout,
    assembly.tree,
    assembly.routing,
    assembly.projection,
    assembly.binding,
    assembly.directory,
    assembly.prepared,
    assembly.joined,
    assembly.before_handoff,
    assembly.service_case,
)

OUTER = r"""
import json, os, socket, sys
from pathlib import Path
sys.path[:0] = [sys.argv[1], str(Path(sys.argv[1]).parent / "src")]
import supplemental_recording_peer_bootstrap as m
m.ROOT_UID = os.geteuid()
listener = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
listener.bind(sys.argv[2])
Path(sys.argv[2]).chmod(0o600)
listener.listen(2)
print("listening", flush=True)
connections = {}
for _ in range(2):
    connection, _ = listener.accept()
    connection.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
    connection.setblocking(False)
    pid, uid, gid = m.links.control.returns.CREDENTIALS.unpack(
        connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
    connections[pid] = connection
listener.close()
print("connected", flush=True)
config = json.loads(sys.stdin.buffer.readline())
table = {item["pid"]: item["container_id"] for item in config["identities"]}
def identity(pid, cid):
    assert table[pid] == cid
    return m.links.processes.process_identity(pid, cid, Path(f"/proc/{pid}/stat").read_text(),
        f"0::/system.slice/docker-{cid}.scope\n")
m.links.processes.read_identity = identity
plan = m.links.plans.load_bytes(config["plan"].encode(), config["sha256"])
clock = m.links.clock.ClockWitness(m.links.clock.read())
local = identity(os.getpid(), table[os.getpid()])
peers = {role: m.links.processes.ProcessWitness(identity(config[role], table[config[role]]))
    for role in ("writer", "observer")}
bundles = m.links.pair()
endpoints = []
try:
    for role, channels in zip(("writer", "observer"), bundles):
        other = "observer" if role == "writer" else "writer"
        endpoint = m.Endpoint(connections[config[role]], plan, clock, local, peers[role],
            peers[other], role=role, mode="deliver", declaration_sha256=config["declaration"])
        endpoints.append(endpoint)
        receipt = endpoint.deliver(channels)
        channels.close()
        endpoint.close()
    print("delivered", flush=True)
    sys.stdin.buffer.readline()
except Exception:
    print("refused", flush=True)
    sys.stdin.buffer.readline()
finally:
    for endpoint in endpoints: endpoint.close()
    for channels in bundles: channels.close()
    for peer in peers.values(): peer.close()
    for connection in connections.values(): connection.close()
    clock.close()
"""

# Same outer process authenticates clock-free inputs, then performs the final
# handoff. Host/cgroup/runtime pins are still synthetic, not installed proof.
PREPARING_OUTER = OUTER.replace(
    "connections = {}",
    r"""
import supplemental_recording_peer_preparation as p
p.ROOT_UID = p.listeners.ROOT_UID = p.connections.ROOT_UID = os.geteuid()
p.inputs_module.ROOT_UID = p.domains.ROOT_UID = os.geteuid()
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
from contextlib import ExitStack, closing
import time
with ExitStack() as stack:
    original = stack.enter_context(p.inputs_module.declarations.Declaration(
        Path(config["template_root"]), config["template_sha256"]))
    inputs = stack.enter_context(closing(p.inputs_module.Inputs(
        original, Path(config["input_root"]), config["expectations_sha256"])))
    writer = stack.enter_context(p.domains.process.ProcessWitness(
        identity(config["writer"], table[config["writer"]])))
    observer = stack.enter_context(p.domains.process.ProcessWitness(
        identity(config["observer"], table[config["observer"]])))
    timer = stack.enter_context(closing(p.domains.clock.ClockWitness(p.domains.clock.read())))
    owned = stack.enter_context(closing(p.listeners.Listener(
        Path(config["preparation_root"]), writer, deadline=time.monotonic() + 2)))
    print("preparation-listening", flush=True)
    owned.accept()
    receipt = p.send_inputs(inputs, owned, timer, identity(os.getpid(), table[os.getpid()]),
        role="writer", baseline_sha256=config["baseline_sha256"], counterpart=observer,
        preparation=config.get("command", False))
    print(json.dumps(dict(prepared=receipt, deadline=owned.deadline)), flush=True)
    # The writer retires its preparation socket before the outer retires ours.
    assert json.loads(sys.stdin.buffer.readline()) == {"mode": "prepared"}
    owned.close()  # Its original two-second bound is NOT the next phase's lease.
    if config.get("permission_root"):
        import supplemental_recording_permission_sender as grants
        gate = grants.permission
        gate.ROOT_UID = os.geteuid()
        path = Path(config["permission_root"]) / "observer.sock"
        server = stack.enter_context(closing(socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)))
        server.bind(str(path))
        path.chmod(0o600)
        server.listen(1)
        server.settimeout(2)
        print("permission-listening", flush=True)
        channel = stack.enter_context(closing(server.accept()[0]))
        channel.setblocking(False)
        domain = stack.enter_context(closing(p.domains.ZeroDomain(timer.original, writer)))
        def qualify(review):
            # Deliberately synthetic command/runtime review, NOT installed authority.
            assert review.baseline_sha256 == config["baseline_sha256"]
            assert review.template_sha256 == config["template_sha256"]
            inputs.recheck()
            assert not writer.exited() and not observer.exited()
        sender = stack.enter_context(closing(grants.Sender(
            original.template, config["template_sha256"], config["baseline_sha256"],
            identity(os.getpid(), table[os.getpid()]), writer, domain, timer, channel, qualify)))
        sender.receive()
        sender.send()
        print("permission-sent", flush=True)
        assert json.loads(sys.stdin.buffer.readline()) == {"mode": "baseline-prepared"}
connections = {}
""",
)

# The fixed command receives no test-only phase callbacks. Both private
# listeners are provisioned by this original outer before the command begins.
# Separate final acceptance is still explicitly synthetic in the fixture.
COMMAND_OUTER = (
    PREPARING_OUTER.replace(
        '    print("preparation-listening", flush=True)',
        """    permission_server = stack.enter_context(closing(
        socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)))
    permission_path = Path(config["permission_root"]) / "observer.sock"
    permission_server.bind(str(permission_path))
    permission_path.chmod(0o600)
    permission_server.listen(1)
    permission_server.settimeout(2)
    print("preparation-listening", flush=True)""",
    )
    .replace(
        '    assert json.loads(sys.stdin.buffer.readline()) == {"mode": "prepared"}',
        "    pass  # No fixture handshake with the fixed command.",
    )
    .replace(
        "        server = stack.enter_context(closing("
        "socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)))\n"
        """\
        server.bind(str(path))
        path.chmod(0o600)
        server.listen(1)
        server.settimeout(2)""",
        "        server = permission_server",
    )
)
# Keep both input and permission owners until final handoff ends. Immediate
# sender close could race the receiver's final input checks under coverage.
# Retention is passive: no expired exchange/scope gets reused or renewed.
_command_prefix, _command_tail = COMMAND_OUTER.split("\nconnections = {}\n", 1)
COMMAND_OUTER = (
    _command_prefix.replace(
        '        assert json.loads(sys.stdin.buffer.readline()) == {"mode": "baseline-prepared"}',
        "        pass  # No test-only baseline-complete message to the outer.",
    ).replace(
        "    owned.close()  # Its original two-second bound is NOT the next phase's lease.",
        "    pass  # Retain through the receiver's final checks, without renewal.",
    )
    + "\n    connections = {}\n"
    + "\n".join("    " + line for line in _command_tail.splitlines())
    + "\n"
)


@pytest.fixture
def connection(service_case, monkeypatch, request):
    s = service_case
    monkeypatch.setattr(m.bootstrap, "ROOT_UID", os.geteuid())
    monkeypatch.setattr(m.bootstrap.links, "ROOT_UID", os.geteuid())
    monkeypatch.setattr(connections.m, "ROOT_UID", os.geteuid())
    temporary = tempfile.TemporaryDirectory(prefix="sds-writer-")
    input_temporary = None
    path = str(Path(temporary.name) / connections.m.NAME)
    processes, witnesses, channels = [], [], []
    accepted = None
    retained = input_owner = None
    prep_stack = ExitStack()
    prep_clock = prep_connection = prep_baseline = None
    permission = permission_connection = None
    retained_path = getattr(request, "param", False)
    command_path = retained_path == "command"
    preparation_path = retained_path in {
        "prepared-inputs",
        "permitted-inputs",
        "permission-only",
        "command",
    }
    permission_path = retained_path in {"permitted-inputs", "permission-only", "command"}

    def spawn(code):
        process = subprocess.Popen(
            [sys.executable, "-I", "-B", "-c", code, str(Path(m.__file__).parent), path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        processes.append(process)
        return process

    try:
        outer = spawn(
            COMMAND_OUTER if command_path else PREPARING_OUTER if preparation_path else OUTER
        )
        assert transport.line(outer) == "listening"
        observer = spawn(
            transport.CHILD.replace(
                'identity(os.getppid(), config["outer"])',
                'identity(config["outer_pid"], config["outer"])',
            ).replace(
                "m.links.clock.ClockWitness(plan.original_clock)",
                "m.links.clock.ClockWitness(m.links.clock.read())",
            )
        )
        assert transport.line(observer) == "ready"
        if not retained_path:
            accepted = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
            accepted.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
            accepted.connect(path)
            accepted.setblocking(False)
            assert transport.line(outer) == "connected"
        table = {os.getpid(): "a" * 64, outer.pid: "b" * 64, observer.pid: "c" * 64}

        def identity(pid, cid):
            assert table[pid] == cid
            return m.bootstrap.links.processes.process_identity(
                pid,
                cid,
                Path(f"/proc/{pid}/stat").read_text(),
                f"0::/system.slice/docker-{cid}.scope\n",
            )

        monkeypatch.setattr(m.bootstrap.links.processes, "read_identity", identity)
        identities = {pid: identity(pid, cid) for pid, cid in table.items()}
        outer_witness = m.bootstrap.links.processes.ProcessWitness(identities[outer.pid])
        witnesses.append(outer_witness)
        if not preparation_path:
            observer_witness = m.bootstrap.links.processes.ProcessWitness(identities[observer.pid])
            witnesses.append(observer_witness)
        values = declared.value()
        values["template_sha256"] = s.startup.template.sha256
        values["writer"]["runtime"] = json.loads(s.startup.template.raw)["plan"]["helper"]
        values["observer"]["runtime"]["source"] = values["writer"]["runtime"]["source"]
        if command_path:
            values["kind"] = m.expectations.PREPARATION_KIND
            values["source_kind"] = m.expectations.peer_source.PreparationProfile.KIND
            declaration = m.expectations.decode_peer_preparation(values)
        elif (
            s.input_publication is not None
            and m.expectations._read(s.expected_inputs.raw)["kind"] == m.expectations.PEER_KIND
        ):
            values["kind"] = m.expectations.PEER_KIND
            values["source_kind"] = m.expectations.peer_source.KIND
            declaration = m.expectations.decode_peer_handoff(values)
        else:
            declaration = m.expectations.decode(values)
        expected_sha256 = declaration.sha256
        if retained_path == "inputs" or preparation_path:
            # File intake precedes the baseline read and original clock capture.
            # Pins are synthetic; this does not supply installed provenance.
            if s.input_publication is None:
                input_temporary = tempfile.TemporaryDirectory(prefix="sds-writer-input-")
                input_root = Path(input_temporary.name)
                input_path = input_root / m.input_files.NAME
                input_path.write_bytes(declaration.raw)
                input_path.chmod(0o600)
            else:
                # This pair was exclusively published BEFORE Startup existed.
                # Use the original independent pin, not a hash learned by readback.
                input_root = s.peer_input_root
                assert declaration.raw == s.expected_inputs.raw
                expected_sha256 = s.expected_input_digest
            case_id = json.loads(s.startup.template.raw)["plan"]["case"]
            root_for_case = m.input_files.inputs_root
            monkeypatch.setattr(
                m.input_files,
                "inputs_root",
                lambda case: input_root if case == case_id else root_for_case(case),
            )
            monkeypatch.setattr(m.input_files, "ROOT_UID", os.geteuid())
            assert not s.clocks and not s.cached_calls
            if preparation_path:
                assert s.input_publication is not None
                for module in (prep, prep.listeners, prep.domains):
                    monkeypatch.setattr(module, "ROOT_UID", os.geteuid())
                prep_root = Path(
                    prep_stack.enter_context(tempfile.TemporaryDirectory(prefix="sds-prepare-"))
                )
                monkeypatch.setattr(prep, "preparation_root", lambda case, role: prep_root)
                # Persist the pre-existing original inventory, never capture a
                # new snapshot or authenticate a digest learned by file readback.
                # Inventory/host provenance is synthetic; the file read is real.
                baseline_root = s.root.parent / "preparation-baseline"
                baseline_root.mkdir(mode=0o700)
                stored = s.projected.host
                protected = m.startup.plans.projection.recording
                baseline_raw = protected.manifest_bytes(
                    stored.baseline,
                    stored.writer,
                    stored.contract.audio_endpoint_sha256,
                    maximum_recording_seconds=stored.contract.maximum_recording_seconds,
                )
                prep_baseline = baseline_root / "baseline.json"
                prep_baseline.write_bytes(baseline_raw)
                prep_baseline.chmod(0o600)
                baseline_pin = stored.manifest_sha256
                permission_root = None
                if permission_path:
                    permission_root = Path(
                        prep_stack.enter_context(
                            tempfile.TemporaryDirectory(prefix="sds-preflight-")
                        )
                    )
                transport.command(
                    outer,
                    dict(
                        identities=[asdict(item) for item in identities.values()],
                        writer=os.getpid(),
                        observer=observer.pid,
                        template_root=str(s.declaration.root),
                        template_sha256=s.template.sha256,
                        input_root=str(input_root),
                        expectations_sha256=expected_sha256,
                        preparation_root=str(prep_root),
                        baseline_sha256=baseline_pin,
                        permission_root=str(permission_root) if permission_root else None,
                        command=command_path,
                    ),
                )
                assert transport.line(outer) == "preparation-listening"
                if command_path:
                    # The command, not this fixture, acquires the actual original
                    # Declaration/Startup, clocks, inputs, permission and channels.
                    s.startup.close()  # Retire the unused inherited fixture owner.
                    monkeypatch.setattr(preflight_probe.m.permission, "ROOT_UID", os.geteuid())
                    monkeypatch.setattr(
                        preflight_probe.m, "current_identity", lambda: identities[os.getpid()]
                    )
                    monkeypatch.setattr(
                        preflight_probe.m, "peer_root", lambda case: permission_root
                    )
                    monkeypatch.setattr(prep, "baseline_root", lambda case: baseline_root)
                    monkeypatch.setattr(prep, "handoff_root", lambda case: Path(temporary.name))
                    # A private constructor alias supplies the synthetic Engine
                    # only at the command boundary; no shared class is replaced.
                    ordinary = SimpleNamespace(**vars(prep.startups.plans.ordinary))
                    ordinary.Docker = lambda: s.docker
                    plans = SimpleNamespace(**vars(prep.startups.plans))
                    plans.ordinary = ordinary
                    startup = SimpleNamespace(**vars(prep.startups))
                    startup.plans = plans
                    monkeypatch.setattr(prep, "startups", startup)
                    yield SimpleNamespace(
                        s=s,
                        outer=outer,
                        observer=observer,
                        identities=identities,
                        table=table,
                        baseline_pin=baseline_pin,
                        case=case_id,
                        run=lambda: prep.prepare_idle_writer(
                            case_id, s.template.sha256, baseline_pin, identities[outer.pid]
                        ),
                    )
                    return
                prep_clock = prep.domains.clock.ClockWitness(prep.domains.clock.read())
                prep_stack.callback(prep_clock.close)
                prep_connection = prep.connections.Connection(
                    prep_root, outer_witness, deadline=time.monotonic() + 2
                )
                prep_stack.callback(prep_connection.close)
                input_owner, observer_witness = prep_stack.enter_context(
                    prep.receive_inputs(
                        s.declaration,
                        prep_connection,
                        prep_clock,
                        identities[os.getpid()],
                        role="writer",
                        baseline_sha256=baseline_pin,
                    )
                )
                prepared_receipt = json.loads(transport.line(outer))
                assert prepared_receipt["prepared"]
                assert prep_connection.preparation_attempted
                assert s.startup.clock is None and not s.cached_calls and not s.preflights
                assert s.clocks == [prep_clock]
                prep_connection.close()
                transport.command(outer, dict(mode="prepared"))
                if permission_path:
                    gate = preflight_probe.m.permission
                    monkeypatch.setattr(gate, "ROOT_UID", os.geteuid())
                    monkeypatch.setattr(prep, "baseline_root", lambda case: prep_baseline.parent)
                    monkeypatch.setattr(
                        preflight_probe.m, "peer_root", lambda case: permission_root
                    )
                    assert transport.line(outer) == "permission-listening"
                    domain = prep.domains.ZeroDomain(prep_clock.original, outer_witness)
                    prep_stack.callback(domain.close)
                    permission_connection = preflight_probe.m.PeerConnection(
                        permission_root,
                        prep_clock.original.after_ns / gate.clock.NS + gate.WAIT_SECONDS,
                    )
                    prep_stack.callback(permission_connection.close)
                    permission = gate.Permission(
                        s.startup.template,
                        s.template.sha256,
                        baseline_pin,
                        identities[os.getpid()],
                        outer_witness,
                        domain,
                        prep_clock,
                        permission_connection.channel,
                    )
                    prep_stack.callback(permission.close)
                    permission.wait()
                    assert transport.line(outer) == "permission-sent"
                    assert permission.approved and not permission.used
                    assert s.startup.clock is None and s.clocks == [prep_clock]
            else:
                input_owner = m.input_files.Inputs(s.declaration, input_root, expected_sha256)
            declaration = input_owner.recheck()
            assert len(s.clocks) == int(prep_clock is not None) and not s.cached_calls
        if prep_baseline is not None:
            # Authenticated input preparation does not authorize this call.
            # The fixture explicitly drives the passive library join; an
            # installed entrypoint still requires separate preflight admission.
            owner = s.startup
            if permission is not None:

                def permitted_prepare():
                    return prep.prepare_writer(
                        input_owner,
                        owner,
                        permission,
                        observer_witness,
                        prep_baseline.parent,
                        s.docker,
                        original_timer=prep_clock,
                        original_outer=outer_witness,
                        original_connection=permission_connection,
                    )

                if retained_path == "permission-only":
                    yield SimpleNamespace(
                        s=s,
                        owner=owner,
                        input_owner=input_owner,
                        permission=permission,
                        prepare=permitted_prepare,
                        prep_clock=prep_clock,
                        prep_connection=prep_connection,
                        outer=outer,
                        observer=observer,
                        outer_witness=outer_witness,
                        observer_witness=observer_witness,
                        permission_connection=permission_connection,
                    )
                    return
                permitted_prepare()
                transport.command(outer, dict(mode="baseline-prepared"))
            else:
                owner.prepare_service_from_baseline(prep_baseline.parent, baseline_pin, s.docker)
            s.projected = owner.projected
            assembly.baseline_tests.integration.startups.submit(owner)
            assert owner.poll() is owner.original
            assert prep_baseline.read_bytes() == baseline_raw
        else:
            owner = assembly.accept(s)
        plan = owner.original.plan
        config = dict(
            plan=plan.raw.decode(),
            sha256=plan.sha256,
            identities=[asdict(item) for item in identities.values()],
            declaration=declaration.sha256,
        )
        if retained_path:
            retained = connections.m.Connection(
                Path(temporary.name),
                outer_witness,
                deadline=min(time.monotonic() + 2, plan.lease["ready_by"]),
            )
            accepted = retained.channel
            assert transport.line(outer) == "connected"

        def start():
            transport.command(outer, config | dict(writer=os.getpid(), observer=observer.pid))
            transport.command(
                observer,
                config
                | dict(
                    role="observer",
                    local=table[observer.pid],
                    outer=table[outer.pid],
                    outer_pid=outer.pid,
                    peer=table[os.getpid()],
                    peer_pid=os.getpid(),
                ),
            )

        def receive(**kwargs):
            if input_owner is None:
                result = m.receive(
                    owner,
                    declaration,
                    expected_sha256,
                    accepted,
                    identities[os.getpid()],
                    outer_witness,
                    observer_witness,
                    **kwargs,
                )
            else:
                result = m.receive_from_inputs(
                    owner,
                    input_owner,
                    retained,
                    identities[os.getpid()],
                    outer_witness,
                    observer_witness,
                    **kwargs,
                )
            channels.append(result[0])
            return result

        yield SimpleNamespace(
            s=s,
            owner=owner,
            plan=plan,
            declaration=declaration,
            start=start,
            receive=receive,
            outer=outer,
            observer=observer,
            channel=accepted,
            local=identities[os.getpid()],
            outer_witness=outer_witness,
            observer_witness=observer_witness,
            channels=channels,
            retained=retained,
            input_owner=input_owner,
            prep_clock=prep_clock,
            prep_connection=prep_connection,
            prep_baseline=prep_baseline,
            prep_baseline_pin=baseline_pin if prep_baseline is not None else None,
            permission=permission,
            permission_connection=permission_connection,
        )
    finally:
        for bundle in channels:
            bundle.close()
        for process in processes:
            if process.poll() is None:
                process.kill()  # Only original disposable subprocesses owned by this fixture.
            process.wait(timeout=3)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()
        for witness in witnesses:
            witness.close()
        if retained is not None:
            retained.close()
        elif accepted is not None:
            accepted.close()
        prep_stack.close()
        if input_owner is not None and prep_clock is None:
            input_owner.close()
        if input_temporary is not None:
            input_temporary.cleanup()
        temporary.cleanup()


def denied(call):
    with pytest.raises(m.UnconfirmedWriterChannel) as error:
        call()
    assert str(error.value) == m.MESSAGE and error.value.__suppress_context__


def test_original_post_baseline_clock_reaches_delivered_link_then_passive_assembly(connection):
    c = connection
    owner = c.owner
    original, clock, baseline, projected = (
        owner.original,
        owner.clock,
        owner.baseline,
        owner.projected,
    )
    assert len(c.s.clocks) == 2 and c.s.clocks[0].closed and c.s.clocks[1] is clock
    c.start()
    channels, receipt = c.receive()
    assert transport.line(c.outer) == "delivered"
    assert json.loads(transport.line(c.observer))["received"]
    assert owner.original is original and owner.clock is clock and not clock.closed
    assert owner.baseline is baseline and owner.projected is projected
    assert owner.peer_channel_attempted is True and not owner.service_used
    assert len(c.s.clocks) == 2 + int(c.prep_clock is not None) and len(c.s.cached_calls) == 1
    assert not (c.s.root / "journal").exists() and not (c.s.root / "inbox").exists()
    assert receipt.context_sha256 and receipt.offer_sha256
    link = m.bootstrap.links.Link(channels, c.plan, clock, c.observer_witness, role="writer")
    try:
        transport.command(c.observer, dict(mode="link"))
        assert transport.line(c.observer) == "linked"
        value = dict(schema=1, fixture="original-writer-startup", plan=c.plan.sha256)
        link._send(value, time.monotonic() + 2)
        transport.command(c.observer, dict(mode="link-read"))
        assert json.loads(transport.line(c.observer)) == value
        with owner.idle_service(c.s.docker) as service:
            assert service.original is original and service.clock_witness is clock
            assert not service.used and not service.dispatch.used
            assert len(service.journal.entries) == 1
        assert service.closed and not clock.closed
    finally:
        link.close()
    channels.close()
    assert not owner.closed and not c.outer_witness.exited() and not c.observer_witness.exited()
    assert c.channel.fileno() >= 0


@pytest.mark.parametrize("connection", ["inputs"], indirect=True)
def test_retained_inputs_precede_actual_baseline_and_reach_original_link_and_service(
    connection, monkeypatch
):
    c = connection
    assert c.input_owner.declaration is c.owner.declaration
    assert c.input_owner.template is c.owner.template
    assert c.input_owner.expectations is c.declaration
    ends, recheck, endpoint_init = [], m.input_files.Inputs.recheck, m.bootstrap.Endpoint.__init__

    def read(inputs, *, deadline=None):
        ends.append(deadline)
        return recheck(inputs, deadline=deadline)

    def endpoint(value, *args, **kwargs):
        ends.append(kwargs["deadline"])
        endpoint_init(value, *args, **kwargs)

    monkeypatch.setattr(m.input_files.Inputs, "recheck", read)
    monkeypatch.setattr(m.bootstrap.Endpoint, "__init__", endpoint)
    test_original_post_baseline_clock_reaches_delivered_link_then_passive_assembly(c)
    assert len(ends) >= 6 and set(ends) == {c.retained.deadline}
    assert not c.input_owner.closed and not c.input_owner.declaration.closed


@pytest.mark.parametrize(
    "service_case", ["published-inputs", "published-peer-inputs"], indirect=True
)
@pytest.mark.parametrize("connection", ["inputs"], indirect=True)
def test_exclusive_publication_reaches_original_startup_handoff_and_idle_assembly(
    connection, monkeypatch
):
    c = connection
    assert c.s.input_publication is not None
    assert c.s.input_publication.template_sha256 == c.owner.expected
    assert c.s.input_publication.expectations_sha256 == c.s.expected_input_digest
    files = (
        c.owner.declaration.root / m.startup.declaration.NAME,
        c.input_owner.root / m.input_files.NAME,
    )
    # Reads may advance atime. Use the same identity/content metadata contract
    # as the retained readers instead of treating access as a file mutation.
    before = {
        path: (m.input_files.files.identity(path.stat()), path.read_bytes()) for path in files
    }
    test_retained_inputs_precede_actual_baseline_and_reach_original_link_and_service(c, monkeypatch)
    assert {
        path: (m.input_files.files.identity(path.stat()), path.read_bytes()) for path in files
    } == before


def prepare_idle(c):
    return m.prepare_idle_from_inputs(
        c.owner,
        c.input_owner,
        c.retained,
        c.local,
        c.outer_witness,
        c.observer_witness,
        c.s.docker,
    )


@pytest.mark.parametrize("connection", ["inputs"], indirect=True)
@pytest.mark.parametrize(
    "fault",
    [
        None,
        "input",
        "late",
        "callback",
        "used",
        "dispatch-used",
        "closed",
        "failed",
        "body",
        "interrupt",
    ],
)
def test_retained_passive_scope_keeps_original_borrowers_until_caller_finishes(
    connection, monkeypatch, fault
):
    c = connection
    services = []
    construct = assembly.operator.IdleService.__init__

    def capture(service, *args, **kwargs):
        construct(service, *args, **kwargs)
        services.append(service)

    def forbidden(*_args, **_kwargs):
        pytest.fail("Retained passive custody attempted active work")

    monkeypatch.setattr(assembly.operator.IdleService, "__init__", capture)
    monkeypatch.setattr(assembly.operator.IdleService, "run", forbidden)
    monkeypatch.setattr(assembly.operator.Inbox, "consume", forbidden)
    original, clock, cutoff = c.owner.original, c.owner.clock, c.retained.deadline
    c.start()

    def trial():
        with m.retained_idle_from_inputs(
            c.owner,
            c.input_owner,
            c.retained,
            c.local,
            c.outer_witness,
            c.observer_witness,
            c.s.docker,
        ) as receipt:
            assert type(receipt) is m.bootstrap.Receipt
            assert transport.line(c.outer) == "delivered"
            assert json.loads(transport.line(c.observer))["received"]
            assert len(services) == 1
            service = services[0]
            link = service._dispatch_observer.__self__
            assert service.original is original and service.clock_witness is clock
            assert c.owner._service_active and not service.closed and not link.closed
            assert link.timer is clock and link.plan is original.plan
            assert link.channel.incoming.fileno() >= 0 and link.channel.outgoing.fileno() >= 0
            assert c.retained.deadline == cutoff and time.monotonic() < cutoff
            assert not c.input_owner.closed and not clock.closed and not c.retained.closed
            assert not service.used and not service.dispatch.used
            if fault is None:
                # A second intake cannot replace or close the still-borrowed
                # original scope while it is held, nor renew its cutoff.
                denied(lambda: prepare_idle(c))
                assert c.owner._service_active and not service.closed and not link.closed
                assert c.owner.clock is clock and not clock.closed
                assert not c.retained.closed and c.retained.deadline == cutoff
            elif fault == "input":
                (c.input_owner.root / m.input_files.NAME).write_bytes(b"PRIVATE_SCOPE_DRIFT")
            elif fault == "late":
                monkeypatch.setattr(m, "time", SimpleNamespace(monotonic=lambda: cutoff))
            elif fault == "callback":
                service.dispatch.observe = None
            elif fault == "used":
                service.used = True
            elif fault == "dispatch-used":
                service.dispatch.used = True
            elif fault == "closed":
                service.close()
            elif fault == "failed":
                service.failed = True
            elif fault == "body":
                raise ValueError("PRIVATE_SCOPE_BODY")
            elif fault == "interrupt":
                raise KeyboardInterrupt

    if fault == "interrupt":
        with pytest.raises(KeyboardInterrupt):
            trial()
    elif fault is not None:
        denied(trial)
    else:
        trial()
        assert not c.owner.closed and not clock.closed and not c.retained.closed
        assert c.owner.original is original and c.owner.clock is clock
    service = services[0]
    link = service._dispatch_observer.__self__
    assert service.closed and service.journal.fd == -1 and link.closed
    assert link.channel.incoming.fileno() == link.channel.outgoing.fileno() == -1
    assert c.owner.peer_channel_attempted and c.owner.service_used
    assert (c.s.root / "journal/0000.json").is_file()
    denied(lambda: prepare_idle(c))


@pytest.mark.parametrize(
    "service_case,connection",
    [
        ("published-inputs", "inputs"),
        ("published-peer-inputs", "inputs"),
        ("published-peer-inputs", "prepared-inputs"),
        ("published-peer-inputs", "permitted-inputs"),
    ],
    indirect=True,
)
def test_published_original_inputs_wire_received_link_to_dispatcher_then_retire_passively(
    connection, monkeypatch
):
    c = connection
    original, clock = c.owner.original, c.owner.clock
    assert c.input_owner.expectations is c.declaration
    assert c.declaration.raw == c.s.expected_inputs.raw
    assert c.input_owner.expected == c.s.expected_input_digest
    if c.prep_clock is not None:
        assert c.prep_connection.closed and c.prep_connection.preparation_attempted
        assert c.prep_connection.peer is c.outer_witness
        assert c.s.clocks == [c.prep_clock, c.s.clocks[1], clock]
        assert c.s.clocks[1].closed and not c.prep_clock.closed
        assert c.prep_clock.original.after_ns < clock.original.before_ns
        assert c.retained is not c.prep_connection
        assert c.retained.deadline > c.prep_connection.deadline
        assert c.owner.projected.host.manifest_sha256 == c.prep_baseline_pin
    if c.permission is not None:
        assert c.permission.used and not c.permission.active and not c.permission.failed
        assert c.permission.timer is c.prep_clock and c.permission.observer is c.outer_witness
        assert c.permission.consume_end <= c.permission.deadline
        assert c.permission.deadline == c.prep_clock.original.after_ns / prep.domains.clock.NS + 15
    links, services, cleanup, deadlines = [], [], [], []
    link_init, link_close = m.bootstrap.links.Link.__init__, m.bootstrap.links.Link.close
    service_init, service_close = (
        assembly.operator.IdleService.__init__,
        assembly.operator.IdleService.close,
    )
    idle_service = m.startup.Startup.idle_service

    def construct_link(link, *args, **kwargs):
        link_init(link, *args, **kwargs)
        links.append(link)

    def construct_service(service, *args, **kwargs):
        service_init(service, *args, **kwargs)
        services.append(service)
        callback = service.dispatch.observe
        assert callback.__self__ is links[0]
        assert callback.__func__ is m.bootstrap.links.Link.observe
        assert service.dispatch._original_observe is callback
        assert service.clock_witness is clock and service.original is original

    def close_service(service):
        if service in services and not service.closed:
            cleanup.append("service")
            assert not links[0].closed and not clock.closed
        return service_close(service)

    def close_link(link):
        if link in links and not link.closed:
            cleanup.append("link")
            assert services[0].closed and services[0].journal.fd == -1
            assert not clock.closed and link.channel.incoming.fileno() >= 0
        return link_close(link)

    def idle(owner, docker, **kwargs):
        deadlines.append(kwargs["deadline"])
        return idle_service(owner, docker, **kwargs)

    monkeypatch.setattr(m.bootstrap.links.Link, "__init__", construct_link)
    monkeypatch.setattr(m.bootstrap.links.Link, "close", close_link)
    monkeypatch.setattr(assembly.operator.IdleService, "__init__", construct_service)
    monkeypatch.setattr(assembly.operator.IdleService, "close", close_service)
    monkeypatch.setattr(m.startup.Startup, "idle_service", idle)

    def forbidden(*_args, **_kwargs):
        pytest.fail("Passive integration attempted active service or peer evidence")

    monkeypatch.setattr(assembly.operator.IdleService, "run", forbidden)
    monkeypatch.setattr(m.bootstrap.links.Link, "_send", forbidden)
    c.start()
    receipt = prepare_idle(c)
    assert transport.line(c.outer) == "delivered"
    assert json.loads(transport.line(c.observer))["received"]
    assert receipt.context_sha256 and receipt.offer_sha256
    assert len(links) == len(services) == 1 and cleanup == ["service", "link"]
    assert deadlines == [c.retained.deadline]
    assert c.owner.original is original and c.owner.clock is clock and not clock.closed
    assert c.owner.peer_channel_attempted and c.owner.service_used and not c.owner.closed
    assert not c.input_owner.closed and not c.retained.closed
    assert c.outer_witness.fd >= 0 and c.observer_witness.fd >= 0
    assert len(c.s.clocks) == 2 + int(c.prep_clock is not None) and len(c.s.cached_calls) == 1
    assert all(s.fileno() == -1 for s in (links[0].channel.incoming, links[0].channel.outgoing))
    assert not services[0].used and not services[0].dispatch.used
    assert len(services[0].journal.entries) == 1
    journal = (c.s.root / "journal/0000.json").read_bytes()
    denied(lambda: prepare_idle(c))
    assert (c.s.root / "journal/0000.json").read_bytes() == journal


@pytest.mark.parametrize("connection", ["inputs"], indirect=True)
@pytest.mark.parametrize("fault", ["input", "late", "peer-exit"])
def test_idle_join_fault_after_intake_refuses_before_service_and_retires_channels(
    connection, monkeypatch, fault
):
    c = connection
    receive = m.receive_from_inputs
    captured = []

    def changed(*args, **kwargs):
        result = receive(*args, **kwargs)
        captured.append(result[0])
        if fault == "input":
            (c.input_owner.root / m.input_files.NAME).write_bytes(b"PRIVATE changed input")
        elif fault == "late":
            monkeypatch.setattr(m, "time", SimpleNamespace(monotonic=lambda: c.retained.deadline))
        else:
            c.observer.kill()
            c.observer.wait(timeout=3)
        return result

    monkeypatch.setattr(m, "receive_from_inputs", changed)
    c.start()
    denied(lambda: prepare_idle(c))
    assert len(captured) == 1
    assert captured[0].incoming.fileno() == captured[0].outgoing.fileno() == -1
    assert not c.owner.service_used and not (c.s.root / "journal").exists()
    assert not c.owner.clock.closed and not c.retained.closed


@pytest.mark.parametrize("connection", ["inputs"], indirect=True)
@pytest.mark.parametrize("interrupt", [False, True])
def test_idle_join_assembly_failure_preserves_partial_case_and_retires_link(
    connection, monkeypatch, interrupt
):
    c = connection
    links = []
    construct_link = m.bootstrap.links.Link.__init__

    def link(value, *args, **kwargs):
        construct_link(value, *args, **kwargs)
        links.append(value)

    def fail(*_args, **_kwargs):
        raise (KeyboardInterrupt if interrupt else OSError)("PRIVATE failed assembly")

    monkeypatch.setattr(m.bootstrap.links.Link, "__init__", link)
    monkeypatch.setattr(assembly.operator.IdleService, "__init__", fail)
    c.start()
    if interrupt:
        with pytest.raises(KeyboardInterrupt):
            prepare_idle(c)
    else:
        denied(lambda: prepare_idle(c))
    assert len(links) == 1 and links[0].closed
    assert links[0].channel.incoming.fileno() == links[0].channel.outgoing.fileno() == -1
    assert (c.s.root / "journal/0000.json").is_file()
    assert c.owner.closed and c.owner.clock.closed  # Startup invalidates its own failed assembly.
    assert not c.input_owner.closed and not c.retained.closed


@pytest.mark.parametrize("connection", ["inputs"], indirect=True)
@pytest.mark.parametrize("fault", ["callback", "input", "late"])
def test_idle_join_rechecks_same_inputs_and_deadline_during_assembly(
    connection, monkeypatch, fault
):
    c = connection
    construct, services = assembly.operator.IdleService.__init__, []

    def changed(service, *args, **kwargs):
        construct(service, *args, **kwargs)
        services.append(service)
        if fault == "callback":
            service.dispatch.observe = None
        elif fault == "input":
            (c.input_owner.root / m.input_files.NAME).write_bytes(b"PRIVATE changed input")
        else:
            monkeypatch.setattr(m, "time", SimpleNamespace(monotonic=lambda: c.retained.deadline))

    monkeypatch.setattr(assembly.operator.IdleService, "__init__", changed)
    c.start()
    denied(lambda: prepare_idle(c))
    assert len(services) == 1 and services[0].closed
    link = services[0]._dispatch_observer.__self__
    assert link.closed and link.channel.incoming.fileno() == link.channel.outgoing.fileno() == -1
    assert (c.s.root / "journal/0000.json").is_file() and not services[0].dispatch.used


@pytest.mark.parametrize("connection", ["inputs"], indirect=True)
@pytest.mark.parametrize("fault", ["cleanup-error", "late-cleanup", "reused-fd"])
def test_idle_join_cleanup_failure_still_retires_other_owned_channels(
    connection, monkeypatch, fault
):
    c = connection
    close, links, foreign = m.bootstrap.links.Link.close, [], []

    def changed(link):
        if link in links:
            return close(link)
        links.append(link)
        close(link)
        if fault == "cleanup-error":
            raise OSError("PRIVATE cleanup acknowledgment")
        if fault == "late-cleanup":
            monkeypatch.setattr(m, "time", SimpleNamespace(monotonic=lambda: c.retained.deadline))
        else:
            fd = link.channel.incoming.fileno()
            replacement = os.open("/dev/null", os.O_RDONLY | os.O_CLOEXEC)
            try:
                os.dup2(replacement, fd, inheritable=False)
                foreign.append((fd, m.bootstrap.links._identity(fd)))
            finally:
                os.close(replacement)

    monkeypatch.setattr(m.bootstrap.links.Link, "close", changed)
    try:
        c.start()
        denied(lambda: prepare_idle(c))
        assert len(links) == 1 and links[0].closed
        assert links[0].channel.incoming.fileno() == links[0].channel.outgoing.fileno() == -1
        assert (c.s.root / "journal/0000.json").is_file()
        assert not c.owner.closed and not c.owner.clock.closed and not c.retained.closed
        for fd, identity in foreign:
            assert m.bootstrap.links._identity(fd) == identity
    finally:
        for fd, _ in foreign:
            os.close(fd)


@pytest.mark.parametrize("connection", ["inputs"], indirect=True)
@pytest.mark.parametrize(
    "fault",
    ["input", "socket-path", "observer-exit", "outer-exit", "clock", "startup", "plan", "cutoff"],
)
def test_idle_join_final_channel_retirement_rechecks_original_custody(
    connection, monkeypatch, fault
):
    c = connection
    construct, close = m.bootstrap.links.Link.__init__, socket.socket.close
    links, changed = [], []

    def link(value, *args, **kwargs):
        construct(value, *args, **kwargs)
        links.append(value)

    def retire(channel):
        result = close(channel)
        if not links or channel is not links[0].channel.outgoing or changed:
            return result
        changed.append(fault)
        assert links[0].closed and links[0].channel.incoming.fileno() == -1
        if fault == "input":
            (c.input_owner.root / m.input_files.NAME).write_bytes(b"PRIVATE changed input")
        elif fault == "socket-path":
            (c.retained.root / "foreign").touch()
        elif fault in {"observer-exit", "outer-exit"}:
            child = c.observer if fault == "observer-exit" else c.outer
            child.kill()
            child.wait(timeout=3)
        elif fault == "clock":
            c.owner.clock.close()
        elif fault == "startup":
            c.owner.failed = True
        elif fault == "plan":
            object.__setattr__(c.plan, "raw", c.plan.raw + b" ")
        else:
            monkeypatch.setattr(m, "time", SimpleNamespace(monotonic=lambda: c.retained.deadline))
        return result

    monkeypatch.setattr(m.bootstrap.links.Link, "__init__", link)
    monkeypatch.setattr(socket.socket, "close", retire)
    c.start()
    denied(lambda: prepare_idle(c))
    assert changed == [fault] and len(links) == 1
    assert links[0].channel.incoming.fileno() == links[0].channel.outgoing.fileno() == -1
    assert c.owner.peer_channel_attempted and c.owner.service_used
    assert (c.s.root / "journal/0000.json").is_file()
    assert len(c.s.clocks) == 2 and len(c.s.cached_calls) == 1
    denied(lambda: prepare_idle(c))


@pytest.mark.parametrize("connection", ["inputs"], indirect=True)
@pytest.mark.parametrize(
    "fault", ["file", "new-template", "socket-path", "outer-witness", "cutoff"]
)
def test_retained_writer_input_or_connection_faults_refuse_before_transport(
    connection, monkeypatch, fault
):
    c = connection
    if fault == "file":
        (c.input_owner.root / m.input_files.NAME).write_bytes(b"PRIVATE")
    elif fault == "new-template":
        c.input_owner.template = m.startup.declaration.codec.load_bytes(
            c.owner.template.raw, c.owner.template.sha256
        )
    elif fault == "socket-path":
        (c.retained.root / "foreign").touch()
    elif fault == "outer-witness":
        c.retained.peer = c.observer_witness
    else:
        c.retained.deadline += 10
    monkeypatch.setattr(
        m.bootstrap, "Endpoint", lambda *_a, **_k: pytest.fail("No uncertain transport")
    )
    denied(c.receive)
    assert c.owner.peer_channel_attempted and not c.owner.closed
    assert not (c.s.root / "journal").exists()


@pytest.mark.parametrize("connection", ["inputs"], indirect=True)
def test_input_change_after_receipt_retires_descriptors_and_preserves_original_startup(
    connection, monkeypatch
):
    c = connection
    receive, captured = m.bootstrap.Endpoint.receive, []

    def change(endpoint):
        result = receive(endpoint)
        captured.append(result[0])
        (c.input_owner.root / m.input_files.NAME).write_bytes(b"PRIVATE changed input")
        return result

    monkeypatch.setattr(m.bootstrap.Endpoint, "receive", change)
    c.start()
    denied(c.receive)
    assert len(captured) == 1
    assert captured[0].incoming.fileno() == captured[0].outgoing.fileno() == -1
    assert not c.owner.closed and not c.owner.clock.closed and c.channel.fileno() >= 0
    assert not (c.s.root / "journal").exists()


def test_missing_retained_input_owner_cannot_select_legacy_receive(connection):
    c = connection
    denied(
        lambda: m.receive_from_inputs(
            c.owner, None, None, c.local, c.outer_witness, c.observer_witness
        )
    )
    assert c.owner.peer_channel_attempted and not c.owner.closed


@pytest.mark.parametrize("connection", [True], indirect=True)
def test_original_writer_private_path_and_bootstrap_share_one_cutoff(connection):
    c = connection
    retained = c.retained
    original = c.owner.original
    clock = c.owner.clock
    c.start()
    retained.recheck()
    channels, receipt = c.receive(deadline=retained.deadline)
    retained.recheck()
    assert transport.line(c.outer) == "delivered"
    assert json.loads(transport.line(c.observer))["received"]
    assert c.owner.original is original and c.owner.clock is clock
    assert retained.peer is c.outer_witness and retained.channel is c.channel
    assert receipt.context_sha256 and receipt.offer_sha256
    assert not (c.s.root / "journal").exists()
    link = m.bootstrap.links.Link(channels, c.plan, clock, c.observer_witness, role="writer")
    try:
        transport.command(c.observer, dict(mode="link"))
        assert transport.line(c.observer) == "linked"
        value = dict(schema=1, fixture="retained-original-path", plan=c.plan.sha256)
        link._send(value, time.monotonic() + 2)
        transport.command(c.observer, dict(mode="link-read"))
        assert json.loads(transport.line(c.observer)) == value
        with c.owner.idle_service(c.s.docker) as service:
            assert service.original is original and service.clock_witness is clock
            assert not service.used and not service.dispatch.used
    finally:
        link.close()


def test_second_intake_cannot_reconstruct_or_replay_on_same_startup(connection):
    c = connection
    c.start()
    channels, _ = c.receive()
    before = len(os.listdir("/proc/self/fd"))
    denied(c.receive)
    assert len(os.listdir("/proc/self/fd")) == before and not c.owner.closed
    assert channels.incoming.fileno() >= 0


@pytest.mark.parametrize("fault", ["pin", "plan", "clock", "baseline", "service", "publication"])
def test_changed_input_refuses_before_transport_and_consumes_one_attempt(
    connection, monkeypatch, fault
):
    c = connection
    if fault == "pin":
        object.__setattr__(
            c.declaration,
            "raw",
            c.declaration.raw.replace(b"recording-observer", b"changed-observer"),
        )
    elif fault == "plan":
        c.owner.original._plan = m.startup.plans.load_bytes(c.plan.raw, c.plan.sha256)
    elif fault == "clock":
        c.owner.clock = m.startup.plans.clock.ClockWitness(c.plan.original_clock)
    elif fault == "baseline":
        c.owner.baseline = replace(c.owner.baseline)
    elif fault == "service":
        c.owner.service_used = True
    else:
        c.owner.app_idle_publication_used = True
    monkeypatch.setattr(m.bootstrap, "Endpoint", lambda *a, **k: pytest.fail("Transport started"))
    denied(c.receive)
    assert c.owner.peer_channel_attempted is True
    denied(c.receive)
    assert not (c.s.root / "journal").exists()
    if fault == "clock":
        c.owner.clock.close()


@pytest.mark.parametrize("deadline", [True, "1", float("nan"), float("inf"), -1])
def test_invalid_or_expired_shared_budget_does_not_start_transport(
    connection, monkeypatch, deadline
):
    monkeypatch.setattr(m.bootstrap, "Endpoint", lambda *a, **k: pytest.fail("Transport started"))
    denied(lambda: connection.receive(deadline=deadline))
    assert connection.owner.peer_channel_attempted is True
    assert not connection.owner.closed


@pytest.mark.parametrize("fault", ["clock", "baseline", "attempt", "expired", "interrupt"])
def test_post_delivery_drift_closes_received_fds_and_never_constructs_journal(
    connection, monkeypatch, fault
):
    c = connection
    receive = m.bootstrap.Endpoint.receive
    received = []

    def changed(endpoint):
        result = receive(endpoint)
        received.append(result[0])
        if fault == "clock":
            c.owner.clock.original = replace(c.owner.clock.original)
        elif fault == "baseline":
            c.owner.baseline = replace(c.owner.baseline)
        elif fault == "attempt":
            c.owner.peer_channel_attempted = False
        elif fault == "expired":
            monkeypatch.setattr(m.time, "monotonic", lambda: endpoint.end)
        return result

    monkeypatch.setattr(m.bootstrap.Endpoint, "receive", changed)
    if fault == "interrupt":
        guard = m.startup.Startup.accepted_input

        def interrupted(owner):
            if received:
                raise KeyboardInterrupt("PRIVATE")
            return guard(owner)

        monkeypatch.setattr(m.startup.Startup, "accepted_input", interrupted)
    c.start()
    if fault == "interrupt":
        with pytest.raises(KeyboardInterrupt):
            c.receive()
    else:
        denied(c.receive)
    assert len(received) == 1
    assert all(s.fileno() == -1 for s in (received[0].incoming, received[0].outgoing))
    assert (
        c.channel.fileno() >= 0 and not c.outer_witness.exited() and not c.observer_witness.exited()
    )
    assert not (c.s.root / "journal").exists()


def test_initial_startup_read_is_charged_to_original_shared_budget(connection, monkeypatch):
    original = m.startup.Startup.accepted_input
    began = time.monotonic()

    def slow(owner):
        result = original(owner)
        monkeypatch.setattr(m.time, "monotonic", lambda: began + 3)
        return result

    monkeypatch.setattr(m.startup.Startup, "accepted_input", slow)
    monkeypatch.setattr(m.bootstrap, "Endpoint", lambda *a, **k: pytest.fail("Budget renewed"))
    denied(connection.receive)
    assert connection.owner.peer_channel_attempted and not connection.owner.closed


@pytest.mark.parametrize("fault", ["last-binding", "reused-fd", "blocking-fd"])
def test_last_recheck_cannot_return_changed_owner_or_foreign_received_descriptor(
    connection, monkeypatch, fault
):
    c = connection
    original = m.startup.Startup.accepted_input
    actual_receive = m.bootstrap.Endpoint.receive
    received, calls, replacements = [], [], []
    spare = os.open("/dev/null", os.O_RDONLY | os.O_CLOEXEC)

    def remember(endpoint):
        result = actual_receive(endpoint)
        received.append(result[0])
        return result

    def changed(owner):
        result = original(owner)
        calls.append(True)
        if len(calls) == 5:
            if fault == "last-binding":
                owner.baseline = replace(owner.baseline)
            elif fault == "reused-fd":
                fd = received[0].incoming.fileno()
                os.close(fd)
                os.dup2(spare, fd, inheritable=False)
                replacements.append(fd)
            else:
                received[0].incoming.setblocking(True)
        return result

    monkeypatch.setattr(m.bootstrap.Endpoint, "receive", remember)
    monkeypatch.setattr(m.startup.Startup, "accepted_input", changed)
    try:
        c.start()
        denied(c.receive)
        assert len(calls) == 5 and len(received) == 1
        assert received[0].incoming.fileno() == received[0].outgoing.fileno() == -1
        for fd in replacements:
            assert os.fstat(fd) == os.fstat(spare)  # Foreign reused fd stays caller-owned.
        assert not (c.s.root / "journal").exists()
    finally:
        for fd in replacements:
            os.close(fd)
        os.close(spare)


@pytest.mark.parametrize("state", ["unaccepted", "probe-only"])
def test_original_baseline_acceptance_required_without_creating_service(
    service_case, monkeypatch, state
):
    owner = service_case.startup
    if state == "probe-only":
        assembly.accept(service_case, read_host=False)
    monkeypatch.setattr(m.bootstrap, "Endpoint", lambda *a, **k: pytest.fail("Not service input"))
    denied(lambda: m.receive(owner, None, "0" * 64, None, None, None, None))
    assert owner.peer_channel_attempted is True
    assert not (service_case.root / "journal").exists()


def test_outer_loss_refuses_without_adopting_another_sender(connection):
    c = connection
    c.outer.kill()
    c.outer.wait(timeout=3)
    denied(c.receive)
    assert c.owner.peer_channel_attempted is True and not c.owner.clock.closed
    assert not (c.s.root / "journal").exists()
    denied(c.receive)


def preparation_denied(c, call=None):
    with pytest.raises(prep.UnconfirmedPreparation) as error:
        (call or c.prepare)()
    assert str(error.value) == prep.MESSAGE and error.value.__suppress_context__
    assert c.permission.failed and c.owner.failed and c.owner.closed
    assert not c.prep_clock.closed and not c.s.declaration.closed
    assert not (c.s.root / "journal").exists() and not (c.s.root / "inbox").exists()


@pytest.mark.parametrize("service_case", ["published-peer-inputs"], indirect=True)
@pytest.mark.parametrize("connection", ["permission-only"], indirect=True)
@pytest.mark.parametrize("fault", ["unapproved", "closed", "used", "path", "outer"])
def test_input_authentication_cannot_bypass_separate_preflight_admission(connection, fault):
    c = connection
    assert c.input_owner.expected == c.s.expected_input_digest
    if fault == "unapproved":
        c.permission.approved = False
    elif fault == "closed":
        c.permission.close()
    elif fault == "used":
        c.permission.used = True
    elif fault == "path":
        c.permission_connection.root.chmod(0o755)
    else:
        c.outer.kill()
        c.outer.wait(timeout=3)
    preparation_denied(c)
    assert c.s.clocks == [c.prep_clock] and not c.s.cached_calls and not list(c.s.root.iterdir())


@pytest.mark.parametrize("service_case", ["published-peer-inputs"], indirect=True)
@pytest.mark.parametrize("connection", ["permission-only"], indirect=True)
@pytest.mark.parametrize("fault", ["inputs", "counterpart", "scope", "interrupt"])
def test_original_inputs_and_counterpart_stay_guarded_during_baseline(connection, fault):
    c = connection

    def drift():
        if fault == "inputs":
            (c.input_owner.root / m.input_files.NAME).write_bytes(b"PRIVATE changed inputs")
        elif fault == "counterpart":
            c.observer.kill()
            c.observer.wait(timeout=3)
        elif fault == "scope":
            c.permission.consume_end += 1  # A renewed allowance is never adopted.
        else:
            raise KeyboardInterrupt

    c.s.after_cached = drift
    if fault == "interrupt":
        with pytest.raises(KeyboardInterrupt):
            c.prepare()
        assert c.permission.failed and c.owner.closed and c.owner.failed
    else:
        preparation_denied(c)
    assert len(c.s.cached_calls) == 1 and len(c.s.clocks) == 2 and c.s.clocks[-1].closed
    assert c.owner.clock is None and not list(c.s.root.iterdir())
    assert not c.prep_clock.closed and c.permission.used and not c.permission.active


@pytest.mark.parametrize("service_case", ["published-peer-inputs"], indirect=True)
@pytest.mark.parametrize("connection", ["permission-only"], indirect=True)
@pytest.mark.parametrize("fault", ["inputs", "counterpart", "path"])
def test_permission_scope_retirement_cannot_return_stale_startup(connection, monkeypatch, fault):
    c = connection
    consume = preflight_probe.m.permission.Permission.consume

    @contextmanager
    def retired(value):
        with consume(value) as guard:
            yield guard
        if fault == "inputs":
            (c.input_owner.root / m.input_files.NAME).write_bytes(b"PRIVATE retirement drift")
        elif fault == "counterpart":
            c.observer.kill()
            c.observer.wait(timeout=3)
        else:
            c.permission_connection.root.chmod(0o755)

    monkeypatch.setattr(preflight_probe.m.permission.Permission, "consume", retired)
    preparation_denied(c)
    assert c.owner.clock.closed and len(c.s.clocks) == 3 and len(c.s.cached_calls) == 1
    files = {p.name: p.read_bytes() for p in c.s.root.iterdir()}
    assert set(files) == {"startup-claim.json", "plan.json"}
    preparation_denied(c)
    assert {p.name: p.read_bytes() for p in c.s.root.iterdir()} == files


@pytest.mark.parametrize("service_case", ["published-peer-inputs"], indirect=True)
@pytest.mark.parametrize("connection", ["permission-only"], indirect=True)
def test_preflight_prepares_original_without_acceptance_or_another_attempt(connection):
    c = connection
    original = c.prepare()
    assert original is c.owner.original and not c.owner.accepted
    assert c.permission.used and not c.permission.active and not c.permission.failed
    assert c.owner.clock is not c.prep_clock and len(c.s.clocks) == 3
    assert c.permission.origin is c.prep_clock.original and len(c.s.cached_calls) == 1
    assert not c.owner.service_used and not c.owner.peer_channel_attempted
    files = {p.name: p.read_bytes() for p in c.s.root.iterdir()}
    preparation_denied(c)
    assert len(c.s.cached_calls) == 1 and len(c.s.clocks) == 3
    assert {p.name: p.read_bytes() for p in c.s.root.iterdir()} == files
    assert not c.owner.peer_channel_attempted and c.owner.clock.closed
