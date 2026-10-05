"""Native loopback control/worker with real runtime, PCM routing and WAV lifecycle.

No physical scanner or live service. Scheduling uses a controlled clock; native
write/reply waits keep their production 250ms budget. Audio input is synthetic,
not an RTSP/RTP/network coexistence qualification.
"""

import os
import socket
import wave
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from threading import Event, Lock, Thread
from types import SimpleNamespace

import pytest

from sds200 import daemon_quick_keys
from sds200.audio import AudioChunk
from sds200.audio_recording import decode_mulaw
from sds200.audio_session import AudioSessionStatus
from sds200.daemon_display_frames import DaemonDisplayFrames
from sds200.daemon_display_profile import DaemonDisplayProfile
from sds200.daemon_quick_keys import DaemonQuickKeyCache
from sds200.daemon_recording import DaemonRecordingManager
from sds200.exceptions import DaemonControlBusyError
from sds200.network import UdpTransport
from sds200.radio import SDS200

from .test_audio_sinks import CollectingSink
from .test_bounded_supplemental_io import REPLIES
from .test_daemon_display_frames import configured as configured
from .test_daemon_display_read_research import runtime_for
from .test_daemon_quick_key_worker import wait_for

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Native POSIX UDP candidate")
PSI = (
    b'PSI,<XML>,<ScannerInfo Mode="Trunk Scan" V_Screen="trunk_scan">'
    b'<MonitorList Q_Key="None"/><System Name="Synthetic" Q_Key="None"/>'
    b'<TGID Name="Synthetic channel"/><Footer No="1" EOT="1"/></ScannerInfo>'
)


class LoopbackScanner:
    """One loopback-only peer; withheld replies never block PSI or its receiver."""

    def __init__(self):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind(("127.0.0.1", 0))
        self.socket.settimeout(0.005)
        self.stop = Event()
        self.lock = Lock()
        self.commands = []
        self.gates = {}
        self.pending = []
        self.errors = []
        self.target = None
        self.volume = 0
        self.psi_sent = 0
        self.thread = Thread(target=self.run, name="loopback-scanner")
        self.thread.start()

    @property
    def reads(self):
        with self.lock:
            return [command for command in self.commands if command in REPLIES]

    @contextmanager
    def hold(self, command):
        gate = SimpleNamespace(entered=Event(), release=Event())
        with self.lock:
            assert command not in self.gates
            self.gates[command] = gate
        try:
            yield gate
        finally:
            gate.release.set()

    def reply(self, command, target):
        if command == "MDL":
            data = b"MDL,SDS200\r"
        elif command == "VER":
            data = b"VER,Version 1.26.01\r"
        elif command.startswith("PSI,"):
            self.target = None if command == "PSI,0" else target
            data = None if self.target is None else PSI
        elif command.startswith("VOL,"):
            self.volume = int(command.split(",")[1])
            data = b"VOL,OK\r"
        elif command == "VOL":
            data = f"VOL,{self.volume}\r".encode()
        else:
            data = REPLIES[command]  # Any unexpected command fails the fixture.
        if data is not None:
            self.socket.sendto(data, target)

    def run(self):
        try:
            while not self.stop.is_set():
                try:
                    data, target = self.socket.recvfrom(4096)
                except TimeoutError:
                    pass
                else:
                    assert target[0] == "127.0.0.1"
                    command = data.decode().rstrip("\r")
                    with self.lock:
                        self.commands.append(command)
                        gate = self.gates.pop(command, None)
                    if gate is None:
                        self.reply(command, target)
                    else:
                        self.pending.append((command, target, gate))
                        gate.entered.set()
                for item in list(self.pending):
                    command, target, gate = item
                    if gate.release.is_set():
                        self.reply(command, target)
                        self.pending.remove(item)
                if self.target is not None:
                    self.socket.sendto(PSI, self.target)
                    self.psi_sent += 1
        except BaseException as error:
            self.errors.append(error)

    def close(self):
        self.stop.set()
        self.thread.join(1)
        self.socket.close()
        assert not self.thread.is_alive()
        assert not self.errors


@pytest.fixture
def rig(configured, monkeypatch):
    # Accelerate worker wakes only; do not extend native response budgets.
    monkeypatch.setattr(daemon_quick_keys, "MIN_READ_GAP", 0.005)
    peer = LoopbackScanner()
    transport = UdpTransport("127.0.0.1", remote_port=peer.socket.getsockname()[1], reconnect=False)
    scanner = SDS200.from_transport(transport)
    runtime = runtime_for(scanner)
    clock = SimpleNamespace(now=10.0)
    config = replace(configured, scanner_target=scanner.endpoint)
    cache = DaemonQuickKeyCache(
        scanner,
        config.binding.endpoint_id,
        scanner.endpoint,
        clock=lambda: clock.now,
        include_clock=True,
        allow_scoped_reads=False,
        bounded_writes=True,
        read_scope=runtime._supplemental_read_scope,
    )
    feed = DaemonDisplayFrames(
        DaemonDisplayProfile(config, lambda: scanner.endpoint),
        scanner,
        clock=lambda: clock.now,
        quick_keys=cache,
    )
    value = SimpleNamespace(
        peer=peer,
        scanner=scanner,
        runtime=runtime,
        clock=clock,
        cache=cache,
        feed=feed,
        transport=transport,
    )
    try:
        runtime.start()  # Real MDL/VER/PSI startup against the loopback peer.
        feed.start()
        wait_for(lambda: cache.supplemental_snapshot().sequence is not None)
        assert peer.reads == []
        yield value
    finally:
        feed.close()
        runtime.stop()  # Real stop/PSI teardown, not a substituted state setter.
        scanner.close()
        peer.close()
        assert not feed.quick_key_worker_status().alive
        assert feed.quick_key_worker_status().failure is None


def demand(rig, command="FQK"):
    if command == "DTM":
        rig.feed.snapshot()
        wait_for(lambda: rig.cache.snapshot().banks[0].states is not None)
        rig.clock.now += 0.5
    rig.feed.snapshot()


def settled(rig):
    wait_for(lambda: not rig.scanner._responses and rig.cache._pending is None)


def test_native_worker_yields_to_actual_foreground_control(rig):
    with rig.peer.hold("VOL,7") as gate, ThreadPoolExecutor(max_workers=1) as pool:
        control = pool.submit(rig.runtime.set_volume, 7)
        assert gate.entered.wait(1)
        rig.feed.snapshot()
        wait_for(lambda: rig.cache._demand_until == 0)  # Gate refused and invalidated.
        assert rig.peer.reads == [] and not control.done()
        gate.release.set()
        control.result(timeout=1)
    assert rig.peer.volume == 7
    rig.clock.now += 0.5
    demand(rig)
    wait_for(lambda: rig.cache.snapshot().banks[0].states is not None)
    assert rig.peer.reads == ["FQK"]
    assert rig.peer.commands.index("VOL") < rig.peer.commands.index("FQK")


@pytest.mark.parametrize("command", ["FQK", "DTM"])
@pytest.mark.parametrize("reply", [True, False])
def test_inflight_native_read_keeps_controls_busy_but_pcm_and_psi_progress(rig, command, reply):
    sink = CollectingSink("live-test")
    rig.runtime.attach_sink(sink)
    with rig.peer.hold(command) as gate:
        demand(rig, command)
        assert gate.entered.wait(1)
        before = rig.cache.supplemental_snapshot().sequence
        with pytest.raises(DaemonControlBusyError):
            rig.runtime.set_volume(7)
        with pytest.raises(DaemonControlBusyError):
            rig.runtime.reconnect()
        with pytest.raises(RuntimeError, match="reserved"):
            rig.scanner.waterfall_session.subscribe()
        payload = bytes(range(160))
        rig.runtime.audio.stream.transport.feed(AudioChunk(payload))
        wait_for(lambda: bool(sink.received))
        wait_for(lambda: rig.cache.supplemental_snapshot().sequence > before)
        assert b"".join(sink.received) == decode_mulaw(payload)
        assert rig.runtime.snapshot().audio.packets == 1
        if reply:
            gate.release.set()
        settled(rig)
        assert rig.cache.snapshot().blocked_until_reconnect == (None if reply else "timeout")
        assert "VOL,7" not in rig.peer.commands  # Busy calls were never queued.
    rig.runtime.set_volume(7)  # Both success/timeout release the reservation.
    assert rig.peer.commands[-2:] == ["VOL,7", "VOL"]


@pytest.mark.parametrize("command", ["FQK", "DTM"])
@pytest.mark.parametrize("reply", [True, False])
def test_native_worker_stop_waits_for_one_read_and_leaves_no_late_work(rig, command, reply):
    with rig.peer.hold(command) as gate, ThreadPoolExecutor(max_workers=1) as pool:
        demand(rig, command)
        assert gate.entered.wait(1)
        entered = Event()

        def stop():
            entered.set()
            rig.runtime.stop()

        shutdown = pool.submit(stop)
        assert entered.wait(1) and not shutdown.done()
        if reply:
            gate.release.set()
        shutdown.result(timeout=2)
    count = len(rig.peer.reads)
    assert not rig.runtime.running and not rig.scanner.connected
    assert not rig.cache.snapshot().active
    rig.clock.now += 3
    rig.feed.snapshot()
    rig.feed.close()
    assert len(rig.peer.reads) == count and not rig.scanner._responses
    assert rig.peer.commands[-1] == "PSI,0"


@pytest.mark.parametrize("reply", [True, False])
def test_actual_native_reconnect_invalidates_session_and_needs_new_demand(rig, reply):
    with rig.peer.hold("FQK") as gate:
        demand(rig)
        assert gate.entered.wait(1)
        old_session = rig.cache.supplemental_snapshot().session
        if reply:
            gate.release.set()
        settled(rig)
    rig.runtime.reconnect(timeout=2)
    wait_for(lambda: rig.cache.supplemental_snapshot().sequence is not None)
    assert rig.cache.supplemental_snapshot().session is not old_session
    assert rig.cache.snapshot().banks[0].states is None
    assert rig.cache.snapshot().blocked_until_reconnect is None
    assert rig.peer.reads == ["FQK"]
    rig.clock.now += 1
    demand(rig)
    wait_for(lambda: rig.cache.snapshot().banks[0].states is not None)
    assert rig.peer.reads == ["FQK", "FQK"]


@pytest.mark.parametrize("barrier", ["clear_demand", "close_feed"])
@pytest.mark.parametrize("reply", [True, False])
def test_inflight_native_result_cannot_restore_withdrawn_demand(rig, barrier, reply):
    with rig.peer.hold("FQK") as gate, ThreadPoolExecutor(max_workers=1) as pool:
        demand(rig)
        assert gate.entered.wait(1)
        if barrier == "clear_demand":
            rig.cache.clear_demand()
            closing = None
        else:
            closing = pool.submit(rig.feed.close)
            wait_for(lambda: rig.feed.quick_key_worker_status().stopped)
        if reply:
            gate.release.set()
        settled(rig)
        if closing is not None:
            closing.result(timeout=1)
    rig.clock.now += 3
    assert not rig.cache.snapshot().active
    assert rig.cache.snapshot().banks[0].states is None
    assert rig.cache.clock_snapshot().local_time is None
    assert rig.peer.reads == ["FQK"]


@pytest.mark.parametrize("command", ["FQK", "DTM"])
@pytest.mark.parametrize("reply", [True, False])
def test_native_read_does_not_block_existing_recording_pcm_or_wav_finalization(
    rig,
    tmp_path,
    command,
    reply,
):
    manager = DaemonRecordingManager(rig.runtime, tmp_path)
    try:
        assert manager.start_recording().active
        with rig.peer.hold(command) as gate:
            demand(rig, command)
            assert gate.entered.wait(1)
            payload = bytes(range(160))
            for _ in range(8):
                rig.runtime.audio.stream.transport.feed(AudioChunk(payload))
            wait_for(lambda: manager.snapshot().samples == 1280)
            assert rig.runtime.snapshot().audio.samples == 1280
            if reply:
                gate.release.set()
            settled(rig)
        stopped = manager.stop_recording()
        assert not stopped.active and stopped.error is None and stopped.samples == 1280
        with wave.open(str(stopped.recording_path), "rb") as wav:
            assert wav.getnframes() == 1280
            assert wav.readframes(1280) == decode_mulaw(payload) * 8
        assert stopped.metadata_path.is_file()
        assert rig.peer.reads == (["FQK"] if command == "FQK" else ["FQK", "DTM"])
    finally:
        manager.close()


def test_audio_attachment_first_yields_reader_and_keeps_existing_pcm_routing(rig):
    entered, release = Event(), Event()
    live = CollectingSink("already-playing")
    rig.runtime.attach_sink(live)

    class SlowStart(CollectingSink):
        def start(self):
            entered.set()
            assert release.wait(2)
            super().start()

    slow = SlowStart("slow-new-sink")
    with ThreadPoolExecutor(max_workers=1) as pool:
        attaching = pool.submit(rig.runtime.attach_sink, slow)
        try:
            assert entered.wait(1)
            demand(rig)
            wait_for(lambda: rig.cache._demand_until == 0)
            assert rig.peer.reads == [] and not attaching.done()
            rig.runtime.audio.stream.transport.feed(AudioChunk(b"\xff" * 160))
            wait_for(lambda: bool(live.received))
            assert live.received == [b"\x00\x00" * 160]
            before = rig.cache.supplemental_snapshot().sequence
            wait_for(lambda: rig.cache.supplemental_snapshot().sequence > before)
        finally:
            release.set()
        attaching.result(timeout=1)
    rig.clock.now += 0.5
    demand(rig)
    wait_for(lambda: rig.cache.snapshot().banks[0].states is not None)
    assert rig.peer.reads == ["FQK"]


@pytest.mark.parametrize("operation", ["start", "stop"])
@pytest.mark.parametrize("reply", [True, False])
def test_recording_lifecycle_behind_native_read_completes_after_reservation(
    rig,
    tmp_path,
    operation,
    reply,
):
    manager = DaemonRecordingManager(rig.runtime, tmp_path)
    entered = Event()
    blocked_status = (
        AudioSessionStatus.STARTING if operation == "start" else AudioSessionStatus.STOPPING
    )
    unsubscribe = manager.on_state(
        lambda state: entered.set() if state.status is blocked_status else None
    )
    try:
        if operation == "stop":
            manager.start_recording()
            rig.runtime.audio.stream.transport.feed(AudioChunk(b"\xff" * 160))
            wait_for(lambda: manager.snapshot().samples == 160)
        with rig.peer.hold("FQK") as gate, ThreadPoolExecutor(max_workers=1) as pool:
            demand(rig)
            assert gate.entered.wait(1)
            action = manager.start_recording if operation == "start" else manager.stop_recording
            changing = pool.submit(action)
            assert entered.wait(1) and not changing.done()
            before = rig.cache.supplemental_snapshot().sequence
            wait_for(lambda: rig.cache.supplemental_snapshot().sequence > before)
            if reply:
                gate.release.set()
            result = changing.result(timeout=2)
            settled(rig)
        assert result.error is None
        assert result.active is (operation == "start")
        assert rig.cache.snapshot().blocked_until_reconnect == (None if reply else "timeout")
        assert rig.peer.reads == ["FQK"]
        if operation == "start":
            rig.runtime.audio.stream.transport.feed(AudioChunk(b"\xff" * 160))
            wait_for(lambda: manager.snapshot().samples == 160)
            result = manager.stop_recording()
        with wave.open(str(result.recording_path), "rb") as wav:
            assert wav.getnframes() == 160
            assert wav.readframes(160) == b"\x00\x00" * 160
    finally:
        unsubscribe()
        manager.close()


def test_reconnect_startup_yields_worker_until_new_psi_and_demand(rig):
    demand(rig)
    wait_for(lambda: rig.cache.snapshot().banks[0].states is not None)
    old_session = rig.cache.supplemental_snapshot().session
    with rig.peer.hold("PSI,500") as gate, ThreadPoolExecutor(max_workers=1) as pool:
        reconnecting = pool.submit(rig.runtime.reconnect)
        try:
            assert gate.entered.wait(1)
            rig.clock.now += 1
            rig.feed.snapshot()
            assert rig.cache.supplemental_snapshot().session is not old_session
            assert not rig.cache.snapshot().active and rig.cache.clock_snapshot().local_time is None
            assert rig.peer.reads == ["FQK"] and not reconnecting.done()
        finally:
            gate.release.set()
        reconnecting.result(timeout=1)
    wait_for(lambda: rig.cache.supplemental_snapshot().sequence is not None)
    assert rig.peer.reads == ["FQK"]
    demand(rig)
    wait_for(lambda: rig.cache.snapshot().banks[0].states is not None)
    assert rig.peer.reads == ["FQK", "FQK"]
