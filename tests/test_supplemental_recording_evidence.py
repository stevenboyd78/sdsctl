"""Offline content evidence only: local temporary files and synthetic RTP."""

import hashlib
import importlib.util
import io
import json
import os
import struct
import sys
import wave
from copy import deepcopy
from dataclasses import FrozenInstanceError, asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from .test_supplemental_handoff_files import f
from .test_supplemental_native_lifecycle import configured as configured
from .test_supplemental_native_lifecycle import demand, settled, wait_for
from .test_supplemental_native_lifecycle import rig as rig
from .test_supplemental_native_pcmu import network_rig as network_rig

NAME = "supplemental_recording_evidence"
SPEC = importlib.util.spec_from_file_location(
    NAME, Path(__file__).resolve().parents[1] / "scripts" / (NAME + ".py")
)
r = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = r
SPEC.loader.exec_module(r)

CASE = "c" * 32
GENERATION = "a" * 64
START = "2026-09-22T08:00:00-06:00"
STOP = "2026-09-22T08:00:01-06:00"
ENDPOINT = "rtsp://PRIVATE_SCANNER/au:scanner.au"
UNCONFIRMED = "^Recording artifact evidence is unconfirmed; preserve the case.$"


def wav_bytes(samples=160):
    stream = io.BytesIO()
    with wave.open(stream, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(8000)
        writer.writeframes(b"\x00\x00" * samples)
    return stream.getvalue()


def publish(case):
    case.wav.write_bytes(case.wav_bytes)
    case.sidecar.write_bytes(json.dumps(case.metadata, allow_nan=False).encode())


@pytest.fixture
def case(tmp_path):
    root = tmp_path / "PRIVATE_RECORDINGS"
    root.mkdir()
    (root / "previous.wav").write_bytes(wav_bytes())
    (root / "previous.wav.json").write_bytes(b'{"private": "older evidence"}')
    baseline = r.capture_baseline(root, CASE)
    name = r.filename(CASE, START)
    expected = r.RecordingExpectation(
        CASE, GENERATION, hashlib.sha256(ENDPOINT.encode()).hexdigest(), START
    )
    stopped = {
        "status": "stopped",
        "active": False,
        "recording": name,
        "metadata": name + ".json",
        "started_at": START,
        "stopped_at": STOP,
        "elapsed_seconds": 1.0,
        "packets": 1,
        "samples": 160,
        "audio_duration_seconds": 0.02,
        "reliability": dict.fromkeys(r.RELIABILITY, 0),
        "sink": dict.fromkeys(r.SINK, 0),
        "completed_recordings": 1,
        "closed": False,
        "error": None,
    }
    stopped["sink"].update(bytes_submitted=320, bytes_written=320)
    metadata = {
        "schema": "sds200.recording-metadata",
        "version": 1,
        "recording": {
            "file": name,
            "format": "wav",
            "sample_rate_hz": 8000,
            "channels": 1,
            "sample_width_bytes": 2,
        },
        "source": {"endpoint": ENDPOINT, "scanner": "PRIVATE_RADIO"},
        "boundaries": {
            "started": {"at": "2026-09-22T14:00:00+00:00", "state": {"channel": "PRIVATE_CHANNEL"}},
            "stopped": {"at": "2026-09-22T14:00:01+00:00", "state": {}},
        },
        "statistics": {
            key: deepcopy(stopped[key])
            for key in (
                "elapsed_seconds",
                "packets",
                "samples",
                "audio_duration_seconds",
                "reliability",
            )
        },
        "error": None,
    }
    value = SimpleNamespace(
        root=root,
        baseline=baseline,
        expected=expected,
        stopped=stopped,
        wav=root / name,
        sidecar=root / (name + ".json"),
        wav_bytes=wav_bytes(),
        metadata=metadata,
    )
    publish(value)
    return value


def verify(case, **kwargs):
    return r.verify_finalized(
        case.baseline, case.expected, generation=GENERATION, stopped=case.stopped, **kwargs
    )


def refuse(case):
    with pytest.raises(r.UnconfirmedRecording, match=UNCONFIRMED) as caught:
        verify(case)
    assert "PRIVATE" not in str(caught.value)
    assert caught.value.__suppress_context__


def test_exact_pair_retains_old_files_and_returns_redacted_frozen_content_proof(case):
    before = f.inventory(case.root)
    proof = verify(case)
    assert proof.samples == 160 and proof.packets == 1 and proof.audio_seconds == 0.02
    assert proof.case == CASE and proof.generation == GENERATION and proof.old_files == 2
    assert proof.wav_sha256 == hashlib.sha256(case.wav.read_bytes()).hexdigest()
    assert proof.metadata_sha256 == hashlib.sha256(case.sidecar.read_bytes()).hexdigest()
    assert f.inventory(case.root) == before
    assert "PRIVATE" not in json.dumps(asdict(proof))
    assert not any(key in asdict(proof) for key in ("trial_passed", "writer_exited", "audible"))
    with pytest.raises(FrozenInstanceError):
        proof.samples = 0


def test_already_closed_manager_and_equivalent_timezone_instants_allowed(case):
    case.stopped["closed"] = True
    case.stopped["started_at"] = "2026-09-22T14:00:00Z"
    assert verify(case).samples == 160


def test_consistent_old_transport_faults_do_not_claim_clean_or_audible_trial(case):
    case.stopped["reliability"]["packets_lost"] = 4
    case.metadata["statistics"]["reliability"]["packets_lost"] = 4
    publish(case)
    assert verify(case).samples == 160


def test_optional_scanner_name_may_be_absent(case):
    del case.metadata["source"]["scanner"]
    publish(case)
    assert verify(case).samples == 160


def test_exact_duration_and_sample_ceiling_is_not_an_off_by_one(case):
    case.wav_bytes = wav_bytes(180 * 8000)
    case.stopped.update(
        samples=180 * 8000,
        audio_duration_seconds=180.0,
        elapsed_seconds=180.0,
        stopped_at="2026-09-22T08:03:00-06:00",
    )
    case.stopped["sink"].update(bytes_submitted=180 * 16000, bytes_written=180 * 16000)
    case.metadata["statistics"].update(
        samples=180 * 8000, audio_duration_seconds=180.0, elapsed_seconds=180.0
    )
    case.metadata["boundaries"]["stopped"]["at"] = case.stopped["stopped_at"]
    publish(case)
    assert verify(case).audio_seconds == 180


@pytest.mark.parametrize("key", r.SNAPSHOT)
def test_every_stop_snapshot_field_required(case, key):
    del case.stopped[key]
    refuse(case)


@pytest.mark.parametrize(
    "key,value",
    [
        ("status", "recording"),
        ("status", "error"),
        ("active", True),
        ("active", 0),
        ("closed", 0),
        ("error", "PRIVATE_ERROR"),
        ("completed_recordings", 2),
        ("completed_recordings", True),
        ("recording", "other.wav"),
        ("metadata", "../PRIVATE.json"),
        ("started_at", STOP),
        ("stopped_at", START[:-6]),
        ("stopped_at", "2026-09-22T07:59:59-06:00"),
        ("samples", 0),
        ("packets", 0),
        ("packets", 161),
        ("samples", 8000 * 180 + 1),
        ("audio_duration_seconds", 1.0),
        ("elapsed_seconds", 0),
        ("elapsed_seconds", 181),
        ("elapsed_seconds", float("inf")),
        ("elapsed_seconds", float("nan")),
        ("elapsed_seconds", True),
        ("unexpected", "PRIVATE_EXTRA"),
    ],
)
def test_unfinished_mismatched_or_unbounded_snapshot_refused(case, key, value):
    case.stopped[key] = value
    refuse(case)


@pytest.mark.parametrize("value", [True, False, -1, 1.0, "1", None, float("nan"), 2**63])
def test_counters_are_not_coerced(case, value):
    case.stopped["samples"] = value
    refuse(case)


@pytest.mark.parametrize("key", r.SINK)
def test_every_unflushed_or_faulted_sink_counter_fails(case, key):
    case.stopped["sink"][key] += 1
    refuse(case)


@pytest.mark.parametrize("key", r.RELIABILITY)
@pytest.mark.parametrize("fault", ["absent", "mismatch", "boolean"])
def test_metadata_reliability_is_exact_not_missing_or_coerced(case, key, fault):
    values = case.metadata["statistics"]["reliability"]
    if fault == "absent":
        del values[key]
    else:
        values[key] = 1 if fault == "mismatch" else False
    publish(case)
    refuse(case)


@pytest.mark.parametrize("fault", ["absent", "mismatch", "boolean"])
def test_runtime_reliability_is_strict(case, fault):
    values = case.stopped["reliability"]
    if fault == "absent":
        del values["receive_errors"]
    else:
        values["receive_errors"] = 1 if fault == "mismatch" else False
    refuse(case)


@pytest.mark.parametrize(
    "path,value",
    [
        (("schema",), "other"),
        (("version",), True),
        (("version",), 2),
        (("error",), "PRIVATE_ERROR"),
        (("recording", "file"), "other.wav"),
        (("recording", "format"), "raw"),
        (("recording", "sample_rate_hz"), 16000),
        (("recording", "channels"), True),
        (("recording", "sample_width_bytes"), 2.0),
        (("source", "endpoint"), "PRIVATE_WRONG_ENDPOINT"),
        (("source", "scanner"), 8),
        (("source", "scanner"), "x" * 129),
        (("source", "extra"), "private"),
        (("boundaries", "started", "at"), STOP),
        (("boundaries", "stopped", "at"), START),
        (("boundaries", "started", "state", "mode"), False),
        (("boundaries", "started", "state", "unknown"), "private"),
        (("statistics", "samples"), 161),
        (("statistics", "packets"), True),
        (("statistics", "elapsed_seconds"), 2),
        (("statistics", "audio_duration_seconds"), 0.021),
        (("statistics", "extra"), "private"),
        (("extra",), "private"),
    ],
)
def test_metadata_must_match_writer_schema_and_bound_receipt(case, path, value):
    target = case.metadata
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    publish(case)
    refuse(case)


@pytest.mark.parametrize(
    "key", ["schema", "version", "recording", "source", "boundaries", "statistics", "error"]
)
def test_required_metadata_sections_cannot_be_missing(case, key):
    del case.metadata[key]
    publish(case)
    refuse(case)


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"PRIVATE malformed",
        b"\xff",
        b"[]",
        b"null",
        b'{"schema":NaN}',
        b'{"schema":Infinity}',
        b'{"schema":1,"schema":2}',
        b'{"nested":{"at":1,"at":2}}',
        b"[" * 1100 + b"]" * 1100,
        b" " * (65536 + 1),
    ],
)
def test_malformed_ambiguous_or_oversize_json_refused_privately(case, payload):
    case.sidecar.write_bytes(payload)
    refuse(case)


@pytest.mark.parametrize("key,value", [("version", "1"), ("channels", "1"), ("packets", "1")])
def test_duplicate_keys_in_otherwise_valid_metadata_do_not_pass(case, key, value):
    raw = case.sidecar.read_text()
    entry = f'"{key}": {value}'
    assert entry in raw
    case.sidecar.write_text(raw.replace(entry, entry + ", " + entry, 1))
    refuse(case)


@pytest.mark.parametrize("fault", ["empty", "header", "truncated", "trailing", "max_size"])
def test_incomplete_or_extra_wav_bytes_refused(case, fault):
    raw = case.wav_bytes
    values = {
        "empty": b"",
        "header": raw[:43],
        "truncated": raw[:-1],
        "trailing": raw + b"PRIVATE",
        "max_size": b"\x00" * (r.MAX_WAV_BYTES + 1),
    }
    case.wav.write_bytes(values[fault])
    refuse(case)


@pytest.mark.parametrize(
    "offset,value,fmt",
    [
        (0, b"RIFX", "4s"),
        (4, 0, "I"),
        (8, b"XXXX", "4s"),
        (12, b"JUNK", "4s"),
        (16, 18, "I"),
        (20, 7, "H"),
        (22, 2, "H"),
        (24, 16000, "I"),
        (28, 32000, "I"),
        (32, 4, "H"),
        (34, 8, "H"),
        (36, b"JUNK", "4s"),
        (40, 0, "I"),
    ],
)
def test_each_canonical_wav_header_field_checked(case, offset, value, fmt):
    raw = bytearray(case.wav_bytes)
    struct.pack_into("<" + fmt, raw, offset, value)
    case.wav.write_bytes(raw)
    refuse(case)


@pytest.mark.parametrize(
    "fault", ["change", "remove", "rename", "chmod", "extra", "temporary", "second_recording"]
)
def test_preserves_all_old_files_and_permits_only_one_exact_pair(case, fault):
    old = case.root / "previous.wav"
    if fault == "change":
        old.write_bytes(b"PRIVATE_REPLACEMENT")
    elif fault == "remove":
        old.unlink()
    elif fault == "rename":
        old.rename(case.root / "other.wav")
    elif fault == "chmod":
        old.chmod(0o400)
    else:
        target = {
            "extra": "extra.json",
            "temporary": ".metadata.tmp",
            "second_recording": r.filename("d" * 32, START),
        }[fault]
        (case.root / target).write_bytes(b"private")
    before = {str(path): path.read_bytes() for path in case.root.iterdir()}
    refuse(case)
    assert {str(path): path.read_bytes() for path in case.root.iterdir()} == before


@pytest.mark.parametrize("target", ["wav", "sidecar"])
@pytest.mark.parametrize("fault", ["missing", "symlink", "hardlink", "fifo", "directory"])
def test_expected_pair_never_follows_unsafe_file_objects(case, target, fault):
    path = getattr(case, target)
    content = path.read_bytes()
    path.unlink()
    if fault in ("symlink", "hardlink"):
        other = case.root.parent / "PRIVATE_OTHER"
        other.write_bytes(content)
        if fault == "symlink":
            path.symlink_to(other)
        else:
            path.hardlink_to(other)
    elif fault == "fifo":
        os.mkfifo(path)
    elif fault == "directory":
        path.mkdir()
    refuse(case)


def test_no_existing_reserved_file_is_accepted_as_a_new_baseline(case):
    with pytest.raises(r.UnconfirmedRecording, match="baseline is unconfirmed"):
        r.capture_baseline(case.root, CASE)


@pytest.mark.parametrize("suffix", ["-other.wav", "-other.wav.json", "-other-2.wav"])
def test_any_previous_case_artifact_refuses_baseline_even_in_subdirectory(tmp_path, suffix):
    root = tmp_path / "recordings"
    (root / "older").mkdir(parents=True)
    (root / "older" / ("sdsctl-acceptance-" + CASE + suffix)).write_bytes(b"preserve")
    with pytest.raises(r.UnconfirmedRecording):
        r.capture_baseline(root, CASE)


def test_filename_uses_original_start_zone_not_utc_or_current_clock():
    assert r.filename(CASE, START) == f"sdsctl-acceptance-{CASE}-20260922-080000.wav"
    assert r.filename(CASE, "2026-09-22T14:00:00Z").endswith("-20260922-140000.wav")


def test_collision_suffix_cannot_be_promoted_to_the_expected_recording(case):
    name = case.wav.stem + "-2.wav"
    case.wav.rename(case.root / name)
    case.sidecar.rename(case.root / (name + ".json"))
    case.stopped.update(recording=name, metadata=name + ".json")
    refuse(case)


@pytest.mark.parametrize("path", [Path("/"), Path("relative"), Path("/tmp/../tmp"), "/tmp"])
def test_baseline_requires_explicit_safe_existing_root(path):
    with pytest.raises(r.UnconfirmedRecording):
        r.capture_baseline(path, CASE)


@pytest.mark.parametrize("kind", ["root", "ancestor", "replaced_root"])
def test_root_identity_and_no_follow_ancestry(case, kind):
    original = case.root
    if kind == "replaced_root":
        original.rename(original.parent / "moved")
        original.mkdir()
        for path in (original.parent / "moved").iterdir():
            (original / path.name).write_bytes(path.read_bytes())
    else:
        alias = original.parent / "link"
        alias.symlink_to(original if kind == "root" else original.parent)
        case.baseline = replace(
            case.baseline, root=alias if kind == "root" else alias / original.name
        )
    refuse(case)


@pytest.mark.parametrize(
    "field,value",
    [
        ("case", "d" * 32),
        ("files", []),
        ("root_identity", (1,)),
        ("root_identity", (True,) * 6),
    ],
)
def test_malformed_or_wrong_baseline_cannot_authorize_pair(case, field, value):
    case.baseline = replace(case.baseline, **{field: value})
    refuse(case)


def test_duplicate_baseline_files_refused(case):
    case.baseline = replace(case.baseline, files=case.baseline.files * 2)
    refuse(case)


@pytest.mark.parametrize(
    "field,value",
    [
        ("case", "../PRIVATE"),
        ("case", "C" * 32),
        ("generation", "bad"),
        ("audio_endpoint_sha256", "PRIVATE_ENDPOINT"),
        ("started_at", "2026-09-22"),
        ("started_at", "PRIVATE_INVALID_TIME"),
        ("started_at", None),
    ],
)
def test_expectation_requires_case_hashes_and_aware_clock(case, field, value):
    with pytest.raises(r.UnconfirmedRecording) as caught:
        replace(case.expected, **{field: value})
    assert "PRIVATE" not in str(caught.value)


def test_wrong_generation_cannot_reuse_otherwise_valid_artifacts(case):
    with pytest.raises(r.UnconfirmedRecording, match=UNCONFIRMED):
        r.verify_finalized(case.baseline, case.expected, generation="b" * 64, stopped=case.stopped)


@pytest.mark.parametrize("fault", ["change_old", "change_pair", "root_replace", "extra"])
def test_mutation_between_final_observations_is_refused(case, monkeypatch, fault):
    original = r._validate_payloads

    def mutation(*args):
        result = original(*args)
        if fault == "change_old":
            (case.root / "previous.wav.json").write_bytes(b"PRIVATE_CHANGED")
        elif fault == "change_pair":
            case.wav.write_bytes(case.wav_bytes[:-1] + b"\x01")
        elif fault == "root_replace":
            case.root.rename(case.root.parent / "moved")
            case.root.mkdir()
        else:
            (case.root / "extra").write_bytes(b"private")
        return result

    monkeypatch.setattr(r, "_validate_payloads", mutation)
    refuse(case)


@pytest.mark.parametrize("fault", ["replace", "grow", "shrink", "chmod", "link"])
def test_mutation_during_bounded_payload_read_fails(case, monkeypatch, fault):
    # Inject after the inventory hash, in the separate payload read itself.
    original_read_bytes, original_read = r.read_bytes, os.read
    changed = False

    def reading(fd, size):
        nonlocal changed
        raw = original_read(fd, size)
        if not changed and os.fstat(fd).st_ino == case.wav.stat().st_ino:
            changed = True
            if fault == "replace":
                case.wav.unlink()
                case.wav.write_bytes(case.wav_bytes)
            elif fault in ("grow", "shrink"):
                case.wav.write_bytes(case.wav_bytes + b"extra" if fault == "grow" else b"short")
            elif fault == "chmod":
                case.wav.chmod(0o400)
            else:
                (case.root / "alias").hardlink_to(case.wav)
        return raw

    def read_bytes(*args, **kwargs):
        with monkeypatch.context() as patch:
            patch.setattr(os, "read", reading)
            return original_read_bytes(*args, **kwargs)

    monkeypatch.setattr(r, "read_bytes", read_bytes)
    refuse(case)
    assert changed


@pytest.mark.parametrize("target", ["inventory", "read_bytes", "fstat"])
def test_private_failures_are_sanitized_and_all_descriptors_closed(case, monkeypatch, target):
    before = len(list(Path("/proc/self/fd").iterdir()))

    def fail(*_args, **_kwargs):
        raise OSError("PRIVATE_EXCEPTION_TEXT")

    monkeypatch.setattr(os if target == "fstat" else r, target, fail)
    refuse(case)
    assert len(list(Path("/proc/self/fd").iterdir())) == before


@pytest.mark.parametrize(
    "limit,value", [("MAX_SECONDS", -1), ("MAX_METADATA_BYTES", 4), ("MAX_WAV_BYTES", 43)]
)
def test_bounded_checks_never_return_partial_success(case, monkeypatch, limit, value):
    monkeypatch.setattr(r, limit, value)
    refuse(case)


def test_offline_component_is_not_added_to_sealed_handoff_bundle():
    source = Path(r.__file__).read_text()
    assert "import socket" not in source and "subprocess" not in source
    assert "from sds200" not in source and "start_recording(" not in source
    assert len(list(Path(r.__file__).parent.glob("supplemental_handoff_*.py"))) == 14


def test_actual_recording_manager_finalizes_shared_localhost_pcm_with_older_files_intact(
    network_rig, tmp_path
):
    from sds200.daemon_recording import DaemonRecordingManager

    from .test_network_audio import make_rtp

    media = network_rig
    root = tmp_path / "recordings"
    root.mkdir()
    (root / "older.wav").write_bytes(wav_bytes())
    (root / "older.wav.json").write_bytes(b'{"preserve":"exact old content"}')
    baseline = r.capture_baseline(root, CASE)
    manager = DaemonRecordingManager(media.rig.runtime, root, template=r.template(CASE))
    try:
        started = manager.start_recording().as_dict()
        endpoint = media.rig.runtime.snapshot().as_dict()["audio"]["endpoint"]
        expected = r.RecordingExpectation(
            CASE, GENERATION, hashlib.sha256(endpoint.encode()).hexdigest(), started["started_at"]
        )
        for count in range(1, 5):
            media.peer.sendto(
                make_rtp(b"\xff" * 160, sequence=count, timestamp=count * 160),
                ("127.0.0.1", media.rtsp.started_ports[0]),
            )
            wait_for(lambda count=count: manager.snapshot().samples == count * 160)
        stopped = manager.stop_recording().as_dict()
        proof = r.verify_finalized(baseline, expected, generation=GENERATION, stopped=stopped)
        assert proof.samples == 640 and proof.packets == 4 and proof.old_files == 2
        assert (root / r.filename(CASE, started["started_at"])).read_bytes() == wav_bytes(640)
        assert len(media.rtsp.started_ports) == 1
        assert media.rig.peer.reads == []
        assert media.rig.runtime.running
    finally:
        manager.close()


@pytest.mark.parametrize("command", ["FQK", "DTM"])
@pytest.mark.parametrize("reply", [True, False])
def test_final_proof_with_concurrent_pcmu_consumer_and_native_read_or_timeout(
    network_rig, tmp_path, command, reply
):
    from sds200.audio_recording import decode_mulaw
    from sds200.daemon_ipc import DaemonSocketListener, DaemonSocketLocation, DaemonSocketSource
    from sds200.daemon_pcmu_client import DaemonPcmuClient
    from sds200.daemon_pcmu_server import DaemonPcmuServer
    from sds200.daemon_recording import DaemonRecordingManager
    from sds200.pcmu_stream import PcmuStream

    from .test_network_audio import make_rtp

    media = network_rig
    native = media.rig
    root = tmp_path / "recordings"
    root.mkdir()
    baseline = r.capture_baseline(root, CASE)
    stream = PcmuStream(media.transport)
    location = DaemonSocketLocation(tmp_path / "p.sock", DaemonSocketSource.EXPLICIT)
    server = DaemonPcmuServer(DaemonSocketListener(location), stream, accept_poll_interval=0.01)
    client = DaemonPcmuClient(location, timeout=1)
    manager = DaemonRecordingManager(native.runtime, root, template=r.template(CASE))
    try:
        server.start()
        client.connect().settimeout(1)
        wait_for(lambda: server.connected_clients == 1 and stream.subscriber_count == 1)
        started = manager.start_recording().as_dict()
        endpoint = native.runtime.snapshot().as_dict()["audio"]["endpoint"]
        expected = r.RecordingExpectation(
            CASE, GENERATION, hashlib.sha256(endpoint.encode()).hexdigest(), started["started_at"]
        )
        with native.peer.hold(command) as gate:
            demand(native, command)
            assert gate.entered.wait(1)
            payload = bytes(range(160))
            for index in range(8):
                media.peer.sendto(
                    make_rtp(payload, sequence=100 + index, timestamp=1000 + index * 160),
                    ("127.0.0.1", media.rtsp.started_ports[0]),
                )
            delivered = [client.receive() for _ in range(8)]
            wait_for(lambda: manager.snapshot().samples == 1280)
            assert all(packet.packet.payload == payload for packet in delivered)
            if reply:
                gate.release.set()
            settled(native)
            assert native.cache.snapshot().blocked_until_reconnect == (None if reply else "timeout")
        stopped = manager.stop_recording().as_dict()
        proof = r.verify_finalized(baseline, expected, generation=GENERATION, stopped=stopped)
        assert proof.samples == 1280 and proof.packets == 8 and proof.old_files == 0
        assert all(value == 0 for value in stopped["reliability"].values())
        with wave.open(str(root / stopped["recording"]), "rb") as recording:
            assert recording.readframes(1280) == decode_mulaw(payload) * 8
        # A later transport fault belongs to the surviving stream, not this WAV.
        media.peer.sendto(
            make_rtp(payload, sequence=109, timestamp=2440),
            ("127.0.0.1", media.rtsp.started_ports[0]),
        )
        assert client.receive().packet.payload == payload
        wait_for(lambda: media.transport.statistics.packets_lost == 1)
        assert (
            r.verify_finalized(
                baseline, expected, generation=GENERATION, stopped=manager.snapshot().as_dict()
            )
            == proof
        )
        assert native.peer.reads == (["FQK"] if command == "FQK" else ["FQK", "DTM"])
        assert len(media.rtsp.started_ports) == 1 and media.transport.running
    finally:
        client.close()
        manager.close()
        server.stop()
