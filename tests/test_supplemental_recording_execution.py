"""Pure fixed-command/inspection policy; no host, Docker or process operation."""

import copy
import importlib.util
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from . import test_supplemental_recording_static as source

NAME = "supplemental_recording_execution"
SPEC = importlib.util.spec_from_file_location(
    NAME, Path(__file__).parents[1] / "scripts" / (NAME + ".py")
)
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)
EXEC, CONTAINER = "e" * 64, "c" * 64
COMMAND = m.Command("/data/finite-case/launch.json", "a" * 64, "b" * 64, 100.25)


def metadata():
    argv = COMMAND.argv()
    return {
        "ID": EXEC,
        "ContainerID": CONTAINER,
        "ProcessConfig": {
            "entrypoint": argv[0],
            "arguments": list(argv[1:]),
            "privileged": False,
            "tty": False,
            "user": "0",
        },
        "OpenStdin": True,
        "OpenStdout": True,
        "OpenStderr": True,
        "Running": False,
        "Pid": 0,
        "ExitCode": None,
        "CanRemove": False,
        "DetachKeys": "",
    }


def inspect(value):
    return m.inspect(value, execution_id=EXEC, container_id=CONTAINER, command=COMMAND)


def denied(value):
    with pytest.raises(m.UnconfirmedExecution) as caught:
        inspect(value)
    assert str(caught.value) == m.MESSAGE and "PRIVATE" not in str(caught.value)


def test_fixed_description_has_no_general_command_or_environment():
    argv = COMMAND.argv()
    assert argv == (
        "/usr/local/bin/python",
        "-I",
        "-B",
        "/opt/sdsctl-supplemental-recording/accept_supplemental_recording_operator.py",
        "--plan",
        "/data/finite-case/launch.json",
        "--plan-sha256",
        "a" * 64,
        "--source-sha256",
        "b" * 64,
        "--runtime-root",
        "/usr/local/lib/python3.14/site-packages/sds200",
        "--ready-by",
        "100.25",
    )
    body = COMMAND.create_body()
    assert body == {
        "AttachStdin": True,
        "AttachStdout": True,
        "AttachStderr": True,
        "Tty": False,
        "Privileged": False,
        "User": "0",
        "WorkingDir": "/",
        "Env": ["PATH=/usr/local/bin:/usr/bin:/bin"],
        "Cmd": list(argv),
    }
    body["Cmd"].append("--PRIVATE")
    assert COMMAND.create_body()["Cmd"] == list(argv)  # No mutable retained input.
    assert set(COMMAND.__dataclass_fields__) == {"plan", "plan_sha256", "source_sha256", "ready_by"}


def test_fixed_paths_match_the_recording_source_collector():
    assert Path(m.RUNTIME) == Path("/") / source.m.legacy.PACKAGE
    assert Path(m.ENTRY).parent == Path("/") / source.m.NATIVE


@pytest.mark.parametrize(
    "path",
    [
        "PRIVATE",
        "/data/launch.json",
        "/data/case/not-launch.json",
        "/datax/case/launch.json",
        "//data/case/launch.json",
        "/data//case/launch.json",
        "/data/./case/launch.json",
        "/data/case/../case/launch.json",
        "/data/case/launch.json/",
        "/data/\n/launch.json",
        "/data/\0/launch.json",
        "/data/é/launch.json",
        "/data/" + "x" * 1024 + "/launch.json",
        None,
        Path("/data/case/launch.json"),
        True,
    ],
)
def test_invalid_plan_path_never_describes_an_exec(path):
    with pytest.raises(m.UnconfirmedExecution):
        replace(COMMAND, plan=path).create_body()


@pytest.mark.parametrize("field", ["plan_sha256", "source_sha256"])
@pytest.mark.parametrize("value", ["PRIVATE", "a" * 63, "A" * 64, "g" * 64, None, True])
def test_malformed_pins_are_refused(field, value):
    with pytest.raises(m.UnconfirmedExecution):
        replace(COMMAND, **{field: value}).argv()


@pytest.mark.parametrize("deadline", [True, None, "100", float("nan"), float("inf"), 0, -1])
def test_original_deadline_must_be_positive_finite_number(deadline):
    with pytest.raises(m.UnconfirmedExecution):
        replace(COMMAND, ready_by=deadline).argv()


@pytest.mark.parametrize(
    "running,pid,code,phase",
    [
        (False, 0, None, "created"),
        (True, 0, None, "starting"),
        (True, 1234, None, "running"),
        (False, 1234, 0, "not_running"),
        (False, 0, 126, "not_running"),
        (False, 1234, 137, "not_running"),
    ],
)
def test_created_starting_running_and_stopped_are_distinct(running, pid, code, phase):
    value = metadata() | {"Running": running, "Pid": pid, "ExitCode": code}
    before = copy.deepcopy(value)
    assert inspect(value) == m.State(phase, pid, code) and value == before
    assert not hasattr(inspect(value), "native_exited") and not hasattr(
        inspect(value), "authorized"
    )


@pytest.mark.parametrize(
    "key,value",
    [
        ("ID", "PRIVATE"),
        ("ContainerID", "d" * 64),
        ("OpenStdin", False),
        ("OpenStdout", False),
        ("OpenStderr", False),
        ("OpenStdin", 1),
        ("OpenStdout", "true"),
        ("OpenStderr", 1),
        ("Running", 0),
        ("CanRemove", 0),
        ("CanRemove", True),
        ("DetachKeys", "cA=="),
        ("DetachKeys", []),
        ("DetachKeys", False),
        ("Pid", True),
        ("Pid", -1),
        ("Pid", 1),
        ("Pid", 2**31),
        ("Pid", "1234"),
        ("Pid", 1234),
        ("ExitCode", True),
        ("ExitCode", -1),
        ("ExitCode", 256),
        ("ExitCode", "0"),
    ],
)
def test_changed_or_ambiguous_inspection_is_refused(key, value):
    denied(metadata() | {key: value})


@pytest.mark.parametrize(
    "key,value",
    [
        ("entrypoint", "/bin/sh"),
        ("arguments", ["-c", "PRIVATE"]),
        ("arguments", tuple(COMMAND.argv()[1:])),
        ("privileged", True),
        ("privileged", 0),
        ("tty", True),
        ("tty", 0),
        ("user", "root"),
        ("user", 0),
    ],
)
def test_process_config_must_match_the_exact_fixed_command(key, value):
    current = metadata()
    current["ProcessConfig"][key] = value
    denied(current)


@pytest.mark.parametrize("key", list(metadata()))
def test_all_engine_fields_are_required(key):
    value = metadata()
    del value[key]
    denied(value)


@pytest.mark.parametrize("nested", [False, True])
def test_unknown_fields_are_not_ignored(nested):
    value = metadata()
    (value["ProcessConfig"] if nested else value)["PRIVATE"] = True
    denied(value)


@pytest.mark.parametrize("field", ["execution_id", "container_id"])
def test_expected_ids_are_not_arbitrary_inputs(field):
    kwargs = dict(execution_id=EXEC, container_id=CONTAINER, command=COMMAND)
    kwargs[field] = "PRIVATE"
    with pytest.raises(m.UnconfirmedExecution):
        m.inspect(metadata(), **kwargs)


def test_running_cannot_have_an_exit_or_gc_mark():
    for changes in ({"ExitCode": 0}, {"CanRemove": True}, {"Pid": 1}):
        denied(metadata() | {"Running": True, "Pid": 1234} | changes)


def test_gc_mark_on_completed_exec_is_not_success_or_replay_authority():
    value = metadata() | {"Pid": 1234, "ExitCode": 70, "CanRemove": True, "DetachKeys": None}
    assert inspect(value) == m.State("not_running", 1234, 70)
    assert COMMAND.argv()[-1] == "100.25"  # Never replace a past deadline.


@pytest.mark.parametrize("value", [None, [], False, "PRIVATE"])
def test_inspect_and_command_types_are_not_coerced(value):
    denied(value)
    with pytest.raises(m.UnconfirmedExecution):
        m.inspect(metadata(), execution_id=EXEC, container_id=CONTAINER, command=value)
