"""Full outer preflight and actual passive command in one three-process flow.

All process owners are created after exec, not inherited across fork. The writer
owns the actual baseline read, continuing clock, publication, acceptance and
dispatcher retirement. Engine/kernel/root/argv and installed provenance remain
synthetic; final acceptance is an explicit fixture decision, NOT qualification.
No live App, active permission, recording, recovery or installed launcher claim.
"""

import builtins
import copy
import inspect
import io
import json
import os
import select
import signal
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack, closing, contextmanager
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_helper_qualification as helper_tests
from . import test_supplemental_recording_peer_command as command_tests
from . import test_supplemental_recording_peer_delivery as delivery_tests
from . import test_supplemental_recording_peer_preflight_qualification as qualification
from . import test_supplemental_recording_peer_termination as termination_tests
from ._supplemental_failure_diagnostics import failure_locations

writer, p, m = command_tests.writer, command_tests.m, qualification.m
transport, grants = writer.transport, qualification.grants
layout, image_umask, supervised = (
    qualification.layout,
    qualification.image_umask,
    qualification.supervised,
)
image, configured = qualification.image, qualification.configured


def emit(value):
    print(json.dumps(value), flush=True)


def receive():
    raw = sys.stdin.buffer.readline()
    if not raw:
        raise EOFError
    return json.loads(raw)


def preparation_diagnostics(patches):
    """Keep bounded source locations behind sanitation; never private values."""
    cleanup = p._cleanup

    def retire(callbacks, problem):
        try:
            return cleanup(callbacks, problem)
        except Exception as error:
            if problem is not None:
                locations = failure_locations(problem)
                if locations:
                    error.add_note(locations)
            raise

    patches.setattr(p, "_cleanup", retire)


def test_preparation_diagnostics_never_copy_private_exception_values(monkeypatch):
    preparation_diagnostics(monkeypatch)
    with pytest.raises(p.UnconfirmedPreparation) as failure:
        p._cleanup([], ValueError("PRIVATE_PREPARATION_PAYLOAD"))
    assert str(failure.value) == p.MESSAGE
    rendered = repr(getattr(failure.value, "__notes__", []))
    if "PRIVATE_PREPARATION_PAYLOAD" in rendered:
        raise AssertionError("Private values escaped preparation diagnostics")


@contextmanager
def host_fixture(root, patches):
    """Use the explicit existing offline fixture chain in the exec'd writer.

    This is test setup before any declaration/clock of the command. No pytest
    runner, autoloaded plugins, live endpoint discovery or arbitrary fixture name.
    """
    with ExitStack() as stack:
        values = dict(tmp_path=root, monkeypatch=patches, request=SimpleNamespace(param=False))
        for name in (
            "layout",
            "tree",
            "routing",
            "projection",
            "binding",
            "directory",
            "prepared",
            "joined",
            "before_handoff",
        ):
            function = getattr(writer, name).__wrapped__
            value = function(**{key: values[key] for key in inspect.signature(function).parameters})
            if inspect.isgenerator(value):
                generator = value
                value = next(generator)

                def finish(generator=generator):
                    with pytest.raises(StopIteration):
                        next(generator)

                stack.callback(finish)
            values[name] = value
        yield values["before_handoff"]


def child(root):
    """Actual command function under explicitly synthetic host/path boundaries."""
    with pytest.MonkeyPatch.context() as patches, host_fixture(root, patches) as s:
        preparation_diagnostics(patches)
        # The inherited host fixture's inert placeholder is not part of this
        # protocol. Retire it before command setup, so even an intentionally
        # killed writer below cannot orphan a fixture grandchild.
        s.prepared.witness.close()
        s.prepared.process.stdin.close()
        assert s.prepared.process.wait(timeout=3) == 0
        emit(dict(base=json.loads(s.plan.raw), baseline=s.projected.host.manifest_sha256))
        configured = receive()
        s.plan = p.startups.plans.load_bytes(configured["plan"].encode(), configured["sha256"])
        service = writer.service_case.__wrapped__(s, root, patches, SimpleNamespace(param=None))
        s = next(service)
        try:
            # Retire the fixture's unused Startup; the command creates its own.
            s.startup.close()
            baseline_root = root / "baseline"
            baseline_root.mkdir(mode=0o700)
            protected, stored = p.startups.plans.projection.recording, s.projected.host
            raw = protected.manifest_bytes(
                stored.baseline,
                stored.writer,
                stored.contract.audio_endpoint_sha256,
                maximum_recording_seconds=stored.contract.maximum_recording_seconds,
            )
            baseline_path = baseline_root / "baseline.json"
            baseline_path.write_bytes(raw)
            baseline_path.chmod(0o600)
            emit(
                dict(
                    template=s.template.sha256,
                    source=str(s.declaration.root),
                    root=str(s.root),
                    baseline=str(baseline_path),
                )
            )
            config = receive()
            identities = {item["pid"]: item for item in config["identities"]}

            def identity(pid, cid):
                assert identities[pid]["container_id"] == cid
                return p.domains.process.process_identity(
                    pid,
                    cid,
                    Path(f"/proc/{pid}/stat").read_text(),
                    f"0::/system.slice/docker-{cid}.scope\n",
                )

            patches.setattr(p.domains.process, "read_identity", identity)
            for module in (
                p,
                p.listeners,
                p.connections,
                p.inputs_module,
                p.domains,
                p.preflight,
                p.bootstrap,
                p.bootstrap.links,
            ):
                patches.setattr(module, "ROOT_UID", os.geteuid())
            patches.setattr(p.inputs_module, "inputs_root", lambda _: Path(config["inputs"]))
            patches.setattr(p, "preparation_root", lambda *_: Path(config["preparation"]))
            patches.setattr(p, "baseline_root", lambda _: baseline_root)
            patches.setattr(p, "handoff_root", lambda _: Path(config["handoff"]))
            patches.setattr(p.preflight_channel, "peer_root", lambda _: Path(config["permission"]))
            patches.setattr(
                p.preflight_channel,
                "current_identity",
                lambda: identity(os.getpid(), config["writer_cid"]),
            )
            ordinary = SimpleNamespace(**vars(p.startups.plans.ordinary))
            ordinary.Docker = lambda: s.docker
            plans = SimpleNamespace(**vars(p.startups.plans))
            plans.ordinary = ordinary
            startup = SimpleNamespace(**vars(p.startups))
            startup.plans = plans
            patches.setattr(p, "startups", startup)
            owners, services, links = [], [], []
            for cls, collection in (
                (p.startups.Startup, owners),
                (writer.assembly.operator.IdleService, services),
                (p.bootstrap.links.Link, links),
            ):
                constructor = cls.__init__

                def capture(owner, *args, constructor=constructor, collection=collection, **kwargs):
                    constructor(owner, *args, **kwargs)
                    collection.append(owner)

                patches.setattr(cls, "__init__", capture)

            def forbidden(*args, **kwargs):
                raise AssertionError("Passive command attempted an active operation")

            for name in ("run", "start_native", "start_recording", "prepare_launch"):
                patches.setattr(writer.assembly.operator.IdleService, name, forbidden)
            patches.setattr(writer.assembly.operator.Inbox, "consume", forbidden)
            patches.setattr(p.bootstrap.links.Link, "_send", forbidden)
            if config["retained_scope"]:
                # Explicit fixture-only sequencing, NOT a selected installed
                # command or authenticated completion/action protocol. The
                # existing owners remain real and retain their original bound.
                def retain_scope(*args):
                    connection = args[2]
                    end = connection.deadline
                    with writer.m.retained_idle_from_inputs(*args) as receipt:
                        emit(dict(passive_retained=True, offer=receipt.offer_sha256))
                        packet = b""
                        while b"\n" not in packet:
                            assert time.monotonic() < end
                            assert select.select([0], [], [], end - time.monotonic())[0] == [0]
                            chunk = os.read(0, 256)
                            assert chunk and len(packet) + len(chunk) <= 256
                            packet += chunk
                        assert packet.endswith(b"\n") and packet.count(b"\n") == 1
                        assert json.loads(packet) == {"handoff_complete": True}
                        assert time.monotonic() < end
                    return receipt

                patches.setattr(writer.m, "prepare_idle_from_inputs", retain_scope)
            if config["authenticated_release"] and not config["fixed_release"]:
                # Explicit offline function selection, NOT installed command
                # provenance. No stdin completion signal: actual original
                # bootstrap credentials/receipt/deadline carry retirement.
                patches.setattr(
                    writer.m, "prepare_idle_from_inputs", writer.m.prepare_idle_until_released
                )
            if config["fixed_release"]:
                patches.setattr(writer.m, "prepare_idle_from_inputs", forbidden)
            poll = p.startups.Startup.poll
            announced = []

            def polling(owner):
                if not announced:
                    announced.append(owner)
                    plan = owner.original.plan
                    emit(dict(plan=plan.raw.decode(), sha256=plan.sha256))
                return poll(owner)

            patches.setattr(p.startups.Startup, "poll", polling)
            original_fds = writer.preflight_probe.fds()
            result, error, locations = None, None, []
            try:
                command = (
                    p.prepare_retained_idle_writer
                    if config["fixed_release"]
                    else p.prepare_idle_writer
                )
                result = command(
                    s.plan.case,
                    s.template.sha256,
                    stored.manifest_sha256,
                    identity(os.getppid(), config["outer_cid"]),
                )
            except p.UnconfirmedPreparation as refusal:
                error = "refused"
                locations = getattr(refusal, "__notes__", [])[:8]
            emit(
                dict(
                    result=result,
                    error=error,
                    refusal_locations=locations,
                    owners=len(owners),
                    services=len(services),
                    links=len(links),
                    host_reads=len(s.cached_calls),
                    clocks_closed=all(clock.closed for clock in s.clocks),
                    accepted=bool(owners and owners[0].accepted),
                    retired=all(owner.closed for owner in [*owners, *services, *links]),
                    original_dispatch=bool(
                        services
                        and services[0].dispatch._original_observe.__self__ is links[0]
                        and services[0].clock_witness is owners[0].clock
                        and services[0].original is owners[0].original
                    ),
                    input_baseline_preserved=baseline_path.read_bytes() == raw,
                    fd_delta=len(writer.preflight_probe.fds()) - len(original_fds),
                )
            )
            if config["exit_after_result"]:
                assert config["fixed_release"] and result == 75 and error is None
                raise SystemExit(result)
            # Other variants retain the fixture peer for explicit cancellation.
            sys.stdin.buffer.read()
        finally:
            with pytest.raises(StopIteration):
                next(service)


@pytest.fixture
def helper(supervised, image, configured, monkeypatch, tmp_path, request):
    root = tmp_path / "writer"
    root.mkdir(mode=0o700)
    code = (
        "import sys; from pathlib import Path; "
        "sys.path[:0] = [sys.argv[1], str(Path(sys.argv[1]) / 'src')]; "
        "from tests.test_supplemental_recording_qualified_peer_command import child; "
        "child(Path(sys.argv[2]))"
    )
    env = dict(entry.split("=", 1) for entry in configured)
    env.update(HOME="/root", HOSTNAME=helper_tests.env.env.HOSTNAME)
    process = subprocess.Popen(
        [sys.executable, "-I", "-B", "-c", code, str(Path(p.__file__).parent.parent), str(root)],
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )
    generator = h = None
    try:
        initial = json.loads(transport.line(process))
        supplied = dict(plan=initial["base"], baseline=initial["baseline"], child=process)
        selection = getattr(request.node, "callspec", SimpleNamespace(params={})).params.get(
            "joined"
        )
        mode = selection.get("mode") if type(selection) is dict else selection
        profile = "peer-retained" if mode == "release-command-pair" else "peer-preparation"
        generator = qualification.helper.__wrapped__(
            supervised,
            image,
            configured,
            monkeypatch,
            SimpleNamespace(param=(profile, None, supplied)),
        )
        h = next(generator)
        transport.command(process, dict(plan=h.plan.raw.decode(), sha256=h.plan.sha256))
        h.paths = json.loads(transport.line(process))
        assert h.paths["template"] == h.template.sha256
        h.baseline = initial["baseline"]
        yield h
    finally:
        # Original disposable fixture only; never discover or signal a live PID.
        try:
            if generator is not None:
                with pytest.raises(StopIteration):
                    next(generator)
            else:
                process.stdin.close()
                process.wait(timeout=3)
            assert process.returncode == getattr(h, "expected_returncode", 0)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=3)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()


def observer_child(path, *, retained=False):
    """Retain actual authenticated inputs and final plan before descriptor intake.

    Test-only sequencing, paths and cgroups; the outer's future plan/hash is NOT
    supplied through these fixture pipes. No installed command or App grant.
    """
    before = len(os.listdir("/proc/self/fd"))
    result = dict(received=False, error=None)
    with pytest.MonkeyPatch.context() as patch, ExitStack() as stack:
        for module in (p, p.connections, p.inputs_module, p.domains, p.bootstrap, p.links):
            patch.setattr(module, "ROOT_UID", os.geteuid())
        channel = None
        if not retained:
            channel = stack.enter_context(
                closing(socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET))
            )
            channel.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
            channel.connect(path)
            channel.setblocking(False)
        timer = stack.enter_context(closing(p.domains.clock.ClockWitness(p.domains.clock.read())))
        origin = timer.original
        print("ready", flush=True)
        config = receive()
        ids = {item["pid"]: item for item in config["identities"]}

        def identity(pid, cid):
            assert ids[pid]["container_id"] == cid
            return p.domains.process.process_identity(
                pid,
                cid,
                Path(f"/proc/{pid}/stat").read_text(),
                f"0::/system.slice/docker-{cid}.scope\n",
            )

        patch.setattr(p.domains.process, "read_identity", identity)
        patch.setattr(
            p.inputs_module.declarations, "declaration_root", lambda _: Path(config["source"])
        )
        patch.setattr(p.inputs_module, "inputs_root", lambda _: Path(config["inputs"]))
        patch.setattr(p, "preparation_root", lambda *_: Path(config["preparation"]))
        patch.setattr(p, "observer_plan_root", lambda _: Path(config["plan_socket"]))
        patch.setattr(p, "writer_case_root", lambda _: Path(config["case_root"]))
        patch.setattr(p.startups.plans.Plan, "root", property(lambda _: Path(config["case_root"])))
        outer = stack.enter_context(
            p.domains.process.ProcessWitness(
                identity(os.getppid(), ids[os.getppid()]["container_id"])
            )
        )
        local = identity(os.getpid(), ids[os.getpid()]["container_id"])
        declaration = stack.enter_context(
            p.inputs_module.declarations.Declaration(
                Path(config["source"]), config["template_sha256"]
            )
        )
        connection = stack.enter_context(
            closing(
                p.connections.Connection(
                    Path(config["preparation"]), outer, deadline=time.monotonic() + 2
                )
            )
        )
        try:
            inputs, writer_peer = stack.enter_context(
                p.receive_inputs(
                    declaration,
                    connection,
                    timer,
                    local,
                    role="observer",
                    baseline_sha256=config["baseline"],
                    preparation=True,
                )
            )
            emit(dict(inputs_retained=True))
            assert receive() == {"plan_ready": True}
            connection.close()  # Never renew or reuse the old input exchange.
            plan_connection = stack.enter_context(
                closing(
                    p.connections.Connection(
                        Path(config["plan_socket"]), outer, deadline=time.monotonic() + 2
                    )
                )
            )
            selected_clock, selected_peer, selected_local = timer, writer_peer, local
            fault = config.get("receive_fault")
            if fault == "clock":
                selected_clock = stack.enter_context(
                    closing(p.domains.clock.ClockWitness(p.domains.clock.read()))
                )
            elif fault == "peer":
                selected_peer = stack.enter_context(
                    p.domains.process.ProcessWitness(writer_peer.identity)
                )
            elif fault == "local":
                selected_local = p.domains.process.ProcessIdentity(**asdict(local))
            elif fault == "missing-context":
                del inputs._preparation_context
            original = stack.enter_context(
                p.receive_observer_plan(
                    inputs,
                    plan_connection,
                    selected_clock,
                    selected_local,
                    baseline_sha256=config["baseline"],
                    counterpart=selected_peer,
                )
            )
            plan = original.recheck()
            if fault == "replay":
                with (
                    pytest.raises(p.UnconfirmedPreparation),
                    p.receive_observer_plan(
                        inputs,
                        plan_connection,
                        timer,
                        local,
                        baseline_sha256=config["baseline"],
                        counterpart=writer_peer,
                    ),
                ):
                    raise AssertionError("A second plan exchange was admitted")
                assert original.recheck() is plan and not plan_connection.closed
            emit(dict(plan_retained=plan.sha256, input_pin=inputs.expected))
            assert receive() == {"handoff": True}
            plan_connection.close()
            end = min(time.monotonic() + 2, plan.lease["ready_by"])
            if retained:
                final_connection = stack.enter_context(
                    closing(p.connections.Connection(Path(config["handoff"]), outer, deadline=end))
                )
                channel = final_connection.channel
                end = final_connection.deadline
            assert inputs.recheck(deadline=end) is inputs.expectations
            assert original.recheck() is plan and timer.original is origin
            endpoint = stack.enter_context(
                closing(
                    p.bootstrap.Endpoint(
                        channel,
                        plan,
                        timer,
                        local,
                        outer,
                        writer_peer,
                        role="observer",
                        mode="receive",
                        declaration_sha256=inputs.expected,
                        deadline=end,
                    )
                )
            )
            channels, receipt = endpoint.receive()
            stack.callback(channels.close)
            assert (
                original.recheck() is plan and inputs.recheck(deadline=end) is inputs.expectations
            )
            link = stack.enter_context(
                closing(p.links.Link(channels, plan, timer, writer_peer, role="observer"))
            )
            assert link.timer is timer and link.plan is plan and link.sequence == 0
            result = dict(received=True, offer=receipt.offer_sha256, original_clock=True)
            emit(result)
            assert receive() == {"retire": True}
        except p.UnconfirmedPreparation:
            result = dict(received=False, error="refused")
            emit(result)
            receive()  # Keep originals alive until the outer captures the failure.
    emit(dict(fd_delta=len(os.listdir("/proc/self/fd")) - before, **result))
    receive()  # Never turn local cleanup into an observer-exit receipt.


def observer_runtime(h, counterpart, monkeypatch):
    """Reuse the full collector with explicit synthetic metadata for peer two.

    This inert comparison argv is not a fixed observer entrypoint or permission
    to install it. Its actual environment, files, namespace reads and pidfd are
    still collected, independently of the already qualified writer.
    """
    runtime = qualification.runtime_tests.m
    identity = counterpart.identity
    command = (
        "/usr/local/bin/python",
        "-I",
        "-B",
        "/opt/sdsctl-recording-host/supplemental_recording_peer_host_source.py",
        h.template.sha256,
        "observer",
    )
    container = copy.deepcopy(h.container)
    container.update(
        Id=identity.container_id,
        Name=h.container["Name"] + "-observer",
        Path=command[0],
        Args=list(command[1:]),
    )
    container["State"]["Pid"] = identity.pid
    container["Config"]["Entrypoint"] = list(command)
    container["GraphDriver"]["Data"]["ID"] = identity.container_id
    fields = {
        key: h.args[key]
        for key in m.declarations.ROLE_FIELDS
        if key not in {"runtime", "command_sha256", "configuration_sha256"}
    } | dict(
        runtime=asdict(h.plan.helper),
        command_sha256=m.declarations.command_digest(command),
        configuration_sha256=helper_tests.configuration_pin(container, h.plan.helper.environment),
    )
    original_open, original_os_open, original_stat = builtins.open, os.open, os.stat
    original_container = type(h.docker).container
    original_kernel = runtime.launch.runtime.collect_helper_kernel
    counts = dict(container=0, kernel=0, command=0)

    def opened(path, *args, **kwargs):
        if path == f"/proc/{identity.pid}/cmdline":
            counts["command"] += 1
            return io.BytesIO(b"\0".join(item.encode() for item in command) + b"\0")
        if path == f"/proc/{identity.pid}/root/proc/{identity.pid}/cgroup":
            return io.BytesIO(f"0::/system.slice/docker-{identity.container_id}.scope\n".encode())
        return original_open(path, *args, **kwargs)

    def routed(path):
        if path == f"/proc/{identity.pid}/root":
            return h.root
        if path == f"/proc/{identity.pid}/ns/mnt":
            return h.namespace_route
        return path

    def inspect_container(docker, name):
        if name == identity.container_id:
            assert docker is h.docker
            counts["container"] += 1
            return copy.deepcopy(container)
        return original_container(docker, name)

    def kernel(witness, *, deadline):
        if witness is counterpart:
            assert time.monotonic() < deadline
            counts["kernel"] += 1
            return runtime.launch.runtime.HelperKernel("6" * 64, identity, time.monotonic())
        return original_kernel(witness, deadline=deadline)

    monkeypatch.setattr(builtins, "open", opened)
    monkeypatch.setattr(os, "open", lambda path, *a, **kw: original_os_open(routed(path), *a, **kw))
    monkeypatch.setattr(os, "stat", lambda path, *a, **kw: original_stat(routed(path), *a, **kw))
    monkeypatch.setattr(type(h.docker), "container", inspect_container)
    monkeypatch.setattr(runtime.launch.runtime, "collect_helper_kernel", kernel)
    return SimpleNamespace(fields=fields, command=command, container=container, counts=counts)


@pytest.fixture
def joined(helper, monkeypatch, tmp_path, configured, request):
    preparation_diagnostics(monkeypatch)
    h = helper
    selection = getattr(request, "param", None)
    options = selection if type(selection) is dict else dict(mode=selection)
    mode = options["mode"]
    retained_scope = mode == "retained-scope-pair"
    fixed_release = mode == "release-command-pair"
    authenticated_release = mode in ("released-pair", "release-command-pair")
    retained_delivery = (
        mode in ("retained-plan-pair", "retained-scope-pair") or authenticated_release
    )
    plan_delivery = mode == "plan-pair" or retained_delivery
    for module in (
        p,
        p.listeners,
        p.inputs_module,
        p.domains,
        p.bootstrap,
        grants.permission,
        p.bootstrap.links,
    ):
        monkeypatch.setattr(module, "ROOT_UID", os.geteuid())
    with ExitStack() as stack:
        short = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="sds-full-writer-")))
        roots = {name: short / name for name in ("preparation", "permission", "handoff")}
        for root in roots.values():
            root.mkdir(mode=0o700)
        path = roots["handoff"] / p.connections.NAME
        final_server = None
        if not retained_delivery:
            final_server = stack.enter_context(
                closing(socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET))
            )
            final_server.bind(str(path))
            path.chmod(0o600)
            final_server.listen(2)
            final_server.settimeout(3)
        observer_code = (
            "import sys; sys.path[:0] = [sys.argv[1], sys.argv[1] + '/src']; "
            "from tests.test_supplemental_recording_qualified_peer_command import observer_child; "
            f"observer_child(sys.argv[2], retained={retained_delivery!r})"
            if plan_delivery
            else transport.CHILD.replace(
                "m.links.clock.ClockWitness(plan.original_clock)",
                "m.links.clock.ClockWitness(m.links.clock.read())",
            )
        )
        observer = subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-B",
                "-c",
                observer_code,
                str(
                    Path(__file__).resolve().parents[1]
                    if plan_delivery
                    else Path(p.__file__).parent
                ),
                str(path),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            env=dict(entry.split("=", 1) for entry in configured)
            | dict(HOME="/root", HOSTNAME=helper_tests.env.env.HOSTNAME),
        )

        def stop_observer():
            if observer.poll() is None:
                observer.kill()
            observer.wait(timeout=3)
            for stream in (observer.stdin, observer.stdout, observer.stderr):
                stream.close()

        stack.callback(stop_observer)
        observer_channel = None
        if not retained_delivery:
            observer_channel = stack.enter_context(closing(final_server.accept()[0]))
            observer_channel.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
            observer_channel.setblocking(False)
        assert transport.line(observer) == "ready"
        read_identity = p.domains.process.read_identity
        observer_id = p.domains.process.process_identity(
            observer.pid,
            "7" * 64,
            Path(f"/proc/{observer.pid}/stat").read_text(),
            f"0::/system.slice/docker-{'7' * 64}.scope\n",
        )

        def identity(pid, cid):
            if pid == observer.pid:
                assert cid == observer_id.container_id
                return p.domains.process.process_identity(
                    pid,
                    cid,
                    Path(f"/proc/{pid}/stat").read_text(),
                    f"0::/system.slice/docker-{cid}.scope\n",
                )
            return read_identity(pid, cid)

        monkeypatch.setattr(p.domains.process, "read_identity", identity)
        counterpart = stack.enter_context(p.domains.process.ProcessWitness(observer_id))
        comparison = (
            observer_runtime(h, counterpart, monkeypatch)
            if mode == "pair" or plan_delivery
            else None
        )
        input_root = tmp_path / "inputs"
        input_root.mkdir(mode=0o700)
        values = qualification.runtime_tests.declarations.value()
        values.update(
            kind=m.declarations.PREPARATION_KIND,
            source_kind=m.declarations.peer_source.PreparationProfile.KIND,
            template_sha256=h.template.sha256,
        )
        values["writer"] = {
            key: h.args[key]
            for key in m.declarations.ROLE_FIELDS
            if key not in {"runtime", "command_sha256"}
        } | dict(
            runtime=asdict(h.plan.helper),
            command_sha256=m.declarations.command_digest(h.args["command"]),
        )
        values["observer"]["runtime"]["source"] = h.plan.helper.source
        if comparison is not None:
            values["observer"] = comparison.fields
        expected = m.declarations.decode_peer_preparation(values)
        input_path = input_root / p.inputs_module.NAME
        input_path.write_bytes(expected.raw)
        input_path.chmod(0o600)
        source, case_root = Path(h.paths["source"]), Path(h.paths["root"])
        monkeypatch.setattr(p.inputs_module.declarations, "declaration_root", lambda _: source)
        monkeypatch.setattr(p.inputs_module, "inputs_root", lambda _: input_root)
        monkeypatch.setattr(p, "preparation_root", lambda *_: roots["preparation"])
        declaration = stack.enter_context(
            p.inputs_module.declarations.Declaration(source, h.template.sha256)
        )
        inputs = stack.enter_context(
            closing(p.inputs_module.Inputs(declaration, input_root, expected.sha256))
        )
        timer = stack.enter_context(closing(p.domains.clock.ClockWitness(p.domains.clock.read())))
        domain = stack.enter_context(closing(p.domains.ZeroDomain(timer.original, h.witness)))
        listener = stack.enter_context(
            closing(
                p.listeners.Listener(roots["preparation"], h.witness, deadline=time.monotonic() + 2)
            )
        )
        permission_server = stack.enter_context(
            closing(socket.socket(socket.AF_UNIX, socket.SOCK_STREAM))
        )
        path = roots["permission"] / "observer.sock"
        permission_server.bind(str(path))
        path.chmod(0o600)
        permission_server.listen(1)
        permission_server.settimeout(3)
        identities = [h.observer_identity, h.witness.identity, observer_id]
        transport.command(
            h.child,
            dict(
                identities=[asdict(item) for item in identities],
                inputs=str(input_root),
                outer_cid=h.observer_identity.container_id,
                writer_cid=h.witness.identity.container_id,
                retained_scope=retained_scope,
                authenticated_release=authenticated_release,
                fixed_release=fixed_release,
                exit_after_result=options.get("exit_after_result", False),
                **{key: str(value) for key, value in roots.items()},
            ),
        )
        listener.accept()
        assert p.send_inputs(
            inputs,
            listener,
            timer,
            h.observer_identity,
            role="writer",
            baseline_sha256=h.baseline,
            counterpart=counterpart,
            preparation=True,
        )
        channel = stack.enter_context(closing(permission_server.accept()[0]))
        channel.setblocking(False)
        q = m.PeerWriterPreflightQualification(
            inputs,
            h.observer_identity,
            timer,
            domain,
            h.witness,
            h.docker,
            baseline_sha256=h.baseline,
            generation=h.args["generation"],
            command=h.args["command"],
            passive_retirement=fixed_release,
        )
        sender = stack.enter_context(
            closing(
                grants.Sender(
                    inputs.template,
                    inputs.template.sha256,
                    h.baseline,
                    h.observer_identity,
                    h.witness,
                    domain,
                    timer,
                    channel,
                    q.review_and_qualify,
                )
            )
        )
        sender.receive()
        plan_socket = short / "observer-plan"
        observer_handoff = short / "observer-handoff"
        if plan_delivery:
            preparation = short / "observer-inputs"
            preparation.mkdir(mode=0o700)
            plan_socket.mkdir(mode=0o700)
            observer_handoff.mkdir(mode=0o700)
            monkeypatch.setattr(p, "preparation_root", lambda *_: preparation)
            observer_inputs = stack.enter_context(
                closing(
                    p.listeners.Listener(preparation, counterpart, deadline=time.monotonic() + 2)
                )
            )
            transport.command(
                observer,
                dict(
                    identities=[asdict(item) for item in identities],
                    source=str(source),
                    inputs=str(input_root),
                    preparation=str(preparation),
                    plan_socket=str(plan_socket),
                    case_root=str(case_root),
                    handoff=str(observer_handoff),
                    template_sha256=h.template.sha256,
                    baseline=h.baseline,
                    receive_fault=options.get("receive_fault"),
                ),
            )
            observer_inputs.accept()
            p.send_inputs(
                inputs,
                observer_inputs,
                timer,
                h.observer_identity,
                role="observer",
                baseline_sha256=h.baseline,
                counterpart=h.witness,
                preparation=True,
            )
            assert json.loads(transport.line(observer)) == dict(inputs_retained=True)
            monkeypatch.setattr(p, "observer_plan_root", lambda _: plan_socket)
            monkeypatch.setattr(p, "writer_case_root", lambda _: case_root)
        yield SimpleNamespace(
            h=h,
            q=q,
            sender=sender,
            inputs=inputs,
            timer=timer,
            domain=domain,
            observer=observer,
            counterpart=counterpart,
            identities=identities,
            observer_channel=observer_channel,
            final_server=final_server,
            case_root=case_root,
            expected=expected,
            stack=stack,
            path=input_path,
            comparison=comparison,
            pair=None,
            plan_delivery=plan_delivery,
            plan_socket=plan_socket,
            retained_delivery=retained_delivery,
            retained_scope=retained_scope,
            authenticated_release=authenticated_release,
            fixed_release=fixed_release,
            completion=options.get("completion", True),
            writer_handoff=roots["handoff"],
            observer_handoff=observer_handoff,
        )


def qualify_final(s, plan):
    runtime = qualification.runtime_tests.m
    expected = s.inputs.recheck()
    observers = s.comparison
    qualifiers = {}
    for role, witness, container, command in (
        ("writer", s.h.witness, s.h.container, s.h.args["command"]),
        ("observer", s.counterpart, observers.container, observers.command),
    ):
        qualifiers[role] = runtime.PeerRuntimeQualification(
            plan,
            witness,
            s.h.docker,
            template=s.inputs.template,
            expectations=expected,
            expectations_sha256=s.inputs.expected,
            role=role,
            generation=runtime.launch.plans.ordinary.generation(
                container, name=container["Name"][1:], image=plan.helper.image
            ),
            command=command,
            preparation=True,
        )
    s.pair = runtime.PeerRuntimePair(**qualifiers)
    end = min(time.monotonic() + 2, plan.lease["ready_by"])
    assert s.inputs.recheck(deadline=end) is expected
    s.pair._collect_before(end)
    assert s.inputs.recheck(deadline=end) is expected and time.monotonic() < end
    assert s.pair.writer.plan is s.pair.observer.plan is plan
    assert s.pair.writer.witness is s.h.witness and s.pair.observer.witness is s.counterpart


def finish(s, monkeypatch, after_qualification=None):
    """Explicitly synthetic final acceptance, real file and descriptor transport."""
    announced = json.loads(transport.line(s.h.child))
    plan = p.startups.plans.load_bytes(announced["plan"].encode(), announced["sha256"])
    s.inputs.template.check_plan(plan, plan.original_clock)
    assert plan.original_clock.before_ns > s.timer.original.after_ns
    assert plan.raw != s.q.plan.raw  # Never pass outer preview as continuing writer plan.
    if s.comparison is not None:
        qualify_final(s, plan)
    if after_qualification is not None:
        after_qualification()
    submission = writer.assembly.baseline_tests.integration.startups.sends.m
    # Only local case-file operations use this fixture path. Keeping the override
    # during fresh runtime comparison would change its expected Engine mount and
    # rightly refuse the originally pinned configuration.
    with monkeypatch.context() as files:
        files.setattr(p.startups.plans.Plan, "root", property(lambda _: s.case_root))
        original = s.stack.enter_context(submission.intake.CasePlan(s.case_root, plan.sha256))
        if s.plan_delivery:
            listener = s.stack.enter_context(
                closing(
                    p.listeners.Listener(
                        s.plan_socket, s.counterpart, deadline=time.monotonic() + 2
                    )
                )
            )
            s.plan_listener, s.sent_plan = listener, original
            transport.command(s.observer, dict(plan_ready=True))
            listener.accept()
            s.plan_receipt = p.send_observer_plan(
                s.inputs,
                original,
                listener,
                s.timer,
                s.h.observer_identity,
                baseline_sha256=s.h.baseline,
                counterpart=s.h.witness,
            )
            assert json.loads(transport.line(s.observer)) == dict(
                plan_retained=plan.sha256, input_pin=s.inputs.expected
            )
        if s.retained_delivery:
            end = min(time.monotonic() + 2, plan.lease["ready_by"])
            s.writer_listener = s.stack.enter_context(
                closing(p.listeners.Listener(s.writer_handoff, s.h.witness, deadline=end))
            )
            s.observer_listener = s.stack.enter_context(
                closing(p.listeners.Listener(s.observer_handoff, s.counterpart, deadline=end))
            )
        submission.Submission(original, s.inputs.template.sha256, plan.sha256).submit()
    if s.retained_delivery:
        writer_channel = s.writer_listener.accept()
    else:
        writer_channel = s.stack.enter_context(closing(s.final_server.accept()[0]))
        writer_channel.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        writer_channel.setblocking(False)
    transport.command(
        s.observer,
        dict(handoff=True)
        if s.plan_delivery
        else dict(
            plan=plan.raw.decode(),
            sha256=plan.sha256,
            role="observer",
            declaration=s.expected.sha256,
            identities=[asdict(item) for item in s.identities],
            local=s.counterpart.identity.container_id,
            outer=s.h.observer_identity.container_id,
            peer_pid=s.h.child.pid,
            peer=s.h.witness.identity.container_id,
        ),
    )
    if s.retained_delivery:
        s.observer_channel = s.observer_listener.accept()
    if hasattr(s, "watch"):
        if s.retained_delivery:
            deliver = (
                delivery_tests.m.deliver_and_release_passive_writer
                if s.authenticated_release
                else delivery_tests.m.deliver_from_inputs
            )
            s.delivered = deliver(
                s.custody,
                s.watch,
                s.h.observer_identity,
                s.inputs,
                s.writer_listener,
                s.observer_listener,
            )
        else:
            s.delivered = delivery_tests.m.deliver(
                s.custody, s.watch, s.h.observer_identity, writer_channel, s.observer_channel
            )
        assert s.delivered.plan_sha256 == plan.sha256
        assert s.delivered.declaration_sha256 == s.inputs.expected
    else:
        bundles = p.bootstrap.links.pair()
        for bundle in bundles:
            s.stack.callback(bundle.close)
        for role, connection, target, other, bundle in (
            ("writer", writer_channel, s.h.witness, s.counterpart, bundles[0]),
            ("observer", s.observer_channel, s.counterpart, s.h.witness, bundles[1]),
        ):
            endpoint = s.stack.enter_context(
                closing(
                    p.bootstrap.Endpoint(
                        connection,
                        plan,
                        s.timer,
                        s.h.observer_identity,
                        target,
                        other,
                        role=role,
                        mode="deliver",
                        declaration_sha256=s.expected.sha256,
                    )
                )
            )
            endpoint.deliver(bundle)
            bundle.close()
            endpoint.close()
    assert json.loads(transport.line(s.observer))["received"]
    if s.retained_scope:
        retained = json.loads(transport.line(s.h.child))
        assert retained == dict(passive_retained=True, offer=s.delivered.writer.offer_sha256)
        s.writer_listener.recheck()
        s.observer_listener.recheck()
        assert not s.watch.finished and s.custody.armed_watch is s.watch
        assert time.monotonic() < s.writer_listener.deadline
        if s.completion is not None:
            transport.command(s.h.child, dict(handoff_complete=s.completion))
        if s.completion is not True:
            return json.loads(transport.line(s.h.child))
    outcome = transport.line(s.h.child)
    milestone = p.RETAINED_MILESTONE if s.fixed_release else p.MILESTONE
    if s.authenticated_release and outcome != milestone:
        return json.loads(outcome)  # Refusal, not passive preparation completion.
    assert outcome == milestone
    return json.loads(transport.line(s.h.child))


def test_full_qualification_grants_real_original_writer_then_passive_retirement(
    joined, monkeypatch
):
    s = joined
    assert not list(s.case_root.iterdir())
    assert s.sender.send() is None
    result = finish(s, monkeypatch)
    assert result == dict(
        result=75,
        error=None,
        refusal_locations=[],
        owners=1,
        services=1,
        links=1,
        host_reads=1,
        clocks_closed=True,
        accepted=True,
        retired=True,
        original_dispatch=True,
        input_baseline_preserved=True,
        fd_delta=0,
    )
    assert (s.h.reads, s.h.images, s.h.kernels) == (2, 2, 2)
    assert not s.q.failed and s.q.permission_attempted and 0 < s.q.elapsed_seconds < 2
    assert s.q.inputs is s.inputs and s.q.observer is s.timer and s.q.domain is s.domain
    assert s.inputs.recheck() is s.q.expectations
    assert s.q.expectations.raw == s.expected.raw
    assert not s.h.witness.exited() and not s.counterpart.exited()
    assert not list((s.case_root / "inbox").iterdir())


@pytest.mark.parametrize("fault", ["source", "runtime", "command", "input"])
def test_failed_full_qualification_never_reaches_writer_baseline(joined, fault):
    s = joined
    if fault == "source":
        (s.h.root / s.q.HELPER / "supplemental_recording_peer_preparation.py").write_bytes(
            b"PRIVATE changed"
        )
    elif fault == "runtime":
        (s.h.root / "usr/local/lib/python3.14/site.py").write_bytes(b"PRIVATE changed")
    elif fault == "command":
        s.h.command_fault = b"PRIVATE changed\0"
    else:
        s.path.write_bytes(b"PRIVATE changed")
    with pytest.raises(grants.UnconfirmedDelivery):
        s.sender.send()
    # Refusal closes only this original peer channel; no retry or replacement.
    s.sender.channel.close()
    result = json.loads(transport.line(s.h.child))
    assert result["error"] == "refused" and result["host_reads"] == result["owners"] == 0
    assert result["retired"] and result["clocks_closed"] and result["input_baseline_preserved"]
    assert result["fd_delta"] == 0
    assert not list(s.case_root.iterdir())


def test_permission_cannot_replace_original_persisted_baseline_check(joined):
    s = joined
    path = Path(s.h.paths["baseline"])
    path.write_bytes(b"PRIVATE changed baseline")
    assert s.sender.send() is None  # Qualification grants a read, not baseline authenticity.
    result = json.loads(transport.line(s.h.child))
    assert result["error"] == "refused" and result["host_reads"] == 0
    assert result["owners"] == 1 and result["services"] == result["links"] == 0
    assert result["retired"] and result["clocks_closed"] and result["fd_delta"] == 0
    assert not result["input_baseline_preserved"]
    assert path.read_bytes() == b"PRIVATE changed baseline" and not list(s.case_root.iterdir())
    assert not s.q.failed and s.q.permission_attempted


def test_input_change_after_publication_keeps_failure_files_and_no_service(joined):
    s = joined
    assert s.sender.send() is None
    published = json.loads(transport.line(s.h.child))
    assert "plan" in published
    original = {path.name: path.read_bytes() for path in s.case_root.iterdir()}
    assert set(original) == {"startup-claim.json", "plan.json"}
    s.path.write_bytes(b"PRIVATE changed after publication")
    result = json.loads(transport.line(s.h.child))
    assert result["error"] == "refused" and result["host_reads"] == 1
    assert result["owners"] == 1 and result["services"] == result["links"] == 0
    assert not result["accepted"]
    assert result["retired"] and result["clocks_closed"] and result["fd_delta"] == 0
    assert result["input_baseline_preserved"]
    assert original == {path.name: path.read_bytes() for path in s.case_root.iterdir()}
    assert s.path.read_bytes() == b"PRIVATE changed after publication"


@pytest.mark.parametrize("joined", ["pair"], indirect=True)
def test_original_preflight_then_both_full_final_collectors_then_actual_command(
    joined, monkeypatch
):
    s = joined
    assert s.sender.send() is None
    result = finish(s, monkeypatch)
    assert result["result"] == 75 and result["original_dispatch"] and result["fd_delta"] == 0
    assert s.pair.elapsed_seconds < 2 and not s.pair.failed
    assert s.h.reads == s.h.kernels == 4  # Fresh writer check, not preflight-result reuse.
    assert s.comparison.counts == dict(container=2, kernel=2, command=2)
    assert s.pair.writer.expectations is s.pair.observer.expectations is s.inputs.expectations
    assert s.pair.writer.plan is not s.q.plan


@pytest.mark.parametrize(
    "joined",
    [
        "plan-pair",
        dict(mode="plan-pair", receive_fault="replay"),
    ],
    indirect=True,
)
def test_observer_retains_actual_authenticated_inputs_then_delivered_plan_and_original_link(
    joined, monkeypatch
):
    s = joined
    assert s.sender.send() is None
    result = finish(s, monkeypatch)
    assert result["result"] == 75 and result["original_dispatch"] and result["fd_delta"] == 0
    assert s.plan_receipt and s.pair.elapsed_seconds < 2
    assert s.h.reads == 4 and s.comparison.counts["container"] == 2
    transport.command(s.observer, dict(retire=True))
    result = json.loads(transport.line(s.observer))
    assert result["received"] and result["original_clock"] and result["fd_delta"] == 0
    assert not s.counterpart.exited() and not s.h.witness.exited()


@pytest.mark.parametrize(
    "joined",
    [
        dict(mode="plan-pair", receive_fault=fault)
        for fault in ("clock", "peer", "local", "missing-context")
    ],
    indirect=True,
)
def test_observer_plan_cannot_adopt_replacements_for_authenticated_original_owners(
    joined, monkeypatch
):
    s = joined
    assert s.sender.send() is None
    with pytest.raises(p.UnconfirmedPreparation):
        finish(s, monkeypatch)
    assert json.loads(transport.line(s.observer))["error"] == "refused"
    assert {path.name for path in s.case_root.iterdir()} == {"startup-claim.json", "plan.json"}
    s.observer.kill()
    s.observer.wait(timeout=3)
    result = json.loads(transport.line(s.h.child))
    assert result["services"] == 0 and result["error"] == "refused" and result["fd_delta"] == 0


@pytest.mark.parametrize("joined", ["plan-pair"], indirect=True)
@pytest.mark.parametrize("fault", ["clock", "peer", "local", "inputs"])
def test_plan_sender_keeps_its_completed_observer_input_owners(joined, monkeypatch, fault):
    s = joined
    selected = dict(timer=s.timer, counterpart=s.h.witness, local=s.h.observer_identity)
    inputs = s.inputs
    if fault == "clock":
        # Capture BEFORE the writer's later plan: timestamp ordering alone must
        # not admit a replacement for the original preparation clock.
        selected["timer"] = s.stack.enter_context(
            closing(p.domains.clock.ClockWitness(p.domains.clock.read()))
        )
    elif fault == "peer":
        selected["counterpart"] = s.stack.enter_context(
            p.domains.process.ProcessWitness(s.h.witness.identity)
        )
    elif fault == "local":
        selected["local"] = p.domains.process.ProcessIdentity(**asdict(s.h.observer_identity))
    else:
        inputs = s.stack.enter_context(
            closing(p.inputs_module.Inputs(s.inputs.declaration, s.inputs.root, s.inputs.expected))
        )
    send = p.send_observer_plan

    def replaced(original_inputs, plan, listener, timer, local, **kwargs):
        assert original_inputs is s.inputs and timer is s.timer and local is s.h.observer_identity
        return send(
            inputs,
            plan,
            listener,
            selected["timer"],
            selected["local"],
            **(kwargs | dict(counterpart=selected["counterpart"])),
        )

    monkeypatch.setattr(p, "send_observer_plan", replaced)
    assert s.sender.send() is None
    with pytest.raises(p.UnconfirmedPreparation):
        finish(s, monkeypatch)
    assert json.loads(transport.line(s.observer))["error"] == "refused"
    assert {path.name for path in s.case_root.iterdir()} == {"startup-claim.json", "plan.json"}
    s.observer.kill()
    s.observer.wait(timeout=3)
    result = json.loads(transport.line(s.h.child))
    assert result["error"] == "refused" and result["services"] == 0 and result["fd_delta"] == 0


@pytest.mark.parametrize("joined", ["plan-pair"], indirect=True)
@pytest.mark.parametrize(
    "fault",
    [
        "schema",
        "kind",
        "scope",
        "nonce",
        "case",
        "baseline",
        "plan-pin",
        "input-pin",
        "writer",
        "extra",
        "rights",
        "descendant",
        "expired",
        "input-drift",
        "plan-drift",
    ],
)
def test_observer_plan_refusal_preserves_claim_and_never_accepts_or_builds_service(
    joined, monkeypatch, fault
):
    s = joined
    original_send = p._Exchange.send

    def send(exchange, frame):
        if exchange.final_plan and frame["phase"] == "plan":
            values = {
                "schema": dict(schema=True),
                "kind": dict(kind=p.KIND),
                "scope": dict(scope=p.SCOPE),
                "nonce": dict(nonce="0" * 64),
                "case": dict(case="0" * 32),
                "baseline": dict(baseline_sha256="0" * 64),
                "plan-pin": dict(plan_sha256="0" * 64),
                "input-pin": dict(expectations_sha256="0" * 64),
                "writer": dict(writer=frame["requester"]),
                "extra": dict(action="start"),
            }
            frame = frame | values.get(fault, {})
            if fault == "expired":
                time.sleep(max(0, exchange.end - time.monotonic()) + 0.01)
            if fault in ("rights", "descendant"):
                exchange.wait(sending=True)
                raw = p.links.base.encode(frame)
                if fault == "rights":
                    exchange.channel.sendmsg(
                        [raw],
                        [
                            (
                                socket.SOL_SOCKET,
                                socket.SCM_RIGHTS,
                                p.struct.pack("i", exchange.remote.fd),
                            )
                        ],
                    )
                else:
                    child_pid = os.fork()
                    if child_pid == 0:
                        exchange.channel.send(raw)
                        os._exit(0)
                    assert os.waitpid(child_pid, 0) == (child_pid, 0)
                return p.links.base.checksum(frame)
            result = original_send(exchange, frame)
            if fault == "input-drift":
                s.path.write_bytes(b"PRIVATE input drift after plan offer")
            if fault == "plan-drift":
                (s.case_root / "plan.json").write_bytes(b"PRIVATE plan drift after offer")
            return result
        return original_send(exchange, frame)

    monkeypatch.setattr(p._Exchange, "send", send)
    assert s.sender.send() is None
    with pytest.raises(p.UnconfirmedPreparation):
        finish(s, monkeypatch)
    assert set(path.name for path in s.case_root.iterdir()) == {"startup-claim.json", "plan.json"}
    files = {path.name: path.read_bytes() for path in s.case_root.iterdir()}
    assert json.loads(transport.line(s.observer))["error"] == "refused"
    s.observer.kill()  # Only this original fixture peer; not App recovery.
    s.observer.wait(timeout=3)
    result = json.loads(transport.line(s.h.child))
    assert result["error"] == "refused" and result["host_reads"] == 1
    assert result["services"] == result["links"] == 0 and not result["accepted"]
    assert result["retired"] and result["clocks_closed"] and result["fd_delta"] == 0
    assert files == {path.name: path.read_bytes() for path in s.case_root.iterdir()}


@pytest.mark.parametrize("joined", ["plan-pair"], indirect=True)
@pytest.mark.parametrize("fault", ["inputs", "plan", "clock", "deadline"])
def test_observer_retention_ack_is_not_outer_completion_or_startup_acceptance(
    joined, monkeypatch, fault
):
    s = joined
    close = p._Exchange.close
    changed = []

    def retired(exchange):
        opened = exchange.domain is not None and not exchange.domain.closed
        if exchange.final_plan and opened:
            # The wire ack precedes the receiver's final checks. Synchronize
            # this fault AFTER that local completion so it tests the sender's
            # retirement, not a race against receiver-side file validation.
            s.retained_before_fault = json.loads(transport.line(s.observer))
            assert s.retained_before_fault["plan_retained"]
        close(exchange)
        if exchange.final_plan and opened:
            changed.append(True)
            if fault == "inputs":
                s.path.write_bytes(b"PRIVATE changed during sender retirement")
            elif fault == "plan":
                (s.case_root / "plan.json").write_bytes(b"PRIVATE changed during sender retirement")
            elif fault == "clock":
                s.timer.close()
            else:
                time.sleep(max(0, exchange.end - time.monotonic()) + 0.01)

    monkeypatch.setattr(p._Exchange, "close", retired)
    assert s.sender.send() is None
    with pytest.raises(p.UnconfirmedPreparation):
        finish(s, monkeypatch)
    assert changed == [True] and s.retained_before_fault["plan_retained"]
    assert {path.name for path in s.case_root.iterdir()} == {"startup-claim.json", "plan.json"}
    with pytest.raises(p.UnconfirmedPreparation):
        p.send_observer_plan(
            s.inputs,
            s.sent_plan,
            s.plan_listener,
            s.timer,
            s.h.observer_identity,
            baseline_sha256=s.h.baseline,
            counterpart=s.h.witness,
        )
    s.observer.kill()
    s.observer.wait(timeout=3)
    result = json.loads(transport.line(s.h.child))
    assert result["error"] == "refused" and result["services"] == 0 and not result["accepted"]
    assert result["fd_delta"] == 0


@pytest.mark.parametrize("joined", ["pair"], indirect=True)
@pytest.mark.parametrize("fault", ["writer-source", "observer-configuration", "observer-exit"])
def test_final_pair_refusal_never_submits_acceptance_or_constructs_service(
    joined, monkeypatch, fault
):
    s = joined
    assert s.sender.send() is None
    assert not s.q.failed and s.h.reads == 2
    if fault == "writer-source":
        (s.h.root / s.q.HELPER / "supplemental_recording_peer_preparation.py").write_bytes(
            b"PRIVATE post-permission source drift"
        )
    elif fault == "observer-configuration":
        s.comparison.container["Config"]["Env"].append("PRIVATE_UNDECLARED=value")
    if fault == "observer-exit":
        announced = json.loads(transport.line(s.h.child))
        plan = p.startups.plans.load_bytes(announced["plan"].encode(), announced["sha256"])
        s.observer.kill()
        s.observer.wait(timeout=3)
        with pytest.raises(m.launch.UnconfirmedHostLaunch):
            qualify_final(s, plan)
    else:
        with pytest.raises(m.launch.UnconfirmedHostLaunch):
            finish(s, monkeypatch)
    original = {path.name: path.read_bytes() for path in s.case_root.iterdir()}
    assert set(original) == {"startup-claim.json", "plan.json"}
    # Test retirement of the original disposable counterpart, not App recovery
    # or a claimed installed outer watchdog. The writer observes its real pidfd.
    if s.observer.poll() is None:
        s.observer.kill()
        s.observer.wait(timeout=3)
    result = json.loads(transport.line(s.h.child))
    assert result["error"] == "refused" and result["host_reads"] == 1
    assert result["owners"] == 1 and result["services"] == result["links"] == 0
    assert not result["accepted"] and result["clocks_closed"] and result["fd_delta"] == 0
    assert result["retired"] and result["input_baseline_preserved"]
    assert original == {path.name: path.read_bytes() for path in s.case_root.iterdir()}


def arm_original_watch(s):
    stops = termination_tests.m
    observer_domain = s.stack.enter_context(
        closing(p.domains.ZeroDomain(s.timer.original, s.counterpart))
    )
    custody = s.stack.enter_context(
        closing(stops.Custody(s.pair, s.timer, s.domain, observer_domain))
    )
    watch = stops.arm(custody, scope=stops.SCOPE)
    s.stack.callback(watch.close)
    s.custody, s.watch = custody, watch
    assert custody.clock is s.timer and custody.origin is s.timer.original
    assert custody.plan is s.pair.writer.plan and custody.plan is not s.q.plan
    assert watch.identities == (s.h.witness.identity, s.counterpart.identity)
    s.h.expected_returncode = -signal.SIGKILL


@pytest.mark.skipif(
    not termination_tests.m.deadlines.timerfd_available(), reason="Linux timerfd API required"
)
@pytest.mark.parametrize(
    "joined",
    ["pair", "plan-pair", "retained-scope-pair", "released-pair", "release-command-pair"],
    indirect=True,
)
def test_original_outer_watch_spans_final_acceptance_dispatcher_and_passive_retirement(
    joined, monkeypatch
):
    s = joined

    assert s.sender.send() is None
    result = finish(s, monkeypatch, lambda: arm_original_watch(s))
    assert result["result"] == 75 and result["original_dispatch"] and result["fd_delta"] == 0
    if s.plan_delivery:
        assert s.plan_receipt  # Original watcher already armed before plan delivery.
    watch, custody = s.watch, s.custody
    assert not watch.closed and not s.h.witness.exited() and not s.counterpart.exited()
    # Initial final comparison, capture comparison and arming comparison ALL
    # run afresh before acceptance, then before/after descriptor delivery;
    # only the writer has the extra preflight.
    assert s.h.reads == 12 and s.comparison.counts["container"] == 10
    assert s.pair.channel_delivery_attempted
    assert s.delivered.writer.context_sha256 != s.delivered.observer.context_sha256
    assert watch.deadline_ns == custody.deadline_ns
    files = {
        path.relative_to(s.case_root): path.read_bytes()
        for path in s.case_root.rglob("*")
        if path.is_file()
    }
    watch.close()  # Explicit cancellation, NOT successful App recovery or disarm.
    assert s.h.child.wait(timeout=3) == s.observer.wait(timeout=3) == -signal.SIGKILL
    assert files == {
        path.relative_to(s.case_root): path.read_bytes()
        for path in s.case_root.rglob("*")
        if path.is_file()
    }


@pytest.mark.skipif(
    not termination_tests.m.deadlines.timerfd_available(), reason="Linux timerfd API required"
)
@pytest.mark.parametrize(
    "joined", [dict(mode="release-command-pair", exit_after_result=True)], indirect=True
)
def test_fixed_passive_command_exit_triggers_original_watch_without_cancellation(
    joined, monkeypatch
):
    s = joined

    def arm():
        arm_original_watch(s)
        s.h.expected_returncode = 75

    assert s.sender.send() is None
    result = finish(s, monkeypatch, arm)
    assert result["result"] == 75 and result["error"] is None
    assert result["retired"] and result["clocks_closed"] and result["fd_delta"] == 0
    assert result["original_dispatch"] and result["input_baseline_preserved"]
    watch = s.watch
    assert not watch.closed and not watch.finished
    assert s.custody.armed_watch is watch and s.plan_receipt
    assert s.h.reads == 12 and s.comparison.counts["container"] == 10
    files = {
        path.relative_to(s.case_root): path.read_bytes()
        for path in s.case_root.rglob("*")
        if path.is_file()
    }
    # Observe actual exits under the ORIGINAL handoff cutoff. No renewed wait,
    # cancellation byte, explicit kill, stdin completion or replacement peer.
    end = s.writer_listener.deadline
    for fd in (s.h.witness.fd, s.counterpart.fd, watch.fd):
        assert time.monotonic() < end
        assert select.select([fd], [], [], end - time.monotonic())[0] == [fd]
    assert s.h.child.wait(timeout=0) == 75
    assert s.observer.wait(timeout=0) == -signal.SIGKILL
    outcome = watch.finish()
    assert outcome.returncode == 11  # Peer loss, NOT successful recording/recovery.
    assert outcome.writer == s.h.witness.identity
    assert outcome.observer == s.counterpart.identity
    assert outcome.deadline_ns == watch.deadline_ns == s.custody.deadline_ns
    assert not watch.closed and watch.finished and time.monotonic() < end
    assert files == {
        path.relative_to(s.case_root): path.read_bytes()
        for path in s.case_root.rglob("*")
        if path.is_file()
    }


@pytest.mark.skipif(
    not termination_tests.m.deadlines.timerfd_available(), reason="Linux timerfd API required"
)
@pytest.mark.parametrize(
    "joined",
    ["plan-pair", "retained-scope-pair", "released-pair", "release-command-pair"],
    indirect=True,
)
@pytest.mark.parametrize("when", ["before-first", "after-writer", "after-observer"])
def test_authenticated_plan_then_source_drift_cancels_original_supervised_handoff(
    joined, monkeypatch, when
):
    s = joined
    source = s.h.root / s.q.HELPER / "supplemental_recording_peer_preparation.py"
    sent, changed = [], []
    delivery_name = (
        "deliver_and_release_passive_writer"
        if s.authenticated_release
        else "deliver_from_inputs"
        if s.retained_delivery
        else "deliver"
    )
    deliver = getattr(delivery_tests.m, delivery_name)
    endpoint_send = p.bootstrap.Endpoint.deliver

    def change():
        changed.append(True)
        source.write_bytes(b"PRIVATE fixture source drift during supervised delivery")

    def delivered(*args):
        if when == "before-first":
            change()
        return deliver(*args)

    def endpoint(endpoint, channels):
        result = endpoint_send(endpoint, channels)
        sent.append(endpoint.role)
        if when == "after-" + endpoint.role:
            change()
        return result

    monkeypatch.setattr(delivery_tests.m, delivery_name, delivered)
    monkeypatch.setattr(p.bootstrap.Endpoint, "deliver", endpoint)
    assert s.sender.send() is None
    with pytest.raises(delivery_tests.m.UnconfirmedDelivery):
        finish(s, monkeypatch, lambda: arm_original_watch(s))
    assert changed == [True] and s.plan_receipt
    assert s.pair.channel_delivery_attempted and s.pair.failed
    assert s.watch.closed and s.watch.finished
    assert s.h.child.wait(timeout=3) == s.observer.wait(timeout=3) == -signal.SIGKILL
    assert sent == ([] if when == "before-first" else ["writer", "observer"])
    files = {
        path.relative_to(s.case_root): path.read_bytes()
        for path in s.case_root.rglob("*")
        if path.is_file()
    }
    assert Path("startup-claim.json") in files and Path("plan.json") in files
    # No retry can replace the consumed pair, watcher or peers. Accepted startup
    # and any partial passive journal stay evidence, never action/recovery success.
    with pytest.raises(delivery_tests.m.UnconfirmedDelivery):
        deliver(
            s.custody,
            s.watch,
            s.h.observer_identity,
            *((s.inputs, None, None) if s.retained_delivery else (None, None)),
        )
    assert files == {
        path.relative_to(s.case_root): path.read_bytes()
        for path in s.case_root.rglob("*")
        if path.is_file()
    }


@pytest.mark.skipif(
    not termination_tests.m.deadlines.timerfd_available(), reason="Linux timerfd API required"
)
@pytest.mark.parametrize("joined", ["retained-plan-pair"], indirect=True)
def test_retained_delivery_refuses_writer_connection_retiring_before_outer_completion(
    joined, monkeypatch
):
    """Real command exposes its still-unjoined passive retirement boundary.

    This expected REFUSAL is not a successful retained-launcher qualification.
    The same bounded guard must reject writer EOF, never ignore it to pass.
    """
    s = joined

    def supervise():
        arm_original_watch(s)
        collect = s.pair._collect_before
        calls = []

        def sampled(end):
            calls.append(True)
            if len(calls) == 2:
                # Allow the ACTUAL passive writer to retire before the final
                # comparison returns. All reads still share the original bound.
                assert transport.line(s.h.child) == p.MILESTONE
                s.writer_retirement = json.loads(transport.line(s.h.child))
                assert time.monotonic() < end and s.writer_retirement["fd_delta"] == 0
            return collect(end)

        monkeypatch.setattr(s.pair, "_collect_before", sampled)

    assert s.sender.send() is None
    with pytest.raises(delivery_tests.m.UnconfirmedDelivery):
        finish(s, monkeypatch, supervise)
    assert s.plan_receipt and s.pair.channel_delivery_attempted
    assert s.writer_listener.failed and s.watch.closed and s.watch.finished
    assert time.monotonic() < s.writer_listener.deadline  # EOF, not an elapsed budget.
    assert s.writer_listener.peer is s.h.witness and s.observer_listener.peer is s.counterpart
    assert s.pair.writer.expectations is s.pair.observer.expectations is s.inputs.expectations
    assert s.h.child.wait(timeout=3) == s.observer.wait(timeout=3) == -signal.SIGKILL
    assert (s.case_root / "startup-claim.json").is_file()
    assert (s.case_root / "plan.json").is_file()


@pytest.mark.skipif(
    not termination_tests.m.deadlines.timerfd_available(), reason="Linux timerfd API required"
)
@pytest.mark.parametrize(
    "joined",
    [
        dict(mode="retained-scope-pair", completion=None),
        dict(mode="retained-scope-pair", completion=False),
    ],
    indirect=True,
)
def test_retained_scope_missing_or_bad_fixture_completion_preserves_failure(joined, monkeypatch):
    """A fixture's completion signal is not an installed protocol or action grant."""
    s = joined
    assert s.sender.send() is None
    result = finish(s, monkeypatch, lambda: arm_original_watch(s))
    assert result["error"] == "refused" and result["result"] is None
    assert result["retired"] and result["clocks_closed"] and result["fd_delta"] == 0
    assert result["owners"] == result["services"] == result["links"] == result["host_reads"] == 1
    assert result["input_baseline_preserved"] and result["original_dispatch"]
    assert s.pair.channel_delivery_attempted and s.plan_receipt
    assert not s.watch.closed and s.custody.armed_watch is s.watch
    if s.completion is None:
        assert time.monotonic() >= s.writer_listener.deadline
    files = {
        path.relative_to(s.case_root): path.read_bytes()
        for path in s.case_root.rglob("*")
        if path.is_file()
    }
    assert Path("startup-claim.json") in files and Path("journal/0000.json") in files
    s.watch.close()  # Failure cancellation, not a successful recovery.
    assert s.h.child.wait(timeout=3) == s.observer.wait(timeout=3) == -signal.SIGKILL
    assert files == {
        path.relative_to(s.case_root): path.read_bytes()
        for path in s.case_root.rglob("*")
        if path.is_file()
    }


@pytest.mark.skipif(
    not termination_tests.m.deadlines.timerfd_available(), reason="Linux timerfd API required"
)
@pytest.mark.parametrize("joined", ["released-pair", "release-command-pair"], indirect=True)
@pytest.mark.parametrize("fault", ["message", "missing"])
def test_authenticated_release_refusal_retires_original_passive_scope_without_retry(
    joined, monkeypatch, fault
):
    s = joined
    calls = []

    def corrupted(endpoint, receipt):
        calls.append(endpoint)
        assert endpoint.passive_retirement and receipt is endpoint.receipt
        if fault == "message":
            endpoint._send(
                endpoint._frame(
                    "retire-passive-writer",
                    endpoint.exchange_nonce,
                    offer_sha256=receipt.offer_sha256,
                    scope="PRIVATE wrong release scope",
                )
            )
        # Omitting the send must expire at the receiver's ORIGINAL cutoff.

    monkeypatch.setattr(p.bootstrap.Endpoint, "send_retirement", corrupted)
    assert s.sender.send() is None
    result = finish(s, monkeypatch, lambda: arm_original_watch(s))
    assert len(calls) == 1 and result["result"] is None and result["error"] == "refused"
    assert result["retired"] and result["clocks_closed"] and result["fd_delta"] == 0
    assert result["original_dispatch"] and result["input_baseline_preserved"]
    assert result["owners"] == result["services"] == result["links"] == 1
    assert not s.watch.closed and s.custody.armed_watch is s.watch
    # Outer send/transport facts are explicitly NOT a receiver-success receipt.
    assert s.delivered.writer is calls[0].receipt
    if fault == "missing":
        assert time.monotonic() >= s.writer_listener.deadline
    assert (s.case_root / "startup-claim.json").is_file()
    assert (s.case_root / "journal/0000.json").is_file()
    s.watch.close()
    assert s.h.child.wait(timeout=3) == s.observer.wait(timeout=3) == -signal.SIGKILL


@pytest.mark.skipif(
    not termination_tests.m.deadlines.timerfd_available(), reason="Linux timerfd API required"
)
@pytest.mark.parametrize("joined", ["released-pair", "release-command-pair"], indirect=True)
@pytest.mark.parametrize("fault", ["sender-retirement", "receipt-replacement"])
def test_failed_final_retirement_or_changed_receipt_never_releases_writer(
    joined, monkeypatch, fault
):
    s = joined
    retirement, release = delivery_tests.m._retire, p.bootstrap.Endpoint.send_retirement
    retired, released = [], []

    def retire(channels, pins):
        retirement(channels, pins)
        retired.append(channels)
        if fault == "sender-retirement" and len(retired) == 3:
            raise ValueError("PRIVATE final sender retirement fault")

    def send(endpoint, receipt):
        released.append(endpoint)
        assert len(retired) == 4  # Both original copies, then final cleanup.
        assert endpoint.passive_retirement and receipt is endpoint.receipt
        return release(endpoint, p.bootstrap.Receipt(receipt.context_sha256, receipt.offer_sha256))

    monkeypatch.setattr(delivery_tests.m, "_retire", retire)
    monkeypatch.setattr(p.bootstrap.Endpoint, "send_retirement", send)
    assert s.sender.send() is None
    with pytest.raises(delivery_tests.m.UnconfirmedDelivery) as error:
        finish(s, monkeypatch, lambda: arm_original_watch(s))
    assert str(error.value) == delivery_tests.m.MESSAGE and len(retired) == 4
    assert len(released) == (0 if fault == "sender-retirement" else 1)
    assert s.watch.closed and s.watch.finished
    assert s.h.child.wait(timeout=3) == s.observer.wait(timeout=3) == -signal.SIGKILL
    assert (s.case_root / "startup-claim.json").is_file()
    assert (s.case_root / "plan.json").is_file()


@pytest.mark.parametrize("joined", ["release-command-pair"], indirect=True)
def test_retained_command_pin_does_not_implicitly_select_new_preflight_policy(joined):
    s = joined
    assert s.q.passive_retirement is True and s.h.args["command"][-1] == p.RETAINED_MODE
    with pytest.raises(m.launch.UnconfirmedHostLaunch):
        m.PeerWriterPreflightQualification(
            s.inputs,
            s.h.observer_identity,
            s.timer,
            s.domain,
            s.h.witness,
            s.h.docker,
            baseline_sha256=s.h.baseline,
            generation=s.h.args["generation"],
            command=s.h.args["command"],
        )
    assert s.h.reads == s.h.images == s.h.kernels == 0
    assert not s.q.failed and not s.q.permission_attempted
    assert not list(s.case_root.iterdir())


@pytest.mark.parametrize("joined", ["release-command-pair"], indirect=True)
def test_original_retained_preflight_selection_cannot_change_before_permission(joined):
    s = joined
    s.q.passive_retirement = False
    with pytest.raises(grants.UnconfirmedDelivery):
        s.sender.send()
    assert s.q.failed and s.q.permission_attempted
    assert s.h.reads == s.h.images == s.h.kernels == 0
    assert not list(s.case_root.iterdir())
