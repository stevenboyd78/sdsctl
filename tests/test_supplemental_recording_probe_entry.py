"""Fixed isolated probe: fake actors/cache unit boundaries plus real native IPC.

None of these fixtures authenticate an installed image or Engine exec. The real
operator fixture owns a synthetic loopback scanner and separate native/watchdog
processes. Probe subprocesses use the actual closed bundle and cached API.
"""

import copy
import hashlib
import importlib.util
import subprocess
import time
from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_launch_plan as plans
from . import test_supplemental_recording_operator as operator

probe = operator.probing.m
wire = operator.w
family, fake_cache = operator.probing.family, operator.probing.fake_cache
tree, configured, cached, prepared, staged = (
    operator.tree,
    operator.configured,
    operator.cached,
    operator.prepared,
    operator.staged,
)
MESSAGE = (probe.MESSAGE + "\n").encode()


def test_prepare_imports_qualified_reader_but_does_not_collect_before_request(monkeypatch):
    entry = plans.Path(plans.m.__file__).with_name("accept_supplemental_recording_probe.py")
    spec = importlib.util.spec_from_file_location("probe_entry_order_fixture", entry)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    trace = []
    runtime = plans.Path("/trusted/fixture/sds200")

    class Layout:
        def __init__(self, product, native):
            assert product == runtime and native == entry.parent
            self.runtime = product

        def verify(self, pin):
            assert pin == "a" * 64
            trace.append("verify")

    class Stream:
        def __init__(self, *args, **kwargs):
            trace.append("stream")

        def receive(self, *, deadline):
            trace.append("receive")
            raise EOFError("test stops before a request")

        def close(self):
            trace.append("close")

    def imported(name):
        trace.append(name)
        if name == "supplemental_recording_source":
            return SimpleNamespace(Layout=Layout)
        if name == "supplemental_recording_wire":
            return SimpleNamespace(Stream=Stream)
        # No sample/probe_inputs method: any premature observation would fail.
        assert name in ("supplemental_recording_probe", "supplemental_recording_launch_plan")
        return SimpleNamespace()

    monkeypatch.setattr(
        module,
        "sys",
        SimpleNamespace(flags=SimpleNamespace(isolated=1, dont_write_bytecode=1), path=[]),
    )
    monkeypatch.setattr(module, "logging", SimpleNamespace(CRITICAL=50, disable=lambda _: None))
    monkeypatch.setattr(
        module,
        "importlib",
        SimpleNamespace(
            import_module=imported,
            util=SimpleNamespace(
                find_spec=lambda _: SimpleNamespace(origin=str(runtime / "__init__.py"))
            ),
        ),
    )
    monkeypatch.setattr(
        module,
        "arguments",
        lambda _: {"runtime-root": str(runtime), "source-sha256": "a" * 64, "probe-by": 10},
    )
    with pytest.raises(EOFError):
        module.run([])
    assert trace == [
        "supplemental_recording_source",
        "verify",
        "supplemental_recording_probe",
        "supplemental_recording_launch_plan",
        "supplemental_recording_wire",
        "stream",
        "receive",
        "close",
    ]


@pytest.fixture
def request_case(family, fake_cache, monkeypatch):
    context = dict.fromkeys(
        (
            "launch",
            "source",
            "projection",
            "host_plan",
            "profile",
            "manifest",
            "contract",
            "generation",
        ),
        "a" * 64,
    )
    inputs = plans.m.ProbeInputs(*fake_cache.paths, "Version 1.26.01", context, 15)
    reads = []

    def read(*args, **kwargs):
        reads.append((args, kwargs))
        return inputs

    monkeypatch.setattr(plans.m, "probe_inputs", read)
    request = {
        "schema": 1,
        "kind": "finite-recording-cached-probe",
        "context": context | {"ready_by": family.expected.deadline - 15},
        "guardian": asdict(family.expected.guardian),
        "native": asdict(family.expected.native),
        "watchdog": asdict(family.expected.watchdog)
        | {"deadline": family.expected.deadline, "grace": 3},
    }
    return request, inputs, reads


def sample(request, fake_cache):
    return probe.sample(
        request,
        fake_cache.paths[0].with_name("launch.json"),
        plan_sha256="a" * 64,
        source_sha256="a" * 64,
        probe_by=time.monotonic() + 5,
    )


@pytest.mark.parametrize(
    "healthy,recording", [(True, False), (True, True), (False, False), (False, True)]
)
def test_bounded_sample_keeps_truthful_flags_and_checks_original_inputs_twice(
    request_case, family, fake_cache, healthy, recording
):
    request, _, reads = request_case
    fake_cache.result = replace(fake_cache.result, healthy=healthy, recording=recording)
    before = copy.deepcopy(request)
    result = sample(request, fake_cache)
    assert request == before
    assert result == {
        "schema": 1,
        "kind": "finite-recording-cached-probe-result",
        "request_sha256": hashlib.sha256(wire.encode(request)).hexdigest(),
        "observed_after": result["observed_after"],
        "observed_at": result["observed_at"],
        "body": asdict(fake_cache.result),
    }
    assert result["observed_after"] <= result["observed_at"] < family.expected.deadline
    assert len(reads) == 2 and reads[0] == reads[1] and len(fake_cache.calls) == 1
    assert not any(
        name in wire.encode(result).decode() for name in ("deployment", "recordings", "daemon.sock")
    )


@pytest.mark.parametrize(
    "fault",
    [
        "extra",
        "schema",
        "kind",
        "context_extra",
        "ready_bool",
        "ready_expired",
        "guardian_extra",
        "actor_ticks",
        "watch_deadline",
        "watch_grace",
        "probe_deadline",
        "launch",
        "source",
        "projection",
        "host_plan",
        "profile",
        "manifest",
        "contract",
        "generation",
    ],
)
def test_bad_request_never_reaches_cached_read(request_case, fake_cache, fault):
    request, inputs, _ = request_case
    if fault in inputs.context:
        request["context"][fault] = "b" * 64
    elif fault == "extra":
        request["recording.start"] = True
    elif fault == "schema":
        request["schema"] = True
    elif fault == "kind":
        request["kind"] = "finite-recording-operator"
    elif fault == "context_extra":
        request["context"]["ready_until"] = 10**20
    elif fault == "ready_bool":
        request["context"]["ready_by"] = True
    elif fault == "ready_expired":
        request["context"]["ready_by"] = 1
    elif fault == "guardian_extra":
        request["guardian"]["parent"] = 1
    elif fault == "actor_ticks":
        request["native"]["start_ticks"] += 1
    elif fault == "watch_deadline":
        request["watchdog"]["deadline"] += 1
    elif fault == "watch_grace":
        request["watchdog"]["grace"] = True
    elif fault == "probe_deadline":
        request["context"]["ready_by"] = time.monotonic() + 1 - inputs.maximum_recording_seconds
        request["watchdog"]["deadline"] = (
            request["context"]["ready_by"] + inputs.maximum_recording_seconds
        )
    operator.probing.denied(lambda: sample(request, fake_cache))
    assert not fake_cache.calls


def test_input_replacement_during_read_discards_good_cache(request_case, fake_cache, monkeypatch):
    _, inputs, _ = request_case
    fake_cache.hook = lambda: monkeypatch.setattr(
        plans.m, "probe_inputs", lambda *a, **kw: replace(inputs, firmware="changed")
    )
    operator.probing.denied(lambda: sample(request_case[0], fake_cache))
    assert len(fake_cache.calls) == 1


def test_request_cannot_change_after_actor_and_context_checks(request_case, fake_cache):
    request, _, _ = request_case
    fake_cache.hook = lambda: request["context"].update(generation="b" * 64)
    operator.probing.denied(lambda: sample(request, fake_cache))
    assert len(fake_cache.calls) == 1


def start(staged, prepared, *, change=None, flags=("-I", "-B"), stdin=subprocess.PIPE):
    args = [
        "--plan",
        str(prepared.path),
        "--plan-sha256",
        hashlib.sha256(plans.p.encode(prepared.value)).hexdigest(),
        "--source-sha256",
        staged.pin,
        "--runtime-root",
        str(staged.layout.runtime),
        "--probe-by",
        str(time.monotonic() + 5),
    ]
    if change:
        args = change(args)
    return subprocess.Popen(
        [
            str(staged.python),
            *flags,
            str(staged.layout.native / "accept_supplemental_recording_probe.py"),
            *args,
        ],
        stdin=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
        env={
            "PATH": "/usr/bin:/bin",
            "PYTHONPATH": "/PRIVATE_IGNORED",
            "SDS200_HOST": "PRIVATE_IGNORED",
        },
    )


@pytest.mark.parametrize(
    "fault",
    [
        "isolated",
        "bytecode",
        "unknown",
        "duplicate",
        "help",
        "bad_pin",
        "wrong_pin",
        "source_pin",
        "runtime_origin",
        "relative_plan",
        "relative_runtime",
        "past",
        "nan",
        "far",
        "input_device",
        "eof",
        "partial",
        "extra_request_field",
        "noncanonical",
        "duplicate_json",
        "huge",
    ],
)
def test_fixed_probe_refuses_without_health_or_side_effects(staged, prepared, fault):
    operator.guard.prepare_source_pin(staged, prepared)
    flags = ("-B",) if fault == "isolated" else (("-I",) if fault == "bytecode" else ("-I", "-B"))

    def change(args):
        if fault == "unknown":
            return args + ["--recording-start", "PRIVATE"]
        if fault == "duplicate":
            return ["--source-sha256" if arg == "--plan-sha256" else arg for arg in args]
        if fault == "help":
            return ["--help"]
        changes = {
            "bad_pin": ("plan-sha256", "PRIVATE"),
            "wrong_pin": ("plan-sha256", "9" * 64),
            "source_pin": ("source-sha256", "9" * 64),
            "runtime_origin": ("runtime-root", str(staged.layout.runtime.parent)),
            "relative_plan": ("plan", "PRIVATE/launch.json"),
            "relative_runtime": ("runtime-root", "PRIVATE"),
            "past": ("probe-by", "0"),
            "nan": ("probe-by", "NaN"),
            "far": ("probe-by", str(time.monotonic() + 50)),
        }
        return operator.child.changed(args, *changes[fault]) if fault in changes else args

    process = start(
        staged,
        prepared,
        change=change,
        flags=flags,
        stdin=subprocess.DEVNULL if fault == "input_device" else subprocess.PIPE,
    )
    valid_envelope = wire.encode(
        {
            "schema": 1,
            "kind": "finite-recording-cached-probe",
            "context": {},
            "guardian": {},
            "native": {},
            "watchdog": {},
        }
    )
    raw = {
        "partial": b"\0\0",
        "extra_request_field": wire.HEADER.pack(2) + b"{}",
        "noncanonical": wire.HEADER.pack(3) + b"{ }",
        "duplicate_json": wire.HEADER.pack(13) + b'{"a":1,"a":2}',
        "huge": wire.HEADER.pack(wire.MAX_BYTES + 1),
    }.get(
        fault,
        wire.HEADER.pack(len(valid_envelope)) + valid_envelope if fault == "wrong_pin" else b"",
    )
    output, error = process.communicate(input=raw if process.stdin else None, timeout=8)
    assert process.returncode == 70 and output == b"" and error == MESSAGE
    assert not (prepared.path.parent / "guardian").exists()
    assert not list(prepared.spec.sockets.iterdir()) and not list(prepared.spec.receipts.iterdir())
    assert plans.p.Collector(prepared.stored).pristine().files.stage == "pristine"


def test_actual_fixed_probe_before_and_during_actual_native_recording(
    staged, prepared, monkeypatch
):
    observations = []

    def fixed(expected, deployment, recordings, daemon_socket, *, firmware):
        # Original context comes from the same fixture's actual operator/plan,
        # not a public caller. Installed Engine provenance is still a later gate.
        original = plans.m.probe_inputs(
            prepared.path,
            expected_sha256=hashlib.sha256(plans.p.encode(prepared.value)).hexdigest(),
            expected_source_sha256=staged.pin,
        )
        assert (deployment, recordings, daemon_socket, firmware) == (
            original.deployment,
            original.recordings,
            original.daemon_socket,
            original.firmware,
        )
        request = {
            "schema": 1,
            "kind": "finite-recording-cached-probe",
            "context": original.context
            | {"ready_by": expected.deadline - original.maximum_recording_seconds},
            "guardian": asdict(expected.guardian),
            "native": asdict(expected.native),
            "watchdog": asdict(expected.watchdog) | {"deadline": expected.deadline, "grace": 3},
        }
        process = start(staged, prepared)
        stream = wire.Stream(process.stdout.fileno(), process.stdin.fileno(), role="probe")
        try:
            stream.send(request, deadline=time.monotonic() + 2)
            result = stream.receive(deadline=time.monotonic() + 5)
            assert result["request_sha256"] == hashlib.sha256(wire.encode(request)).hexdigest()
            assert result["kind"] == "finite-recording-cached-probe-result"
            assert result["observed_after"] <= result["observed_at"] < expected.deadline
        finally:
            stream.close()
            process.stdin.close()
            process.stdin = None
            output, error = process.communicate(timeout=6)
        assert process.returncode == 0 and output == error == b""
        observed = probe.cached.CachedEvidence(**result["body"])
        observations.append(observed)
        return observed

    monkeypatch.setattr(probe, "collect", fixed)
    operator.test_fixed_operator_actual_session(staged, prepared, monkeypatch, "record")
    assert [result.recording for result in observations] == [False, True]
    assert all(result.healthy for result in observations)
