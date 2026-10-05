"""Owned subprocess fixture: real recording manager, synthetic PCM, no network.

The parent pins a pidfd before releasing the stop barrier. Deliberate blocks are
terminated by that parent; no production process, daemon launcher or host policy
is invoked or qualified by this fixture.
"""

import hashlib
import json
import os
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event

from supplemental_recording_evidence import capture_baseline, template
from supplemental_recording_owner import FiniteRecordingOwner, Plan

import sds200.daemon_recording as recording
from tests.test_daemon_recording import FakeClock, FakeRuntime, FakeWallClock


def report(value):
    print(json.dumps(value, separators=(",", ":")), flush=True)


def main():
    root, journal = Path(sys.argv[1]), Path(sys.argv[2])
    scenario = sys.argv[3]
    assert scenario in (
        "complete",
        "blocked_stop",
        "blocked_metadata",
        "lost_ack",
        "lost_receipt_ack",
        "active_exit",
    )
    runtime, clock = FakeRuntime(), FakeClock()
    wall = FakeWallClock(datetime(2026, 9, 22, 12, tzinfo=UTC))
    case, generation = "c" * 32, "a" * 64
    manager = recording.DaemonRecordingManager(
        runtime, root, template=template(case), clock=clock, now=wall
    )
    plan = Plan(
        case,
        generation,
        hashlib.sha256(runtime.audio.stream.endpoint.encode()).hexdigest(),
        100,
        105,
        164,
        170,
    )
    owner = FiniteRecordingOwner(
        manager, capture_baseline(root, case), plan, journal, monotonic=clock
    )
    expected = owner.start()
    runtime.router.submit_pcm(b"\x12\x34" * 8000)
    until = time.monotonic() + 5
    while manager.snapshot().samples != 8000:
        assert time.monotonic() < until
        time.sleep(0.005)
    report({"stage": "started", "expected": asdict(expected)})
    assert sys.stdin.readline() == "stop\n"
    if scenario == "active_exit":
        os._exit(23)
    clock.value = 164
    wall.value += timedelta(seconds=64)

    def block(*_args, **_kwargs):
        report({"stage": "blocked", "scenario": scenario})
        Event().wait()  # Deliberately abandoned native call; only the parent ends it.
        raise AssertionError("unreachable")

    original_stop = manager.stop_recording
    if scenario == "blocked_stop":
        manager.stop_recording = block
    elif scenario == "blocked_metadata":
        recording.write_recording_metadata = block
    elif scenario == "lost_ack":

        def lost_ack():
            original_stop()
            block()

        manager.stop_recording = lost_ack
    elif scenario == "lost_receipt_ack":
        original_publish = owner._publish

        def lost_receipt_ack(name, now, snapshot=None):
            original_publish(name, now, snapshot)
            if name == "stopped":
                block()

        owner._publish = lost_receipt_ack
    stopped = owner.stop()
    report({"stage": "stopped", "snapshot": stopped})
    owner.close()
    manager.close()
    runtime.close()


if __name__ == "__main__":
    main()
