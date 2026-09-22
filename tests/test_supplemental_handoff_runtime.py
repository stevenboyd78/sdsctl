"""Command construction only: these tests never open SSH or launch containers."""

import importlib.util
import shlex
import sys
from pathlib import Path
from uuid import uuid4

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/supplemental_handoff_runtime.py"
SPEC = importlib.util.spec_from_file_location("supplemental_handoff_runtime", SCRIPT)
runtime = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runtime
SPEC.loader.exec_module(runtime)
IMAGE = "sha256:" + "a" * 64
CASE = "f1193f32983a4ce692b585fcdd058841"


@pytest.mark.parametrize("mode,deadline,sleep", [("complete", "30s", "12"), ("expire", "8s", "60")])
def test_fixture_is_bounded_isolated_and_cleans_up_exact_container(mode, deadline, sleep):
    result = runtime.fixture(IMAGE, CASE, mode)
    args = result.argv
    assert result.container == "sdsctl-runtime-" + CASE
    assert result.unit == result.container + ".service"
    assert shlex.split(result.remote_command) == list(args)
    assert len(result.remote_command.encode()) < 2000 < runtime.MAX_REMOTE_COMMAND_BYTES
    for option in (
        "--expand-environment=no",
        "--service-type=exec",
        "--property=Restart=no",
        "--property=RuntimeMaxSec=" + deadline,
        "--property=TimeoutStopSec=5s",
        "--rm",
        "--pull=never",
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--user=65534:65534",
        "--pids-limit=32",
        "--memory=64m",
        "--cpus=0.25",
        "--entrypoint=/usr/local/bin/python",
    ):
        assert option in args
    assert "--property=ExecStopPost=-/usr/bin/docker stop --time=2 " + result.container in args
    assert args[args.index("--name") + 1] == result.container
    assert args[-5:-1] == (IMAGE, "-I", "-B", "-c")
    assert "time.sleep(" + sleep + ")" in args[-1]
    assert ("SIG_IGN" in args[-1]) == (mode == "expire")
    for forbidden in ("--mount", "--volume", "-v", "--privileged", "--pid=host", "--restart"):
        assert not any(arg == forbidden or arg.startswith(forbidden + "=") for arg in args)
    assert "local_sds200" not in result.remote_command
    assert "/var/run/docker.sock" not in result.remote_command
    compile(args[-1], "fixture", "exec")


@pytest.mark.parametrize(
    "image",
    [
        None,
        "latest",
        "sds200:latest",
        "a" * 64,
        "sha256:" + "A" * 64,
        IMAGE + ";true",
        IMAGE + "\n",
    ],
)
def test_invalid_image_refused(image):
    with pytest.raises(ValueError):
        runtime.fixture(image, CASE, "complete")


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "invalid",
        "a" * 32,
        CASE.upper(),
        "f1193f32-983a-4ce6-92b5-85fcd6058841c",
        CASE + ";true",
    ],
)
def test_invalid_fixture_id_refused(value):
    with pytest.raises(ValueError):
        runtime.fixture(IMAGE, value, "complete")


@pytest.mark.parametrize("mode", [None, "restart", "stop", "complete;true", "expire\n"])
def test_invalid_mode_refused(mode):
    with pytest.raises(ValueError):
        runtime.fixture(IMAGE, CASE, mode)


def test_fresh_ids_do_not_share_unit_or_cleanup_target():
    a, b = (runtime.fixture(IMAGE, uuid4().hex, "complete") for _ in range(2))
    assert a.unit != b.unit and a.container != b.container
    assert a.container not in b.remote_command


def test_command_limit_uses_encoded_bytes():
    result = runtime.RuntimeFixture("fixture", "fixture", ("echo", "é" * 5000))
    with pytest.raises(ValueError, match="transport bound"):
        _ = result.remote_command


@pytest.mark.parametrize(
    "log,expected",
    [
        ("", (False, False)),
        ("SDSCTL_RUNTIME_STARTED\n", (True, False)),
        ("SDSCTL_RUNTIME_STARTED\nSDSCTL_RUNTIME_COMPLETED\n", (True, True)),
        (
            "Started docker: print('SDSCTL_RUNTIME_STARTED'); print('SDSCTL_RUNTIME_COMPLETED')",
            (False, False),
        ),
        ("systemd: SDSCTL_RUNTIME_COMPLETED\nSDSCTL_RUNTIME_STARTED\n", (True, False)),
    ],
)
def test_markers_must_be_standalone_worker_output(log, expected):
    assert runtime.log_markers(log) == expected


@pytest.mark.parametrize(
    "log",
    [
        None,
        "x" * 65537,
        "é" * 32769,
        "SDSCTL_RUNTIME_COMPLETED\n",
        "SDSCTL_RUNTIME_COMPLETED\nSDSCTL_RUNTIME_STARTED\n",
        "SDSCTL_RUNTIME_STARTED\n" * 2,
        "SDSCTL_RUNTIME_STARTED\n" + "SDSCTL_RUNTIME_COMPLETED\n" * 2,
    ],
)
def test_unbounded_or_repeated_fixture_log_refused(log):
    with pytest.raises(ValueError):
        runtime.log_markers(log)
