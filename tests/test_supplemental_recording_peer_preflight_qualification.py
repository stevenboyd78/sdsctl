"""Actual retained inputs, original processes/stream, Sender and Permission.

The full existing qualifier reads real fixture source/runtime/environment files.
Engine/configuration/kernel/cgroup routes and installed input provenance remain
synthetic. The child only consumes an empty preflight scope: no baseline/Startup,
final acceptance, App, scanner, recording or recovery action is run here.
"""

import json
import os
import socket
import tempfile
from contextlib import ExitStack, closing
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_peer_runtime_qualification as runtime_tests
from . import test_supplemental_recording_permission_sender as sender_tests
from . import test_supplemental_recording_service_qualification as old

m = old.m
inputs_module, grants = m.input_files, sender_tests.m
layout, image_umask, supervised = old.layout, old.image_umask, old.supervised
image, configured, helper = old.image, old.configured, old.helper
denied = old.denied
line = sender_tests.review_tests.peer_tests.line

CHILD = r"""
import json, os, socket, sys
from pathlib import Path
from contextlib import ExitStack, closing
raw = sys.stdin.buffer.readline()
if not raw:
    sys.exit(0)
config = json.loads(raw)
sys.path[:0] = [config["scripts"], str(Path(config["scripts"]).parent / "src")]
import supplemental_recording_service_permission as gate
gate.ROOT_UID = gate.domains.ROOT_UID = os.geteuid()
def identity(pid, cid):
    return gate.domains.process.process_identity(pid, cid,
        Path(f"/proc/{pid}/stat").read_text(), f"0::/system.slice/docker-{cid}.scope\n")
gate.domains.process.read_identity = identity
with ExitStack() as stack:
    channel = stack.enter_context(closing(socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)))
    channel.connect(config["path"])
    channel.setblocking(False)
    clock = stack.enter_context(closing(gate.clock.ClockWitness(gate.clock.read())))
    peer = stack.enter_context(gate.domains.process.ProcessWitness(
        identity(os.getppid(), config["outer_cid"])))
    domain = stack.enter_context(closing(gate.domains.ZeroDomain(clock.original, peer)))
    template = gate.templates.decode(config["template"])
    permission = stack.enter_context(closing(gate.Permission(template, template.sha256,
        config["baseline"], identity(os.getpid(), config["writer_cid"]),
        peer, domain, clock, channel)))
    print("receiver-ready", flush=True)
    try:
        permission.wait()
        with permission.consume() as guard:
            guard()  # Empty read scope; never a synthetic App action.
        print(json.dumps(dict(approved=permission.approved, consumed=permission.used,
            original_clock_unchanged=clock.original is permission.origin,
            challenge_sha256=permission.challenge_sha256)), flush=True)
    except Exception:
        print("refused", flush=True)
    sys.stdin.buffer.read()
"""

pytestmark = pytest.mark.parametrize(
    "helper", [("peer-preparation", CHILD)], indirect=True, ids=["peer-preparation"]
)


@pytest.fixture
def offered(helper, tmp_path, monkeypatch, request):
    h = helper
    monkeypatch.setattr(inputs_module, "ROOT_UID", os.geteuid())
    monkeypatch.setattr(grants.permission, "ROOT_UID", os.geteuid())
    startup_root, input_root = tmp_path / "startup", tmp_path / "inputs"
    startup_root.mkdir(mode=0o700)
    input_root.mkdir(mode=0o700)
    template_path, expected_path = (
        startup_root / inputs_module.declarations.NAME,
        input_root / inputs_module.NAME,
    )
    template_path.write_bytes(h.template.raw)
    template_path.chmod(0o600)
    values = runtime_tests.declarations.value()
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
    variant = getattr(request, "param", None)
    decode = m.declarations.decode_peer_preparation
    if variant in ("old", "handoff"):
        values["kind"] = m.declarations.KIND if variant == "old" else m.declarations.PEER_KIND
        values["source_kind"] = (
            m.declarations.source.KIND if variant == "old" else m.declarations.peer_source.KIND
        )
        decode = m.declarations.decode if variant == "old" else m.declarations.decode_peer_handoff
    elif variant == "command-digest":
        values["writer"]["command_sha256"] = "0" * 64
    expected = decode(values)
    expected_path.write_bytes(expected.raw)
    expected_path.chmod(0o600)
    monkeypatch.setattr(inputs_module.declarations, "declaration_root", lambda _: startup_root)
    monkeypatch.setattr(inputs_module, "inputs_root", lambda _: input_root)
    with ExitStack() as stack:
        declaration = stack.enter_context(
            inputs_module.declarations.Declaration(startup_root, h.template.sha256)
        )
        inputs = stack.enter_context(
            closing(inputs_module.Inputs(declaration, input_root, expected.sha256))
        )
        timer = stack.enter_context(
            closing(m.launch.plans.clock.ClockWitness(m.launch.plans.clock.read()))
        )
        domain = stack.enter_context(
            closing(m.launch.time_domain.ZeroDomain(timer.original, h.witness))
        )
        directory = stack.enter_context(tempfile.TemporaryDirectory(prefix="sds-qualified-peer-"))
        path = str(Path(directory) / "outer.sock")
        server = stack.enter_context(closing(socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)))
        server.bind(path)
        Path(path).chmod(0o600)
        server.listen(1)
        server.settimeout(3)
        h.child.stdin.write(
            m.launch.base.encode(
                dict(
                    scripts=str(Path(m.__file__).parent),
                    path=path,
                    outer_cid=h.observer_identity.container_id,
                    writer_cid=h.witness.identity.container_id,
                    template=json.loads(inputs.template.raw),
                    baseline="d" * 64,
                )
            )
            + b"\n"
        )
        h.child.stdin.flush()
        channel = stack.enter_context(closing(server.accept()[0]))
        channel.setblocking(False)
        assert line(h.child) == b"receiver-ready\n"

        def make(**changes):
            return m.PeerWriterPreflightQualification(
                **(
                    dict(
                        inputs=inputs,
                        observer_identity=h.observer_identity,
                        observer=timer,
                        domain=domain,
                        witness=h.witness,
                        docker=h.docker,
                        baseline_sha256="d" * 64,
                        generation=h.args["generation"],
                        command=h.args["command"],
                    )
                    | changes
                )
            )

        objects = []

        def sender(qualifier):
            obj = grants.Sender(
                inputs.template,
                inputs.template.sha256,
                "d" * 64,
                h.observer_identity,
                h.witness,
                domain,
                timer,
                channel,
                qualifier.review_and_qualify,
            )
            objects.append(obj)
            return obj

        try:
            yield SimpleNamespace(
                h=h,
                inputs=inputs,
                timer=timer,
                domain=domain,
                make=make,
                sender=sender,
                path=expected_path,
                template_path=template_path,
                channel=channel,
                expected=expected,
            )
        finally:
            for obj in objects:
                obj.close()


def test_original_inputs_and_full_qualifier_join_actual_one_use_permission(offered):
    s, q = offered, offered.make()
    assert Path(m.__file__).stem not in m.declarations.peer_source.PreparationProfile.MODULES
    owner, original = s.sender(q), s.timer.original
    review = owner.receive()
    assert review.timer is s.timer and review.target is s.h.witness
    assert review.template is s.inputs.template and review.domain is s.domain
    assert owner.send() is None
    received = json.loads(line(s.h.child))
    assert received == dict(
        approved=True,
        consumed=True,
        original_clock_unchanged=True,
        challenge_sha256=review.challenge_sha256,
    )
    assert (s.h.reads, s.h.images, s.h.kernels) == (2, 2, 2)
    assert q.permission_attempted and not q.failed and 0 < q.elapsed_seconds < 2
    # The preview serializes the outer sample. It never becomes the continuing
    # writer clock; the actual ClockWitness and its origin retain identity.
    assert q.plan.original_clock == original and s.timer.original is original
    assert q.origin is original and q.observer is s.timer
    assert q.plan is not s.h.plan and not hasattr(q, "accepted")
    assert q._source_layout(s.h.root).observe().sha256 == s.h.plan.helper.source
    assert s.inputs.recheck() is q.expectations
    assert not s.inputs.closed and not s.domain.closed and not s.h.witness.exited()
    denied(lambda: q.review_and_qualify(review))
    assert q.failed and s.h.reads == 2


@pytest.mark.parametrize("index", range(9))
def test_exact_command_fields_refuse_before_engine_observation(offered, index):
    command = list(offered.h.args["command"])
    command[index] = "PRIVATE wrong command"
    denied(lambda: offered.make(command=tuple(command)))
    assert offered.h.reads == offered.h.images == 0


@pytest.mark.parametrize("fault", ["inputs", "expectations", "expectations_raw", "source"])
def test_replaced_original_input_slots_are_sticky(offered, fault):
    q = offered.make()
    previous = getattr(q, fault)
    setattr(q, fault, object())
    denied(q)
    setattr(q, fault, previous)
    denied(q)
    assert q.failed and offered.h.reads == 0


@pytest.mark.parametrize("fault", ["file", "source", "runtime", "kernel", "config", "cutoff"])
def test_drift_after_challenge_prevents_permission_write(offered, monkeypatch, fault):
    s, q = offered, offered.make()
    owner = s.sender(q)
    owner.receive()
    if fault == "file":
        s.path.write_bytes(s.expected.raw + b"\n")
    elif fault == "source":
        (s.h.root / q.HELPER / "supplemental_recording_peer_preparation.py").write_bytes(
            b"PRIVATE changed"
        )
    elif fault == "runtime":
        (s.h.root / "usr/local/bin/python3.14").write_bytes(b"PRIVATE changed")
    elif fault == "kernel":
        s.h.kernel_fault = lambda *_: None
    elif fault == "config":
        s.h.fault = lambda value: value["HostConfig"].update(NetworkMode="host")
    else:
        monkeypatch.setattr(m.launch.time, "monotonic", lambda: q.cutoff)
    sender_tests.denied(owner.send)
    assert owner.failed and not owner.write_attempted
    assert s.path.exists() and s.template_path.exists()
    sender_tests.denied(owner.send)


def test_input_change_after_final_review_is_rejected_before_delivery(offered, monkeypatch):
    s, q = offered, offered.make()
    owner = s.sender(q)
    review = owner.receive()
    actual = review.read
    count = 0

    def changed():
        nonlocal count
        result = actual()
        count += 1
        # Sender's guard reads once, then callback before/after full collection.
        if s.h.reads == 2:
            s.path.write_bytes(s.expected.raw + b"\n")
        return result

    monkeypatch.setattr(review, "read", changed)
    sender_tests.denied(owner.send)
    assert count >= 2 and q.failed and not owner.write_attempted
    assert s.path.read_bytes() == s.expected.raw + b"\n"


def test_old_probe_and_single_service_policies_do_not_admit_peer_writer(offered):
    s = offered
    for kind in (old.m.ServicePreflightQualification, old.m.preceding.PermissionProbeQualification):
        denied(
            lambda kind=kind: kind(
                template=s.inputs.template,
                template_sha256=s.inputs.template.sha256,
                observer=s.timer,
                domain=s.domain,
                witness=s.h.witness,
                docker=s.h.docker,
                observer_identity=s.h.observer_identity,
                baseline_sha256="d" * 64,
                **s.h.args,
            )
        )
    assert s.h.reads == 0


@pytest.mark.parametrize("offered", ["old", "handoff", "command-digest"], indirect=True)
def test_valid_but_wrong_declared_policy_never_reads_engine(offered):
    denied(offered.make)
    assert offered.h.reads == 0
    assert not offered.inputs.closed and offered.path.read_bytes() == offered.expected.raw


@pytest.mark.parametrize("field", ["inputs", "observer_identity", "observer", "domain", "witness"])
def test_serialized_or_missing_original_handles_refuse(offered, field):
    denied(lambda: offered.make(**{field: {}}))
    assert offered.h.reads == 0


@pytest.mark.parametrize(
    "field", ["template", "timer", "domain", "target", "observer", "baseline_sha256"]
)
def test_same_original_review_required_before_collection(offered, field):
    q, owner = offered.make(), None
    owner = offered.sender(q)
    review = owner.receive()
    setattr(review, field, object())
    denied(lambda: q.review_and_qualify(review))
    assert q.failed and q.permission_attempted and offered.h.reads == 0
    assert not owner.write_attempted


@pytest.mark.parametrize("error", [KeyboardInterrupt(), SystemExit(91)])
def test_interruptions_preserve_files_and_borrowed_original_descriptors(
    offered, monkeypatch, error
):
    s, q = offered, offered.make()
    owner = s.sender(q)
    owner.receive()

    def interrupted(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(m.launch.runtime, "collect_helper_kernel", interrupted)
    with pytest.raises(type(error)) as caught:
        owner.send()
    assert caught.value is error and q.failed and not owner.write_attempted
    for fd in (s.timer.fd, s.domain.pidfd, s.h.witness.fd, s.inputs.file):
        os.fstat(fd)
    assert s.path.read_bytes() == s.expected.raw
    assert not s.timer.closed and not s.inputs.closed


def test_post_collection_checks_do_not_get_a_new_two_second_allowance(offered, monkeypatch):
    s, q = offered, offered.make()
    owner = s.sender(q)
    owner.receive()
    actual = m.launch.HelperQualification._collect_before
    cutoff = m.launch.time.monotonic() + q.MAX_SECONDS

    def delayed(self, end):
        result = actual(self, end)
        monkeypatch.setattr(m.launch.time, "monotonic", lambda: cutoff + 0.1)
        return result

    monkeypatch.setattr(m.launch.HelperQualification, "_collect_before", delayed)
    sender_tests.denied(owner.send)
    assert s.h.reads == 2 and q.failed and q.elapsed_seconds is None
    assert not owner.write_attempted and not s.timer.closed


def test_actual_writer_exit_does_not_trigger_replacement_or_permission(offered):
    s, q = offered, offered.make()
    owner = s.sender(q)
    owner.receive()
    s.h.child.terminate()
    s.h.child.wait(timeout=3)
    sender_tests.denied(owner.send)
    denied(q)
    assert s.h.reads == 0 and not owner.write_attempted
    assert s.path.read_bytes() == s.expected.raw and not s.inputs.closed


def test_equal_replacement_file_is_not_adopted_during_full_collection(offered):
    s, q = offered, offered.make()
    owner = s.sender(q)
    owner.receive()
    preserved = s.path.with_name("original-preserved")

    def replace_file(_container):
        s.path.rename(preserved)
        s.path.write_bytes(s.expected.raw)
        s.path.chmod(0o600)

    s.h.fault = replace_file
    sender_tests.denied(owner.send)
    assert q.failed and not owner.write_attempted
    assert preserved.read_bytes() == s.path.read_bytes() == s.expected.raw


def test_callback_deadline_is_passed_into_full_collection_not_renewed(offered, monkeypatch):
    s, q = offered, offered.make()
    owner = s.sender(q)
    owner.receive()
    starts, ends = [], []
    actual = m.launch.HelperQualification._collect_before
    before = m.launch.time.monotonic()

    def observed(self, deadline):
        starts.append(m.launch.time.monotonic())
        ends.append(deadline)
        return actual(self, deadline)

    monkeypatch.setattr(m.launch.HelperQualification, "_collect_before", observed)
    owner.send()
    assert json.loads(line(s.h.child))["consumed"] is True
    assert len(ends) == 1
    assert before < starts[0] < ends[0] < starts[0] + q.MAX_SECONDS
    assert ends[0] <= q.cutoff and ends[0] <= owner.review.cutoff
