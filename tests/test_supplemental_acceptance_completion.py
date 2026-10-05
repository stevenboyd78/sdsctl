"""Real disposable processes qualify the acceptance-only App completion witness.

No sockets, scanner, HA API or credentials: reports come from a synthetic child
under the real shipped guardian, and the staged outer supervisor is exercised.
"""

import json
import os
import subprocess
import sys
import time
import types
from dataclasses import fields
from threading import Timer

import pytest

from .test_home_assistant_app_supervisor import FakeProcess, launch_plan
from .test_stage_supplemental_acceptance import normal_snapshot, snapshot, stager  # noqa: F401
from .test_supplemental_acceptance_guard import SCRIPTS, guard

SOURCE = "a" * 40
CASE = "123456789abc4def8abc123456789abc"
CHILD = """import os,sys,time
from pathlib import Path
from accept_supplemental_daemon import publish,start_ticks,CASE_KIND
root=Path(sys.argv[1]);case=root/'daemon-case';case.mkdir(mode=0o700)
while not (root/'publish-ready').exists():time.sleep(.01)
identity=dict(kind=CASE_KIND,pid=os.getpid(),start_ticks=start_ticks(os.getpid()),generation='b'*32)
publish(case,'ready.json',identity|dict(state='waiting_for_operator',source_revision='a'*40,ready_timeout_seconds=600,window_seconds=64,max_read_attempts=60,reads_require_explicit_consumer_demand=True))
while not (root/'release').exists():time.sleep(.01)
outcome=(root/'release').read_text()
armed=outcome!='operator_wait_expired'
if armed:
    publish(case,'arm.json',identity)
    publish(case,'armed.json',dict(state='armed_waiting_for_demand',generation=identity['generation']))
publish(case,'result.json',dict(
    kind=CASE_KIND,generation=identity['generation'],state='ended',outcome=outcome,
    ever_armed=armed,read_attempts=60 if outcome=='quota_exhausted' else 4 if armed else 0,
    restoration_verified=False))
"""


def wait_for(predicate):
    end = time.monotonic() + 6
    while not predicate():
        assert time.monotonic() < end, "Disposable process did not reach fixture readiness"
        time.sleep(0.01)


@pytest.fixture
def trial(tmp_path):
    directory = tmp_path / "guard"
    directory.mkdir(mode=0o700)
    code = (
        "import signal,sys,threading;from pathlib import Path;"
        "from guard_supplemental_acceptance import supervise;"
        "cancel=threading.Event();"
        "signal.signal(signal.SIGTERM,lambda *_args:cancel.set());"
        f"raise SystemExit(supervise([sys.executable,'-c',{CHILD!r},sys.argv[1]],"
        "Path(sys.argv[1]),deadline_seconds=684,cancel=cancel))"
    )
    environment = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join((str(SCRIPTS), str(SCRIPTS.parent / "src"))),
    }
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(directory)],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        wait_for(lambda: (directory / "guard-started.json").exists())
        yield directory, process
    finally:
        # Let the owned guardian clean up its child; never target a saved PID.
        if process.poll() is None:
            (directory / "publish-ready").touch()
            (directory / "release").write_text("operator_wait_expired")
            process.wait(timeout=6)
        process.stdout.close()
        process.stderr.close()


def bind(trial):
    directory, process = trial
    witness = guard.AppCompletion(directory, process.pid, SOURCE)
    (directory / "publish-ready").touch()
    wait_for(lambda: (directory / "daemon-case/ready.json").exists())
    witness.observe()
    return witness


def finish(trial, outcome="window_expired"):
    directory, process = trial
    (directory / "release").write_text(outcome)
    assert process.wait(timeout=6) == 0


@pytest.mark.parametrize("outcome", ["window_expired", "quota_exhausted", "operator_wait_expired"])
def test_real_owned_guardian_and_child_exit_are_required(trial, outcome):
    with bind(trial) as witness:
        finish(trial, outcome)
        assert witness.finished(0)
        with pytest.raises(ValueError):
            witness.finished(0)
    assert witness.handles == []


def test_missing_readiness_does_not_block_app_checks_or_claim_completion(trial):
    directory, process = trial
    with guard.AppCompletion(directory, process.pid, SOURCE) as witness:
        witness.observe()
        assert witness.ready is None
        with pytest.raises(ValueError):
            witness.finished(0)


def test_alive_owned_process_cannot_complete_from_exit_code_alone(trial):
    with bind(trial) as witness, pytest.raises(ValueError):
        witness.finished(0)


def test_ready_from_wrong_source_or_parent_refused_and_fds_closed(trial):
    directory, process = trial
    with guard.AppCompletion(directory, process.pid, "c" * 40) as witness:
        (directory / "publish-ready").touch()
        wait_for(lambda: (directory / "daemon-case/ready.json").exists())
        with pytest.raises(ValueError):
            witness.observe()
        assert witness.handles == []
    with pytest.raises(ValueError):
        guard.AppCompletion(directory, os.getpid(), SOURCE)


@pytest.mark.parametrize(
    "report,changes",
    [
        ("guard-result.json", {"forced_kill": True}),
        ("guard-result.json", {"child_returncode": 2}),
        ("guard-result.json", {"child_returncode": False}),
        ("guard-result.json", {"forced_kill": 0}),
        ("guard-result.json", {"child_exit_confirmed": False}),
        ("guard-result.json", {"outcome": "deadline_expired"}),
        ("daemon-case/result.json", {"outcome": "launcher_failed"}),
        ("daemon-case/result.json", {"outcome": "cancelled"}),
        ("daemon-case/result.json", {"read_attempts": 61}),
        ("daemon-case/result.json", {"read_attempts": True}),
        ("daemon-case/result.json", {"generation": "c" * 32}),
        ("daemon-case/result.json", {"ever_armed": False}),
        ("daemon-case/result.json", {"restoration_verified": True}),
        ("daemon-case/arm.json", {"pid": 123456789}),
        ("daemon-case/armed.json", {"generation": "c" * 32}),
        ("daemon-case/ready.json", {"source_revision": "c" * 40}),
    ],
)
def test_inconsistent_completion_is_failure_not_success(trial, report, changes):
    with bind(trial) as witness:
        finish(trial)
        path = trial[0] / report
        path.write_text(json.dumps(json.loads(path.read_text()) | changes))
        with pytest.raises(ValueError):
            witness.finished(0)


@pytest.mark.parametrize(
    "damage",
    ["missing", "symlink", "hardlink", "public", "duplicate", "oversized", "directory_swap"],
)
def test_missing_or_untrusted_report_is_never_a_success(trial, damage):
    with bind(trial) as witness:
        finish(trial)
        path = trial[0] / "daemon-case/result.json"
        if damage in ("missing", "symlink", "hardlink"):
            saved = path.with_name("original-result.json")
            path.rename(saved)
            if damage == "symlink":
                path.symlink_to(saved)
            elif damage == "hardlink":
                os.link(saved, path)
        elif damage == "public":
            path.chmod(0o644)
        elif damage == "duplicate":
            path.write_text('{"state":"ended","state":"ended"}')
        elif damage == "oversized":
            path.write_bytes(b" " * 4097)
        else:
            path.parent.rename(path.parent.with_name("previous-case"))
            path.parent.mkdir(mode=0o700)
        with pytest.raises((ValueError, OSError)):
            witness.finished(0)


def test_operator_wait_expiry_with_arm_evidence_is_inconsistent(trial):
    with bind(trial) as witness:
        finish(trial, "operator_wait_expired")
        guard.publish(trial[0] / "daemon-case", "arm.json", witness.identity)
        with pytest.raises(ValueError):
            witness.finished(0)


@pytest.mark.parametrize("sibling_failed", [False, True])
def test_actual_staged_outer_supervisor_handles_only_verified_completion(
    trial,
    snapshot,  # noqa: F811
    monkeypatch,
    tmp_path,
    sibling_failed,  # noqa: F811
):
    directory, process = trial
    source = stager.render(snapshot, SOURCE, case_id=CASE, firmware="Version 1.26.01")[
        stager.SUPERVISOR
    ]
    source = source.replace(
        f"/data/sdsctl-supplemental-acceptance-{CASE}".encode(), str(directory).encode()
    )
    module = types.ModuleType("sds200._finite_supervisor_fixture")
    module.__package__ = "sds200"
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setitem(sys.modules, "guard_supplemental_acceptance", guard)
    monkeypatch.setattr(sys, "path", list(sys.path))
    exec(compile(source, stager.SUPERVISOR, "exec"), module.__dict__)
    normal_plan = launch_plan(tmp_path)
    plan = module.HomeAssistantAppLaunchPlan(
        **{field.name: getattr(normal_plan, field.name) for field in fields(normal_plan)}
    )
    web = FakeProcess("web", [])
    children = iter((process, web))
    (directory / "publish-ready").touch()
    wait_for(lambda: (directory / "daemon-case/ready.json").exists())
    timer = Timer(0.3, lambda: (directory / "release").write_text("window_expired"))
    timer.start()
    if sibling_failed:
        web.returncode = 4
    supervisor = module.HomeAssistantAppSupervisor(
        plan,
        process_factory=lambda *_args: next(children),
        daemon_ready_probe=lambda *_args: True,
    )
    try:
        if sibling_failed:
            with pytest.raises(Exception, match="web process exited unexpectedly"):
                supervisor.run()
        else:
            assert supervisor.run() == 0
            assert web.terminate_calls == 1
    finally:
        timer.cancel()
        timer.join()
    assert json.loads((directory / "guard-result.json").read_text())["child_exit_confirmed"]
