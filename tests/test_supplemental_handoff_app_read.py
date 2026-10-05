"""Fixed private cached probe: exact exec, no shell, no retries or scanner demand."""

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

from . import test_supplemental_handoff_guard_state as guard_tests  # noqa: F401
from . import test_supplemental_handoff_host as host_tests
from . import test_supplemental_handoff_observer as observer_tests

NAME = "supplemental_handoff_app_read"
if NAME not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        NAME, Path(__file__).resolve().parents[1] / "scripts" / (NAME + ".py")
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[NAME] = module
    spec.loader.exec_module(module)
a, o, h, p = sys.modules[NAME], observer_tests.o, host_tests.h, host_tests.p
CASE = "a" * 12 + "4" + "a" * 3 + "8" + "a" * 15
SOURCE = "c" * 40
PATHS = a.ProbePaths("/data/scanner-display-deployment.toml", "/media/accepted/recordings")


class Docker(host_tests.FakeDocker):
    def __init__(self):
        super().__init__()
        self.cli["Name"] = "/app_" + p.NORMAL
        self.calls = []
        self.evidence = {
            "schema": 1,
            "profile": "a" * 64,
            "healthy": True,
            "recording": False,
            "supplemental": False,
        }
        self.reply_lost = False

    def container(self, name):
        assert name == "app_" + p.NORMAL
        return self.cli.copy()

    def _request(self, method, path, body, *, expected):
        assert method == "POST" and path == f"/containers/{host_tests.CID}/exec" and expected == 201
        assert (
            body["AttachStdin"] is False
            and body["AttachStdout"] is True
            and body["AttachStderr"] is True
        )
        assert body["Privileged"] is False and body["Tty"] is False and body["User"] == "0"
        command = body["Cmd"]
        assert command[:4] == ("/usr/local/bin/python", "-I", "-B", "-c")
        ast.parse(command[4])
        self.calls.append("create")
        self.exec = host_tests.execution(command)
        self.exec["ExitCode"] = None
        return p.encode({"Id": host_tests.EID})

    def start_execution(self, eid, *, attach):
        assert eid == host_tests.EID and attach is True
        self.calls.append("start")
        self.exec["ExitCode"] = 0
        if self.reply_lost:
            raise TimeoutError("private reply")
        return host_tests.frame(p.encode(self.evidence))


@pytest.fixture
def reader():
    docker = Docker()
    seals = tuple(
        o.AppSeal(slug, "0.30.0", host_tests.IMAGE, "b" * 64, o.ProtectedFiles(*("a" * 64,) * 4))
        for slug in (p.NORMAL, p.CANDIDATE)
    )
    reader = a.AppReads(
        docker,
        seals=seals,
        case=CASE,
        source=SOURCE,
        firmware="Version 1.26.01",
        paths={slug: PATHS for slug in (p.NORMAL, p.CANDIDATE)},
    )
    generation = h.generation(docker.cli, name="app_" + p.NORMAL, image=host_tests.IMAGE)
    return reader, docker, generation


def test_probe_uses_fixed_reviewed_program_and_exact_incarnation(reader):
    read, docker, generation = reader
    state = read.read(p.NORMAL, generation)
    assert state == o.NativeState(generation, True, False)
    assert docker.calls == ["create", "start"]


@pytest.mark.parametrize(
    "key,value",
    [
        ("profile", "b" * 64),
        ("supplemental", True),
        ("recording", 0),
        ("schema", True),
        ("healthy", "yes"),
        ("extra", "private"),
    ],
)
def test_bad_probe_report_never_becomes_health(reader, key, value):
    read, docker, generation = reader
    docker.evidence[key] = value
    with pytest.raises(p.UnsafeHandoff):
        read.read(p.NORMAL, generation)
    assert docker.calls == ["create", "start"]


@pytest.mark.parametrize("healthy,recording", [(None, False), (False, False), (True, True)])
def test_uncertain_guard_and_recording_status_are_retained(reader, healthy, recording):
    read, docker, generation = reader
    docker.evidence.update(healthy=healthy, recording=recording)
    assert read.read(p.NORMAL, generation) == o.NativeState(generation, healthy, recording)


def test_lost_read_reply_is_not_replayed(reader):
    read, docker, generation = reader
    docker.reply_lost = True
    with pytest.raises(TimeoutError):
        read.read(p.NORMAL, generation)
    assert docker.calls == ["create", "start"]


def test_changed_app_before_read_creates_no_execution(reader):
    read, docker, generation = reader
    docker.cli["State"]["Pid"] += 1
    with pytest.raises(p.UnsafeHandoff):
        read.read(p.NORMAL, generation)
    assert docker.calls == []


def test_changed_app_after_creation_is_not_started(reader, monkeypatch):
    read, docker, generation = reader
    original = docker._request

    def create(*args, **kwargs):
        raw = original(*args, **kwargs)
        docker.cli["State"]["Pid"] += 1
        return raw

    monkeypatch.setattr(docker, "_request", create)
    with pytest.raises(p.UnsafeHandoff):
        read.read(p.NORMAL, generation)
    assert docker.calls == ["create"]


@pytest.mark.parametrize(
    "key,value",
    [("candidate", 1), ("case", "../other"), ("source", "main"), ("firmware", "'; dangerous()")],
)
def test_external_input_cannot_select_code_or_command(key, value):
    inputs = {
        "candidate": False,
        "case": CASE,
        "source": SOURCE,
        "firmware": "Version 1.26.01",
        "paths": PATHS,
    } | {key: value}
    with pytest.raises(p.UnsafeHandoff):
        a.probe_command(**inputs)


@pytest.mark.parametrize(
    "field,value",
    [
        ("deployment", "/etc/config"),
        ("deployment", "/data/../etc/config"),
        ("deployment", "/data//config"),
        ("deployment", "/data/config\n"),
        ("recordings", "/data/recordings"),
        ("recordings", "/media"),
        ("recordings", None),
        ("deployment", "/data/config\0"),
    ],
)
def test_probe_paths_must_match_scoped_sealed_layout(field, value):
    inputs = dict(deployment=PATHS.deployment, recordings=PATHS.recordings)
    inputs[field] = value
    with pytest.raises(p.UnsafeHandoff):
        a.ProbePaths(**inputs)


def test_fixed_probe_avoids_app_startup_import_and_never_negotiates_demand():
    command = a.probe_command(
        candidate=False, case=CASE, source=SOURCE, firmware="Version 1.26.01", paths=PATHS
    )
    tree = ast.parse(command[-1])
    assert not any(
        isinstance(node, ast.ImportFrom) and "home_assistant" in (node.module or "")
        for node in ast.walk(tree)
    )
    assert "Path('/run/sdsctl/daemon.sock')" in command[-1]
