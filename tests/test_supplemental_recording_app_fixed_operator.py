"""Original App publication -> exact fixed operator argv -> actual native Ready.

Only disposable test paths are mounted writable. Scanner/RTSP peers are owned
loopback fixtures; no physical scanner, Docker, App or network endpoint is used.
The source/plan/profile/baseline bytes and fixed paths stay original. Platform,
Startup and interpreter-image provenance are synthetic; the native Ready frame
alone is NOT authenticated AppLaunch/Engine readiness or recording permission.
"""

import json
import os
import select
import signal
import subprocess
import time
from dataclasses import replace

import pytest

from . import test_supplemental_recording_app_native_preflight as preflight
from . import test_supplemental_recording_relay_native as native_io

m = preflight.m
bwrap, staged = preflight.bwrap, preflight.staged
layout, image_umask, supervised = preflight.layout, preflight.image_umask, preflight.supervised
image, configured = preflight.image, preflight.configured
candidate, app, native = preflight.candidate, preflight.app, preflight.native
pytestmark = preflight.pytestmark


@pytest.fixture
def mapped(tmp_path, bwrap, monkeypatch):
    state = preflight.mapped.__wrapped__(tmp_path, bwrap)
    with native_io.peers(monkeypatch) as (scanner, rtsp):
        state.scanner, state.rtsp = scanner, rtsp
        state.scanner_target = f"udp://127.0.0.1:{scanner.socket.getsockname()[1]}"
        state.endpoint = f"rtsp://127.0.0.1:{rtsp.port}/au:scanner.au"
        scanner.thread.start()
        rtsp.thread.start()
        yield state
        assert not scanner.errors


@pytest.fixture
def launch_case(native, monkeypatch, mapped):
    for state in preflight.launch_case.__wrapped__(native, monkeypatch, mapped):
        # Local endpoint/small read policy prepared BEFORE the sole publication.
        # ready_timeout does not renew the original 120s host-plan cutoff.
        state.spec = replace(
            state.spec,
            host="127.0.0.1",
            control_port=mapped.scanner.socket.getsockname()[1],
            rtsp_port=mapped.rtsp.port,
            rtp_bind_address="127.0.0.1",
            rtp_bind_port=0,
            read_window_seconds=1,
            max_read_attempts=2,
            ready_timeout=120,
        )
        yield state


def command_for(s, receipt):
    command = m.qualification.launch.execution.Command(
        str(s.plan.native_root / "launch/launch.json"),
        receipt.sha256,
        s.plan.candidate_runtime.source,
        s.plan.lease["ready_by"],
    )
    return command


def start_operator(s, mapped, staged, command, *, recording=False):
    argv = preflight.namespace_command(
        s,
        mapped,
        staged,
        writable=("launch/guardian", "sockets", "receipts"),
        loopback_peers=True,
        recording=recording,
    ) + list(command.argv())
    return subprocess.Popen(
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
        env={
            "PATH": "/usr/bin:/bin",
            **({"SDSCTL_TEST_FAILURE_LOCATIONS": "1"} if recording else {}),
        },
        start_new_session=True,
    )


def test_original_app_plan_reaches_fixed_native_ready_then_withdraws(launch_case, mapped, staged):
    s = launch_case
    qualified = s.publish()
    receipt = qualified.launch_inputs
    original = s.plan.raw, s.plan.lease, receipt.raw, s.native_baseline
    command = command_for(s, receipt)
    process = start_operator(s, mapped, staged, command)
    stream, descriptors = None, []
    try:
        wire = native_io.operator.w
        stream = wire.Stream(process.stdout.fileno(), process.stdin.fileno(), role="host")
        ready = stream.receive(deadline=min(s.plan.lease["ready_by"], time.monotonic() + 12))
        assert ready["kind"] == "finite-recording-operator" and ready["phase"] == "ready"
        expected = dict(
            launch=receipt.sha256,
            source=staged.pin,
            projection=s.plan.projection_sha256,
            host_plan=s.plan.sha256,
            profile=mapped.profile,
            manifest=s.plan.native_baseline_sha256,
            contract=s.projected.native.contract.sha256,
            generation=s.original.generation,
            ready_by=s.plan.lease["ready_by"],
        )
        assert ready["context"] == expected
        actual = json.loads(ready["body"]["received"]["raw"])
        assert actual["phase"] == "ready" and actual["context"] == expected
        assert ready["watchdog"]["deadline"] == (
            command.ready_by + s.projected.native.contract.maximum_recording_seconds
        )
        for role in ("guardian", "native", "watchdog"):
            actor = ready[role]
            assert (actor["uid"], actor["gid"]) == (os.geteuid(), os.getegid())
            fd = os.pidfd_open(actor["pid"])
            descriptors.append(fd)
            assert native_io.child.c.returns._identity(actor["pid"])[1] == actor["start_ticks"]
            assert not select.select([fd], [], [], 0)[0]
        claim_path = s.case_root / "launch/guardian/launch-claimed.json"
        claim_raw = claim_path.read_bytes()
        claim = json.loads(claim_raw)
        assert claim["context"] == expected and claim["source"]["sha256"] == staged.pin
        assert claim["guardian_pid"] == ready["guardian"]["pid"]
        assert claim["guardian_start_ticks"] == ready["guardian"]["start_ticks"]
        # Withdrawal before begin is intentional: never fabricate host intent.
        stream.close()  # Close the framing object's duplicate writer before EOF.
        process.stdin.close()
        process.stdin = None
        out, err = process.communicate(timeout=6)
        assert process.returncode == 70 and out == b"" and err == native_io.operator.MESSAGE
        assert all(select.select([fd], [], [], 0)[0] for fd in descriptors)
        assert claim_path.read_bytes() == claim_raw
        assert not tuple((s.case_root / "receipts").iterdir())
        assert not mapped.scanner.reads  # No supplemental acquisition before begin.
        assert (mapped.recordings / "previous.wav").read_bytes() == b"old evidence unchanged"
        assert original == (s.plan.raw, s.plan.lease, receipt.raw, s.native_baseline)
        preflight.launches.denied(qualified)  # A frame is not original authenticated Ready.
    finally:
        if stream is not None:
            stream.close()
        if process.stdin is not None:
            process.stdin.close()
            process.stdin = None
        try:
            process.communicate(timeout=6)
        except subprocess.TimeoutExpired:
            # Only this exact newly owned test session, never any installed process.
            assert os.getpgid(process.pid) == process.pid
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate(timeout=3)
        for fd in descriptors:
            os.close(fd)
