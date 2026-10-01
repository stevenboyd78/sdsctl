"""Source-pinned bounded owner on native loopback UDP; never live hardware."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event, get_ident
from types import SimpleNamespace

import pytest

from sds200 import daemon_quick_keys
from sds200.audio import AudioChunk
from sds200.audio_recording import decode_mulaw
from sds200.daemon_display_profile import DaemonDisplayProfile
from sds200.daemon_runtime import DaemonRuntimeState
from sds200.daemon_supplemental_acquisition import (
    DaemonSupplementalAcquisition,
    SupplementalAcquisitionPolicy,
)
from sds200.exceptions import DaemonControlBusyError
from sds200.network import UdpTransport
from sds200.radio import SDS200
from sds200.trace import TrafficTrace

from .test_audio_sinks import CollectingSink
from .test_daemon_display_frames import configured as configured
from .test_daemon_display_read_research import runtime_for
from .test_daemon_quick_key_worker import wait_for
from .test_daemon_supplemental_scope import hold_elsewhere
from .test_supplemental_native_lifecycle import LoopbackScanner

POLICY = SupplementalAcquisitionPolicy("Version 1.26.01", 75, 6)


@pytest.fixture
def rig(configured, monkeypatch):
    monkeypatch.setattr(daemon_quick_keys, "MIN_READ_GAP", 0.01)
    peer = LoopbackScanner()
    transport = UdpTransport("127.0.0.1", remote_port=peer.socket.getsockname()[1], reconnect=False)
    scanner = SDS200.from_transport(transport)
    runtime = runtime_for(scanner)
    clock = SimpleNamespace(now=10.0)
    profile = DaemonDisplayProfile(
        replace(configured, scanner_target=scanner.endpoint), lambda: scanner.endpoint
    )
    owner = DaemonSupplementalAcquisition(runtime, profile, POLICY, clock=lambda: clock.now)
    try:
        runtime.start()
        owner.start()
        wait_for(lambda: owner.frames.supplemental_frame_set() is not None)
        yield SimpleNamespace(
            owner=owner, peer=peer, scanner=scanner, runtime=runtime, profile=profile, clock=clock
        )
    finally:
        owner.close()
        runtime.stop()
        scanner.close()
        peer.close()
        assert not owner.frames.quick_key_worker_status().alive
        assert owner.frames.quick_key_worker_status().failure is None
        assert runtime._supplemental_acquisition is None


def renew(rig):
    capture = rig.owner.frames.supplemental_frame_set()
    assert capture is not None
    return rig.owner.renew(capture)


def read(rig, source, sequence):
    assert renew(rig) is not None
    wait_for(
        lambda: getattr(rig.owner.frames.supplemental_values(), source).sample_sequence == sequence
    )


def resumed_reads(rig):
    # The contended opportunity may precede or follow the worker wake. Do not
    # assume which source is due first; both must commit, without extra commands.
    for count, now in enumerate((10.5, 11), start=1):
        rig.clock.now = now
        assert renew(rig) is not None
        wait_for(lambda count=count: len(rig.peer.reads) == count)
        wait_for(lambda: rig.owner._cache._pending is None)
    assert sorted(rig.peer.reads) == ["DTM", "FQK"]
    sample = rig.owner.frames.supplemental_values()
    assert sample.clock.sample_sequence == sample.favorites.sample_sequence == 1


def test_start_and_ordinary_readers_are_passive_before_and_after_arm(rig):
    original = list(rig.peer.commands)
    assert not rig.owner.status().armed
    assert renew(rig) is None
    for _ in range(10):
        rig.owner.frames.snapshot()
    Event().wait(0.03)
    assert rig.peer.reads == []
    assert rig.owner.arm()
    assert not rig.owner.arm()
    for _ in range(10):
        rig.owner.frames.snapshot()
    Event().wait(0.03)
    assert rig.peer.commands == original
    read(rig, "favorites", 1)
    rig.clock.now = 10.5
    read(rig, "clock", 1)
    assert rig.peer.reads == ["FQK", "DTM"]
    assert rig.owner.status().read_attempts == 2
    assert rig.runtime.snapshot().state is DaemonRuntimeState.RUNNING


def test_explicit_demand_expires_despite_legacy_polling(rig):
    assert rig.owner.arm()
    read(rig, "favorites", 1)
    rig.clock.now = 15
    wait_for(lambda: rig.owner.frames.supplemental_frame_set() is not None)
    for _ in range(10):
        rig.owner.frames.snapshot()
    Event().wait(0.04)
    assert rig.peer.reads == ["FQK"]
    assert not rig.owner._cache.snapshot().active
    assert not rig.owner.status().ended


def test_fixed_window_cannot_be_extended_by_demand(rig):
    assert rig.owner.arm()
    read(rig, "favorites", 1)
    captured = rig.owner.frames.supplemental_frame_set()
    rig.clock.now = 85
    assert rig.owner.status().reason == "window_expired"
    assert rig.owner.renew(captured) is None
    assert not rig.owner.arm()
    assert not rig.owner._cache.snapshot().active
    Event().wait(0.03)
    assert rig.peer.reads == ["FQK"]
    assert rig.scanner.connected and rig.scanner.psi_active


def test_quota_never_permits_seventh_attempt(rig):
    assert rig.owner.arm()
    for i, now in enumerate((10, 10.5, 12, 12.5, 14, 14.5), start=1):
        rig.clock.now = now
        assert renew(rig) is not None
        wait_for(lambda i=i: len(rig.peer.reads) == i)
        wait_for(lambda: rig.owner._cache._pending is None)
    assert rig.peer.reads == ["FQK", "DTM"] * 3
    captured = rig.owner.frames.supplemental_frame_set()
    assert rig.owner.status().reason == "quota_exhausted"
    rig.clock.now = 20
    assert rig.owner.renew(captured) is None
    assert not rig.owner.arm()
    Event().wait(0.03)
    assert len(rig.peer.reads) == 6


@pytest.mark.parametrize("change", ["model", "firmware", "trace", "runtime"])
def test_preflight_fails_without_probe_or_rearm(rig, change, tmp_path):
    before = list(rig.peer.commands)
    if change == "model":
        rig.runtime._scanner_model = "SDS100"
    elif change == "firmware":
        rig.runtime._scanner_firmware = "Version 1.26.02"
    elif change == "trace":
        rig.scanner.trace = TrafficTrace(tmp_path / "trace.log")
    else:
        rig.runtime._state = DaemonRuntimeState.STOPPING
    try:
        assert not rig.owner.arm()
        assert rig.owner.status().reason == "preflight_refused"
        assert not rig.owner.arm()
        assert rig.peer.commands == before
    finally:
        rig.scanner.trace = TrafficTrace()
        rig.runtime._state = DaemonRuntimeState.RUNNING


def test_identity_pin_is_checked_again_for_each_attempt(rig):
    assert rig.owner.arm()
    read(rig, "favorites", 1)
    rig.runtime._scanner_firmware = "Version 1.26.02"
    rig.clock.now = 10.5
    renew(rig)
    wait_for(lambda: rig.owner.status().ended)
    assert rig.owner.status().reason == "qualification_lost"
    assert rig.peer.reads == ["FQK"]
    assert rig.scanner.connected


@pytest.mark.parametrize("lock_name", ["_control_lock", "_lifecycle_lock"])
def test_busy_runtime_yields_without_spending_read_quota(rig, lock_name):
    assert rig.owner.arm()
    with hold_elsewhere(getattr(rig.runtime, lock_name)):
        assert renew(rig) is not None
        Event().wait(0.04)
        assert rig.owner.status().read_attempts == 0
        assert rig.peer.reads == []
    resumed_reads(rig)


def test_idle_waterfall_reservation_yields_without_changing_it(rig):
    assert rig.owner.arm()
    with rig.scanner.waterfall_session.reserve_idle_for_research():
        assert renew(rig) is not None
        Event().wait(0.04)
        assert rig.owner.status().read_attempts == 0
        assert rig.peer.reads == []
    resumed_reads(rig)


def test_disconnect_ends_window_even_after_runtime_recovers(rig):
    assert rig.owner.arm()
    read(rig, "favorites", 1)
    rig.runtime.reconnect(timeout=2)
    wait_for(lambda: rig.owner.frames.supplemental_frame_set() is not None)
    assert rig.owner.status().reason == "connection_ended"
    assert renew(rig) is None and not rig.owner.arm()
    assert rig.scanner.connected and rig.scanner.psi_active


@pytest.mark.parametrize("reply", [True, False])
def test_runtime_stop_drains_one_attempt_and_does_not_rearm(rig, reply):
    assert rig.owner.arm()
    with rig.peer.hold("FQK") as blocked, ThreadPoolExecutor(max_workers=1) as pool:
        assert renew(rig) is not None
        assert blocked.entered.wait(1)
        stopped = pool.submit(rig.runtime.stop)
        if reply:
            blocked.release.set()
        stopped.result(timeout=2)
    assert rig.owner.status().reason == "connection_ended"
    assert not rig.scanner.connected
    assert not rig.owner._cache.snapshot().active
    assert rig.peer.reads == ["FQK"]
    assert rig.peer.commands[-1] == "PSI,0"
    assert not rig.owner.arm()


@pytest.mark.parametrize("reply", [True, False])
def test_native_pending_read_preserves_pcm_and_psi_but_excludes_controls(rig, reply):
    sink = CollectingSink("candidate")
    rig.runtime.attach_sink(sink)
    assert rig.owner.arm()
    with rig.peer.hold("FQK") as blocked:
        assert renew(rig) is not None
        assert blocked.entered.wait(1)
        sequence = rig.owner._cache.supplemental_snapshot().sequence
        with pytest.raises(DaemonControlBusyError):
            rig.runtime.set_volume(7)
        with pytest.raises(RuntimeError, match="reserved"):
            rig.scanner.waterfall_session.subscribe()
        payload = bytes(range(160))
        rig.runtime.audio.stream.transport.feed(AudioChunk(payload))
        wait_for(lambda: bool(sink.received))
        wait_for(lambda: rig.owner._cache.supplemental_snapshot().sequence > sequence)
        assert b"".join(sink.received) == decode_mulaw(payload)
        if reply:
            blocked.release.set()
        wait_for(lambda: not rig.scanner._responses and rig.owner._cache._pending is None)
        assert rig.owner._cache.snapshot().blocked_until_reconnect == (None if reply else "timeout")
    assert "VOL,7" not in rig.peer.commands
    rig.runtime.set_volume(7)
    assert rig.peer.commands[-2:] == ["VOL,7", "VOL"]


@pytest.mark.parametrize("action", ["close", "deadline"])
def test_renewal_blocked_on_profile_cannot_outlive_window(rig, monkeypatch, action):
    assert rig.owner.arm()
    captured = rig.owner.frames.supplemental_frame_set()
    original = rig.profile.frame_context
    entered, release = Event(), Event()
    caller = None

    def context():
        if get_ident() == caller:
            entered.set()
            assert release.wait(2)
        return original()

    def blocked_renewal():
        nonlocal caller
        caller = get_ident()
        return rig.owner.renew(captured)

    monkeypatch.setattr(rig.profile, "frame_context", context)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(blocked_renewal)
        assert entered.wait(1)
        try:
            if action == "close":
                rig.owner.close()
            else:
                rig.clock.now = 85
                assert rig.owner.status().ended
        finally:
            release.set()
        assert future.result(timeout=1) is None
    assert not rig.owner._cache.snapshot().active
    assert rig.peer.reads == []


def test_transient_identity_lock_contention_does_not_end_window(rig, monkeypatch):
    assert rig.owner.arm()
    original = rig.owner._qualified
    monkeypatch.setattr(rig.owner, "_qualified", lambda: None)
    assert renew(rig) is not None
    Event().wait(0.04)
    assert not rig.owner.status().ended
    assert rig.peer.reads == []
    monkeypatch.setattr(rig.owner, "_qualified", original)
    resumed_reads(rig)


@pytest.mark.parametrize("action", ["close", "deadline"])
def test_late_reply_cannot_restore_samples_after_window_ends(rig, action):
    assert rig.owner.arm()
    with rig.peer.hold("FQK") as blocked:
        assert renew(rig) is not None
        assert blocked.entered.wait(1)
        if action == "close":
            rig.owner.close()
        else:
            rig.clock.now = 85
            assert rig.owner.status().ended
        blocked.release.set()
    wait_for(lambda: not rig.scanner._responses)
    assert rig.owner._cache.snapshot().banks[0].states is None
    assert rig.scanner.connected and rig.scanner.psi_active


def test_only_one_acquisition_owner_per_runtime(rig):
    with pytest.raises(ValueError, match="already had"):
        DaemonSupplementalAcquisition(rig.runtime, rig.profile, POLICY)
    assert rig.runtime._supplemental_acquisition is rig.owner
    rig.owner.close()
    assert rig.runtime._supplemental_acquisition is None
    with pytest.raises(ValueError, match="already had"):
        DaemonSupplementalAcquisition(rig.runtime, rig.profile, POLICY)
    assert not rig.owner.arm()
    with pytest.raises(ValueError, match="cannot be started"):
        rig.owner.start()


@pytest.mark.parametrize(
    "field,value",
    [
        ("window_seconds", 0),
        ("window_seconds", 76),
        ("window_seconds", True),
        ("window_seconds", float("nan")),
        ("window_seconds", float("inf")),
        ("max_read_attempts", 0),
        ("max_read_attempts", 151),
        ("max_read_attempts", True),
        ("expected_firmware", ""),
        ("expected_firmware", " x"),
        ("expected_firmware", "x\ny"),
    ],
)
def test_invalid_policy_is_rejected(field, value):
    with pytest.raises(ValueError):
        replace(POLICY, **{field: value})
