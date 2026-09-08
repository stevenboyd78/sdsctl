"""Real Chromium recovery qualification on a private Xvfb, no production targets.

Run with an installed candidate interpreter and the adjacent X11 helper.
The seed extension intentionally writes FICTIONAL pending state via browser APIs.
This qualifies bundle replacement/confirmation/shutdown, NOT how pending state
was originally created, normal authentication, TLS, or physical kiosk usability.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

from sds200.browser_device_bundle import browser_extension_identity, create_browser_bundle
from sds200.browser_device_handoff import BrowserRecoveryHandoff
from sds200.browser_device_launch import _Namespace, run_browser_recovery
from sds200.browser_device_native import load_browser_native_configuration
from sds200.browser_device_profile import create_browser_profile
from sds200.browser_device_recovery import BrowserDeviceRecovery
from sds200.browser_device_registration import MAINTENANCE_MARKER, register_browser_directory
from sds200.browser_device_resume_workflow import BrowserResumeWorkflow
from sds200.browser_device_retirement_bundle import prepare_browser_retirement_bundle
from sds200.browser_device_startup import BrowserStartupError, _launch_lock, check_browser_startup

# The helper is test code, never imported by installed production code.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from browser_recovery_x11 import X11


def put(path, body):
    with path.open("xb") as stream:
        stream.write(body.encode() if isinstance(body, str) else body)
    path.chmod(0o600)


def wait(check, timeout=45):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        result = check()
        if result:
            return result
        time.sleep(0.1)
    raise RuntimeError("Qualification condition deadline")


def main():
    os.umask(0o077)
    stage, browser, bwrap = map(Path, sys.argv[1:4])
    scenario = sys.argv[4] if len(sys.argv) == 5 else "confirm"
    assert scenario in {"confirm", "no-consent"}
    assert os.geteuid() != 0 and all(
        p.is_absolute() and p.resolve() == p for p in (stage, browser, bwrap)
    )
    assert stage.stat().st_uid == os.geteuid() and stage.stat().st_mode & 0o777 == 0o700
    assert not list(stage.iterdir()), "New empty private qualification directory required"
    for key, name in (
        ("XDG_CONFIG_HOME", "config"),
        ("XDG_CACHE_HOME", "cache"),
        ("XDG_DATA_HOME", "data"),
    ):
        (stage / name).mkdir(mode=0o700)
        os.environ[key] = str(stage / name)
    os.environ.pop("WAYLAND_DISPLAY", None)
    os.environ["TMPDIR"] = "/tmp"  # Avoid Unix-socket path limits in long evidence roots.

    def emit(name, **fields):
        print(json.dumps({"step": name, **fields}), flush=True)

    browser_version = subprocess.check_output([browser, "--version"], text=True).strip()
    emit("prepare", scenario=scenario, browser=browser_version)
    put(stage / "private-runtime-path.txt", os.environ["XDG_RUNTIME_DIR"] + "\n")
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=Fictional recovery fixture",
            "-addext",
            "subjectAltName=IP:127.0.0.1",
            "-addext",
            "basicConstraints=critical,CA:TRUE",
            "-keyout",
            str(stage / "ca.key"),
            "-out",
            str(stage / "ca.pem"),
        ],
        check=True,
        capture_output=True,
        timeout=15,
    )
    put(
        stage / "public.pem",
        subprocess.check_output(
            ["openssl", "x509", "-pubkey", "-noout", "-in", str(stage / "ca.pem")], timeout=5
        ),
    )
    key = browser_extension_identity(stage / "public.pem")
    put(
        stage / "enrollment.json",
        json.dumps(
            {
                "version": 1,
                "device_id": "fictional",
                "generation": 1,
                "credential": "sdsctl-browser-v1." + "b" * 64,
                "outcome": {"status": "issued", "completed": False},
            }
        ),
    )
    native, normal, directory = (stage / name for name in ("native", "normal", "browser-data"))
    create_browser_profile(
        native,
        enrollment_file=stage / "enrollment.json",
        ca_file=stage / "ca.pem",
        origin="https://127.0.0.1:8443",
        device_id="fictional",
        extension_id=key.extension_id,
    )
    create_browser_bundle(normal, profile=native, public_key=stage / "public.pem")
    register_browser_directory(
        directory, bundle=normal, profile=native, public_key=stage / "public.pem"
    )
    config = load_browser_native_configuration(native)
    ledger = BrowserDeviceRecovery(native / "recovery.sqlite", config.identity)
    ledger.claim_browser()
    ledger.suspend()
    with _launch_lock(directory):
        pass
    intent = secrets.token_hex(32)
    seed = stage / "seed-extension"
    seed.mkdir(mode=0o700)
    manifest = {
        "manifest_version": 3,
        "version": "0.0.1",
        "name": "Fictional recovery seed",
        "key": key.manifest_key,
        "permissions": ["storage", "cookies", "alarms"],
        "host_permissions": [config.origin + "/*"],
    }
    put(seed / "manifest.json", json.dumps(manifest))
    put(
        seed / "seed.html",
        "<!doctype html><title>Seeding fictional state</title>"
        "<script type='module' src='seed.mjs'></script>",
    )
    state = {
        "version": 2,
        "identity": config.identity,
        "paused": True,
        "phase": "resume_pending",
        "nextAt": 0,
        "intent": intent,
    }
    put(
        seed / "seed.mjs",
        "await chrome.storage.local.set({sdsctlDeviceRecovery:" + json.dumps(state) + "});\n"
        "await chrome.cookies.set({url:'https://127.0.0.1:8443/',name:'__Host-sdsctl-device-session',"
        "value:'fictional-not-a-session',secure:true,httpOnly:true,path:'/'});\n"
        "await chrome.alarms.create('sdsctl-device-recovery',{when:Date.now()+3600000});\n"
        "document.title='SDSCTL fictional seed saved';\n",
    )
    x = X11()
    scope = process = keyring = None
    try:
        # An encrypted fictional keyring on this private bus prevents the test
        # from contacting an existing desktop keyring or requiring a real user's
        # password. No basic/plaintext password-store browser override is used.
        keyring = subprocess.Popen(
            [
                "gnome-keyring-daemon",
                "--foreground",
                "--unlock",
                "--components=secrets",
                "--control-directory",
                os.environ["XDG_RUNTIME_DIR"],
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        keyring.stdin.write((secrets.token_hex(32) + "\n").encode())
        keyring.stdin.close()
        wait(
            lambda: (
                subprocess.run(
                    [
                        "gdbus",
                        "call",
                        "--session",
                        "--dest",
                        "org.freedesktop.DBus",
                        "--object-path",
                        "/org/freedesktop/DBus",
                        "--method",
                        "org.freedesktop.DBus.NameHasOwner",
                        "org.freedesktop.secrets",
                    ],
                    capture_output=True,
                    timeout=3,
                ).stdout.strip()
                == b"(true,)"
            ),
            10,
        )
        mount = stage / "seed-host-proc"
        mount.mkdir(mode=0o700)
        command = (
            str(browser),
            "--kiosk",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-networking",
            f"--user-data-dir={directory}",
            f"--load-extension={seed}",
            f"--disable-extensions-except={seed}",
            f"chrome-extension://{key.extension_id}/seed.html",
        )
        scope = _Namespace(bwrap, command, mount)
        scope.preflight()
        scope.send(b"g")
        assert scope.message(time.monotonic() + 5) == {"event": "running"}
        time.sleep(5)
        emit("seed-visible-titles", titles=[title for _, title in x.windows()])
        window = next(
            (
                wid
                for wid, title in x.windows()
                if title.startswith(f"chrome-extension://{key.extension_id}/seed.html")
            ),
            None,
        )
        if window is not None:
            x.chord(window, "Control_L", "r")
        wait(lambda: any("SDSCTL fictional seed saved" in title for _, title in x.windows()))
        scope.send(b"s")
        assert scope.message(time.monotonic() + 12) == {"event": "stopped"}
        assert scope.child.wait(timeout=3) == 0
        scope.close()
        scope = None
        emit("fictional-browser-api-seed-saved-and-stopped")
        archives, recovery, handoff = (stage / name for name in ("archives", "recovery", "handoff"))
        archives.mkdir(mode=0o700)
        args = dict(
            directory=directory,
            profile=native,
            bundle=normal,
            public_key=stage / "public.pem",
            archives=archives,
        )
        chosen = []

        def consent(review):
            chosen.append(review.operation_id)
            return review.confirmation

        proof = BrowserResumeWorkflow(**args).apply(
            kind="reconcile", browser_intent=intent, confirmation=consent
        )
        assert proof
        args.update(operation_id=chosen[0], browser_intent=intent)
        prepare_browser_retirement_bundle(recovery, **args, handoff=handoff, supervised=True)
        args.update(recovery_bundle=recovery, supervised=True)
        obj = BrowserRecoveryHandoff(handoff, **args)
        frozen = {
            str(p): hashlib.sha256(p.read_bytes()).hexdigest()
            for root in (native, normal, archives)
            for p in root.rglob("*")
            if p.is_file()
        }
        # Fork the trusted local foreground launcher. No CDP, arbitrary runtime
        # switches, changed generated assets or replacement native endpoint.
        process = os.fork()
        if process == 0:
            try:
                result = run_browser_recovery(obj, browser=browser, bwrap=bwrap)
                put(stage / "launcher-result.json", json.dumps({"ok": True, "mode": result.mode}))
                os._exit(0)
            except BaseException as error:
                while error.__context__ is not None:
                    error = error.__context__
                put(
                    stage / "launcher-error.json",
                    json.dumps(
                        {
                            "type": type(error).__name__,
                            "frames": [
                                {"file": Path(frame.filename).name, "line": frame.lineno}
                                for frame in traceback.extract_tb(error.__traceback__)
                            ],
                        }
                    ),
                )
                os._exit(75)
        time.sleep(5)
        x.screenshot(stage / "launched.png")
        emit("recovery-visible-titles", titles=[title for _, title in x.windows()])
        wait(lambda: (handoff / "browser-launch-ready.json").exists(), timeout=65)
        assert not (handoff / "browser-acknowledgement.json").exists()
        emit("matching-real-page-worker-native-ready-no-consent")
        window = wait(
            lambda: next(
                (
                    wid
                    for wid, title in x.windows()
                    if "SDSCTL paused recovery confirmation" in title
                ),
                None,
            )
        )
        x.screenshot(stage / "ready.png")
        if scenario == "no-consent":
            os.kill(process, signal.SIGTERM)
        else:
            # Real trusted keyboard events, not DOM event dispatch or direct
            # native acknowledgement calls. The generated page stays unchanged.
            x.key(window, "Tab")
            x.key(window, "Return")
            time.sleep(5)
            x.screenshot(stage / "reviewed.png")
            x.key(window, "Tab")
            x.key(window, "space")
            x.key(window, "Tab")
            x.key(window, "Return")
        ending = wait(lambda: value if (value := os.waitpid(process, os.WNOHANG))[0] else None, 35)
        process = None
        code = os.waitstatus_to_exitcode(ending[1])
        if scenario == "confirm":
            assert code == 0, "Real recovery confirmation/shutdown did not complete"
            assert obj.confirm().identity == config.identity
            assert obj.restore().identity == config.identity
            assert obj.confirm(restored=True).identity == config.identity
        else:
            assert code == 75
            assert not (handoff / "browser-acknowledgement.json").exists()
        assert frozen == {
            str(p): hashlib.sha256(p.read_bytes()).hexdigest()
            for root in (native, normal, archives)
            for p in root.rglob("*")
            if p.is_file()
        }
        assert (directory / MAINTENANCE_MARKER).exists()
        try:
            check_browser_startup(
                root=directory,
                bundle=normal,
                profile=native,
                public_key=stage / "public.pem",
                browser=browser,
            )
        except BrowserStartupError:
            pass
        else:
            raise AssertionError("Guard unexpectedly released")
        if scenario == "confirm":
            # A separate read-only fixture extension inspects actual browser
            # storage after clean shutdown. It has NO nativeMessaging permission
            # or write APIs, and does not exercise/release the normal launch guard.
            reader = stage / "readback-extension"
            reader.mkdir(mode=0o700)
            put(
                reader / "manifest.json",
                json.dumps(
                    {
                        **manifest,
                        "version": "0.0.5",
                        "name": "Fictional read-only recovery observer",
                    }
                ),
            )
            put(
                reader / "readback.html",
                "<!doctype html><title>Checking recovery readback</title>"
                "<main id='result'></main><script type='module' src='readback.mjs'></script>",
            )
            expected = {
                "version": 1,
                "identity": config.identity,
                "paused": True,
                "phase": "clean",
                "nextAt": 0,
            }
            put(
                reader / "readback.mjs",
                "const expected=" + json.dumps(expected) + ";\n"
                "const values=await chrome.storage.local.get(null);\n"
                "const state=values.sdsctlDeviceRecovery;\n"
                "const cookie=await chrome.cookies.get({url:'https://127.0.0.1:8443/',"
                "name:'__Host-sdsctl-device-session'});\n"
                "const alarm=await chrome.alarms.get('sdsctl-device-recovery');\n"
                "const ok=Object.keys(values).length===1&&state&&"
                "Object.keys(state).sort().join(',')===Object.keys(expected).sort().join(',')&&"
                "Object.keys(expected).every(k=>state[k]===expected[k])&&!cookie&&!alarm;\n"
                "document.getElementById('result').textContent=ok?"
                "'Persisted clean pause; no session cookie; no recovery alarm.'"
                ":'Readback refused';\n"
                "document.title=ok?'SDSCTL readback passed':'SDSCTL readback refused';\n",
            )
            read_mount = stage / "readback-host-proc"
            read_mount.mkdir(mode=0o700)
            read_command = (
                *command[:6],
                f"--load-extension={reader}",
                f"--disable-extensions-except={reader}",
                f"chrome-extension://{key.extension_id}/readback.html",
            )
            scope = _Namespace(bwrap, read_command, read_mount)
            scope.preflight()
            scope.send(b"g")
            assert scope.message(time.monotonic() + 5) == {"event": "running"}
            time.sleep(5)
            window = next(
                (
                    wid
                    for wid, title in x.windows()
                    if title.startswith(f"chrome-extension://{key.extension_id}/readback.html")
                ),
                None,
            )
            if window is not None:
                x.chord(window, "Control_L", "r")  # Fixture-only fresh-extension navigation.
            wait(lambda: any("SDSCTL readback passed" in title for _, title in x.windows()))
            x.screenshot(stage / "readback.png")
            scope.send(b"s")
            assert scope.message(time.monotonic() + 12) == {"event": "stopped"}
            assert scope.child.wait(timeout=3) == 0
            scope.close()
            scope = None
            assert (directory / MAINTENANCE_MARKER).exists()
            emit("browser-restart-readback-clean-paused-no-cookie-no-alarm")
        assert frozen == {
            str(p): hashlib.sha256(p.read_bytes()).hexdigest()
            for root in (native, normal, archives)
            for p in root.rglob("*")
            if p.is_file()
        }
        result = {
            "scenario": scenario,
            "browser": browser_version,
            "guard_retained": True,
            "native_inputs_unchanged": True,
            "acknowledgement_present": (handoff / "browser-acknowledgement.json").exists(),
            "persisted_paused_readback_checked": scenario == "confirm",
        }
        put(stage / "qualification-result.json", json.dumps({"result": "PASS", **result}))
        emit("PASS", **result)
    except BaseException:
        x.screenshot(stage / "failed.png")
        emit("failed-private-display-titles", titles=[title for _, title in x.windows()])
        raise
    finally:
        if process is not None:
            try:
                os.kill(process, signal.SIGTERM)
                wait(lambda: v if (v := os.waitpid(process, os.WNOHANG))[0] else None, 15)
            except (ProcessLookupError, ChildProcessError):
                pass
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
    import signal

    if sys.argv[-1] == "--private-session":
        sys.argv.pop()
        main()
    else:
        root = Path(sys.argv[1])
        assert root.is_absolute() and root.resolve() == root and not list(root.iterdir())
        env = dict(
            os.environ,
            XDG_CONFIG_HOME=str(root / "config"),
            XDG_CACHE_HOME=str(root / "cache"),
            XDG_DATA_HOME=str(root / "data"),
            XDG_RUNTIME_DIR=tempfile.mkdtemp(prefix="sdsctl-q-runtime-", dir="/tmp"),
        )
        for name in (
            "GNOME_KEYRING_CONTROL",
            "SSH_AUTH_SOCK",
            "SESSION_MANAGER",
            "DBUS_SESSION_BUS_ADDRESS",
            "AT_SPI_BUS_ADDRESS",
        ):
            env.pop(name, None)
        result = subprocess.run(
            [
                "dbus-run-session",
                "--",
                sys.executable,
                "-I",
                __file__,
                *sys.argv[1:],
                "--private-session",
            ],
            env=env,
        )
        raise SystemExit(result.returncode)
