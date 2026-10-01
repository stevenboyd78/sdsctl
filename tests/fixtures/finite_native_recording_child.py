"""Owned native-process fault fixture; loopback scanner and synthetic audio only.

The same local test factories build the profile and native object graph. The
parent pins this exact child with a pidfd before releasing its stdin start gate.
Deliberately blocked calls require that parent's bounded TERM/KILL escalation.
"""

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from threading import Event

from sds200 import daemon_recording
from sds200.audio import AudioChunk
from tests.test_daemon_display_frames import configured
from tests.test_daemon_quick_key_worker import wait_for
from tests.test_supplemental_acceptance_launcher import native
from tests.test_supplemental_recording_assembly import native_bundle


def report(value):
    raw = json.dumps(value, separators=(",", ":")).encode() + b"\n"
    assert len(raw) < 4096
    assert os.write(sys.stdout.fileno(), raw) == len(raw)


def main():
    directory, scenario = Path(sys.argv[1]), sys.argv[2]
    assert scenario in (
        "complete",
        "blocked_stop",
        "blocked_metadata",
        "lost_ack",
        "lost_receipt_ack",
    )
    # __wrapped__ invokes the test factory without pytest's fixture dispatcher.
    config = configured.__wrapped__(directory)
    with (
        contextmanager(native.__wrapped__)(config) as source,
        native_bundle(source, directory, sockets_directory=Path(sys.argv[3])) as rig,
    ):
        trial = rig.build()
        finished = Event()
        announced = Event()

        def block(*_args, **_kwargs):
            if not announced.is_set():
                announced.set()
                report({"stage": "blocked", "scenario": scenario})
            Event().wait()  # Only the parent can end this intentional permanent block.

        original_stop = rig.manager.stop_recording
        if scenario == "blocked_stop":
            rig.manager.stop_recording = block
        elif scenario == "blocked_metadata":
            daemon_recording.write_recording_metadata = block
        elif scenario == "lost_ack":

            def lost_ack():
                original_stop()
                block()

            rig.manager.stop_recording = lost_ack

        def operator():
            try:
                wait_for(lambda: trial.ready)
                assert rig.process.signals._active
                report({"stage": "ready"})
                assert sys.stdin.readline() == "start\n"
                trial.request_start()
                wait_for(lambda: rig.acquisition.status().armed)
                if scenario == "lost_receipt_ack":
                    original_publish = trial.controller._publish

                    def lost_receipt_ack(name, now, snapshot=None):
                        original_publish(name, now, snapshot)
                        if name == "stopped":
                            block()

                    trial.controller._publish = lost_receipt_ack
                report({"stage": "recording"})
                while not finished.wait(0.02):
                    if rig.manager.snapshot().active:
                        rig.runtime.audio.stream.transport.feed(AudioChunk(b"\x80" * 160))
            except BaseException:
                trial.cancel()
                raise

        with ThreadPoolExecutor(max_workers=1) as pool:
            operation = pool.submit(operator)
            try:
                result = trial.run()
            finally:
                finished.set()
            operation.result(timeout=3)
        assert scenario == "complete"
        report(
            {
                "stage": "verified",
                "artifact": asdict(result.artifact),
                "cleanup_complete": trial.cleanup_complete,
                "runtime_running": rig.runtime.running,
            }
        )


if __name__ == "__main__":
    main()
