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
from . import test_supplemental_recording_peer_preflight_qualification as qualification
from . import test_supplemental_recording_peer_termination as termination_tests

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
            result, error = None, None
            try:
                result = p.prepare_idle_writer(
                    s.plan.case,
                    s.template.sha256,
                    stored.manifest_sha256,
                    identity(os.getppid(), config["outer_cid"]),
                )
            except p.UnconfirmedPreparation:
                error = "refused"
            emit(
                dict(
                    result=result,
                    error=error,
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
            # Keep original writer alive until outer checks and retirement finish.
            sys.stdin.buffer.read()
        finally:
            with pytest.raises(StopIteration):
                next(service)


@pytest.fixture
def helper(supervised, image, configured, monkeypatch, tmp_path):
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
        generator = qualification.helper.__wrapped__(
            supervised,
            image,
            configured,
            monkeypatch,
            SimpleNamespace(param=("peer-preparation", None, supplied)),
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
    h = helper
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
        final_server = stack.enter_context(
            closing(socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET))
        )
        path = roots["handoff"] / p.connections.NAME
        final_server.bind(str(path))
        path.chmod(0o600)
        final_server.listen(2)
        final_server.settimeout(3)
        observer = subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-B",
                "-c",
                transport.CHILD.replace(
                    "m.links.clock.ClockWitness(plan.original_clock)",
                    "m.links.clock.ClockWitness(m.links.clock.read())",
                ),
                str(Path(p.__file__).parent),
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
            if getattr(request, "param", None) == "pair"
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
    monkeypatch.setattr(p.startups.plans.Plan, "root", property(lambda _: s.case_root))
    submission = writer.assembly.baseline_tests.integration.startups.sends.m
    original = s.stack.enter_context(submission.intake.CasePlan(s.case_root, plan.sha256))
    submission.Submission(original, s.inputs.template.sha256, plan.sha256).submit()
    writer_channel = s.stack.enter_context(closing(s.final_server.accept()[0]))
    writer_channel.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
    writer_channel.setblocking(False)
    transport.command(
        s.observer,
        dict(
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
    assert transport.line(s.h.child) == p.MILESTONE
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


@pytest.mark.skipif(
    not termination_tests.m.deadlines.timerfd_available(), reason="Linux timerfd API required"
)
@pytest.mark.parametrize("joined", ["pair"], indirect=True)
def test_original_outer_watch_spans_final_acceptance_dispatcher_and_passive_retirement(
    joined, monkeypatch
):
    s, stops = joined, termination_tests.m
    watches, custodies = [], []

    def supervise():
        observer_domain = s.stack.enter_context(
            closing(p.domains.ZeroDomain(s.timer.original, s.counterpart))
        )
        custody = s.stack.enter_context(
            closing(stops.Custody(s.pair, s.timer, s.domain, observer_domain))
        )
        custodies.append(custody)
        watch = stops.arm(custody, scope=stops.SCOPE)
        s.stack.callback(watch.close)
        watches.append(watch)
        assert custody.clock is s.timer and custody.origin is s.timer.original
        assert custody.plan is s.pair.writer.plan and custody.plan is not s.q.plan
        assert watch.identities == (s.h.witness.identity, s.counterpart.identity)
        s.h.expected_returncode = -signal.SIGKILL

    assert s.sender.send() is None
    result = finish(s, monkeypatch, supervise)
    assert result["result"] == 75 and result["original_dispatch"] and result["fd_delta"] == 0
    watch, custody = watches[0], custodies[0]
    assert not watch.closed and not s.h.witness.exited() and not s.counterpart.exited()
    # Initial final comparison, capture comparison and arming comparison ALL
    # run afresh before acceptance; only the writer has the extra preflight.
    assert s.h.reads == 8 and s.comparison.counts["container"] == 6
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
