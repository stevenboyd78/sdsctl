"""Disposable original outer for the full passive pipeline; NOT a launcher.

Explicitly assembled only by the independent test driver, without pytest.main. Initial setup,
Engine/runtime/source publication and acceptance are the existing synthetic
fixtures. No installed qualification, active App action or recovery is claimed.
"""

import array
import json
import os
import socket
import sys
import time
from pathlib import Path

from . import test_supplemental_observer_pipeline as pipeline

command, native = pipeline.command, pipeline.native


def exercise_original_outer_loss(s, monkeypatch, config, channel):
    # The caller created this private socket before our exec. These fixture
    # arguments are NOT a production command, permission or publication pin.
    attempted, releases = [], []
    send_retirement = command.p.bootstrap.Endpoint.send_retirement
    deliver = command.p.bootstrap.Endpoint.deliver

    def no_release(endpoint, receipt):
        releases.append(True)
        return send_retirement(endpoint, receipt)

    def checkpoint():
        assert not attempted and not releases
        attempted.append(True)
        assert s.custody.armed_watch is s.watch and not s.watch.finished
        assert s.pair.writer.plan is s.custody.plan
        assert time.monotonic() < s.pipeline_end
        directory = os.open(s.case_root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            payload = json.dumps(
                dict(
                    phase=config["phase"],
                    end=s.pipeline_end,
                    native_deadline_ns=s.custody.deadline_ns,
                    writer_reads=s.h.reads,
                    observer_reads=s.comparison.counts["container"],
                    runner_used=False,
                ),
                separators=(",", ":"),
            ).encode()
            assert channel.sendmsg(
                [payload],
                [
                    (
                        socket.SOL_SOCKET,
                        socket.SCM_RIGHTS,
                        array.array(
                            "i",
                            [
                                s.h.witness.fd,
                                s.counterpart.fd,
                                s.watch.fd,
                                s.watch.cancel,
                                directory,
                            ],
                        ),
                    )
                ],
            ) == len(payload)
        finally:
            os.close(directory)
        # Only the disposable outer is killed. The independent driver already
        # holds each original peer/native handle and another cancellation writer
        # so outer-pidfd loss cannot be confused with cancellation EOF.
        channel.settimeout(max(0.001, s.pipeline_end - time.monotonic()))
        channel.recv(1)
        raise AssertionError("Original outer survived its marked loss; no continuation/retry")

    def endpoint(endpoint, channels):
        receipt = deliver(endpoint, channels)
        if endpoint.role == config["phase"]:
            checkpoint()
        return receipt

    def arm():
        spawn = native.direct_tests.load_direct(config["library"])
        command.arm_original_watch(
            s,
            native.selected_watch(
                "sealed-direct-owner-ingress",
                config["binary"],
                None,
                spawn,
                (native.Path(config["binary"]), config["digest"]),
            ),
        )
        if config["phase"] == "armed":
            checkpoint()

    monkeypatch.setattr(command.p.bootstrap.Endpoint, "send_retirement", no_release)
    monkeypatch.setattr(command.p.bootstrap.Endpoint, "deliver", endpoint)
    assert s.sender.send() is None
    command.finish(s, monkeypatch, arm)
    raise AssertionError("Marked original outer loss did not occur")


def main():
    config = json.loads(sys.argv[1])
    temporary = Path(config["temporary"])
    temporary.mkdir(mode=0o700)
    with (
        socket.socket(fileno=config["fd"]) as channel,
        pipeline.original_pipeline_fixture(temporary) as (joined, patches, reviewed),
    ):
        assert reviewed is None  # Driver supplied the separately reviewed native image.
        exercise_original_outer_loss(joined, patches, config, channel)
    raise AssertionError("Marked original outer loss returned without termination")
