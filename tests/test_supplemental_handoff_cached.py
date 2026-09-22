"""Real private profile storage plus cached-only IPC doubles; no scanner I/O."""

import importlib.util
import os
import struct
import sys
from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import pytest

from . import test_scanner_display_deployment as deployment_tests

NAME = "supplemental_handoff_cached"
if NAME not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        NAME, Path(__file__).resolve().parents[1] / "scripts" / (NAME + ".py")
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[NAME] = module
    spec.loader.exec_module(module)
c = sys.modules[NAME]
configured = deployment_tests.configured


@pytest.fixture
def cached(configured, tmp_path, monkeypatch):
    deployment, config = configured
    repo = config.repository()
    prepared = repo.prepare(config.binding, acquired_at=datetime.now(UTC))
    accepted = repo.commit(prepared, imported_at=datetime.now(UTC))

    class Client:
        def __init__(self, location, *, timeout):
            assert timeout == 0.2
            self.calls, self.closed = [], False
            self.runtime = {
                "state": "running",
                "scanner_connected": True,
                "psi_active": True,
                "scanner_model": "SDS200",
                "scanner_firmware": "Version 1.26.01",
                "scanner_endpoint": config.scanner_target,
            }
            self.operations = ["hello", "runtime.snapshot", "recording.status", "display.profile"]
            self.recording = {"active": False}
            self.profile = {
                "configured": True,
                "failure": None,
                "endpoint_id": str(config.binding.endpoint_id),
                "accepted": {
                    "source_id": str(config.binding.source_id),
                    "source_kind": config.binding.source_kind.value,
                    "revision": accepted.profile.revision,
                },
            }
            self.hook = lambda: None

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.closed = True

        def connect(self):
            return self

        def getsockopt(self, *args):
            return struct.pack("3i", os.getpid(), os.geteuid(), os.getegid())

        def hello(self):
            self.calls.append("hello")
            return {"operations": self.operations}

        def runtime_snapshot(self):
            self.calls.append("runtime.snapshot")
            return deepcopy(self.runtime)

        def recording_status(self):
            self.calls.append("recording.status")
            return self.recording

        def request(self, operation):
            assert operation == "display.profile"
            self.calls.append(operation)
            self.hook()
            return self.profile

    client = Client(None, timeout=0.2)
    monkeypatch.setattr(c, "CachedClient", lambda *args, **kwargs: client)
    return deployment, config, client, tmp_path / "recordings", tmp_path / "daemon.sock"


def collect(cached, **changes):
    deployment, _, _, recordings, socket = cached
    return c.collect_cached(
        deployment,
        recordings,
        socket,
        **({"firmware": "Version 1.26.01", "supplemental": False} | changes),
    )


def test_read_only_real_profile_and_cached_operations(cached, tmp_path):
    before = deployment_tests.contents(tmp_path)
    evidence = collect(cached)
    assert evidence.healthy and evidence.recording is False
    assert evidence.supplemental_advertised is False
    assert len(evidence.profile_sha256) == 64
    assert evidence.peer_pid == os.getpid() and int(evidence.peer_start_ticks) > 0
    assert cached[2].calls == [
        "hello",
        "runtime.snapshot",
        "recording.status",
        "display.profile",
        "runtime.snapshot",
    ]
    assert cached[2].closed
    assert deployment_tests.contents(tmp_path) == before
    report = str(asdict(evidence))
    assert deployment_tests.TARGET not in report and "PRIVATE_SENTINEL" not in report
    assert str(tmp_path) not in report


@pytest.mark.parametrize("fault", ["source", "binding", "permission", "no_acceptance"])
def test_unqualified_profile_refuses_before_ipc(cached, fault):
    _, config, client, _, _ = cached
    if fault == "source":
        config.source_path.write_bytes(deployment_tests.SOURCE + b"Other\tprivate\n")
    elif fault == "binding":
        manifest = cached[0].parent / "display.toml"
        manifest.write_text(
            manifest.read_text().replace(
                str(config.binding.source_id), "00000000-0000-0000-0000-000000000003"
            )
        )
    elif fault == "permission":
        config.source_path.chmod(0o644)
    else:
        (config.state_directory / "accepted-profile.json").unlink()
    with pytest.raises(c.UnconfirmedCache, match="^Cached App evidence is unconfirmed.$"):
        collect(cached)
    assert client.calls == []


@pytest.mark.parametrize(
    "key,value",
    [
        ("scanner_endpoint", "udp://192.0.2.99:50536"),
        ("scanner_model", "SDS100"),
        ("scanner_firmware", "other"),
        ("scanner_connected", 1),
        ("psi_active", None),
        ("state", "unexpected"),
    ],
)
def test_native_identity_and_types_must_match(cached, key, value):
    cached[2].runtime[key] = value
    with pytest.raises(c.UnconfirmedCache):
        collect(cached)
    assert cached[2].closed


@pytest.mark.parametrize(
    "key,value", [("state", "stopping"), ("scanner_connected", False), ("psi_active", False)]
)
def test_known_unhealthy_state_is_not_unknown_or_healthy(cached, key, value):
    cached[2].runtime[key] = value
    assert collect(cached).healthy is False


def test_recording_active_is_retained(cached):
    cached[2].recording["active"] = True
    assert collect(cached).recording is True


@pytest.mark.parametrize("value", [0, 1, None, "false"])
def test_recording_unknown_is_never_idle(cached, value):
    cached[2].recording["active"] = value
    with pytest.raises(c.UnconfirmedCache):
        collect(cached)


@pytest.mark.parametrize(
    "key",
    ["source_id", "source_kind", "revision", "endpoint_id", "failure", "configured", "accepted"],
)
def test_cached_profile_must_match_accepted_disk_state(cached, key):
    target = (
        cached[2].profile
        if key in ("endpoint_id", "failure", "configured", "accepted")
        else cached[2].profile["accepted"]
    )
    target[key] = "wrong"
    with pytest.raises(c.UnconfirmedCache):
        collect(cached)


def test_supplemental_capability_is_checked_without_requesting_it(cached):
    client = cached[2]
    client.operations += ["display.supplemental." + kind for kind in ("context", "frame", "demand")]
    with pytest.raises(c.UnconfirmedCache):
        collect(cached)
    assert collect(cached, supplemental=True).supplemental_advertised is True
    assert not any(op.startswith("display.supplemental.") for op in client.calls)


@pytest.mark.parametrize(
    "ops", [[], [None], ["display.supplemental.other"], ["display.supplemental.context"]]
)
def test_partial_or_unrecognized_capability_refused(cached, ops):
    cached[2].operations = ops
    with pytest.raises(c.UnconfirmedCache):
        collect(cached, supplemental=True)


def test_disk_change_during_ipc_read_is_refused(cached):
    def mutate():
        path = cached[0]
        path.write_bytes(path.read_bytes() + b"\n# changed\n")

    cached[2].hook = mutate
    with pytest.raises(c.UnconfirmedCache):
        collect(cached)


def test_runtime_becomes_disconnected_during_read(cached):
    cached[2].hook = lambda: cached[2].runtime.update(scanner_connected=False)
    assert collect(cached).healthy is False


def test_error_text_sanitized_and_ipc_closed(cached):
    def fail():
        raise OSError("PRIVATE_SENTINEL")

    cached[2].hook = fail
    with pytest.raises(c.UnconfirmedCache, match="^Cached App evidence is unconfirmed.$"):
        collect(cached)
    assert cached[2].closed


def test_slow_collection_refused(cached, monkeypatch):
    moments = iter([0, 2])
    monkeypatch.setattr(c.time, "monotonic", lambda: next(moments))
    with pytest.raises(c.UnconfirmedCache):
        collect(cached)


@pytest.mark.parametrize("kind", ["pid", "uid", "ticks", "exit"])
def test_actual_ipc_peer_must_stay_bound_and_live(cached, monkeypatch, kind):
    before = len(list(Path("/proc/self/fd").iterdir()))
    if kind in ("pid", "uid"):
        monkeypatch.setattr(
            cached[2],
            "getsockopt",
            lambda *_: struct.pack(
                "3i",
                1 if kind == "pid" else os.getpid(),
                os.geteuid() + (kind == "uid"),
                os.getegid(),
            ),
        )
    elif kind == "ticks":
        ticks = iter(["10", "11"])
        monkeypatch.setattr(c, "process_ticks", lambda _: next(ticks))
    else:

        class Poll:
            def register(self, *args):
                pass

            def poll(self, *_):
                return [(5, 1)]

        monkeypatch.setattr(c.select, "poll", Poll)
    with pytest.raises(c.UnconfirmedCache):
        collect(cached)
    assert len(list(Path("/proc/self/fd").iterdir())) == before
