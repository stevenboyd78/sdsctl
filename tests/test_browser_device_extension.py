"""Run the deterministic browser-coordination contract without a real browser."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from sds200.browser_device_native import load_browser_native_configuration
from sds200.browser_device_recovery import BrowserDeviceRecovery, RecoveryMode


def test_browser_recovery_coordinator_contract() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")
    result = subprocess.run(
        [node, "--test", "scripts/experimental/test_browser_device_recovery.mjs",
         "scripts/experimental/test_browser_device_logout.mjs",
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


def test_generated_bundle_harness_help_is_non_mutating() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")
    result = subprocess.run(
        [node, "scripts/experimental/audit_browser_bundle.mjs", "--help"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0 and result.stderr == ""
    assert "no login or production access" in result.stdout
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
import {spawn} from 'node:child_process';
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
// Native pause still wins over browser-state replacement.
saved = initialBrowserRecoveryState(config);
assert.equal((await createBrowserRecovery(ports, config).tick()).mode, 'paused');
assert.deepEqual(actions, ['suspend', 'suspend', 'status']);
'''
    result = subprocess.run(
        [node, "--input-type=module", "-e", script, sys.executable, str(root), identity],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == result.stderr == ""
    assert recovery.status().mode is RecoveryMode.PAUSED
