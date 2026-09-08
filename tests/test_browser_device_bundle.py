from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import shutil
import struct
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from sds200 import browser_device_bundle as bundle
from sds200 import browser_device_native as native
from sds200 import cli
from sds200.browser_device_profile import create_browser_profile
from sds200.browser_device_recovery import BrowserDeviceRecovery, ExchangeFailure, RecoveryMode
from tests.test_browser_device_native import certificates as certificates
from tests.test_browser_device_native import frame
from tests.test_browser_device_profile import CREDENTIAL, issuance, private, snapshot

pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or os.geteuid() == 0, reason="Non-root Linux browser bundle",
)


@pytest.fixture
def public_key(tmp_path, certificates):
    result = subprocess.run(
        ["openssl", "x509", "-pubkey", "-noout", "-in", str(certificates[0][0])],
        check=True, capture_output=True,
    )
    path = tmp_path / "public.pem"
    private(path, result.stdout)
    return path


@pytest.fixture
def profile(tmp_path, public_key, certificates):
    private(tmp_path / "enrollment.json", json.dumps(issuance()))
    private(tmp_path / "ca.pem", certificates[0][0].read_bytes())
    root = tmp_path / "native profile's $(no-shell)"
    create_browser_profile(
        root, enrollment_file=tmp_path / "enrollment.json", ca_file=tmp_path / "ca.pem",
        origin="https://127.0.0.1:8443", device_id="display",
        extension_id=bundle.browser_extension_identity(public_key).extension_id,
    )
    return root


def create(tmp_path, public_key, profile, name="review bundle's $(no-shell)"):
    root = tmp_path / name
    bundle.create_browser_bundle(root, profile=profile, public_key=public_key)
    return root


def invoke(root, origin, action="status", **kwargs):
    result = subprocess.run([str(root / "native-host"), origin],
                            input=frame({"version": 1, "action": action}),
                            capture_output=True, timeout=13, **kwargs)
    assert result.returncode == 0 and result.stderr == b""
    size = struct.unpack("=I", result.stdout[:4])[0]
    assert len(result.stdout) == size + 4
    assert CREDENTIAL.encode() not in result.stdout
    return json.loads(result.stdout[4:])


def test_public_key_identity_matches_node_and_is_path_independent(tmp_path, public_key):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node identity cross-check")
    result = bundle.browser_extension_identity(public_key)
    checked = subprocess.run(
        [node, "--input-type=module", "-e", """
import {createPublicKey, createHash} from 'node:crypto';
import {readFileSync} from 'node:fs';
const key = createPublicKey(readFileSync(process.argv[1])).export({type:'spki',format:'der'});
const hash = createHash('sha256').update(key).digest('hex');
const id = [...hash.slice(0,32)].map(c => String.fromCharCode(97+parseInt(c,16))).join('');
console.log(JSON.stringify({id, hash, key:key.toString('base64')}));
""", str(public_key)], capture_output=True, check=True,
    )
    expected = json.loads(checked.stdout)
    assert (result.extension_id, result.public_key_sha256, result.manifest_key) == (
        expected["id"], expected["hash"], expected["key"],
    )
    private(tmp_path / "relocated.pem", public_key.read_bytes().replace(b"\n", b"\r\n"))
    assert bundle.browser_extension_identity(tmp_path / "relocated.pem") == result


@pytest.mark.parametrize("malformed", [
    b"", b"secret-do-not-echo", b"x" * 4097,
    b"-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----\n",
    b"-----BEGIN PUBLIC KEY-----\nAAAA\n-----END PUBLIC KEY-----\n",
    b"-----BEGIN PUBLIC KEY-----\nA===\n-----END PUBLIC KEY-----\n",
    b"-----BEGIN PUBLIC KEY-----\n" + b"\n" * 4000 + b"-----END PUBLIC KEY-----\n",
])
def test_bad_keys_are_bounded_and_redacted(public_key, malformed):
    private(public_key, malformed)
    with pytest.raises(bundle.BrowserBundleError, match="Extension public key is invalid") as error:
        bundle.browser_extension_identity(public_key)
    assert "secret-do-not-echo" not in str(error.value)


@pytest.mark.parametrize("change", ["prefix", "suffix", "double", "der-suffix"])
def test_public_key_rejects_ignored_extra_material(public_key, change):
    original = public_key.read_bytes()
    if change == "der-suffix":
        der = base64.b64decode(b"".join(original.splitlines()[1:-1]))
        body = (b"-----BEGIN PUBLIC KEY-----\n" + base64.b64encode(der + b"ignored")
                + b"\n-----END PUBLIC KEY-----\n")
    else:
        body = {"prefix": b"extra" + original, "suffix": original + b"extra",
                "double": original * 2}[change]
    private(public_key, body)
    with pytest.raises(bundle.BrowserBundleError):
        bundle.browser_extension_identity(public_key)


@pytest.mark.parametrize("unsafe", ["mode", "parent", "symlink", "hardlink", "fifo"])
def test_unsafe_key_files_rejected(tmp_path, public_key, unsafe):
    if unsafe == "mode":
        public_key.chmod(0o644)
    elif unsafe == "parent":
        tmp_path.chmod(0o755)
    elif unsafe == "symlink":
        target = tmp_path / "target"
        public_key.rename(target)
        public_key.symlink_to(target)
    elif unsafe == "hardlink":
        os.link(public_key, tmp_path / "copy")
    else:
        public_key.unlink()
        os.mkfifo(public_key, 0o600)
    with pytest.raises(bundle.BrowserBundleError):
        bundle.browser_extension_identity(public_key)


@pytest.mark.parametrize("failure", ["missing", "timeout", "invalid", "noncanonical"])
def test_openssl_failure_is_redacted_and_bounded(public_key, monkeypatch, failure):
    if failure == "missing":
        monkeypatch.setattr(bundle.shutil, "which", lambda *a, **kw: None)
    else:
        def run(args, **kwargs):
            assert kwargs["timeout"] == 5 and kwargs["capture_output"]
            assert kwargs["env"]["OPENSSL_CONF"] == os.devnull
            if failure == "timeout":
                raise subprocess.TimeoutExpired("do-not-echo", 5)
            return subprocess.CompletedProcess(args, 1 if failure == "invalid" else 0,
                                               b"do-not-echo", b"do-not-echo")
        monkeypatch.setattr(bundle.subprocess, "run", run)
    with pytest.raises(bundle.BrowserBundleError) as error:
        bundle.browser_extension_identity(public_key)
    assert "do-not-echo" not in str(error.value)


def test_manifest_assets_receipt_and_no_secret_copy(tmp_path, public_key, profile):
    before = snapshot(profile)
    root = create(tmp_path, public_key, profile)
    assert snapshot(profile) == before
    extension = root / "extension"
    manifest = json.loads((extension / "manifest.json").read_bytes())
    config = native.load_browser_native_configuration(profile)
    assert manifest["manifest_version"] == 3
    assert manifest["permissions"] == ["nativeMessaging", "storage", "cookies", "alarms", "tabs"]
    assert manifest["host_permissions"] == ["https://127.0.0.1:8443/*"]
    assert manifest["content_scripts"] == [{
        "matches": ["https://127.0.0.1:8443/", "https://127.0.0.1:8443/device-display"],
        "js": ["content.js"],
        "run_at": "document_start", "all_frames": False, "world": "ISOLATED",
    }]
    assert manifest["incognito"] == "not_allowed"
    assert not set(manifest) & {"externally_connectable", "web_accessible_resources", "update_url"}
    assert manifest["key"] == bundle.browser_extension_identity(public_key).manifest_key
    host = json.loads((root / (bundle.NATIVE_HOST + ".json")).read_bytes())
    assert host["allowed_origins"] == [config.extension_origin]
    assert host["path"] == str(root / "native-host") and host["type"] == "stdio"
    receipt = json.loads((root / "bundle.json").read_bytes())
    assert receipt["identity"] == config.identity and receipt["experimental"] is True
    actual = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert set(actual) == set(receipt["files"]) | {"bundle.json"}
    assert not any(CREDENTIAL.encode() in body or b"BEGIN CERTIFICATE" in body
                   for body in actual.values())
    for name, digest in receipt["files"].items():
        assert hashlib.sha256(actual[name]).hexdigest() == digest
    for path in [root, *root.rglob("*")]:
        mode = 0o700 if path.is_dir() or path.name == "native-host" else 0o600
        assert path.stat().st_mode & 0o777 == mode
    for name in bundle.MODULES:
        canonical = bundle.files("sds200.browser_assets").joinpath(name).read_bytes()
        assert actual["extension/" + name] == canonical
    assert "initialBrowserRecoveryState" not in (extension / "worker.mjs").read_text()
    assert not (extension / "recovery.html").exists()
    assert not (extension / "browser_device_retirement_ui.mjs").exists()
    assert "connectRetirementWorker" not in (extension / "worker.mjs").read_text()
    denied = subprocess.run([str(root / "native-host"), config.extension_origin],
        input=frame({"version": 1, "action": "confirm-retirement",
                     "identity": config.identity, "intent": "f" * 64}),
        capture_output=True, timeout=13)
    assert denied.returncode == 0 and denied.stderr == b""
    assert json.loads(denied.stdout[4:]) == {"version": 1, "ok": False, "mode": "setup_error"}
    assert snapshot(profile) == before
    assert "window === window.top && ['/', '/device-display']" in (
        extension / "content.js"
    ).read_text()


@pytest.mark.parametrize("origin,pattern", [
    ("https://display.example", "https://display.example:443/"),
    ("https://192.168.1.20:8443", "https://192.168.1.20:8443/"),
    ("https://[fd00::20]:8443", "https://[fd00::20]:8443/"),
])
def test_generated_scope_supports_dns_and_ip(public_key, tmp_path, origin, pattern):
    key = bundle.browser_extension_identity(public_key)
    config = native.parse_browser_native_configuration(tmp_path, json.dumps({
        "version": 1, "origin": origin, "device_id": "display",
        "extension_origin": f"chrome-extension://{key.extension_id}/",
    }).encode())
    manifest = json.loads(bundle._artifacts(tmp_path, config, key)["extension/manifest.json"])
    assert manifest["host_permissions"] == [pattern + "*"]
    assert manifest["content_scripts"][0]["matches"] == [pattern, pattern + "device-display"]


def test_generated_worker_and_content_execute_with_empty_state_and_exact_scope(
    tmp_path, public_key, profile,
):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node generated entrypoint checks")
    root = create(tmp_path, public_key, profile)
    result = subprocess.run([node, "--input-type=module", "-e", """
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {pathToFileURL} from 'node:url';
import vm from 'node:vm';
const extension = process.argv[1];
const origin = 'https://127.0.0.1:8443';
const id = JSON.parse(readFileSync(extension + '/../bundle.json')).extension_id;
const handlers = [];
const forbidden = () => assert.fail('Unprovisioned worker attempted native I/O or state writes');
globalThis.chrome = {
  runtime: {id, getURL: name => `chrome-extension://${id}/${name}`,
    onStartup: {addListener: () => {}}, onInstalled: {addListener: () => {}},
    onMessage: {addListener: fn => handlers.push(fn)}, sendNativeMessage: forbidden},
  storage: {local: {setAccessLevel: async level =>
    assert.deepEqual(level,{accessLevel:'TRUSTED_CONTEXTS'}),
    get: async () => ({}), set: forbidden}},
  cookies: {remove: async () => null, get: async () => null, set: forbidden},
  tabs: {onUpdated: {addListener: () => {}}, query: async () => [], reload: forbidden},
  alarms: {onAlarm: {addListener: () => {}}, create: forbidden, clear: async () => true},
};
await import(pathToFileURL(extension + '/worker.mjs'));
await new Promise(resolve => setTimeout(resolve, 0));
let response;
for (const handler of handlers) handler({action:'status'},
  {id, url:chrome.runtime.getURL('control.html')}, result => { response=result; });
assert.equal(response.mode, 'setup_error');
const content = readFileSync(extension + '/content.js', 'utf8');
for (const [href, child, count] of [[origin+'/',false,1], [origin+'/',true,0],
  [origin+'/device-display',false,1], [origin+'/device-display?extra',false,0],
  [origin+'/?query',false,0], [origin+'/other',false,0], ['https://127.0.0.1:9443/',false,0],
  ['https://another.example/',false,0]]) {
  let listeners=0;
  const window={location:{href}};
  window.top=child ? {} : window;
  vm.runInNewContext(content, {window, location:window.location, URL,
    document:{addEventListener: () => listeners++}, chrome, fetch:forbidden});
  assert.equal(listeners,count);
}
""", str(root / "extension")], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""


@pytest.mark.parametrize("mode", [RecoveryMode.ACTIVE, RecoveryMode.PAUSED, RecoveryMode.REJECTED,
                                   RecoveryMode.TLS_ERROR, RecoveryMode.PROTOCOL_ERROR])
def test_creation_never_resets_profile_or_overwrites(tmp_path, public_key, profile, mode):
    config = native.load_browser_native_configuration(profile)
    recovery = BrowserDeviceRecovery(profile / "recovery.sqlite", config.identity)
    if mode is RecoveryMode.PAUSED:
        recovery.suspend()
    elif mode is not RecoveryMode.ACTIVE:
        def fail():
            raise ExchangeFailure(mode)
        recovery.authenticate(fail)
    before = snapshot(profile)
    root = create(tmp_path, public_key, profile)
    assert snapshot(profile) == before
    assert invoke(root, config.extension_origin)["mode"] == mode.value
    saved = {p: snapshot(p) for p in (root, root / "extension", profile)}
    with pytest.raises(bundle.BrowserBundleError, match="already exists"):
        bundle.create_browser_bundle(root, profile=profile, public_key=public_key)
    assert {p: snapshot(p) for p in saved} == saved


def test_launcher_isolated_quoting_and_exact_caller(tmp_path, public_key, profile):
    root = create(tmp_path, public_key, profile)
    config = native.load_browser_native_configuration(profile)
    shadow = tmp_path / "sds200"
    shadow.mkdir()
    (shadow / "__init__.py").write_text("raise RuntimeError('UNTRUSTED IMPORT')")
    env = {**os.environ, "PYTHONPATH": str(tmp_path), "PYTHONHOME": str(tmp_path)}
    assert invoke(root, config.extension_origin, cwd=tmp_path, env=env)["mode"] == "active"
    assert invoke(root, "chrome-extension://" + "a" * 32 + "/")["ok"] is False
    assert invoke(root, config.extension_origin, action="suspend")["mode"] == "paused"
    assert invoke(root, config.extension_origin)["mode"] == "paused"


@pytest.mark.parametrize("action", ["status", "authenticate", "suspend", "claim-browser"])
def test_pinned_identity_rejects_before_ledger_or_secret_access(profile, monkeypatch, action):
    config = native.load_browser_native_configuration(profile)
    def forbidden(*args, **kwargs):
        pytest.fail("Mismatched wrapper reached ledger or exchange")
    monkeypatch.setattr(native, "BrowserDeviceRecovery", forbidden)
    monkeypatch.setattr(native, "exchange_browser_device", forbidden)
    destination = io.BytesIO()
    assert native._native_request(
        profile, [config.extension_origin], io.BytesIO(frame({"version": 1, "action": action})),
        destination, expected_identity="0" * 64,
    ) == 0
    assert json.loads(destination.getvalue()[4:]) == {
        "version": 1, "ok": False, "mode": "setup_error",
    }


def test_actual_launcher_refuses_retargeted_profile(tmp_path, public_key, profile):
    root = create(tmp_path, public_key, profile)
    config = native.load_browser_native_configuration(profile)
    document = json.loads((profile / "client.json").read_bytes())
    document["origin"] = "https://other.example"
    private(profile / "client.json", json.dumps(document))
    before = snapshot(profile)
    assert invoke(root, config.extension_origin, action="suspend")["ok"] is False
    assert snapshot(profile) == before


def test_valid_but_different_public_key_cannot_package_profile(
    tmp_path, public_key, profile, certificates,
):
    other = subprocess.run(
        ["openssl", "x509", "-pubkey", "-noout", "-in", str(certificates[1][0])],
        check=True, capture_output=True,
    ).stdout
    before = snapshot(profile)
    private(public_key, other)
    config = native.load_browser_native_configuration(profile)
    assert bundle.browser_extension_identity(public_key).extension_id not in config.extension_origin
    with pytest.raises(bundle.BrowserBundleError, match="inputs are invalid"):
        bundle.create_browser_bundle(tmp_path / "mismatch", public_key=public_key, profile=profile)
    assert not (tmp_path / "mismatch").exists() and snapshot(profile) == before


@pytest.mark.parametrize("bad", ["identity", "profile", "destination", "interpreter"])
def test_invalid_inputs_leave_no_bundle(tmp_path, public_key, profile, monkeypatch, bad):
    root = tmp_path / "bundle"
    if bad == "identity":
        document = json.loads((profile / "client.json").read_bytes())
        document["extension_origin"] = "chrome-extension://" + "a" * 32 + "/"
        private(profile / "client.json", json.dumps(document))
    elif bad == "profile":
        (profile / "device.secret").chmod(0o644)
    elif bad == "destination":
        root = Path("relative")
    else:
        monkeypatch.setattr(bundle.sys, "executable", "relative-python")
    before = snapshot(profile)
    with pytest.raises(bundle.BrowserBundleError, match="inputs are invalid"):
        bundle.create_browser_bundle(root, public_key=public_key, profile=profile)
    assert not root.exists() and snapshot(profile) == before


def test_partial_failure_retained_without_completion_marker(
    tmp_path, public_key, profile, monkeypatch,
):
    root = tmp_path / "bundle"
    writer = bundle._write
    def fail(descriptor, name, body):
        if name == "manifest.json":
            raise OSError("do-not-echo")
        writer(descriptor, name, body)
    monkeypatch.setattr(bundle, "_write", fail)
    before = snapshot(profile)
    with pytest.raises(bundle.BrowserBundleError, match="could not be confirmed"):
        bundle.create_browser_bundle(root, public_key=public_key, profile=profile)
    assert (root / "extension/browser_device_recovery.mjs").is_file()
    assert not (root / "bundle.json").exists()
    assert snapshot(profile) == before
    with pytest.raises(bundle.BrowserBundleError, match="already exists"):
        bundle.create_browser_bundle(root, public_key=public_key, profile=profile)


def test_concurrent_creation_has_one_winner(tmp_path, public_key, profile):
    root = tmp_path / "bundle"
    def attempt(_):
        try:
            bundle.create_browser_bundle(root, public_key=public_key, profile=profile)
            return True
        except bundle.BrowserBundleError:
            return False
    before = snapshot(profile)
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, range(2))) == [False, True]
    assert (root / "bundle.json").is_file() and snapshot(profile) == before


def test_cli_explicit_opt_in_and_safe_output(tmp_path, public_key, profile, capsys):
    with pytest.raises(SystemExit) as error:
        cli.main(["browser-device-bundle", "identity", "--public-key", str(public_key)])
    assert error.value.code == 2
    capsys.readouterr()
    assert cli.main(["browser-device-bundle", "--experimental", "identity",
                     "--public-key", str(public_key)]) == 0
    assert "Extension ID:" in capsys.readouterr().out
    assert cli.main(["browser-device-bundle", "--experimental", "create",
                     "--directory", str(tmp_path / "bundle"), "--profile", str(profile),
                     "--public-key", str(public_key)]) == 0
    output = capsys.readouterr()
    assert "No browser registration" in output.out and output.err == ""
    private(public_key, b"do-not-echo")
    assert cli.main(["browser-device-bundle", "--experimental", "identity",
                     "--public-key", str(public_key)]) == 78
    output = capsys.readouterr()
    assert "do-not-echo" not in output.err and str(public_key) not in output.err
