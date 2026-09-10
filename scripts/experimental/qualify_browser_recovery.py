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
import socket
import subprocess
import sys
import tempfile
import time
import traceback
from contextlib import ExitStack
from pathlib import Path

from sds200.browser_device_bundle import browser_extension_identity, create_browser_bundle
from sds200.browser_device_guard_release import BrowserPausedGuardRelease
from sds200.browser_device_handoff import BrowserRecoveryHandoff
from sds200.browser_device_launch import _Namespace, run_browser_recovery
from sds200.browser_device_native import load_browser_native_configuration
from sds200.browser_device_profile import create_browser_profile
from sds200.browser_device_recovery import BrowserDeviceRecovery
from sds200.browser_device_registration import MAINTENANCE_MARKER, register_browser_directory
from sds200.browser_device_resume_workflow import BrowserResumeWorkflow
from sds200.browser_device_retirement_bundle import prepare_browser_retirement_bundle
from sds200.browser_device_startup import (
    BrowserStartupError,
    _launch_lock,
    browser_startup_command,
    check_browser_startup,
)

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


def released_pause_text(text):
    """Require the current paused-only warning, never an ordinary resumable pause."""
    normalized = " ".join(text.split())
    expected = (
        "Automatic sign-in is paused after completed recovery. A separate administrator "
        "continuation is required before this display can sign in again. Keep the saved "
        "profile and recovery evidence; do not repeat setup or remove the guard."
    )
    return expected in normalized and "Review automatic sign-in resume" not in normalized


def main():
    os.umask(0o077)
    initial_owner = ExitStack()
    stage, browser, bwrap = map(Path, sys.argv[1:4])
    scenario = sys.argv[4] if len(sys.argv) == 5 else "confirm"
    assert scenario in {"confirm", "no-consent", "release", "release-seed-first",
                        "continuation-read"}
    continuing = scenario == "continuation-read"
    releasing = scenario in {"release", "release-seed-first", "continuation-read"}
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
    # Own a fictional endpoint, accept no TLS and read no request bodies. Queued
    # connections are checked at every boundary; no background thread crosses
    # the recovery launcher's fork/PID-namespace boundary.
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    listener.setblocking(False)
    origin = f"https://127.0.0.1:{listener.getsockname()[1]}"

    def no_connections():
        try:
            peer, _ = listener.accept()
        except BlockingIOError:
            return
        peer.close()
        raise AssertionError("Unexpected fictional authentication-endpoint connection")
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
        origin=origin,
        device_id="fictional",
        extension_id=key.extension_id,
    )
    create_browser_bundle(normal, profile=native, public_key=stage / "public.pem")
    registered = register_browser_directory(
        directory, bundle=normal, profile=native, public_key=stage / "public.pem"
    )
    config = load_browser_native_configuration(native)
    ledger = BrowserDeviceRecovery(native / "recovery.sqlite", config.identity)
    if scenario not in {"release", "continuation-read"}:
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
        "await chrome.cookies.set({url:" + json.dumps(origin + "/")
        + ",name:'__Host-sdsctl-device-session',"
        "value:'fictional-not-a-session',secure:true,httpOnly:true,path:'/'});\n"
        "await chrome.alarms.create('sdsctl-device-recovery',{when:Date.now()+3600000});\n"
        "document.title='SDSCTL fictional seed saved';\n",
    )
    x = X11()
    scope = process = keyring = ordinary = None
    phase = "private-session"
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
        if scenario in {"release", "continuation-read"}:
            initial_owner.enter_context(_launch_lock(directory))
            phase = "prior-normal-setup"
            # Establish the actual prior normal installation in Chromium, not
            # just filesystem registration. The seed below is still deliberately
            # synthetic pending state, but no longer the browser's first bundle.
            initial_mount = stage / "initial-host-proc"
            initial_mount.mkdir(mode=0o700)
            initial_command = browser_startup_command(directory, browser=browser,
                bundle=normal, registration=registered, setup=True)
            scope = _Namespace(bwrap, initial_command, initial_mount)
            scope.preflight()
            scope.send(b"g")
            assert scope.message(time.monotonic() + 5) == {"event": "running"}
            window = wait(lambda: next((wid for wid, title in x.windows()
                if "SDSCTL experimental first-run setup" in title), None), 45)
            wait(lambda: "Initialization saves local state" in x.text(window))
            emit("normal-setup-idle-before-first-confirmation", seconds=55)
            time.sleep(55)  # No browser/native calls until the trusted input below.
            x.key(window, "Tab")
            x.key(window, "space")
            x.key(window, "Tab")
            x.key(window, "Return")
            wait(lambda: "Setup saved. No login was attempted." in x.text(window))
            x.screenshot(stage / "initial-normal-setup.png")
            scope.send(b"s")
            assert scope.message(time.monotonic() + 12) == {"event": "stopped"}
            assert scope.child.wait(timeout=3) == 0
            scope.close()
            scope = None
            initial_owner.close()
            assert ledger.inspect().revision == 2
            ledger.suspend()  # Fictional pre-maintenance pause, never after release.
            no_connections()
            emit("prior-canonical-normal-setup-completed-and-stopped")
        phase = "synthetic-pending-seed"
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
        phase = "supervised-recovery"
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
            emit("recovery-idle-before-first-review", seconds=55)
            time.sleep(55)  # Natural MV3 idle; no consent/readiness messages sent.
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
        if scenario == "confirm" or releasing:
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
        no_connections()
        if releasing:
            phase = "local-guard-release"
            # Exact recovery-produced browser pause: no storage fixture rewrite
            # or extra native suspend between ACK and the normal product starts.
            old_ack = obj.confirm(restored=True)
            released = BrowserPausedGuardRelease(obj).apply(
                confirmation=lambda review: review.confirmation)
            assert released and released.mode == "paused"
            assert obj.confirm(restored=True) == old_ack
            assert BrowserPausedGuardRelease(obj).confirm(
                release_id=released.release_id) == released
            startup_args = dict(root=directory, bundle=normal, profile=native,
                                public_key=stage / "public.pem", browser=browser)
            registered = check_browser_startup(**startup_args)
            expected_command = browser_startup_command(
                directory, browser=browser, bundle=normal, registration=registered)
            assert expected_command[-1] == (
                f"chrome-extension://{registered.extension_id}/startup.html")
            put(stage / "ordinary-expected-argv.json", json.dumps(expected_command))
            cli = [sys.executable, "-I", "-c",
                   "from sds200.cli import main; raise SystemExit(main())",
                   "browser-device-start", "--experimental", "--directory", str(directory),
                   "--browser", str(browser), "--bundle", str(normal), "--profile", str(native),
                   "--public-key", str(stage / "public.pem")]
            # Reuse the separately tested bounded fixture-only argv observer.
            from qualify_browser_first_start import process_arguments

            for name in ("released-normal-start", "released-normal-restart"):
                phase = name
                ordinary = subprocess.Popen(cli, stdin=subprocess.DEVNULL,
                                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

                def ordinary_window(child=ordinary):
                    assert child.poll() is None, "Ordinary foreground startup exited"
                    return next((wid for wid, title in x.windows()
                                 if "SDSCTL managed display startup" in title), None)

                try:
                    window = wait(ordinary_window, 45)
                except BaseException:
                    # Only this private fictional browser: inspect visible tab
                    # titles using keyboard navigation, never storage or CDP.
                    for diagnostic in range(4):
                        visible = x.windows()
                        emit("ordinary-tab-diagnostic", index=diagnostic,
                             titles=[title for _, title in visible])
                        if not visible:
                            break
                        x.chord(visible[0][0], "Control_L", "Tab")
                        time.sleep(1)
                    raise

                def paused_text(child=ordinary, selected=window):
                    assert child.poll() is None
                    no_connections()
                    text = x.text(selected)
                    return text if released_pause_text(text) else None

                text = wait(paused_text, 60)
                put(stage / (name + "-visible.txt"), text)
                x.screenshot(stage / (name + ".png"))
                children = Path(
                    f"/proc/{ordinary.pid}/task/{ordinary.pid}/children").read_text().split()
                observed = []
                for pid in children:
                    try:
                        with Path(f"/proc/{int(pid)}/cmdline").open("rb") as stream:
                            values = process_arguments(stream.read(16385))
                    except FileNotFoundError:
                        continue
                    if f"--user-data-dir={directory}" in values:
                        observed.append(values)
                assert len(observed) == 1
                assert all(arg in observed[0] for arg in expected_command[1:])
                assert not any(arg.startswith(("--no-sandbox", "--disable-setuid-sandbox",
                    "--ignore-certificate", "--remote-debugging", "--headless", "--password-store",
                    "--test-type")) for arg in observed[0])
                put(stage / (name + "-observed-argv.json"), json.dumps(observed[0]))
                ordinary.send_signal(signal.SIGTERM)
                assert ordinary.wait(timeout=15) == 0
                ordinary = None
                check_browser_startup(**startup_args)
                no_connections()
                emit(name, paused=True, manual_reload=False, authentication_connections=0)
            assert obj.confirm(restored=True) == old_ack
            assert BrowserPausedGuardRelease(obj).confirm(
                release_id=released.release_id) == released
            if continuing:
                phase = "actual-continuation-reader"
                from qualify_browser_continuation_read import qualify

                frozen = qualify(obj, released, startup_args, expected_command,
                    stage, x, wait, put, emit, no_connections)
        if scenario == "confirm" or releasing:
            phase = "read-only-observer"
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
                "const cookie=await chrome.cookies.get({url:" + json.dumps(origin + "/") + ","
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
        no_connections()
        result = {
            "scenario": scenario,
            "browser": browser_version,
            "guard_retained": True,
            "native_inputs_unchanged": not continuing,
            "explicit_fixture_continuation_activation": continuing,
            "activated_inputs_unchanged_by_reader": continuing,
            "actual_continuation_reader_starts": 2 if continuing else 0,
            "continuation_cdp_diagnostic": bool(os.environ.get("SDSCTL_READER_PLAYWRIGHT"))
                if continuing else False,
            "acknowledgement_present": (handoff / "browser-acknowledgement.json").exists(),
            "persisted_paused_readback_checked": scenario == "confirm" or releasing,
            "ordinary_paused_starts_after_release": 2 if releasing else 0,
            "authentication_connections": 0,
        }
        put(stage / "qualification-result.json", json.dumps({"result": "PASS", **result}))
        emit("PASS", **result)
    except BaseException:
        try:
            no_connections()
            no_endpoint_connection = True
        except AssertionError:
            no_endpoint_connection = False
        try:
            if phase == "actual-continuation-reader":
                from sds200.browser_device_continuation_current import inspect_stopped_continuation

                observed = inspect_stopped_continuation(directory, bundle=normal, profile=native,
                                                        public_key=stage / "public.pem")
            else:
                observed = ledger.inspect()
            native_mode = str(observed.mode)
        except Exception:
            native_mode = "unconfirmed"  # Diagnostic failure must not mask original failure.
        failure = {"result": "FAIL", "scenario": scenario, "browser": browser_version,
                   "phase": phase, "no_endpoint_connection": no_endpoint_connection,
                   "guard_retained": (directory / MAINTENANCE_MARKER).exists(),
                   "native_mode": native_mode}
        put(stage / "qualification-failure.json", json.dumps(failure))
        emit("FAIL", **failure)
        x.screenshot(stage / "failed.png")
        emit("failed-private-display-titles", titles=[title for _, title in x.windows()])
        raise
    finally:
        if ordinary is not None and ordinary.poll() is None:
            ordinary.send_signal(signal.SIGTERM)
            ordinary.wait(timeout=15)
        if process is not None:
            try:
                os.kill(process, signal.SIGTERM)
                wait(lambda: v if (v := os.waitpid(process, os.WNOHANG))[0] else None, 15)
            except (ProcessLookupError, ChildProcessError):
                pass
        if scope is not None:
            scope.close()
        initial_owner.close()
        if keyring is not None and keyring.poll() is None:
            keyring.terminate()
            try:
                keyring.wait(timeout=3)
            except subprocess.TimeoutExpired:
                keyring.kill()
                keyring.wait(timeout=3)
        x.close()
        listener.close()


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
