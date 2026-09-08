"""Guarded host switches with synthetic native clients, not real browser acceptance."""

from __future__ import annotations

import json
import os
import selectors
import shutil
import signal
import subprocess
import sys
from contextlib import suppress
from dataclasses import asdict

import pytest

from sds200 import browser_device_handoff as handoff
from sds200.browser_device_bundle import NATIVE_HOST
from sds200.browser_device_handoff import BrowserHandoffError, BrowserRecoveryHandoff
from sds200.browser_device_native import BrowserRetirementSelection
from sds200.browser_device_profile_access import BrowserProfileAccessError, browser_profile_access
from sds200.browser_device_protocol import (
    BrowserDeviceProtocolError,
    BrowserRetirementAcknowledgement,
    parse_browser_device_request,
)
from sds200.browser_device_recovery import ExchangeFailure, RecoveryMode
from sds200.browser_device_registration import MAINTENANCE_MARKER
from sds200.browser_device_retirement_bundle import prepare_browser_retirement_bundle
from sds200.browser_device_startup import BrowserStartupError, _launch_lock, check_browser_startup
from tests.test_browser_device_bundle import profile as profile
from tests.test_browser_device_bundle import public_key as public_key
from tests.test_browser_device_native import certificates as certificates
from tests.test_browser_device_profile import CREDENTIAL, private, snapshot
from tests.test_browser_device_registration import source as source
from tests.test_browser_device_resume import INTENT
from tests.test_browser_device_resume_workflow import lab as lab
from tests.test_browser_device_retirement_bundle import JOINED, committed, invoke
from tests.test_browser_device_startup import inputs as inputs

pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or os.geteuid() == 0, reason="Non-root Linux guarded handoff",
)
FAILURE = {"version": 1, "ok": False, "mode": "setup_error"}
ACKNOWLEDGED = {"version": 1, "ok": True, "mode": "retired_paused", "acknowledged": True}


def snap(lab):
    # Include nested bundle/native-host/archive files, not just root entries.
    return {name: {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mtime_ns)
                   for p in root.rglob("*") if p.is_file()}
            for name, root in lab.args.items() if name != "public_key"}


def prepared(lab, tmp_path, kind="reconcile"):
    args, proof = committed(lab, kind)
    root, bundle = tmp_path / "guarded handoff's $(literal)", tmp_path / "recovery bundle"
    key = prepare_browser_retirement_bundle(bundle, **args, handoff=root)
    values = {**args, "recovery_bundle": bundle}
    return root, bundle, key, proof, lambda: BrowserRecoveryHandoff(root, **values), values


def acknowledge(bundle, lab, proof):
    return invoke(bundle, lab.configuration,
                  {"version": 1, "action": "acknowledge-retirement", **asdict(proof)})


@pytest.mark.parametrize("kind", ["retire", "reconcile"])
@pytest.mark.parametrize("mode", [m for m in RecoveryMode if m is not RecoveryMode.ACTIVE])
def test_exact_switch_ack_readonly_confirmation_and_restoration(lab, tmp_path, kind, mode):
    if mode is not RecoveryMode.PAUSED:
        lab.ledger.resume(lab.ledger.inspect().revision)

        def fail():
            raise ExchangeFailure(mode)

        lab.ledger.authenticate(fail)
    root, bundle, key, proof, make, _ = prepared(lab, tmp_path, kind)
    before = snap(lab)
    assert invoke(bundle, lab.configuration) == FAILURE  # No handoff/live owner yet.
    hosts = lab.args["directory"] / "NativeMessagingHosts"
    host = hosts / (NATIVE_HOST + ".json")
    original = host.read_bytes()

    def callback():
        assert host.read_bytes() == (bundle / host.name).read_bytes()
        assert {p.name for p in hosts.iterdir()} == {host.name}
        assert (root / "native-host.before.json").read_bytes() == original
        assert (root / "guard.before.json").read_bytes() == (
            before["directory"][MAINTENANCE_MARKER][0])
        with pytest.raises(BlockingIOError), _launch_lock(lab.args["directory"]):
            pytest.fail("A second managed launcher acquired ownership")
        with pytest.raises(BrowserProfileAccessError), browser_profile_access(
                lab.args["profile"], exclusive=True):
            pytest.fail("A participating credential writer acquired ownership")
        assert invoke(bundle, lab.configuration) == {
            "version": 1, "ok": True, "evidence": asdict(proof)}
        if mode is RecoveryMode.PAUSED and kind == "reconcile":
            for action in ("status", "suspend", "authenticate", "claim-browser", "review-resume"):
                assert invoke(bundle, lab.configuration,
                              {"version": 1, "action": action}) == FAILURE
        assert acknowledge(bundle, lab, proof) == ACKNOWLEDGED
        ack = snapshot(root)
        assert acknowledge(bundle, lab, proof) == FAILURE  # No duplicate writes/replay.
        assert snapshot(root) == ack

    result = make().activate(callback)
    assert result.mode == mode and result.revision == proof.revision
    assert CREDENTIAL not in repr(result) and INTENT not in repr(result)
    after = snap(lab), snapshot(root)
    assert make().confirm() == result
    assert (snap(lab), snapshot(root)) == after
    assert invoke(bundle, lab.configuration) == FAILURE  # Stale live-owner record is not a lease.
    with pytest.raises(BrowserStartupError):
        check_browser_startup(**lab.inputs)
    assert make().restore() == result
    restored = snap(lab), snapshot(root)
    assert host.read_bytes() == original
    assert make().confirm(restored=True) == result
    assert (snap(lab), snapshot(root)) == restored
    with pytest.raises(BrowserHandoffError):
        make().restore()
    assert (snap(lab), snapshot(root)) == restored
    assert lab.make().confirm(operation_id=result.operation_id, browser_intent=INTENT) == proof
    assert snap(lab)["profile"] == before["profile"]
    assert snap(lab)["bundle"] == before["bundle"]
    assert snap(lab)["archives"] == before["archives"]
    assert {p: value for p, value in snap(lab)["directory"].items()
            if p != "NativeMessagingHosts/" + host.name} == {
        p: value for p, value in before["directory"].items()
        if p != "NativeMessagingHosts/" + host.name}
    with pytest.raises(BrowserStartupError):
        check_browser_startup(**lab.inputs)  # Even verified restoration never removes the guard.


@pytest.mark.parametrize("result", [None, 0, True, "acknowledged"])
def test_callback_return_or_clean_exit_is_never_browser_ack(lab, tmp_path, result):
    root, bundle, _, _, make, _ = prepared(lab, tmp_path)
    before = snap(lab)
    with pytest.raises(BrowserHandoffError):
        make().activate(lambda: result)
    assert not (root / handoff._ACK).exists()
    assert invoke(bundle, lab.configuration) == FAILURE
    after = snap(lab), snapshot(root)
    for method in (lambda: make().activate(lambda: None), lambda: make().confirm(),
                   lambda: make().restore()):
        with pytest.raises(BrowserHandoffError):
            method()
        assert (snap(lab), snapshot(root)) == after
    assert snap(lab)["profile"] == before["profile"]


@pytest.mark.parametrize("change", ["guard", "normal-bundle", "recovery-bundle", "receipt",
    "extra-host", "owner-replaced", "owner-mode", "owner-hardlink", "operation", "proof",
    "ready", "switch-started", "restoration-started", "singleton"])
def test_changed_live_context_refuses_ack_and_preserves_changes(lab, tmp_path, change):
    root, bundle, _, proof, make, _ = prepared(lab, tmp_path)
    changed = []

    def callback():
        directory = lab.args["directory"]
        if change == "guard":
            private(directory / MAINTENANCE_MARKER, b"changed")
        elif change == "normal-bundle":
            private(lab.args["bundle"] / "extension/worker.mjs", b"changed")
        elif change == "recovery-bundle":
            private(bundle / "extension/worker.mjs", b"changed")
        elif change == "receipt":
            private(directory / ".sdsctl-browser-registration.json", b"changed")
        elif change == "extra-host":
            private(directory / "NativeMessagingHosts/other.json", b"{}")
        elif change == "owner-replaced":
            (root / handoff._OWNER).rename(root / "old-owner.lock")
            private(root / handoff._OWNER, b"")
        elif change == "owner-mode":
            (root / handoff._OWNER).chmod(0o644)
        elif change == "owner-hardlink":
            os.link(root / handoff._OWNER, root / "hardlink.lock")
        elif change == "operation":
            private(root / "operation.json", b"{}")
        elif change == "proof":
            # Deliberate uncoordinated low-level mutation, outside the SH owner.
            lab.ledger.resume(lab.ledger.inspect().revision)
        elif change == "singleton":
            (directory / "SingletonLock").symlink_to("keep-browser-marker")
        else:
            private(root / (change + ".json"), b"changed")
        changed.append((snap(lab), snapshot(root)))
        if change == "singleton":
            # While the actual owner holds launcher/lease locks, browser-owned
            # Singleton markers are expected and do not disable the native pipe.
            assert acknowledge(bundle, lab, proof) == ACKNOWLEDGED
        else:
            assert acknowledge(bundle, lab, proof) == FAILURE
            assert (snap(lab), snapshot(root)) == changed[0]

    with pytest.raises(BrowserHandoffError):
        make().activate(callback)
    if change != "singleton":
        assert not (root / handoff._ACK).exists()
    else:
        assert (lab.args["directory"] / "SingletonLock").is_symlink()
    with pytest.raises(BrowserHandoffError):
        make().confirm()


@pytest.mark.parametrize("stage", ["backup", "switch-started", "missing-host", "replacement",
                                  "ready", "ack", "callback"])
def test_uncertain_activation_is_never_replayed(lab, tmp_path, monkeypatch, stage):
    root, bundle, _, proof, make, _ = prepared(lab, tmp_path)
    original_write, original_switch = handoff._write_file, BrowserRecoveryHandoff._switch_host

    def write(directory, name, body):
        original_write(directory, name, body)
        if name == {"backup": "native-host.before.json", "switch-started": "switch-started.json",
                    "ready": "ready.json"}.get(stage):
            raise OSError("private diagnostic")

    def switch(instance, before, after):
        if stage == "missing-host":
            (lab.args["directory"] / "NativeMessagingHosts" / handoff._HOST).unlink()
        else:
            original_switch(instance, before, after)
        if stage in {"missing-host", "replacement"}:
            raise OSError("private diagnostic")

    monkeypatch.setattr(handoff, "_write_file", write)
    monkeypatch.setattr(BrowserRecoveryHandoff, "_switch_host", switch)

    def callback():
        if stage == "ack":
            assert acknowledge(bundle, lab, proof) == ACKNOWLEDGED
        raise OSError("private diagnostic")

    with pytest.raises(BrowserHandoffError) as caught:
        make().activate(callback)
    assert "private diagnostic" not in str(caught.value)
    before = snap(lab), snapshot(root)
    with pytest.raises(BrowserHandoffError):
        make().activate(callback)
    if stage == "ack":
        assert make().confirm().identity == proof.identity
    else:
        with pytest.raises(BrowserHandoffError):
            make().confirm()
    assert (snap(lab), snapshot(root)) == before


@pytest.mark.parametrize("stage", ["journal", "missing-host", "replacement", "completed"])
def test_restoration_failure_retains_guard_and_only_completed_result_confirms(
        lab, tmp_path, monkeypatch, stage):
    root, bundle, _, proof, make, _ = prepared(lab, tmp_path)
    result = make().activate(lambda: acknowledge(bundle, lab, proof))
    original_write, original_switch = handoff._write_file, BrowserRecoveryHandoff._switch_host

    def write(directory, name, body):
        original_write(directory, name, body)
        if (stage in {"journal", "completed"}
                and name == ("restored.json" if stage == "completed"
                             else "restoration-started.json")):
            raise OSError()

    def switch(instance, before, after):
        if stage == "missing-host":
            (lab.args["directory"] / "NativeMessagingHosts" / handoff._HOST).unlink()
        else:
            original_switch(instance, before, after)
        if stage in {"missing-host", "replacement"}:
            raise OSError()

    monkeypatch.setattr(handoff, "_write_file", write)
    monkeypatch.setattr(BrowserRecoveryHandoff, "_switch_host", switch)
    with pytest.raises(BrowserHandoffError):
        make().restore()
    before = snap(lab), snapshot(root)
    with pytest.raises(BrowserHandoffError):
        make().restore()
    if stage == "completed":
        assert make().confirm(restored=True) == result
    else:
        with pytest.raises(BrowserHandoffError):
            make().confirm(restored=True)
    assert (snap(lab), snapshot(root)) == before
    with pytest.raises(BrowserStartupError):
        check_browser_startup(**lab.inputs)


@pytest.mark.parametrize("field,value", [
    ("identity", True), ("intent", "x"), ("retirement", "f" * 64 + "\n"),
    ("revision", True), ("revision", 0), ("revision", 2**53 - 1), ("mode", "active"),
    ("mode", None), ("operation_id", "f" * 64), ("archive", "/tmp"), ("paused", True),
])
def test_acknowledgement_parser_refuses_unbound_or_aliased_fields(field, value):
    request = {"version": 1, "action": "acknowledge-retirement", "identity": "a" * 64,
               "intent": "b" * 64, "retirement": "c" * 64, "mode": "paused", "revision": 2}
    with pytest.raises(BrowserDeviceProtocolError):
        parse_browser_device_request(json.dumps({**request, field: value}).encode())
    parsed = parse_browser_device_request(json.dumps(request).encode())
    assert isinstance(parsed, BrowserRetirementAcknowledgement)
    assert "a" * 64 not in repr(parsed)


@pytest.mark.parametrize("field", ["identity", "intent", "retirement", "mode", "revision"])
def test_live_ack_must_match_exact_current_proof(lab, tmp_path, field):
    root, bundle, _, proof, make, _ = prepared(lab, tmp_path)

    def callback():
        body = {"version": 1, "action": "acknowledge-retirement", **asdict(proof)}
        body[field] = "e" * 64 if field in {"identity", "intent", "retirement"} else (
            "tls_error" if field == "mode" else proof.revision + 1)
        before = snap(lab), snapshot(root)
        assert invoke(bundle, lab.configuration, body) == FAILURE
        assert (snap(lab), snapshot(root)) == before

    with pytest.raises(BrowserHandoffError):
        make().activate(callback)
    assert not (root / handoff._ACK).exists()


def test_normal_and_unselected_confirmation_wrappers_never_record_ack(lab, tmp_path):
    args, proof = committed(lab)
    bundle = tmp_path / "unselected-confirmation"
    prepare_browser_retirement_bundle(bundle, **args)
    before = snap(lab)
    assert acknowledge(lab.args["bundle"], lab, proof) == FAILURE
    assert acknowledge(bundle, lab, proof) == FAILURE
    assert snap(lab) == before


@pytest.mark.parametrize("stage", ["partial", "complete-lost-reply"])
def test_uncertain_ack_write_is_preserved_and_only_exact_complete_receipt_confirms(
        lab, tmp_path, monkeypatch, stage):
    root, _, _, proof, make, args = prepared(lab, tmp_path)
    original = handoff._write_file

    def write(directory, name, body):
        if name == handoff._ACK:
            original(directory, name, body[:20] if stage == "partial" else body)
            raise OSError("private diagnostic")
        original(directory, name, body)

    monkeypatch.setattr(handoff, "_write_file", write)

    def callback():
        handoff.handoff_native_request(lab.args["profile"], BrowserRetirementSelection(
            lab.args["archives"], args["operation_id"], root),
            BrowserRetirementAcknowledgement(**asdict(proof)))

    with pytest.raises(BrowserHandoffError):
        make().activate(callback)
    before = snap(lab), snapshot(root)
    if stage == "partial":
        with pytest.raises(BrowserHandoffError):
            make().confirm()
    else:
        assert make().confirm().identity == proof.identity
    assert (snap(lab), snapshot(root)) == before


@pytest.mark.parametrize("change", ["existing", "relative", "nested", "symlink", "busy",
                                   "guard", "wrong-operation", "unselected-bundle"])
def test_unsafe_handoff_never_changes_registration(lab, tmp_path, change):
    root, bundle, _, _, make, args = prepared(lab, tmp_path)
    if change == "existing":
        root.mkdir(mode=0o700)
        private(root / "keep", b"unrelated")
    elif change == "relative":
        root = root.relative_to(root.anchor)
    elif change == "nested":
        root = lab.args["directory"] / "nested"
    elif change == "symlink":
        root.symlink_to(bundle, target_is_directory=True)
    elif change == "guard":
        private(lab.args["directory"] / MAINTENANCE_MARKER, b"unrelated")
    elif change == "wrong-operation":
        args["operation_id"] = "a" * 64
    elif change == "unselected-bundle":
        other = tmp_path / "no-handoff-bundle"
        prepare_browser_retirement_bundle(other, **{k: v for k, v in args.items()
                                                   if k != "recovery_bundle"})
        args["recovery_bundle"] = other
    before = snap(lab)
    if change == "busy":
        with _launch_lock(lab.args["directory"]), pytest.raises(BrowserHandoffError):
            make().activate(lambda: pytest.fail("Unsafe context reached callback"))
    else:
        with pytest.raises(BrowserHandoffError):
            BrowserRecoveryHandoff(root, **args).activate(
                lambda: pytest.fail("Unsafe context reached callback"))
    assert snap(lab) == before


@pytest.mark.parametrize("change", ["missing", "partial", "symlink", "hardlink", "mode", "proof"])
def test_lost_ack_confirmation_cannot_bless_edited_receipts(lab, tmp_path, change):
    root, bundle, _, proof, make, _ = prepared(lab, tmp_path)
    make().activate(lambda: acknowledge(bundle, lab, proof))
    ack = root / handoff._ACK
    if change == "missing":
        ack.unlink()
    elif change == "partial":
        private(ack, b"{}")
    elif change == "symlink":
        target = tmp_path / "ack"
        ack.rename(target)
        ack.symlink_to(target)
    elif change == "hardlink":
        os.link(ack, tmp_path / "ack")
    elif change == "mode":
        ack.chmod(0o644)
    else:
        lab.ledger.resume(lab.ledger.inspect().revision)
    before = snap(lab), snapshot(root)
    with pytest.raises(BrowserHandoffError):
        make().confirm()
    with pytest.raises(BrowserHandoffError):
        make().restore()
    assert (snap(lab), snapshot(root)) == before


@pytest.mark.parametrize("kind", ["retire", "reconcile"])
@pytest.mark.parametrize("failure", ["none", "lost-save", "incorrect-save", "lost-ack"])
def test_generated_page_worker_native_ack_and_stopped_confirmation_joined(
        lab, tmp_path, kind, failure):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node generated handoff pipeline")
    root, bundle, key, _, make, _ = prepared(lab, tmp_path, kind)
    before = snap(lab)

    def callback():
        result = subprocess.run([node, "--input-type=module", "-e", JOINED,
            str(bundle), key.extension_id, lab.configuration.identity, lab.configuration.origin,
            INTENT, failure, "handoff"], capture_output=True, timeout=30)
        assert result.returncode == 0, result.stderr.decode()
        assert result.stderr == b""
        assert json.loads(result.stdout) == {
            "passed": True, "paused": True, "acknowledged": failure == "none"}

    if failure in {"lost-save", "incorrect-save"}:
        with pytest.raises(BrowserHandoffError):
            make().activate(callback)
        assert not (root / handoff._ACK).exists()
    else:
        ack = make().activate(callback)
        assert make().confirm() == ack
        assert (root / handoff._ACK).is_file()
    assert snap(lab)["profile"] == before["profile"]
    assert snap(lab)["archives"] == before["archives"]
    with pytest.raises(BrowserStartupError):
        check_browser_startup(**lab.inputs)


PROCESS = r"""
import json,os,sys
from pathlib import Path
from sds200.browser_device_handoff import BrowserRecoveryHandoff
from sds200.browser_device_retirement_bundle import prepare_browser_retirement_bundle
data=json.loads(sys.argv[1]);stage=sys.argv[2]
values={key:Path(value) if key not in {'operation_id','browser_intent'} else value
        for key,value in data.items()}
root=values.pop('root')
obj=BrowserRecoveryHandoff(root,**values)
from sds200 import browser_device_handoff as module
original=module._write_file
def write(directory,name,body):
    original(directory,name,body)
    if name=={'before-switch':'switch-started.json','after-switch':'ready.json'}.get(stage):
        print('interrupted',flush=True)
        sys.stdin.readline()
module._write_file=write
def callback():
    if stage=='inherited-lock':
        child=os.fork()
        if child==0:
            import time
            time.sleep(30)
            os._exit(0)
        print('inherited '+str(child),flush=True)
        sys.stdin.readline()
        return
    if stage=='after-ack':
        import struct,subprocess
        from sds200.browser_device_native import load_browser_native_configuration
        from sds200.browser_device_resume_boundary import BrowserResumeBoundary
        from dataclasses import asdict
        cfg=load_browser_native_configuration(values['profile'])
        proof=BrowserResumeBoundary(values['profile'],archives=values['archives']).confirm(
            operation_id=values['operation_id'],browser_intent=values['browser_intent'])
        from sds200.browser_device_worker import worker_graph
        body=json.dumps({'version':1,'action':'worker-request','build':worker_graph()[0],
            'request':{'version':1,'action':'acknowledge-retirement',**asdict(proof)}}).encode()
        result=subprocess.run([str(values['recovery_bundle']/'native-host'),cfg.extension_origin],
            input=struct.pack('=I',len(body))+body,capture_output=True,timeout=13)
        assert json.loads(result.stdout[4:])['acknowledged'] is True
    print('interrupted',flush=True)
    sys.stdin.readline()
obj.activate(callback)
"""


@pytest.mark.parametrize("stage", ["before-switch", "after-switch", "before-ack", "after-ack"])
def test_real_process_loss_releases_ownership_without_replaying_or_releasing_guard(
        lab, tmp_path, stage):
    root, bundle, _, proof, make, args = prepared(lab, tmp_path)
    config = {key: str(value) for key, value in {"root": root, **args}.items()}
    process = subprocess.Popen([sys.executable, "-c", PROCESS, json.dumps(config), stage],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert process.stdout is not None
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            assert selector.select(timeout=10), "Handoff process did not reach the fault boundary"
        assert process.stdout.readline() == "interrupted\n"
        with pytest.raises(BlockingIOError), _launch_lock(lab.args["directory"]):
            pytest.fail("Launcher ownership lost before process death")
        process.kill()
        stdout, stderr = process.communicate(timeout=5)
        assert stdout == stderr == ""
        before = snap(lab), snapshot(root)
        with _launch_lock(lab.args["directory"]):
            pass  # Kernel ownership released; coordination file remains in place.
        assert invoke(bundle, lab.configuration) == FAILURE
        if stage == "after-ack":
            assert make().confirm().identity == proof.identity
        else:
            with pytest.raises(BrowserHandoffError):
                make().confirm()
        with pytest.raises(BrowserHandoffError):
            make().activate(lambda: None)
        assert (snap(lab), snapshot(root)) == before
        with pytest.raises(BrowserStartupError):
            check_browser_startup(**lab.inputs)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)


def test_dead_owner_with_fork_inherited_locks_cannot_ack(lab, tmp_path):
    if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
        pytest.skip("Linux pidfd cleanup for owned fault-injection child")
    root, bundle, _, _, make, args = prepared(lab, tmp_path)
    config = {key: str(value) for key, value in {"root": root, **args}.items()}
    process = subprocess.Popen(
        [sys.executable, "-c", PROCESS, json.dumps(config), "inherited-lock"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert process.stdout is not None
    child_fd = None
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            assert selector.select(timeout=10)
        message = process.stdout.readline().split()
        assert len(message) == 2 and message[0] == "inherited"
        child_fd = os.pidfd_open(int(message[1]))
        process.kill()
        process.wait(timeout=5)
        with pytest.raises(BlockingIOError), _launch_lock(lab.args["directory"]):
            pytest.fail("Fault fixture failed to retain inherited ownership")
        before = snap(lab), snapshot(root)
        assert invoke(bundle, lab.configuration) == FAILURE
        assert (snap(lab), snapshot(root)) == before
        signal.pidfd_send_signal(child_fd, signal.SIGKILL)
        with selectors.DefaultSelector() as selector:
            selector.register(child_fd, selectors.EVENT_READ)
            assert selector.select(timeout=5)
        assert process.communicate(timeout=5) == ("", "")
        with pytest.raises(BrowserHandoffError):
            make().confirm()
    finally:
        if child_fd is not None:
            with suppress(ProcessLookupError):
                signal.pidfd_send_signal(child_fd, signal.SIGKILL)
            os.close(child_fd)
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=5)
