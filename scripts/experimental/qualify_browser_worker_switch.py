"""Fictional worker-cache probe, NOT SDSCTL recovery or deployment acceptance.

Run with a new private directory, absolute Chromium/bubblewrap paths, a public
fixture identity key, and 'stable' or 'changed'. Only private Xvfb is used.
Identical worker bytes select a fictional role from the current manifest name;
this is deliberately NOT a proposed production mode/authority protocol.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from sds200.browser_device_bundle import browser_extension_identity
from sds200.browser_device_launch import _Namespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
from browser_recovery_x11 import X11
from qualify_browser_recovery import put, wait


def fresh_readback(body, *, role, started_ms, seen):
    """Bounded visible fictional response, rejecting restored tabs/old workers."""
    if type(body) is not str or len(body) > 2048:
        raise ValueError("Invalid fixture readback")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate fixture field")
            result[key] = value
        return result

    value = json.loads(body, object_pairs_hook=unique)
    if (type(value) is not dict or set(value) != {
            "role", "manifestRole", "run", "pageRun", "started"}
            or role not in {"A", "B"}
            or value["role"] != role or value["manifestRole"] != role
            or type(value["run"]) is not str
            or not re.fullmatch(r"[0-9a-f]{32}", value["run"])
            or value["pageRun"] != value["run"] or value["run"] in seen
            or type(value["started"]) is not int
            or type(started_ms) not in (int, float) or not math.isfinite(started_ms)
            or not started_ms <= value["started"] <= time.time() * 1000 + 1000):
        raise ValueError("Stale or mismatched fixture worker")
    return value


def fixture_worker(role, *, stable):
    select = "chrome.runtime.getManifest().name.slice(-1)" if stable else json.dumps(role)
    return (
        f"const role={select};\n"
        "const run=crypto.randomUUID().replaceAll('-','');const started=Date.now();\n"
        "const manifestRole=chrome.runtime.getManifest().name.slice(-1);\n"
        "function open(){chrome.tabs.create({url:chrome.runtime.getURL('view.html?run='+run)});}\n"
        "chrome.runtime.onInstalled.addListener(open);chrome.runtime.onStartup.addListener(open);\n"
        "chrome.runtime.onMessage.addListener((m,s,reply)=>{if(m==='probe')"
        "reply({role,manifestRole,run,started});});\n"
    )


def main():
    stage, browser, bwrap, public_key = map(Path, sys.argv[1:5])
    scenario = sys.argv[5]
    assert scenario in {"stable", "changed"} and os.geteuid() != 0
    assert all(p.is_absolute() and p.resolve() == p for p in (stage, browser, bwrap, public_key))
    assert stage.stat().st_uid == os.geteuid() and stage.stat().st_mode & 0o777 == 0o700
    assert not list(stage.iterdir())
    os.umask(0o077)
    for variable, name in (("XDG_CONFIG_HOME", "config"), ("XDG_CACHE_HOME", "cache"),
                           ("XDG_DATA_HOME", "data")):
        (stage / name).mkdir(mode=0o700)
        os.environ[variable] = str(stage / name)
    key = browser_extension_identity(public_key)
    profile = stage / "browser-data"
    profile.mkdir(mode=0o700)
    bundles = {}
    for role in ("A", "B"):
        bundle = stage / role
        bundle.mkdir(mode=0o700)
        bundles[role] = bundle
        put(bundle / "manifest.json", json.dumps({
            "manifest_version": 3, "version": "0.0.4", "name": "Fictional worker " + role,
            "key": key.manifest_key, "background": {"service_worker": "worker.js"},
            "incognito": "not_allowed",
            "content_security_policy": {
                "extension_pages": "script-src 'self'; object-src 'none'; connect-src 'none'"},
        }))
        put(bundle / "worker.js", fixture_worker(role, stable=scenario == "stable"))
        put(bundle / "view.html", "<!doctype html><title>Checking fictional worker</title>"
            "<main></main><script src='view.js'></script>")
        put(bundle / "view.js", "chrome.runtime.sendMessage('probe').then(v=>{"
            "const pageRun=new URL(location.href).searchParams.get('run');"
            "document.querySelector('main').textContent=JSON.stringify({...v,pageRun});"
            "document.title='SDSCTL fictional worker '+v.role+' ready';"
            "}).catch(()=>{document.title='SDSCTL fictional worker refused';});")
    frozen = {p: hashlib.sha256(p.read_bytes()).hexdigest()
              for bundle in bundles.values() for p in bundle.iterdir()}
    equal = (bundles["A"] / "worker.js").read_bytes() == (bundles["B"] / "worker.js").read_bytes()
    assert equal == (scenario == "stable")
    version = subprocess.check_output([browser, "--version"], text=True).strip()
    x = X11()
    scope = keyring = None
    results, seen = [], set()
    try:
        keyring = subprocess.Popen(["gnome-keyring-daemon", "--foreground", "--unlock",
            "--components=secrets", "--control-directory", os.environ["XDG_RUNTIME_DIR"]],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        keyring.stdin.write((secrets.token_hex(32) + "\n").encode())
        keyring.stdin.close()
        wait(lambda: subprocess.run(["gdbus", "call", "--session", "--dest",
            "org.freedesktop.DBus", "--object-path", "/org/freedesktop/DBus", "--method",
            "org.freedesktop.DBus.NameHasOwner", "org.freedesktop.secrets"],
            capture_output=True, timeout=3).stdout.strip() == b"(true,)", 10)
        for index, role in enumerate(("A", "A", "B", "B", "A", "A")):
            mount = stage / f"host-proc-{index}"
            mount.mkdir(mode=0o700)
            command = (str(browser), "--kiosk", "--no-first-run", "--no-default-browser-check",
                "--disable-background-networking", f"--user-data-dir={profile}",
                f"--load-extension={bundles[role]}",
                f"--disable-extensions-except={bundles[role]}", "about:blank")
            started_ms = time.time() * 1000
            scope = _Namespace(bwrap, command, mount)
            scope.preflight()
            scope.send(b"g")
            assert scope.message(time.monotonic() + 5) == {"event": "running"}
            deadline = time.monotonic() + 20
            proof = None
            while time.monotonic() < deadline:
                for window, title in x.windows():
                    if title == f"SDSCTL fictional worker {role} ready - Chromium":
                        try:
                            proof = fresh_readback(x.text(window), role=role,
                                                   started_ms=started_ms, seen=seen)
                        except (ValueError, RuntimeError):
                            continue
                        break
                if proof is not None:
                    break
                time.sleep(0.2)
            if proof:
                seen.add(proof["run"])
            result = {"index": index, "requested_role": role, "fresh_worker": proof is not None,
                      "proof": proof, "titles": [title for _, title in x.windows()]}
            results.append(result)
            print(json.dumps(result), flush=True)
            x.screenshot(stage / f"phase-{index}.png")
            scope.send(b"s")
            assert scope.message(time.monotonic() + 12) == {"event": "stopped"}
            assert scope.child.wait(timeout=3) == 0
            scope.close()
            scope = None
            assert not any((profile / name).exists() or (profile / name).is_symlink()
                for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"))
            assert all(hashlib.sha256(p.read_bytes()).hexdigest() == digest
                       for p, digest in frozen.items())
        result = {"scenario": scenario, "browser": version, "worker_bytes_identical": equal,
                  "all_fresh_workers": all(item["fresh_worker"] for item in results),
                  "manual_refresh_or_reload": False, "production_acceptance": False,
                  "results": results}
        put(stage / "qualification-result.json", json.dumps(result))
        print(json.dumps({k: v for k, v in result.items() if k != "results"}), flush=True)
        expected = [True] * 6 if scenario == "stable" else [True, True, False, False, True, True]
        if [item["fresh_worker"] for item in results] != expected:
            raise RuntimeError("Worker-switch probe outcome did not match the selected experiment")
    finally:
        if scope is not None:
            scope.close()
        if keyring is not None and keyring.poll() is None:
            keyring.terminate()
            try:
                keyring.wait(timeout=3)
            except subprocess.TimeoutExpired:
                keyring.kill()
                keyring.wait(timeout=3)
        x.close()


if __name__ == "__main__":
    if sys.argv[-1] == "--private-session":
        sys.argv.pop()
        main()
    else:
        env = dict(os.environ, XDG_RUNTIME_DIR=tempfile.mkdtemp(
            prefix="sdsctl-worker-switch-", dir="/tmp"))
        for name in ("GNOME_KEYRING_CONTROL", "SSH_AUTH_SOCK", "SESSION_MANAGER",
                     "DBUS_SESSION_BUS_ADDRESS", "AT_SPI_BUS_ADDRESS", "WAYLAND_DISPLAY"):
            env.pop(name, None)
        raise SystemExit(subprocess.run(["dbus-run-session", "--", sys.executable, "-I",
            __file__, *sys.argv[1:], "--private-session"], env=env).returncode)
