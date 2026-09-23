"""Dedicated execution through the actual private gate, with loopback PCM only."""

import json
import socket
import time
from contextlib import contextmanager
from dataclasses import replace
from threading import Event, Thread

import pytest

from . import test_supplemental_recording_construction as construction
from . import test_supplemental_recording_control as control

m = control.m
tree, configured, prepared, context = (
    control.tree,
    control.configured,
    control.prepared,
    control.context,
)


@pytest.mark.parametrize("fault", [None, "ready_only", "invalid_begin", "lost_begin_return"])
def test_exact_child_runs_only_after_one_authenticated_begin(prepared, context, monkeypatch, fault):
    # Bind the synthetic scanner before the fork, but start its thread only in
    # the child. The parent never forks an already running scanner thread.
    with monkeypatch.context() as patch:
        patch.setattr(Thread, "start", lambda self: None)
        scanner = construction.LoopbackScanner()
    spec = replace(prepared.spec, control_port=scanner.socket.getsockname()[1], ready_timeout=6)
    config = replace(prepared.config, scanner_target=f"udp://127.0.0.1:{spec.control_port}")
    context = replace(
        context,
        plan=replace(context.plan, specification=spec, configuration=config),
        ready_by=time.monotonic() + 5,
    )

    def run(right, parent):
        scanner.thread.start()
        done, feed_failed = Event(), Event()
        original = m.launch.construction.construct
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as packets:
                packets.bind(("127.0.0.1", 0))

                class Rtsp(construction.FakeRtspClient):
                    def start(self, client_port):
                        self.started_ports.append(client_port)
                        return construction.RtpTransportInfo(
                            source="127.0.0.1", server_port=packets.getsockname()[1], ssrc=5678
                        )

                rtsp = Rtsp()

                @contextmanager
                def observed(*args, **kwargs):
                    with original(*args, **kwargs) as trial:
                        # Construction and lifecycle remain real. Only the
                        # external scanner's RTSP reply and RTP source are fake.
                        trial.runtime.audio.stream.transport._rtsp_client_factory = lambda *_: rtsp

                        def feed():
                            try:
                                while not trial.manager.snapshot().active:
                                    if done.wait(0.01):
                                        return
                                for i in range(8):
                                    packets.sendto(
                                        construction.make_rtp(
                                            bytes(range(160)),
                                            sequence=100 + i,
                                            timestamp=1000 + i * 160,
                                        ),
                                        ("127.0.0.1", rtsp.started_ports[0]),
                                    )
                                construction.wait_for(
                                    lambda: trial.manager.snapshot().samples == 1280
                                )
                            except BaseException:
                                feed_failed.set()
                                trial.cancel()

                        worker = Thread(target=feed)
                        worker.start()
                        try:
                            yield trial
                        finally:
                            done.set()
                            worker.join(1)
                            assert not worker.is_alive() and not feed_failed.is_set()

                m.launch.construction.construct = observed
                if fault is None:
                    result = m.execute(
                        right,
                        context,
                        parent_pid=parent["pid"],
                        parent_start_ticks=parent["start_ticks"],
                        parent_uid=parent["uid"],
                        parent_gid=parent["gid"],
                    )
                    assert result.artifact.samples == 1280
                else:
                    control.refused(
                        lambda: m.execute(
                            right,
                            context,
                            parent_pid=parent["pid"],
                            parent_start_ticks=parent["start_ticks"],
                            parent_uid=parent["uid"],
                            parent_gid=parent["gid"],
                        )
                    )
                assert len(rtsp.started_ports) == rtsp.teardowns == 1
                assert not scanner.reads and not scanner.errors
        finally:
            m.launch.construction.construct = original
            scanner.close()

    try:
        with control.child(context, run) as guardian:
            guardian.receive_ready()
            assert list(spec.receipts.iterdir()) == []
            assert (
                m.launch.protected.Collector(prepared.stored).pristine().files.stage == "pristine"
            )
            if fault == "invalid_begin":
                m._send(guardian, "begin", {"PRIVATE_INVALID": True}, deadline=context.ready_by)
            elif fault == "lost_begin_return":
                original = m._send

                def lost(*args, **kwargs):
                    original(*args, **kwargs)
                    raise TimeoutError("Delivered begin, but guardian lost its return")

                monkeypatch.setattr(m, "_send", lost)
                control.refused(
                    lambda: guardian.begin(
                        control.binding(context), intent_at=time.monotonic(), intent_sha256="1" * 64
                    )
                )
                # Observe the real queued start frame only to synchronize this
                # fixture before closing its report channel. started.json is
                # published BEFORE that send; closing as soon as the file exists
                # races the send and can legitimately prevent stopped.json.
                # The lost begin remains unconfirmed and cannot be retried or
                # upgraded to a host acknowledgment by this raw fixture read.
                guardian.channel.incoming.settimeout(2)
                assert json.loads(guardian.channel.incoming.recv(m.returns.MAX_BYTES))["phase"] == (
                    "started"
                )
                assert guardian.phase == "unconfirmed"
                construction.wait_for(lambda: (spec.receipts / "started.json").exists())
                control.refused(
                    lambda: guardian.begin(
                        control.binding(context), intent_at=time.monotonic(), intent_sha256="1" * 64
                    )
                )
            elif fault is None:
                reader = guardian.begin(
                    control.binding(context), intent_at=time.monotonic(), intent_sha256="1" * 64
                )
                try:
                    assert json.loads(reader.receive().raw)["phase"] == "started"
                    report = json.loads(reader.receive().raw)
                    assert report["body"]["artifact"]["samples"] == 1280
                    assert report["body"]["stopped"]["active"] is False
                    for endpoint in (guardian.channel.incoming, guardian.channel.outgoing):
                        m.returns._socket(endpoint)  # Remain anonymous after both directions send.
                finally:
                    reader.close()
        assert guardian.fixture_returncode == 0
        if fault in ("ready_only", "invalid_begin"):
            assert not list(spec.receipts.iterdir())
            assert (
                m.launch.protected.Collector(prepared.stored).pristine().files.stage == "pristine"
            )
        else:
            assert (spec.receipts / "started.json").is_file()
            assert (spec.receipts / "stopped.json").is_file()
        assert (prepared.tree.root / "older/old.wav").read_bytes() == b"old evidence unchanged"
    finally:
        scanner.socket.close()  # Parent's duplicate of the never-started fixture.
