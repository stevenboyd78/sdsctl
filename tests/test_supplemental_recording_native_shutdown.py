"""Independent exit evidence for the assembled native lifecycle, never a live App."""

import json
import os
import select
import signal
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from .test_supplemental_recording_assembly import n
from .test_supplemental_recording_shutdown import receive

pytestmark = pytest.mark.skipif(not hasattr(os, "pidfd_open"), reason="Linux native pidfd test")


@pytest.mark.parametrize(
    "scenario", ("complete", "blocked_stop", "blocked_metadata", "lost_ack", "lost_receipt_ack")
)
def test_native_sigterm_is_not_exit_or_finalize_proof(tmp_path, scenario):
    repo = Path(n.__file__).parent.parent
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        (str(repo / "scripts"), str(repo), str(repo / "src"))
    )
    sockets = TemporaryDirectory(prefix="sds-native-")
    child = subprocess.Popen(
        [
            sys.executable,
            "-u",
            str(repo / "tests/fixtures/finite_native_recording_child.py"),
            str(tmp_path),
            scenario,
            sockets.name,
        ],
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    witness = -1
    try:
        assert receive(child) == {"stage": "ready"}
        witness = os.pidfd_open(child.pid)
        assert child.poll() is None and not select.select([witness], [], [], 0)[0]
        child.stdin.write(b"start\n")
        child.stdin.flush()
        assert receive(child) == {"stage": "recording"}
        if scenario == "complete":
            result = receive(child)
            assert result["stage"] == "verified" and result["artifact"]["samples"] > 0
            assert result["cleanup_complete"] and not result["runtime_running"]
            assert child.wait(timeout=5) == 0
            assert child.stderr.read() == b""
        else:
            assert receive(child) == {"stage": "blocked", "scenario": scenario}
            signal.pidfd_send_signal(witness, signal.SIGTERM)
            # The REAL native SIGTERM handler requests graceful cleanup, which
            # is itself blocked here. A sent signal is never treated as exit.
            with pytest.raises(subprocess.TimeoutExpired):
                child.wait(timeout=0.2)
            assert not select.select([witness], [], [], 0)[0]
            signal.pidfd_send_signal(witness, signal.SIGKILL)
            assert child.wait(timeout=5) == -signal.SIGKILL
            assert b'"stage":"verified"' not in child.stdout.read()
        assert select.select([witness], [], [], 0)[0]
        root, journal = tmp_path / "recordings", tmp_path / "receipts"
        assert (root / "older.txt").read_bytes() == b"older evidence unchanged"
        assert len(list(root.glob("*.wav"))) == 1
        assert (journal / "started.json").is_file() and (journal / "stop-intent.json").is_file()
        assert (journal / "stopped.json").exists() == (scenario in ("complete", "lost_receipt_ack"))
        if scenario != "blocked_stop":
            assert bool(list(root.glob("*.wav.json"))) == (
                scenario in ("complete", "lost_ack", "lost_receipt_ack")
            )
        # With stop_recording blocked before native dispatch, ordinary close()
        # may independently finalize the WAV while the scheduled worker remains
        # stuck. That sidecar is neither forbidden nor a successful trial.
        # A receipt can exist even though its successful publication was never
        # acknowledged. Do not create a success report from it after forced exit.
        for receipt in journal.iterdir():
            assert json.loads(receipt.read_bytes())  # Preserved, not rewritten/deleted.
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
        if witness >= 0:
            os.close(witness)
        for pipe in (child.stdin, child.stdout, child.stderr):
            pipe.close()
        sockets.cleanup()  # Parent owns this namespace, including after forced child exit.
