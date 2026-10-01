"""Real native manager, synthetic PCM and requests; no installed transport changes."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from sds200.daemon_api import DaemonApiErrorCode, DaemonReadOnlyApi
from sds200.daemon_api import DaemonApiOperation as Op

from .test_daemon_api_recording import FakeRecordingManager, request
from .test_daemon_server import connect, make_server, read_line
from .test_supplemental_recording_owner import due, o, r
from .test_supplemental_recording_owner import native as native

NAME = "supplemental_recording_api"
SPEC = importlib.util.spec_from_file_location(NAME, Path(o.__file__).with_name(NAME + ".py"))
a = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = a
SPEC.loader.exec_module(a)

ENTRIES = ("payload", "json", "authorized", "control")
BLOCKED = tuple(operation for operation in Op if operation not in a.OBSERVATIONS)


def dispatch(api, entry, payload, **kwargs):
    if entry == "payload":
        return api.handle_payload(payload).to_json_line()
    if entry == "control":
        return api.handle_control_payload(payload).to_json_line()
    data = json.dumps(payload).encode()
    if entry == "json":
        return api.handle_json_line(data)
    return api.handle_authorized_json_line(
        data, allowed_operations=kwargs.pop("allowed_operations", tuple(Op)), **kwargs
    )


@pytest.mark.parametrize("entry", ENTRIES)
@pytest.mark.parametrize("operation", BLOCKED)
def test_every_entry_point_denies_mutation_even_with_a_full_peer_grant(native, entry, operation):
    api = a.FiniteRecordingApi(native.runtime, recording_manager=native.manager)
    result = json.loads(dispatch(api, entry, request(operation)))
    assert result["error"]["code"] == DaemonApiErrorCode.AUTHORIZATION_DENIED
    assert native.manager.snapshot().status == "idle"
    assert native.runtime.attach_calls == native.runtime.detach_calls == 0
    assert not list(native.journal.iterdir())


@pytest.mark.parametrize("entry", ("payload", "json", "authorized"))
@pytest.mark.parametrize("operation", (Op.HELLO, Op.CAPABILITIES))
def test_capabilities_truthfully_advertise_only_available_observations(native, entry, operation):
    api = a.FiniteRecordingApi(native.runtime, recording_manager=native.manager)
    result = json.loads(dispatch(api, entry, request(operation)))["result"]
    assert result["read_only"] is True and result["control_operations"] == []
    assert set(result["operations"]) <= {op.value for op in a.OBSERVATIONS}
    assert {Op.RECORDING_STATUS.value, Op.RECORDINGS_LIST.value} <= set(result["operations"])
    assert result["read_only_operations"] == result["operations"]
    assert native.runtime.attach_calls == 0


@pytest.mark.parametrize("operation", (Op.HELLO, Op.CAPABILITIES))
def test_remote_permissions_are_intersected_not_replaced(native, operation):
    api = a.FiniteRecordingApi(native.runtime, recording_manager=native.manager)
    permissions = (operation, Op.PING, Op.RECORDING_START)
    raw = dispatch(api, "authorized", request(operation), allowed_operations=permissions)
    assert set(json.loads(raw)["result"]["operations"]) == {operation.value, Op.PING.value}
    denied = dispatch(
        api, "authorized", request(Op.RECORDING_STATUS), allowed_operations=permissions
    )
    assert json.loads(denied)["error"]["code"] == DaemonApiErrorCode.AUTHORIZATION_DENIED
    empty = dispatch(
        api, "authorized", request(Op.RECORDING_STOP), allowed_operations=(Op.RECORDING_STOP,)
    )
    assert json.loads(empty)["error"]["code"] == DaemonApiErrorCode.AUTHORIZATION_DENIED


def test_native_owner_still_records_and_reads_are_truthful_and_redacted(native):
    api = a.FiniteRecordingApi(native.runtime, recording_manager=native.manager)
    owner = native.build()
    expected = owner.start()
    active = json.loads(dispatch(api, "json", request(Op.RECORDING_STATUS)))["result"]
    assert active["active"] is True and active["status"] == "recording"
    redacted = json.loads(
        dispatch(
            api,
            "authorized",
            request(Op.RECORDING_STATUS),
            allowed_operations=(Op.RECORDING_STATUS,),
            redacted_result_fields=("recording",),
        )
    )["result"]
    assert "recording" not in redacted and redacted["active"] is True
    native.runtime.router.submit_pcm(b"\x12\x34" * 160)
    for operation in (Op.RECORDING_START, Op.RECORDING_STOP):
        for entry in ENTRIES:
            assert json.loads(dispatch(api, entry, request(operation)))["error"]["code"] == (
                DaemonApiErrorCode.AUTHORIZATION_DENIED
            )
    due(native)
    stopped = owner.stop()
    observed = json.loads(dispatch(api, "json", request(Op.RECORDING_STATUS)))["result"]
    assert observed == native.manager.snapshot().as_dict() and not observed["active"]
    library = json.loads(dispatch(api, "json", request(Op.RECORDINGS_LIST)))["result"]
    assert library["total_entries"] >= 1
    assert (
        r.verify_finalized(
            native.baseline, expected, generation=native.plan.generation, stopped=stopped
        ).samples
        == 160
    )
    assert (
        native.runtime.running and native.runtime.attach_calls == native.runtime.detach_calls == 1
    )


def test_real_unix_clients_cannot_take_recording_ownership_or_reopen_a_closed_owner(
    native, tmp_path
):
    api = a.FiniteRecordingApi(native.runtime, recording_manager=native.manager)
    server, path = make_server(tmp_path, api=api, accept_poll_interval=0.01)
    owner = native.build()
    server.start()
    try:
        with connect(path) as reader, connect(path) as controller:

            def query(peer, operation):
                peer.sendall(json.dumps(request(operation)).encode() + b"\n")
                return json.loads(read_line(peer))

            assert query(reader, Op.RECORDING_STATUS)["result"]["status"] == "idle"
            expected = owner.start()
            assert query(reader, Op.RECORDING_STATUS)["result"]["active"] is True
            for operation in (Op.RECORDING_START, Op.RECORDING_STOP):
                assert query(controller, operation)["error"]["code"] == (
                    DaemonApiErrorCode.AUTHORIZATION_DENIED
                )
            native.runtime.router.submit_pcm(b"\x12\x34" * 160)
            due(native)
            stopped = owner.stop()
            owner.close()
            assert query(reader, Op.RECORDING_STATUS)["result"]["status"] == "stopped"
            assert query(reader, Op.RECORDINGS_LIST)["result"]["total_entries"] >= 1
            assert query(controller, Op.RECORDING_START)["error"]["code"] == (
                DaemonApiErrorCode.AUTHORIZATION_DENIED
            )
            assert (
                r.verify_finalized(
                    native.baseline, expected, generation=native.plan.generation, stopped=stopped
                ).samples
                == 160
            )
    finally:
        server.stop()
    assert not server.active and not path.exists()
    assert native.runtime.attach_calls == native.runtime.detach_calls == 1


@pytest.mark.parametrize("failure", ("start", "metadata"))
def test_failed_owner_state_is_visible_without_granting_client_retry(native, monkeypatch, failure):
    import sds200.daemon_recording as module

    api = a.FiniteRecordingApi(native.runtime, recording_manager=native.manager)
    owner = native.build()
    if failure == "start":
        native.runtime.attach_error = OSError("PRIVATE_START_FAILURE")
        with pytest.raises(o.UnconfirmedOwner):
            owner.start()
    else:
        owner.start()
        native.runtime.router.submit_pcm(b"\x12\x34" * 160)
        due(native)

        def fail(_metadata):
            raise OSError("PRIVATE_METADATA_FAILURE")

        with monkeypatch.context() as patch:
            patch.setattr(module, "write_recording_metadata", fail)
            with pytest.raises(o.UnconfirmedOwner):
                owner.stop()
    response = json.loads(dispatch(api, "json", request(Op.RECORDING_STATUS)))["result"]
    assert response == native.manager.snapshot().as_dict() and response["status"] == "failed"
    assert "PRIVATE" not in str(response["error"])
    assert owner.phase == "unconfirmed"
    before = (native.runtime.attach_calls, native.runtime.detach_calls)
    for operation in (Op.RECORDING_START, Op.RECORDING_STOP):
        assert json.loads(dispatch(api, "json", request(operation)))["error"]["code"] == (
            DaemonApiErrorCode.AUTHORIZATION_DENIED
        )
    assert (native.runtime.attach_calls, native.runtime.detach_calls) == before


@pytest.mark.parametrize("entry", ENTRIES)
@pytest.mark.parametrize("field", ("runtime", "recording_manager", "manager_runtime"))
def test_replaced_binding_fails_without_dispatch(native, entry, field, monkeypatch):
    api = a.FiniteRecordingApi(native.runtime, recording_manager=native.manager)
    if field == "manager_runtime":
        monkeypatch.setattr(native.manager, "runtime", object())
    else:
        monkeypatch.setattr(api, field, object())
    result = json.loads(dispatch(api, entry, request(Op.RECORDING_STATUS)))
    assert result["error"]["code"] == DaemonApiErrorCode.INTERNAL_ERROR
    assert "PRIVATE" not in json.dumps(result)
    assert native.runtime.attach_calls == 0


@pytest.mark.parametrize("fault", ("missing", "fake", "other_runtime"))
def test_constructor_requires_same_native_runtime_and_manager(native, fault):
    runtime, manager = native.runtime, native.manager
    if fault == "missing":
        manager = None
    elif fault == "fake":
        manager = FakeRecordingManager()
    else:
        runtime = object()
    with pytest.raises(ValueError, match="matching native manager"):
        a.FiniteRecordingApi(runtime, recording_manager=manager)
    assert native.runtime.attach_calls == 0


@pytest.mark.parametrize("entry", ENTRIES)
@pytest.mark.parametrize("payload", (None, [], {}, {"private": "PRIVATE"}))
def test_native_invalid_request_behavior_is_preserved(native, entry, payload):
    api = a.FiniteRecordingApi(native.runtime, recording_manager=native.manager)
    normal = DaemonReadOnlyApi(native.runtime, recording_manager=native.manager)
    assert dispatch(api, entry, payload) == dispatch(normal, entry, payload)


def test_restriction_is_not_installed_in_normal_product_or_existing_handoff():
    root = Path(a.__file__).parent.parent
    assert len(list((root / "scripts").glob("supplemental_handoff_*.py"))) == 14
    for path in (
        root / "src" / "sds200" / "cli.py",
        root / "scripts" / "accept_supplemental_daemon.py",
    ):
        assert "supplemental_recording_api" not in path.read_text()
    assert {op.value for op in a.OBSERVATIONS} == {
        "hello",
        "daemon.capabilities",
        "ping",
        "runtime.snapshot",
        "remote.clients",
        "display.profile",
        "display.frame",
        "display.supplemental.context",
        "display.supplemental.frame",
        "scanner.state",
        "audio.health",
        "recording.status",
        "recordings.list",
    }
