"""Confirmation-only native frames joined to trusted browser recovery controls."""

from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

import pytest

import sds200.browser_assets
from sds200.browser_device_native import BrowserRetirementSelection
from sds200.browser_device_profile_access import browser_profile_access
from sds200.browser_device_protocol import (
    BrowserDeviceProtocolError,
    BrowserRetirementRequest,
    parse_browser_device_request,
)
from sds200.browser_device_recovery import RecoveryMode
from sds200.browser_device_resume_boundary import BrowserResumeBoundary
from tests.test_browser_device_native import certificates as certificates
from tests.test_browser_device_native import private
from tests.test_browser_device_native import root as root
from tests.test_browser_device_resume import INTENT
from tests.test_browser_device_resume import lab as lab
from tests.test_browser_device_resume_boundary import archives as archives
from tests.test_browser_device_resume_boundary import selection
from tests.test_browser_device_resume_maintenance import snapshot

pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or os.geteuid() == 0, reason="Non-root Linux native confirmation",
)

RUNNER = """
import os,sys
from pathlib import Path
from sds200.browser_device_native import run_browser_native,BrowserRetirementSelection
raise SystemExit(run_browser_native(Path(sys.argv[1]),[sys.argv[2]],
    os.fdopen(os.dup(0),'rb',buffering=0),os.fdopen(os.dup(1),'wb',buffering=0),
    expected_identity=None if sys.argv[3]=='none' else sys.argv[3],
    retirement=None if sys.argv[4]=='none' else BrowserRetirementSelection(
        Path(sys.argv[4]),sys.argv[5])))
"""
FAILURE = {"version": 1, "ok": False, "mode": "setup_error"}


def request(lab):
    return {"version": 1, "action": "confirm-retirement",
            "identity": lab.configuration.identity, "intent": INTENT}


def invoke(lab, archives, review, *, body=None, caller=None, identity=None,
           operation=None, script=RUNNER):
    data = json.dumps(request(lab) if body is None else body).encode()
    result = subprocess.run(
        [sys.executable, "-c", script, str(lab.root),
         caller or lab.configuration.extension_origin, identity or lab.configuration.identity,
         str(archives), operation or review.operation_id],
        input=struct.pack("=I", len(data)) + data, capture_output=True, timeout=13,
    )
    assert result.returncode == 0 and result.stderr == b""
    assert len(result.stdout) >= 4
    length = struct.unpack("=I", result.stdout[:4])[0]
    assert 0 < length <= 4096 and len(result.stdout) == length + 4
    assert str(lab.root).encode() not in result.stdout
    assert (lab.root / "device.secret").read_bytes().strip() not in result.stdout
    return json.loads(result.stdout[4:])


@pytest.mark.parametrize("kind", ["retire", "reconcile"])
@pytest.mark.parametrize("mode", [m for m in RecoveryMode if m is not RecoveryMode.ACTIVE])
def test_fixed_read_only_endpoint_confirms_each_stopped_mode(lab, archives, kind, mode):
    from sds200.browser_device_recovery import ExchangeFailure

    if mode is not RecoveryMode.PAUSED:
        lab.ledger.resume(lab.ledger.inspect().revision)

        def fail():
            raise ExchangeFailure(mode)

        lab.ledger.authenticate(fail)
    core = BrowserResumeBoundary(lab.root, archives=archives)
    reviewed = selection(lab, core, kind)
    core.execute(reviewed)
    before = snapshot(lab), {p: p.read_bytes() for p in archives.rglob("*.json")}
    result = invoke(lab, archives, reviewed)
    assert result == {"version": 1, "ok": True, "evidence": {
        "identity": lab.configuration.identity, "intent": INTENT,
        "retirement": reviewed.native.fingerprint, "mode": mode,
        "revision": reviewed.native.revision + 1}}
    assert invoke(lab, archives, reviewed) == result  # Repeated confirmation is read-only.
    assert (snapshot(lab), {p: p.read_bytes() for p in archives.rglob("*.json")}) == before
    assert lab.ledger.inspect().mode is mode
    assert repr(BrowserRetirementSelection(
        archives, reviewed.operation_id)) == "BrowserRetirementSelection()"


@pytest.mark.parametrize("change", ["no-selection", "no-identity", "wrong-identity", "caller",
                                   "request-identity",
                                   "intent", "operation", "uncommitted", "input", "state", "busy"])
def test_unconfirmed_or_unbound_selection_is_refused_without_writes(lab, archives, change):
    core = BrowserResumeBoundary(lab.root, archives=archives)
    review = selection(lab, core, "reconcile")
    if change != "uncommitted":
        core.execute(review)
    args = {}
    if change == "no-selection":
        archives = "none"
    if change == "no-identity":
        args["identity"] = "none"
    if change == "wrong-identity":
        args["identity"] = "f" * 64
    if change == "caller":
        args["caller"] = "chrome-extension://" + "p" * 32 + "/"
    if change == "intent":
        args["body"] = {**request(lab), "intent": "e" * 64}
    if change == "request-identity":
        args["body"] = {**request(lab), "identity": "e" * 64}
    if change == "operation":
        args["operation"] = "../outside"
    if change == "input":
        private(lab.root / "device.secret", "sdsctl-browser-v1." + "e" * 64)
    if change == "state":
        # Boundary uses wall time; keep the fixture clock from looking like a
        # clock rollback (which itself changes the revision before resume).
        lab.clock[0] = time.time()
        lab.ledger.resume(lab.ledger.inspect().revision)
        lab.ledger.suspend()
    before = snapshot(lab)
    if change == "busy":
        with browser_profile_access(lab.root, exclusive=True):
            assert invoke(lab, archives, review, **args) == FAILURE
    else:
        assert invoke(lab, archives, review, **args) == FAILURE
    assert snapshot(lab) == before


@pytest.mark.parametrize("action", ["status", "suspend", "authenticate", "claim-browser",
                                    "review-resume", "retire", "reconcile", "execute", "confirm"])
def test_confirmation_endpoint_never_dispatches_other_actions(lab, archives, action):
    core = BrowserResumeBoundary(lab.root, archives=archives)
    review = selection(lab, core, "reconcile")
    core.execute(review)
    before = snapshot(lab)
    assert invoke(lab, archives, review, body={"version": 1, "action": action}) == FAILURE
    assert snapshot(lab) == before


@pytest.mark.parametrize("field", ["archive", "root", "operation_id", "review", "credential",
                                  "origin"])
def test_browser_cannot_supply_selection_or_private_inputs(lab, archives, field):
    core = BrowserResumeBoundary(lab.root, archives=archives)
    review = selection(lab, core, "reconcile")
    before = snapshot(lab)
    assert invoke(lab, archives, review, body={**request(lab), field: "untrusted"}) == FAILURE
    assert snapshot(lab) == before and not list(archives.iterdir())


@pytest.mark.parametrize("field", ["identity", "intent"])
@pytest.mark.parametrize("value", [None, True, "", "../path", "F" * 64, "a" * 64 + "\n"])
def test_parser_refuses_malformed_identifiers(lab, field, value):
    with pytest.raises(BrowserDeviceProtocolError, match="^Invalid browser-device request.$"):
        parse_browser_device_request(json.dumps({**request(lab), field: value}).encode())


def test_strict_parser_redacts_identifiers_and_rejects_duplicate_keys(lab):
    raw = json.dumps(request(lab)).encode()
    parsed = parse_browser_device_request(raw)
    assert isinstance(parsed, BrowserRetirementRequest)
    assert parsed.identity == lab.configuration.identity and parsed.intent == INTENT
    assert repr(parsed) == "BrowserRetirementRequest()"
    with pytest.raises(BrowserDeviceProtocolError):
        parse_browser_device_request(raw.replace(b'"version": 1', b'"version": 1, "version": 1'))


def test_supervisor_bounds_confirmation_and_releases_locks(lab, archives):
    core = BrowserResumeBoundary(lab.root, archives=archives)
    review = selection(lab, core, "reconcile")
    core.execute(review)
    script = RUNNER.replace("raise SystemExit(run_browser_native", """
import time
from sds200.browser_device_resume_boundary import BrowserResumeBoundary
def blocked(*args,**kwargs):
    while True: time.sleep(1)
BrowserResumeBoundary.confirm=blocked
raise SystemExit(run_browser_native""")
    raw = json.dumps(request(lab)).encode()
    before = snapshot(lab)
    start = time.monotonic()
    result = subprocess.run([sys.executable, "-c", script, str(lab.root),
        lab.configuration.extension_origin, lab.configuration.identity, str(archives),
        review.operation_id],
        input=struct.pack("=I", len(raw)) + raw, capture_output=True, timeout=13)
    assert result.returncode == 2 and result.stdout == result.stderr == b""
    assert 9 <= time.monotonic() - start < 13
    with browser_profile_access(lab.root, exclusive=True):
        assert snapshot(lab) == before
    assert invoke(lab, archives, review)["ok"]


JOINED = r"""
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {endianness} from 'node:os';
import {pathToFileURL} from 'node:url';
const [assets,python,runner,root,caller,identity,archives,operation,intent,caseName]=
  process.argv.slice(1);
const {createBrowserRecovery}=await import(pathToFileURL(assets+'/browser_device_recovery.mjs'));
const {connectRetirementWorker,connectRetirementPage,createChromeRetirementPorts}=
  await import(pathToFileURL(assets+'/browser_device_retirement_ui.mjs'));
const id=caller.split('/')[2],url=caller+'recovery.html',nativeHost='org.sdsctl.retirement';
let handler,proofs=0,saves=0;
let saved={version:2,identity,paused:true,phase:'resume_pending',nextAt:0,intent};
const chrome={runtime:{id,getURL:name=>caller+name,onMessage:{addListener:fn=>{handler=fn;}},
  sendNativeMessage:async(host,request)=>{
    assert.equal(host,nativeHost);assert.deepEqual(Object.keys(request).sort(),['action','identity','intent','version']);
    assert.equal(request.action,'confirm-retirement');proofs++;
    const payload=Buffer.from(JSON.stringify(request)),header=Buffer.alloc(4);
    header[endianness()==='LE'?'writeUInt32LE':'writeUInt32BE'](payload.length);
    const child=spawnSync(python,['-c',runner,root,caller,identity,archives,operation],
      {input:Buffer.concat([header,payload]),timeout:13000});
    assert.equal(child.status,0);assert.equal(child.stderr.toString(),'');
    const length=child.stdout[endianness()==='LE'?'readUInt32LE':'readUInt32BE'](0);
    assert.equal(child.stdout.length,length+4);assert(length<=4096);
    return JSON.parse(child.stdout.subarray(4));
  }}};
const ports={now:Date.now,load:async()=>structuredClone(saved),save:async value=>{
  saves++;saved=structuredClone(value);
  if(caseName==='lost-write')throw new Error('lost acknowledgement');},
  retirement:createChromeRetirementPorts(chrome,{nativeHost,identity}),
  clearCookie:async()=>{},cancel:async()=>{},setCookie:async()=>assert.fail('session installation'),
  schedule:async()=>assert.fail('authentication scheduled'),
  native:async()=>assert.fail('native mutation/authentication')};
const controller=createBrowserRecovery(ports,{origin:'https://192.0.2.18:8443',identity,nativeHost});
connectRetirementWorker(chrome,controller);
const sender={id,url,frameId:0,documentLifecycle:'active',documentId:'document-1',
  tab:{id:1,incognito:false}};
let pending;
const runtime={getURL:chrome.runtime.getURL,sendMessage:message=>{
  pending=new Promise(resolve=>assert.equal(handler(message,sender,resolve),true));return pending;
}};
const elements={};
for(const name of ['review','recovery-form','confirm','resolve','notice','reviewed']) {
  const handlers={};elements[name]={checked:false,disabled:false,textContent:'',
    addEventListener:(event,fn)=>{handlers[event]=fn;},
    emit:event=>handlers[event]({isTrusted:true,preventDefault(){}})};
}
const window={location:{href:url}};window.top=window;
connectRetirementPage({document:{getElementById:name=>elements[name]},window,runtime});
const settle=()=>new Promise(resolve=>setImmediate(resolve));
assert.equal(proofs,0);elements.review.emit('click');await settle();await pending;await settle();
assert.equal(saved.phase,'resume_pending');assert.equal(saves,0);
if(caseName==='uncommitted') {
  assert(elements.resolve.disabled);assert.equal(proofs,1);
} else {
  assert(!elements.resolve.disabled);elements.confirm.checked=true;elements['recovery-form'].emit('submit');
  await settle();await pending;await settle();
  assert.equal(proofs,2);assert.equal(saves,1);assert.equal(saved.phase,'clean');
  assert.match(elements.notice.textContent,
    caseName==='lost-write'?/could not be confirmed/:/No login was attempted/);
}
assert(saved.paused);assert.equal(controller.readiness().sessionReady,false);
console.log(JSON.stringify({proofs,saves,paused:saved.paused}));
"""


@pytest.mark.parametrize("kind", ["retire", "reconcile"])
@pytest.mark.parametrize("case", ["healthy", "uncommitted", "lost-write"])
def test_page_worker_coordinator_adapter_and_real_native_evidence(lab, archives, kind, case):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js unavailable")
    core = BrowserResumeBoundary(lab.root, archives=archives)
    review = selection(lab, core, kind)
    if case != "uncommitted":
        core.execute(review)
    before = snapshot(lab)
    assets = Path(sds200.browser_assets.__file__).parent
    result = subprocess.run([node, "--input-type=module", "-e", JOINED, str(assets), sys.executable,
        RUNNER, str(lab.root), lab.configuration.extension_origin, lab.configuration.identity,
        str(archives), review.operation_id, INTENT, case],
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == "" and json.loads(result.stdout)["paused"]
    assert snapshot(lab) == before and lab.ledger.inspect().mode is RecoveryMode.PAUSED


def test_shared_graph_contains_inert_confirmation_code():
    from sds200.browser_device_bundle import MODULES

    assert "browser_device_retirement_ui.mjs" in MODULES
    # Availability is not initialization: dispatch is selected by native context.
    from sds200.browser_device_worker import worker_graph
    assert b"startBrowserWorker" in worker_graph()[1]["extension/worker.mjs"]
