"""Real PID-namespace supervision with a fictional executable, not Chromium acceptance."""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import traceback
from contextlib import suppress
from pathlib import Path

import pytest

from sds200 import browser_device_launch as launch
from sds200.browser_device_handoff import BrowserHandoffError, BrowserRecoveryHandoff
from sds200.browser_device_protocol import BrowserDeviceProtocolError, parse_browser_device_request
from sds200.browser_device_retirement_bundle import prepare_browser_retirement_bundle
from sds200.browser_device_startup import BrowserStartupError, check_browser_startup
from tests.test_browser_device_bundle import profile as profile
from tests.test_browser_device_bundle import public_key as public_key
from tests.test_browser_device_handoff import snap
from tests.test_browser_device_native import certificates as certificates
from tests.test_browser_device_profile import private, snapshot
from tests.test_browser_device_registration import source as source
from tests.test_browser_device_resume_workflow import lab as lab
from tests.test_browser_device_retirement_bundle import committed
from tests.test_browser_device_startup import inputs as inputs

pytestmark = pytest.mark.skipif(sys.platform != "linux" or os.geteuid() == 0,
                               reason="Non-root Linux recovery namespace")

# This executable mimics only version/status/native-pipe behavior. It deliberately
# does NOT claim to load an extension or write real browser state. Browser API
# tests separately exercise the generated page/worker readiness handshake.
FAKE = r'''
import json,os,signal,struct,subprocess,sys,time
from pathlib import Path
kind=KIND
if sys.argv[1:]==['--version']:
    if kind=='version-hang': time.sleep(30)
    if kind=='version-large': print('x'*2048);sys.exit(0)
    if kind=='version-other': print('Google Chrome 152.0.0');sys.exit(0)
    print('Chromium 152.0.0');sys.exit(0)
root=Path(ROOT);bundle=Path(BUNDLE)
import traceback
def diagnostic(kind,value,tb):
    while value.__context__ is not None:value=value.__context__
    (root/'fixture-error.txt').write_text(''.join(traceback.format_exception(value)))
sys.excepthook=diagnostic
receipt=json.loads((bundle/'bundle.json').read_text())
identity=receipt['evidence']['identity']
assert sys.argv[-1]=='chrome-extension://'+receipt['extension_id']+'/recovery.html'
if kind=='ignore-stop':signal.signal(signal.SIGINT,signal.SIG_IGN)
else:signal.signal(signal.SIGINT,lambda *_:sys.exit(0))
def native(body):
    data=json.dumps(body).encode()
    p=subprocess.run([str(bundle/'native-host'),'chrome-extension://'+receipt['extension_id']+'/'],
        input=struct.pack('=I',len(data))+data,capture_output=True,timeout=13)
    assert p.stderr==b''
    result=json.loads(p.stdout[4:])
    return result
if kind=='early-exit':sys.exit(0)
if kind=='crash':sys.exit(2)
if kind=='descendant':
    child=os.fork()
    if child==0:
        os.setsid()
        (root/'detached-pid').write_text(os.readlink('/proc/self'))
        while True:time.sleep(.1)
if kind not in {'no-ready','descendant'}:
    body={'version':1,'action':'recovery-launch-ready','identity':identity,
          'intent':receipt['browser_intent'],'binding':receipt['launch_binding']}
    if kind=='wrong-binding':body['binding']='e'*64
    result=native(body)
    if kind=='wrong-binding':assert result['ok'] is False
    else:
        assert result=={'version':1,'ok':True,'binding':receipt['launch_binding']}
        before=(root/'browser-launch-ready.json').stat().st_mtime_ns
        assert native(body)==result
        assert (root/'browser-launch-ready.json').stat().st_mtime_ns==before
        if kind not in {'no-ack'}:
            assert native({'version':1,'action':'acknowledge-retirement',**receipt['evidence']})=={
                'version':1,'ok':True,'mode':'retired_paused','acknowledged':True}
while True:time.sleep(.05)
'''


@pytest.fixture
def staged(lab, tmp_path, monkeypatch):
    bwrap = shutil.which("bwrap")
    if bwrap is None or not hasattr(os, "pidfd_open"):
        pytest.skip("Existing bubblewrap and Linux pidfd required")
    probe = subprocess.run([bwrap, "--unshare-pid", "--as-pid-1", "--die-with-parent",
        "--bind", "/", "/", "--dev-bind", "/dev", "/dev",
        "--ro-bind", "/proc", "/proc", "--", "/bin/true"],
        capture_output=True, timeout=5)
    if probe.returncode:
        pytest.skip("PID isolation unavailable; no security bypass fallback")
    args, proof = committed(lab)
    root, bundle = tmp_path / "supervised handoff", tmp_path / "supervised bundle"
    prepare_browser_retirement_bundle(bundle, **args, handoff=root, supervised=True)
    values = {**args, "recovery_bundle": bundle, "supervised": True}
    monkeypatch.setenv("DISPLAY", ":fictional")
    monkeypatch.setattr(launch, "_START_SECONDS", 3)
    monkeypatch.setattr(launch, "_TOTAL_SECONDS", 8)

    def run(kind="success"):
        browser = tmp_path / ("fictional-browser-" + kind)
        body = FAKE.replace("KIND", repr(kind)).replace("ROOT", repr(str(root))).replace(
            "BUNDLE", repr(str(bundle)))
        private(browser, ("#!" + sys.executable + "\n" + body).encode())
        browser.chmod(0o700)
        obj = BrowserRecoveryHandoff(root, **values)
        return obj, browser, Path(bwrap)

    return root, bundle, proof, run, values


def test_real_namespace_handoff_and_exact_receipts_restore(lab, staged):
    root, bundle, proof, setup, _ = staged
    obj, browser, bwrap = setup()
    before = snap(lab)
    try:
        result = launch.run_browser_recovery(obj, browser=browser, bwrap=bwrap)
    except launch.BrowserRecoveryLaunchError as error:
        cause = error
        while cause.__context__ is not None:
            cause = cause.__context__
        # Only fictional fixture diagnostics; production errors remain redacted.
        diagnostic = root / "fixture-error.txt"
        pytest.fail("".join(traceback.format_exception(cause)) + (
            diagnostic.read_text() if diagnostic.exists() else ""))
    assert result.identity == proof.identity
    assert obj.confirm() == result
    scope = json.loads((root / "supervisor.json").read_text())
    assert scope["namespace"] != [Path("/proc/self/ns/pid").stat().st_dev,
                                  Path("/proc/self/ns/pid").stat().st_ino]
    with pytest.raises((ProcessLookupError, FileNotFoundError, ValueError)):
        launch._process_identity(scope["process"][0])
    assert json.loads((root / "browser-launch-ready.json").read_text())["browser_consent"] is False
    assert obj.restore() == result
    assert obj.confirm(restored=True) == result
    assert snap(lab)["profile"] == before["profile"]
    assert snap(lab)["archives"] == before["archives"]
    assert snap(lab)["bundle"] == before["bundle"]
    with pytest.raises(BrowserStartupError):
        check_browser_startup(**lab.inputs)


@pytest.mark.parametrize("kind", ["version-other", "version-large", "version-hang", "early-exit",
    "crash", "no-ready", "wrong-binding", "no-ack", "ignore-stop", "descendant"])
def test_runtime_failure_retains_guard_no_replay_and_cleans_owned_namespace(lab, staged, kind):
    root, _, _, setup, _ = staged
    obj, browser, bwrap = setup(kind)
    before = snap(lab)
    with pytest.raises(launch.BrowserRecoveryLaunchError):
        launch.run_browser_recovery(obj, browser=browser, bwrap=bwrap)
    if kind.startswith("version-"):
        assert not root.exists()
        assert snap(lab) == before
    else:
        scope = json.loads((root / "supervisor.json").read_text())
        with pytest.raises((ProcessLookupError, FileNotFoundError, ValueError)):
            launch._process_identity(scope["process"][0])
        if kind == "descendant":
            pid = int((root / "detached-pid").read_text())
            with pytest.raises((ProcessLookupError, FileNotFoundError, ValueError)):
                launch._process_identity(pid)
        after = snap(lab), snapshot(root)
        with pytest.raises(launch.BrowserRecoveryLaunchError):
            launch.run_browser_recovery(obj, browser=browser, bwrap=bwrap)
        assert (snap(lab), snapshot(root)) == after
        if kind == "ignore-stop":
            assert obj.confirm().identity
        else:
            with pytest.raises(BrowserHandoffError):
                obj.confirm()
    with pytest.raises(BrowserStartupError):
        check_browser_startup(**lab.inputs)


@pytest.mark.parametrize("field,value", [("binding", True), ("binding", "a"*64+"\n"),
    ("identity", "bad"), ("intent", None), ("url", "https://example.test"), ("pid", 123)])
def test_launch_parser_rejects_paths_process_selection_and_aliases(field, value):
    body = {"version": 1, "action": "recovery-launch-ready", "identity": "a"*64,
            "intent": "b"*64, "binding": "c"*64}
    assert parse_browser_device_request(json.dumps(body).encode()).identity == "a"*64
    with pytest.raises(BrowserDeviceProtocolError):
        parse_browser_device_request(json.dumps({**body, field: value}).encode())


@pytest.mark.parametrize("change", ["display", "browser-mode", "browser-link", "bwrap-mode",
                                   "changed-browser", "non-supervised"])
def test_invalid_launch_preflight_preserves_registration(
        lab, staged, monkeypatch, tmp_path, change):
    root, _, _, setup, _ = staged
    obj, browser, bwrap = setup()
    if change == "display":
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    elif change == "browser-mode":
        browser.chmod(0o722)
    elif change == "browser-link":
        link = tmp_path / "browser-link"
        link.symlink_to(browser)
        browser = link
    elif change == "bwrap-mode":
        bwrap = tmp_path / "not-safe-bwrap"
        private(bwrap, b"#!/bin/sh\nexit 0\n")
        bwrap.chmod(0o777)
    elif change == "non-supervised":
        obj._supervised = False
    else:
        original = launch._Namespace.preflight

        def preflight(scope):
            original(scope)
            with browser.open("a") as stream:
                stream.write("\n# replaced after qualification\n")

        monkeypatch.setattr(launch._Namespace, "preflight", preflight)
    before = snap(lab)
    with pytest.raises(launch.BrowserRecoveryLaunchError):
        launch.run_browser_recovery(obj, browser=browser, bwrap=bwrap)
    assert not root.exists()
    assert snap(lab) == before


def test_absent_browser_markers_do_not_allow_confirmation_while_supervisor_lives(staged):
    from sds200.browser_device_profile_access import (
        BrowserProfileAccessError,
        browser_profile_access,
    )
    root, _, _, setup, _ = staged
    obj, browser, bwrap = setup()
    command = launch.recovery_browser_command(obj, browser)
    scope = launch._Namespace(bwrap, command)
    try:
        scope.preflight()

        def callback():
            record, _, _ = obj._checked(stopped=True, live=True)
            launch._write_file(root, "supervisor.json", launch._json({
                "version": 1, "record_sha256": launch._digest(record),
                "process": scope.process, "namespace": scope.namespace,
                "owner_namespace": scope.owner_namespace,
                "expires_at": launch.time.monotonic() + 30,
                "command_sha256": launch._digest(launch._json(list(command)))}))
            scope.send(b"g")
            assert scope.message(launch.time.monotonic() + 5) == {"event": "running"}
            limit = launch.time.monotonic() + 8
            while launch.time.monotonic() < limit:
                if (root / launch._ACK).exists():
                    try:
                        with browser_profile_access(root, exclusive=False):
                            current, _, proof = obj._checked(stopped=False, live=True)
                            obj._ack(current, proof)
                            return  # Deliberately WRONG: browser/PID 1 still alive.
                    except BrowserProfileAccessError:
                        pass
                launch.time.sleep(.05)
            pytest.fail("Fictional native ACK was not produced")

        with pytest.raises(BrowserHandoffError):
            obj.activate(callback)
        assert (root / launch._ACK).exists()
        before = snapshot(root)
        with pytest.raises(BrowserHandoffError):
            obj.confirm()
        with pytest.raises(BrowserHandoffError):
            obj.restore()
        assert snapshot(root) == before
    finally:
        scope.close()
    assert obj.confirm().identity  # Exact stopped confirmation is now possible.



def test_independent_pid1_deadline_bounds_stalled_version(lab, staged, monkeypatch):
    root, _, _, setup, _ = staged
    obj, browser, bwrap = setup("version-hang")
    monkeypatch.setattr(launch, "_ENTRY",
        "import sds200.browser_device_launch as m; m._NAMESPACE_SECONDS=1; m._child()")
    before = snap(lab)
    start = launch.time.monotonic()
    with pytest.raises(launch.BrowserRecoveryLaunchError):
        launch.run_browser_recovery(obj, browser=browser, bwrap=bwrap)
    assert launch.time.monotonic() - start < 5
    assert not root.exists() and snap(lab) == before


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGKILL])
def test_owner_loss_kills_private_namespace_including_detached_descendant(
        lab, staged, signum):
    root, _, _, setup, values = staged
    obj, browser, bwrap = setup("descendant")
    code = """
import json,sys
from pathlib import Path
from sds200.browser_device_handoff import BrowserRecoveryHandoff
from sds200.browser_device_launch import run_browser_recovery
args=json.loads(sys.argv[1])
for key in ('directory','profile','bundle','public_key','archives','recovery_bundle'):
    args[key]=Path(args[key])
run_browser_recovery(BrowserRecoveryHandoff(Path(sys.argv[2]),**args),
                     browser=Path(sys.argv[3]),bwrap=Path(sys.argv[4]))
"""
    args = {k: str(v) if isinstance(v, Path) else v for k, v in values.items()}
    process = subprocess.Popen([sys.executable, "-I", "-c", code, json.dumps(args),
        str(root), str(browser), str(bwrap)], stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    descriptors = []
    try:
        limit = launch.time.monotonic() + 15
        while not (root / "detached-pid").exists():
            assert process.poll() is None
            assert launch.time.monotonic() < limit
            launch.time.sleep(.05)
        scope = json.loads((root / "supervisor.json").read_text())
        for pid in (scope["process"][0], int((root / "detached-pid").read_text())):
            descriptors.append(os.pidfd_open(pid))
        process.send_signal(signum)
        process.wait(timeout=5)
        for fd in descriptors:
            assert launch.select.select([fd], [], [], 5)[0], "Owned namespace process survived"
        before = snap(lab), snapshot(root)
        with pytest.raises(BrowserHandoffError):
            obj.confirm()
        assert (snap(lab), snapshot(root)) == before
        with pytest.raises(BrowserStartupError):
            check_browser_startup(**lab.inputs)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        for fd in descriptors:
            with suppress(ProcessLookupError):
                signal.pidfd_send_signal(fd, signal.SIGKILL)
            os.close(fd)


@pytest.mark.parametrize("field,value", [("version", True), ("process", [1,1]),
    ("namespace", [1,1]), ("owner_namespace", [1,1]), ("expires_at", 0),
    ("command_sha256", "e"*64), ("record_sha256", "e"*64)])
def test_changed_supervisor_evidence_cannot_confirm_or_restore(staged, field, value):
    root, _, _, setup, _ = staged
    obj, browser, bwrap = setup()
    launch.run_browser_recovery(obj, browser=browser, bwrap=bwrap)
    path = root / "supervisor.json"
    record = json.loads(path.read_text())
    private(path, launch._json({**record, field: value}))
    before = snapshot(root)
    with pytest.raises(BrowserHandoffError):
        obj.confirm()
    with pytest.raises(BrowserHandoffError):
        obj.restore()
    assert snapshot(root) == before


def test_ordinary_wrapper_rejects_readiness_action_without_mutation(lab):
    from tests.test_browser_device_retirement_bundle import invoke
    before = snap(lab)
    result = invoke(lab.args["bundle"], lab.configuration, {
        "version": 1, "action": "recovery-launch-ready",
        "identity": lab.configuration.identity, "intent": "b"*64, "binding": "c"*64})
    assert result == {"version": 1, "ok": False, "mode": "setup_error"}
    assert snap(lab) == before
