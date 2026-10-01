"""OS-exit evidence is separate from recording receipts and content evidence.

Each signal targets only the subprocess created by this test. Synthetic clocks,
PCM and owned temporary directories; no Home Assistant, scanner or host adapter.
"""

import json
import os
import select
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from .test_supplemental_recording_evidence import wav_bytes
from .test_supplemental_recording_owner import native as native
from .test_supplemental_recording_owner import o, r

pytestmark = pytest.mark.skipif(not hasattr(os, "pidfd_open"), reason="Linux pidfd qualification")


def receive(child):
    deadline = time.monotonic() + 10
    data = bytearray()
    while not data.endswith(b"\n"):
        remaining = deadline - time.monotonic()
        assert remaining > 0 and select.select([child.stdout], [], [], remaining)[0]
        chunk = os.read(child.stdout.fileno(), 1)
        if not chunk:
            # Test-owned, synthetic children only. Preserve bounded diagnostic
            # stderr instead of discarding the cause when stdout closes early.
            try:
                code = child.wait(timeout=1)
            except subprocess.TimeoutExpired:
                code = None
            os.set_blocking(child.stderr.fileno(), False)
            try:
                detail = os.read(child.stderr.fileno(), 8192).decode("utf-8", errors="replace")
            except BlockingIOError:
                detail = "No stderr available"
            raise AssertionError(f"Synthetic child stdout closed (exit={code}): {detail}")
        assert chunk and len(data) < 8192
        data.extend(chunk)
    return json.loads(data)


@pytest.mark.parametrize(
    "scenario",
    ("complete", "blocked_stop", "blocked_metadata", "lost_ack", "lost_receipt_ack", "active_exit"),
)
def test_process_exit_cannot_substitute_for_acknowledged_finalization(native, scenario):
    repo = Path(o.__file__).parent.parent
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        (str(repo / "scripts"), str(repo), str(repo / "src"))
    )
    child = subprocess.Popen(
        [
            sys.executable,
            "-u",
            str(repo / "tests/fixtures/finite_recording_child.py"),
            str(native.root),
            str(native.journal),
            scenario,
        ],
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    witness = -1
    try:
        first = receive(child)
        assert first["stage"] == "started"
        expected = r.RecordingExpectation(**first["expected"])
        witness = os.pidfd_open(child.pid)
        assert not select.select([witness], [], [], 0)[0] and child.poll() is None
        child.stdin.write(b"stop\n")
        child.stdin.flush()
        stopped = None
        if scenario == "complete":
            message = receive(child)
            assert message["stage"] == "stopped"
            stopped = message["snapshot"]
            assert child.wait(timeout=5) == 0
        elif scenario == "active_exit":
            assert child.wait(timeout=5) == 23
        else:
            assert receive(child) == {"stage": "blocked", "scenario": scenario}
            # A native stop stuck past this fixture's independent short deadline
            # cannot produce an acknowledgment or force the supervisor to wait forever.
            assert not select.select([witness], [], [], 0.05)[0] and child.poll() is None
            signal.pidfd_send_signal(witness, signal.SIGTERM)
            assert child.wait(timeout=5) == -signal.SIGTERM
        assert select.select([witness], [], [], 0)[0]
        assert child.stderr.read() == b""
        name = r.filename(expected.case, expected.started_at)
        wav = native.root / name
        # Baseline old bytes have not been promoted or replaced on any failure.
        assert wav.exists() and (native.root / "older.wav").read_bytes() == wav_bytes()
        receipts = {path.name: path.read_bytes() for path in native.journal.iterdir()}
        artifacts = {path.name: path.read_bytes() for path in native.root.iterdir()}
        assert "start-intent.json" in receipts and "started.json" in receipts
        if scenario == "complete":
            assert "stopped.json" in receipts
            proof = r.verify_finalized(
                native.baseline, expected, generation=native.plan.generation, stopped=stopped
            )
            assert proof.samples == 8000 and proof.old_files == 1
        else:
            assert ("stopped.json" in receipts) == (scenario == "lost_receipt_ack")
            assert ("stop-intent.json" in receipts) == (scenario != "active_exit")
            with pytest.raises(r.UnconfirmedRecording):
                r.verify_finalized(
                    native.baseline, expected, generation=native.plan.generation, stopped=None
                )
            # Lost acknowledgment can leave complete-looking files. Neither those
            # files, a receipt left before a lost return, nor independently proven
            # process exit fabricate a timely successful operation acknowledgment.
            assert (native.root / (name + ".json")).exists() == (
                scenario in ("lost_ack", "lost_receipt_ack")
            )
        with pytest.raises(o.UnconfirmedOwner):
            native.build()
        assert {path.name: path.read_bytes() for path in native.journal.iterdir()} == receipts
        assert {path.name: path.read_bytes() for path in native.root.iterdir()} == artifacts
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
        if witness >= 0:
            os.close(witness)
        for pipe in (child.stdin, child.stdout, child.stderr):
            pipe.close()
