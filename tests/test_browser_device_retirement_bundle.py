"""Inert exact recovery-only bundle; opaque Chromium storage is never edited."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys

import pytest

from sds200 import browser_device_bundle as normal
from sds200 import browser_device_retirement_bundle as recovery
from sds200.browser_device_recovery import ExchangeFailure, RecoveryMode
from sds200.browser_device_registration import MAINTENANCE_MARKER, BrowserRegistrationError
from sds200.browser_device_resume_boundary import BrowserResumeBoundary
from sds200.browser_device_resume_workflow import BrowserResumeWorkflowError
from sds200.browser_device_startup import BrowserStartupError, _launch_lock, check_browser_startup
from tests.test_browser_device_bundle import profile as profile
from tests.test_browser_device_bundle import public_key as public_key
from tests.test_browser_device_native import certificates as certificates
from tests.test_browser_device_native import frame
from tests.test_browser_device_profile import CREDENTIAL, private, snapshot
from tests.test_browser_device_registration import source as source
from tests.test_browser_device_resume import INTENT
from tests.test_browser_device_resume_workflow import apply, prepare, snap
from tests.test_browser_device_resume_workflow import lab as lab
from tests.test_browser_device_startup import inputs as inputs

pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or os.geteuid() == 0, reason="Non-root Linux recovery bundle",
)


def committed(lab, kind="reconcile"):
    prepare(lab, kind)
    selected = []

    def consent(review):
        selected.append(review.operation_id)
        return review.confirmation

    result = apply(lab, consent, kind=kind)
    return {**lab.args, "operation_id": selected[0], "browser_intent": INTENT}, result


def invoke(root, config, body=None, caller=None):
    result = subprocess.run([str(root / "native-host"), caller or config.extension_origin],
        input=frame(body or {"version": 1, "action": "confirm-retirement",
                            "identity": config.identity, "intent": INTENT}),
        capture_output=True, timeout=13)
    assert result.returncode == 0 and result.stderr == b""
    assert len(result.stdout) >= 4
    size = struct.unpack("=I", result.stdout[:4])[0]
    assert 0 < size <= 4096 and len(result.stdout) == size + 4
    assert CREDENTIAL.encode() not in result.stdout
    return json.loads(result.stdout[4:])


@pytest.mark.parametrize("kind", ["retire", "reconcile"])
@pytest.mark.parametrize("mode", [m for m in RecoveryMode if m is not RecoveryMode.ACTIVE])
def test_same_identity_recovery_bundle_and_actual_native_wrapper(lab, tmp_path, kind, mode):
    if mode is not RecoveryMode.PAUSED:
        lab.ledger.resume(lab.ledger.inspect().revision)

        def fail():
            raise ExchangeFailure(mode)

        lab.ledger.authenticate(fail)
    args, proof = committed(lab, kind)
    root = tmp_path / "recovery bundle's $(no-shell)"
    before = snap(lab)
    key = recovery.prepare_browser_retirement_bundle(root, **args)
    assert key == normal.browser_extension_identity(lab.args["public_key"])
    assert recovery.inspect_browser_retirement_bundle(root, **args) == key
    assert snap(lab) == before
    manifest = json.loads((root / "extension/manifest.json").read_bytes())
    assert manifest["key"] == key.manifest_key
    assert manifest["permissions"] == ["nativeMessaging", "storage", "cookies", "alarms"]
    assert manifest["host_permissions"] == ["https://127.0.0.1:8443/*"]
    assert not set(manifest) & {"content_scripts", "externally_connectable",
                                "web_accessible_resources", "update_url"}
    assert set(p.name for p in (root / "extension").iterdir()) == {
        "manifest.json", "worker.mjs", "recovery.html", "recovery.mjs", "recovery.css",
        *recovery._MODULES}
    receipt = json.loads((root / "bundle.json").read_bytes())
    assert receipt["operation"] == "prepare-retirement-bundle"
    assert receipt["operation_id"] == args["operation_id"]
    for name, digest in receipt["files"].items():
        path = root / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
        assert path.stat().st_mode & 0o777 == (0o700 if name == "native-host" else 0o600)
        assert CREDENTIAL.encode() not in path.read_bytes()
    page = (root / "extension/recovery.html").read_text()
    assert args["operation_id"] not in page and INTENT not in page
    assert "automatic sign-in paused" in page
    assert "connectChromeRecovery(" not in (root / "extension/worker.mjs").read_text()
    host = json.loads((root / (normal.NATIVE_HOST + ".json")).read_bytes())
    assert host["name"] == normal.NATIVE_HOST
    assert host["allowed_origins"] == [lab.configuration.extension_origin]
    assert invoke(root, lab.configuration) == {"version": 1, "ok": True, "evidence": {
        "identity": proof.identity, "intent": proof.intent, "retirement": proof.retirement,
        "mode": mode, "revision": proof.revision}}
    assert snap(lab) == before
    with pytest.raises(BrowserStartupError):
        check_browser_startup(**lab.inputs)
    # Neither normal registration nor normal bundle inspection can bless it.
    from sds200.browser_device_registration import _validated_bundle, register_browser_directory
    with pytest.raises(ValueError):
        _validated_bundle(root, lab.args["profile"], lab.args["public_key"], fresh=False)
    with pytest.raises(BrowserRegistrationError):
        register_browser_directory(tmp_path / "must-not-create", bundle=root,
            profile=lab.args["profile"], public_key=lab.args["public_key"])
    assert not (tmp_path / "must-not-create").exists()


@pytest.mark.parametrize("action", ["status", "suspend", "authenticate", "claim-browser",
    "review-resume", "prepare-resume", "commit-resume", "retire", "reconcile", "execute"])
def test_generated_wrapper_rejects_all_other_actions(lab, tmp_path, action):
    args, _ = committed(lab)
    root = tmp_path / "confirmation"
    recovery.prepare_browser_retirement_bundle(root, **args)
    before = snap(lab)
    assert invoke(root, lab.configuration, {"version": 1, "action": action}) == {
        "version": 1, "ok": False, "mode": "setup_error"}
    assert snap(lab) == before


@pytest.mark.parametrize("change", ["no-guard", "wrong-guard", "guard-symlink", "missing-lock",
    "uncommitted", "later-state", "different-intent", "different-operation", "edited-bundle",
    "singleton", "busy"])
def test_unconfirmed_context_never_creates_recovery_bundle(lab, tmp_path, monkeypatch, change):
    if change == "uncommitted":
        def uncommitted(*args):
            raise ValueError()

        monkeypatch.setattr(BrowserResumeBoundary, "execute", uncommitted)
        with pytest.raises(BrowserResumeWorkflowError):
            apply(lab)
        marker = json.loads((lab.args["directory"] / MAINTENANCE_MARKER).read_bytes())
        args = {**lab.args, "operation_id": marker["operation_id"], "browser_intent": INTENT}
    else:
        args, _ = committed(lab)
    directory = lab.args["directory"]
    guard = directory / MAINTENANCE_MARKER
    if change == "no-guard":
        guard.unlink()
    elif change == "wrong-guard":
        private(guard, b"unrelated work")
    elif change == "guard-symlink":
        target = tmp_path / "old-guard"
        guard.rename(target)
        guard.symlink_to(target)
    elif change == "missing-lock":
        (directory / ".sdsctl-device-launch.lock").unlink()
    elif change == "later-state":
        lab.ledger.suspend()
        lab.ledger.resume(lab.ledger.inspect().revision)
    elif change == "different-intent":
        args["browser_intent"] = "d" * 64
    elif change == "different-operation":
        args["operation_id"] = "e" * 64
    elif change == "edited-bundle":
        private(lab.args["bundle"] / "extension/worker.mjs", b"edited")
    elif change == "singleton":
        (directory / "SingletonLock").symlink_to("preserve")
    before = snap(lab)
    root = tmp_path / "must-not-create"
    if change == "busy":
        with _launch_lock(directory), pytest.raises(recovery.BrowserRetirementBundleError):
            recovery.prepare_browser_retirement_bundle(root, **args)
    else:
        with pytest.raises(recovery.BrowserRetirementBundleError):
            recovery.prepare_browser_retirement_bundle(root, **args)
    assert not root.exists() and snap(lab) == before


@pytest.mark.parametrize("change", ["worker", "receipt", "extra", "symlink", "hardlink",
                                   "mode", "state", "guard"])
def test_exact_inspection_refuses_edited_output_or_superseded_context(lab, tmp_path, change):
    args, _ = committed(lab)
    root = tmp_path / "confirmation"
    recovery.prepare_browser_retirement_bundle(root, **args)
    worker = root / "extension/worker.mjs"
    if change == "worker":
        private(worker, b"edited")
        # Rehashing an edited receipt does not authorize noncanonical code.
        receipt = json.loads((root / "bundle.json").read_bytes())
        receipt["files"]["extension/worker.mjs"] = hashlib.sha256(b"edited").hexdigest()
        private(root / "bundle.json", normal._json(receipt))
    elif change == "receipt":
        private(root / "bundle.json", b"{}")
    elif change == "extra":
        private(root / "extension/resume.html", b"not allowed")
    elif change == "symlink":
        target = tmp_path / "worker"
        worker.rename(target)
        worker.symlink_to(target)
    elif change == "hardlink":
        os.link(worker, tmp_path / "worker")
    elif change == "mode":
        worker.chmod(0o644)
    elif change == "state":
        lab.ledger.resume(lab.ledger.inspect().revision)
    else:
        private(lab.args["directory"] / MAINTENANCE_MARKER, b"changed")
    before = snap(lab), snapshot(root)
    with pytest.raises(recovery.BrowserRetirementBundleError):
        recovery.inspect_browser_retirement_bundle(root, **args)
    assert (snap(lab), snapshot(root)) == before


@pytest.mark.parametrize("destination", ["existing", "directory", "profile", "bundle", "archives",
                                        "nested", "parent", "relative", "symlink"])
def test_unsafe_destinations_preserved(lab, tmp_path, destination):
    args, _ = committed(lab)
    root = tmp_path / "confirmation"
    if destination == "existing":
        root.mkdir(mode=0o700)
        private(root / "keep", b"unrelated")
    elif destination in lab.args:
        root = lab.args[destination]
    elif destination == "nested":
        root = lab.args["archives"] / "nested"
    elif destination == "parent":
        root = tmp_path
    elif destination == "relative":
        root = root.relative_to(root.anchor)
    elif destination == "symlink":
        root.symlink_to(lab.args["bundle"], target_is_directory=True)
    before = snap(lab)
    with pytest.raises(recovery.BrowserRetirementBundleError):
        recovery.prepare_browser_retirement_bundle(root, **args)
    assert snap(lab) == before


@pytest.mark.parametrize("stage", ["file", "fsync", "after-write"])
def test_uncertain_output_is_retained_and_cannot_be_overwritten(lab, tmp_path, monkeypatch, stage):
    args, _ = committed(lab)
    root = tmp_path / "confirmation"
    original = recovery._write_bundle
    before = snap(lab)

    def write(*a):
        if stage == "after-write":
            original(*a)
            private(lab.args["directory"] / MAINTENANCE_MARKER, b"newer guard")
        else:
            original_write, original_sync = normal._write, normal.os.fsync

            def fail_file(fd, name, body):
                original_write(fd, name, body)
                raise OSError("private failure")

            count = 0

            def fail_sync(fd):
                nonlocal count
                count += 1
                if count == 2:
                    raise OSError("private failure")
                original_sync(fd)

            with monkeypatch.context() as m:
                m.setattr(normal, "_write", fail_file if stage == "file" else original_write)
                m.setattr(normal.os, "fsync", fail_sync if stage == "fsync" else original_sync)
                original(*a)

    monkeypatch.setattr(recovery, "_write_bundle", write)
    with pytest.raises(recovery.BrowserRetirementBundleError) as caught:
        recovery.prepare_browser_retirement_bundle(root, **args)
    assert "private failure" not in str(caught.value) and root.is_dir()
    partial = snapshot(root)
    with pytest.raises(recovery.BrowserRetirementBundleError):
        recovery.prepare_browser_retirement_bundle(root, **args)
    assert snapshot(root) == partial
    if stage != "after-write":
        assert snap(lab) == before


JOINED = r"""
import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
import {execFileSync} from 'node:child_process';
const [root,id,identity,origin,intent,failure]=process.argv.slice(1);
const stateKey='sdsctlDeviceRecovery';
let state={version:2,identity,paused:true,phase:'resume_pending',nextAt:0,intent};
const original=structuredClone(state), calls=[], listeners=[];
const extensionOrigin=`chrome-extension://${id}/`;
const sender={id,url:extensionOrigin+'recovery.html',frameId:0,
  documentLifecycle:'active',documentId:'doc-1',tab:{id:1,incognito:false}};
globalThis.chrome={runtime:{id,getURL:name=>extensionOrigin+name,
  onMessage:{addListener:fn=>listeners.push(fn)},
  sendMessage:message=>new Promise((resolve,reject)=>{
    assert.equal(listeners.length,1);
    if(!listeners[0](message,sender,resolve))reject(Error('unregistered'));
  }),
  sendNativeMessage:async(host,request)=>{
    calls.push(request.action);assert.equal(host,'org.sdsctl.browser_device');
    assert.deepEqual(request,{version:1,action:'confirm-retirement',identity,intent});
    const body=Buffer.from(JSON.stringify(request)),header=Buffer.alloc(4);
    // Native messaging uses host byte order; fixture runs on this Linux host.
    if(new Uint8Array(new Uint32Array([1]).buffer)[0]===1)header.writeUInt32LE(body.length);
    else header.writeUInt32BE(body.length);
    const bytes=execFileSync(root+'/native-host',[extensionOrigin],{
      input:Buffer.concat([header,body]),timeout:13000,maxBuffer:8192});
    const size=new Uint8Array(new Uint32Array([1]).buffer)[0]===1
      ? bytes.readUInt32LE() : bytes.readUInt32BE();
    assert.equal(bytes.length,size+4);return JSON.parse(bytes.subarray(4));
  }},
  storage:{local:{setAccessLevel:async value=>
      assert.deepEqual(value,{accessLevel:'TRUSTED_CONTEXTS'}),
    get:async key=>{assert.equal(key,stateKey);return {[key]:structuredClone(state)};},
    set:async value=>{calls.push('save');state=structuredClone(value[stateKey]);
      if(failure==='lost-save')throw Error('private test diagnostic');
      if(failure==='incorrect-save')state=structuredClone(original);
    }}},
  cookies:{remove:async()=>{calls.push('remove-cookie');},get:async()=>null,
    set:()=>assert.fail('cookie install forbidden')},
  alarms:{clear:async()=>{calls.push('cancel-alarm');},create:()=>assert.fail('alarm forbidden')},
};
const names=['review','recovery-form','confirm','resolve','notice','reviewed'];
const elements=Object.fromEntries(names.map(name=>[name,{checked:false,disabled:true,textContent:'',
  handlers:{},addEventListener(event,handler){this.handlers[event]=handler;}}]));
globalThis.document={getElementById:name=>elements[name]};
globalThis.window={location:{href:sender.url}};window.top=window;
const settle=()=>new Promise(resolve=>setImmediate(resolve));
await import(pathToFileURL(root+'/extension/worker.mjs'));
await import(pathToFileURL(root+'/extension/recovery.mjs'));
await settle();assert.deepEqual(calls,[]);assert.deepEqual(state,original);
elements.review.handlers.click({isTrusted:true});await settle();
assert.deepEqual(calls,['confirm-retirement']);assert.equal(elements.resolve.disabled,false);
assert.deepEqual(state,original);
elements.confirm.checked=true;
elements['recovery-form'].handlers.submit({isTrusted:true,preventDefault(){}});await settle();
assert.equal(state.paused,true);assert.equal(calls.filter(c=>c==='save').length,1);
assert.equal(calls.filter(c=>c==='confirm-retirement').length,2);
assert.equal(elements.notice.textContent.includes('No login was attempted'),failure==='none');
assert(!elements.notice.textContent.includes('private test diagnostic'));
assert.deepEqual(state,failure==='incorrect-save' ? original :
  {version:1,identity,paused:true,phase:'clean',nextAt:0});
console.log(JSON.stringify({passed:true,paused:state.paused,acknowledged:failure==='none'}));
"""


@pytest.mark.parametrize("kind", ["retire", "reconcile"])
@pytest.mark.parametrize("failure", ["none", "lost-save", "incorrect-save"])
def test_actual_generated_page_worker_and_native_process_joined(lab, tmp_path, kind, failure):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node joined generated artifacts")
    args, _ = committed(lab, kind)
    root = tmp_path / "confirmation"
    key = recovery.prepare_browser_retirement_bundle(root, **args)
    before = snap(lab), snapshot(root)
    result = subprocess.run([node, "--input-type=module", "-e", JOINED,
        str(root), key.extension_id, lab.configuration.identity, lab.configuration.origin,
        INTENT, failure], capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr.decode()
    assert result.stderr == b""
    assert json.loads(result.stdout) == {
        "passed": True, "paused": True, "acknowledged": failure == "none"}
    assert (snap(lab), snapshot(root)) == before
    # A successful page response is not a durable local acknowledgement receipt
    # and never releases the guarded installation for ordinary startup.
    with pytest.raises(BrowserStartupError):
        check_browser_startup(**lab.inputs)


@pytest.mark.parametrize("change", ["caller", "identity", "intent", "extra", "state", "archive"])
def test_wrapper_refuses_unbound_requests_or_superseded_native_proof(lab, tmp_path, change):
    args, _ = committed(lab)
    root = tmp_path / "confirmation"
    recovery.prepare_browser_retirement_bundle(root, **args)
    body = {"version": 1, "action": "confirm-retirement",
            "identity": lab.configuration.identity, "intent": INTENT}
    caller = None
    if change == "caller":
        caller = "chrome-extension://" + "p" * 32 + "/"
    elif change in {"identity", "intent"}:
        body[change] = "e" * 64
    elif change == "extra":
        body["operation_id"] = args["operation_id"]
    elif change == "state":
        lab.ledger.resume(lab.ledger.inspect().revision)
    else:
        private(lab.args["archives"] / args["operation_id"] / "review.json", b"{}")
    before = snap(lab)
    assert invoke(root, lab.configuration, body, caller) == {
        "version": 1, "ok": False, "mode": "setup_error"}
    assert snap(lab) == before
