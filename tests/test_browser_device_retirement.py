"""Native evidence and joined JS/SQLite checks, not a production protocol adapter."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

import sds200.browser_assets
from sds200.browser_device_native import load_browser_native_configuration
from sds200.browser_device_recovery import BrowserDeviceRecovery, RecoveryMode
from sds200.browser_device_resume import BrowserDeviceResume
from sds200.browser_device_resume_maintenance import (
    BrowserResumeMaintenance,
    BrowserResumeMaintenanceError,
)
from tests.test_browser_device_native import certificates as certificates
from tests.test_browser_device_native import configure, initialize, private
from tests.test_browser_device_native import root as root
from tests.test_browser_device_resume import INTENT, evidence, prepare
from tests.test_browser_device_resume import lab as lab
from tests.test_browser_device_resume_maintenance import archive as archive
from tests.test_browser_device_resume_maintenance import core as core
from tests.test_browser_device_resume_maintenance import snapshot

pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or os.geteuid() == 0, reason="Non-root Linux native retirement",
)


def test_native_evidence_is_exact_read_only_and_redacted(lab, core, archive):
    approval = prepare(lab)
    review = core.review()
    retired = core.retire(review, archive=archive)
    before = snapshot(lab), archive.read_bytes()
    proof = core.confirm_browser_intent(review, archive=archive, browser_intent=INTENT)
    assert asdict(proof) == {
        "identity": lab.configuration.identity, "intent": INTENT, "retirement": review.fingerprint,
        "mode": retired.mode, "revision": retired.revision,
    }
    for private_value in (INTENT, review.fingerprint, lab.configuration.identity, approval.ticket):
        assert private_value not in repr(proof)
    assert (snapshot(lab), archive.read_bytes()) == before
    assert lab.ledger.inspect().mode is RecoveryMode.PAUSED


@pytest.mark.parametrize("intent", [None, True, "", "bad", "f" * 64 + "\n", "0" * 64])
def test_bad_or_unrelated_intent_cannot_be_confirmed(lab, core, archive, intent):
    prepare(lab)
    review = core.review()
    core.retire(review, archive=archive)
    before = snapshot(lab)
    with pytest.raises(BrowserResumeMaintenanceError):
        core.confirm_browser_intent(review, archive=archive, browser_intent=intent)
    assert snapshot(lab) == before


def test_an_older_intent_in_archive_cannot_authorize_clearing_current_browser(lab, core, archive):
    prepare(lab)
    lab.ledger.suspend()
    newest = "e" * 64
    prepare(lab, browser_intent=newest)
    review = core.review()
    core.retire(review, archive=archive)
    with pytest.raises(BrowserResumeMaintenanceError):
        core.confirm_browser_intent(review, archive=archive, browser_intent=INTENT)
    proof = core.confirm_browser_intent(review, archive=archive, browser_intent=newest)
    assert proof.intent == newest


def test_superseded_native_result_cannot_clear_browser_intent(lab, core, archive):
    prepare(lab)
    review = core.review()
    core.retire(review, archive=archive)
    prepare(lab)
    with pytest.raises(BrowserResumeMaintenanceError):
        core.confirm_browser_intent(review, archive=archive, browser_intent=INTENT)


_BRIDGE = """
import json, sys
from dataclasses import asdict
from pathlib import Path
from sds200.browser_device_native import load_browser_native_configuration
from sds200.browser_device_recovery import BrowserDeviceRecovery, RecoveryMode
from sds200.browser_device_resume_maintenance import (
    BrowserResumeMaintenance, BrowserResumeMaintenanceReview)
root, review_file, archive = map(Path,sys.argv[1:4])
request = json.load(sys.stdin)
config = load_browser_native_configuration(root)
ledger = BrowserDeviceRecovery(root/'recovery.sqlite',config.identity)
try:
    if sys.argv[4] == 'confirm':
        assert set(request) == {'identity','intent'} and request['identity'] == config.identity
        value = json.loads(review_file.read_bytes())
        value['mode'] = RecoveryMode(value['mode'])
        core = BrowserResumeMaintenance(root)
        result = asdict(core.confirm_browser_intent(
            BrowserResumeMaintenanceReview(**value),archive=archive,browser_intent=request['intent']))
    elif sys.argv[4] == 'invalidate':
        # Fixture-only concurrent administrator change, never a browser API.
        ledger.resume(ledger.inspect().revision)
        ledger.suspend()
        result = {'changed':True}
    else:
        assert sys.argv[4] == 'native' and request == {'version':1,'action':'suspend'}
        value = ledger.suspend()
        result = {'version':1,'ok':True,'mode':value.mode,'revision':value.revision,
                  'retry_after':0,'renew_after':0}
except Exception:
    result = {'refused':True}
print(json.dumps(result))
"""

_JOINED = """
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {readFileSync} from 'node:fs';
import {pathToFileURL} from 'node:url';
const {createBrowserRecovery}=await import(pathToFileURL(process.argv[1]));
const [root,review,archive,python,bridgeFile,caseName,identity,origin]=process.argv.slice(2);
const bridge=readFileSync(bridgeFile,'utf8');
let calls=0,auth=0;
function request(action,body) {
  const value=spawnSync(python,['-c',bridge,root,review,archive,action],{
    input:JSON.stringify(body),encoding:'utf8',timeout:10000});
  assert.equal(value.status,0);assert.equal(value.stderr,'');return JSON.parse(value.stdout);
}
let saved={version:2,identity,paused:true,phase:'resume_pending',nextAt:0,
  intent:(caseName==='wrong-intent'?'e':'f').repeat(64)};
let cookie='old-session',alarm=true;
const ports={now:Date.now,load:async()=>structuredClone(saved),
  save:async v=>{saved=structuredClone(v);if(caseName==='lost-write-ack')throw new Error('lost');},
  clearCookie:async()=>{cookie=null;},cancel:async()=>{alarm=false;},schedule:async()=>{alarm=true;},
  setCookie:async()=>assert.fail('No session installation during retirement'),
  native:async body=>{if(body.action==='authenticate')auth++;return request('native',body);},
  retirement:{confirm:async body=>{calls++;return request('confirm',body);}}};
const config={origin,identity,nativeHost:'org.sdsctl.browser_device'};
const controller=createBrowserRecovery(ports,config);
const reviewed=await controller.reviewPendingRetirement();
if(['wrong-intent','uncommitted'].includes(caseName)) {
  assert.equal(reviewed.mode,'retirement_refused');assert.equal(saved.phase,'resume_pending');
  assert.equal(cookie,'old-session');
} else {
  assert.equal(reviewed.mode,'retirement_reviewed');
  if(caseName==='native-change')assert(request('invalidate',{}).changed);
  const result=await controller.retirePending({nativeRevision:reviewed.nativeRevision,
    nativeMode:reviewed.nativeMode});
  assert.equal(result.mode,caseName==='healthy'?'retired_paused':'setup_error');
  assert.equal(saved.phase,caseName==='native-change'?'resume_pending':'clean');
  assert.equal(saved.paused,true);assert.equal(cookie,null);assert.equal(alarm,false);
}
ports.save=async v=>{saved=structuredClone(v);};
const next=createBrowserRecovery(ports,config);
assert.equal((await next.tick()).mode,'paused');
assert.equal(auth,0);assert.equal(saved.paused,true);
assert.equal(next.readiness().sessionReady,false);
console.log(JSON.stringify({passed:true,caseName,phase:saved.phase,confirmations:calls,auth}));
"""


@pytest.mark.parametrize("origin", ["https://display.example", "https://192.0.2.18:8443",
                                    "https://[fd00::18]:8443"])
@pytest.mark.parametrize("case", ["healthy", "wrong-intent", "uncommitted", "native-change",
                                  "lost-write-ack"])
def test_browser_coordinator_joins_real_native_retirement_without_authentication(
    root, certificates, tmp_path, monkeypatch, origin, case,
):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js unavailable")
    configure(root, origin)
    private(root / "ca.pem", certificates[0][0].read_bytes())
    configuration = initialize(root)
    ledger = BrowserDeviceRecovery(root / "recovery.sqlite", configuration.identity)
    stopped = ledger.suspend()
    BrowserDeviceResume(root).prepare(expected_revision=stopped.revision, browser_intent=INTENT,
                                     reviewed_server=evidence(configuration))
    core = BrowserResumeMaintenance(root)
    review = core.review()
    archive = tmp_path / "history.json"
    if case == "uncommitted":
        with monkeypatch.context() as patch:
            patch.setattr(core._recovery, "_save", lambda *_: (_ for _ in ()).throw(OSError()))
            with pytest.raises(BrowserResumeMaintenanceError):
                core.retire(review, archive=archive)
    else:
        core.retire(review, archive=archive)
    review_file, bridge_file = tmp_path / "review.json", tmp_path / "bridge.py"
    private(review_file, json.dumps(asdict(review)))
    private(bridge_file, _BRIDGE)
    module = Path(sds200.browser_assets.__file__).with_name("browser_device_recovery.mjs")
    result = subprocess.run(
        [node, "--input-type=module", "-e", _JOINED, str(module), str(root), str(review_file),
         str(archive), sys.executable, str(bridge_file), case, configuration.identity, origin],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""
    summary = json.loads(result.stdout)
    assert summary["passed"] and summary["auth"] == 0
    assert ledger.inspect().mode is RecoveryMode.PAUSED
    assert load_browser_native_configuration(root) == configuration
    # Addresses are identity inputs only: this harness never opens a network socket.
