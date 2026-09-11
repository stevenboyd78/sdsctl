"""Run the deterministic browser-coordination contract without a real browser."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from sds200.browser_device_native import load_browser_native_configuration
from sds200.browser_device_recovery import BrowserDeviceRecovery, RecoveryMode
from tests.test_browser_device_native import certificates as certificates


def test_browser_recovery_coordinator_contract() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")
    result = subprocess.run(
        [node, "--test", "scripts/experimental/test_browser_device_recovery.mjs",
         "scripts/experimental/test_browser_device_logout.mjs",
         "scripts/experimental/test_browser_device_setup.mjs",
         "scripts/experimental/test_browser_device_startup.mjs",
         "scripts/experimental/test_browser_device_resume.mjs",
         "scripts/experimental/test_browser_device_resume_ui.mjs",
         "scripts/experimental/test_browser_device_continuation_state.mjs",
         "scripts/experimental/test_browser_device_continuation_probe.mjs",
         "scripts/experimental/test_browser_device_continuation_context.mjs",
         "scripts/experimental/test_browser_device_continuation_cookie.mjs",
         "scripts/experimental/test_browser_device_continuation_worker.mjs",
         "scripts/experimental/test_browser_device_continuation_consent.mjs",
         "scripts/experimental/test_browser_device_continuation_install.mjs",
         "scripts/experimental/test_browser_device_continuation_operation.mjs",
         "scripts/experimental/test_browser_device_retirement.mjs",
         "scripts/experimental/test_browser_device_retirement_ui.mjs",
         "scripts/experimental/test_browser_device_retirement_startup.mjs",
         "scripts/experimental/test_browser_device_worker.mjs",
         "scripts/experimental/test_browser_device_worker_gate.mjs",
         "scripts/experimental/test_browser_device_launch.mjs",
         "scripts/experimental/test_browser_cookie_interruption.mjs"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""


def test_real_browser_logout_harness_help_is_non_mutating() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")
    result = subprocess.run(
        [node, "scripts/experimental/audit_browser_logout.mjs", "--help"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0 and result.stderr == ""
    assert "Fictional credentials and loopback HTTPS only" in result.stdout
    assert "sandbox-enabled Chromium and verified TLS" in result.stdout
    assert "Driver never installs authentication cookies" in result.stdout
    assert "cookie-set-stop, cookie-remove-stop" in result.stdout


def test_continuation_install_harness_describes_modeled_authority() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")
    result = subprocess.run(
        [node, "scripts/experimental/audit_browser_continuation_install.mjs", "--help"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0 and result.stderr == ""
    assert "Actual sandboxed Chromium storage/cookies" in result.stdout
    assert "modeled native and page observations" in result.stdout
    assert "no real sign-in, network request or production profile" in result.stdout


def test_generated_bundle_harness_help_is_non_mutating() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")
    result = subprocess.run(
        [node, "scripts/experimental/audit_browser_bundle.mjs", "--help"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0 and result.stderr == ""
    assert "no dashboard login or production access" in result.stdout
    assert "[review|first-run]" in result.stdout
    assert "sandbox required" in result.stdout


def test_integrated_recovery_harness_help_is_non_mutating() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")
    result = subprocess.run(
        [node, "scripts/experimental/audit_browser_recovery.mjs", "--help"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0 and result.stderr == ""
    assert "Actual ASGI/native/Chromium on loopback only" in result.stdout
    assert "No cookie injection; real clocks and retry alarms" in result.stdout


def test_generated_recovery_harness_help_is_non_mutating() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")
    result = subprocess.run(
        [node, "scripts/experimental/audit_browser_generated_recovery.mjs", "--help"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0 and result.stderr == ""
    assert "Actual CLI/generated bundle/setup form" in result.stdout
    assert "no recovery-state seeding, cookie injection or production access" in result.stdout
    assert "Sandbox and verified TLS required" in result.stdout
    assert "bad-ca, bad-name" in result.stdout
    assert "headed-startup uses an existing private X server and CDP" in result.stdout
    assert "production ownership checks stay strict" in result.stdout


@pytest.mark.skipif(not sys.platform.startswith("linux") or os.geteuid() == 0,
                    reason="Non-root Linux fixture/native profile")
@pytest.mark.parametrize("generated", [False, True])
def test_recovery_fixture_delivery_modes_keep_native_state_ownership(
    tmp_path, certificates, generated,
):
    from sds200.browser_device_profile import create_browser_profile
    from sds200.browser_device_store import BrowserDeviceStore

    root = tmp_path / "generated"
    root.mkdir(mode=0o700)
    for name, source in [("server.pem", certificates[0][0]), ("server.key", certificates[0][1]),
                         ("ca.pem", certificates[0][0])]:
        target = root / name
        target.write_bytes(source.read_bytes())
        target.chmod(0o600)
    if not generated:
        target = root / "client.json"
        target.write_text(json.dumps({"version": 1, "origin": "https://localhost",
                                      "device_id": "fixture",
                                      "extension_origin": "chrome-extension://" + "a" * 32 + "/"}))
        target.chmod(0o600)
    result = subprocess.run(
        [sys.executable, "-I", "scripts/experimental/browser_recovery_server.py",
         str(root), "https://localhost", *(["generated"] if generated else [])],
        input='{"action":"status"}\n{"action":"stop"}\n', capture_output=True, text=True,
        timeout=15,
    )
    assert result.returncode == 0, "Fictional generated server failed"
    replies = [json.loads(line) for line in result.stdout.splitlines()]
    assert replies[0]["ready"]
    assert replies[1] == {"action": "status", "exchanges": 0, "state": "active"}
    store = BrowserDeviceStore(root / "authority/devices.sqlite")
    if not generated:
        config = load_browser_native_configuration(root)
        assert replies[0]["identity"] == config.identity
        ledger = BrowserDeviceRecovery(root / "recovery.sqlite", config.identity)
        assert ledger.inspect().revision == 1
        secret = (root / "device.secret").read_text()
        assert store.authenticate("fixture", secret) is not None
        assert secret not in result.stdout + result.stderr
        assert not (root / "enrollment.json").exists()
        return
    assert replies[0]["identity"] is None
    assert not (root / "device.secret").exists()
    assert not (root / "recovery.sqlite").exists()
    assert not (root / "client.json").exists()
    attachment = root / "enrollment.json"
    assert attachment.stat().st_mode & 0o777 == 0o600
    issued = json.loads(attachment.read_bytes())
    assert issued["credential"] not in result.stdout + result.stderr
    assert issued["outcome"] == {"status": "issued", "completed": False}
    state = create_browser_profile(
        root / "native", enrollment_file=attachment, ca_file=root / "ca.pem",
        origin="https://localhost", device_id="fixture", extension_id="a" * 32,
    )
    assert state.revision == 1 and state.mode is RecoveryMode.ACTIVE
    assert store.authenticate("fixture", issued["credential"]) is not None


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux native supervisor")
def test_javascript_pause_uses_real_native_framing_and_persistent_ledger(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")
    root = tmp_path / "installation"
    root.mkdir(mode=0o700)
    config = root / "client.json"
    config.write_text(json.dumps({
        "version": 1, "origin": "https://192.0.2.18:8443", "device_id": "display",
        "extension_origin": "chrome-extension://" + "a" * 32 + "/",
    }))
    config.chmod(0o600)
    identity = load_browser_native_configuration(root).identity
    recovery = BrowserDeviceRecovery.initialize(root / "recovery.sqlite", identity)
    # No credential or CA exists: suspend/status must work offline without either.
    script = r'''
import assert from 'node:assert/strict';
import {spawn, spawnSync} from 'node:child_process';
import {endianness} from 'node:os';
import {createBrowserRecovery, initialBrowserRecoveryState} from
  './scripts/experimental/browser_device_recovery.mjs';
const config = {origin: 'https://192.0.2.18:8443', identity: process.argv[3],
  nativeHost: 'org.sdsctl.browser_device'};
let saved = initialBrowserRecoveryState(config);
const actions = [];
const runner = `import os, sys
from pathlib import Path
from sds200.browser_device_native import run_browser_native
raise SystemExit(run_browser_native(Path(sys.argv[1]), sys.argv[2:],
 os.fdopen(os.dup(0), 'rb', buffering=0), os.fdopen(os.dup(1), 'wb', buffering=0)))`;
const ports = {
  now: Date.now, load: async () => saved, save: async state => { saved = structuredClone(state); },
  clearCookie: async () => {}, setCookie: async () => assert.fail('No cookie expected'),
  schedule: async () => {}, cancel: async () => {},
  native: request => new Promise((resolve, reject) => {
    actions.push(request.action); assert.notEqual(request.action, 'authenticate');
    const child = spawn(process.argv[1], ['-c', runner, process.argv[2],
      'chrome-extension://' + 'a'.repeat(32) + '/']);
    const chunks = [], errors = [];
    child.stdout.on('data', chunk => chunks.push(chunk));
    child.stderr.on('data', chunk => errors.push(chunk));
    child.on('error', reject);
    child.on('close', code => { try {
      assert.equal(code, 0); assert.equal(Buffer.concat(errors).length, 0);
      const output = Buffer.concat(chunks);
      const size = endianness() === 'LE' ? output.readUInt32LE(0) : output.readUInt32BE(0);
      assert(size <= 4096); assert.equal(output.length, size + 4);
      resolve(JSON.parse(output.subarray(4)));
    } catch (error) { reject(error); } });
    const body = Buffer.from(JSON.stringify(request)), header = Buffer.alloc(4);
    if (endianness() === 'LE') header.writeUInt32LE(body.length);
    else header.writeUInt32BE(body.length);
    child.stdin.end(Buffer.concat([header, body]));
  }),
};
const stopped = await createBrowserRecovery(ports, config).suspend();
assert.equal(stopped.nativePaused, true);
assert.equal(stopped.serverRevocation, 'unconfirmed');
assert.equal((await createBrowserRecovery(ports, config).tick()).mode, 'paused');
// A trusted native-only administrator reset must not grant browser consent.
const reset = spawnSync(process.argv[1], ['-c', `import sys
from pathlib import Path
from sds200.browser_device_recovery import BrowserDeviceRecovery
ledger = BrowserDeviceRecovery(Path(sys.argv[1]) / 'recovery.sqlite', sys.argv[2])
ledger.resume(ledger.inspect().revision)`, process.argv[2], config.identity], {timeout:10000});
assert.equal(reset.status, 0); assert.equal(reset.stdout.length, 0);
assert.equal(reset.stderr.length, 0);
const restarted = createBrowserRecovery(ports, config);
assert.equal((await restarted.tick()).mode, 'paused');
assert.equal((await restarted.initialize()).mode, 'setup_refused');
assert.equal(restarted.readiness().sessionReady, false);
// Native pause still wins over browser-state replacement.
saved = initialBrowserRecoveryState(config);
assert.equal((await createBrowserRecovery(ports, config).tick()).mode, 'paused');
assert.deepEqual(actions, ['suspend', 'suspend', 'suspend', 'status']);
'''
    result = subprocess.run(
        [node, "--input-type=module", "-e", script, sys.executable, str(root), identity],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == result.stderr == ""
    assert recovery.status().mode is RecoveryMode.PAUSED
