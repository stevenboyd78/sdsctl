"""Exact headed foreground startup on a private Xvfb with fictional profiles.

No browser debugging, injected extension state, trust changes or real server.
Invoke with an installed candidate interpreter, a new empty mode-0700 case
directory and an absolute Chromium executable. Keep the adjacent X11 helper and
qualify_browser_recovery.py available. Never use a production browser directory.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from sds200.browser_device_bundle import browser_extension_identity, create_browser_bundle
from sds200.browser_device_native import load_browser_native_configuration
from sds200.browser_device_profile import create_browser_profile, inspect_browser_profile
from sds200.browser_device_recovery import BrowserDeviceRecovery
from sds200.browser_device_registration import register_browser_directory
from sds200.browser_device_startup import browser_startup_command, check_browser_startup

sys.path.insert(0, str(Path(__file__).resolve().parent))
from browser_recovery_x11 import X11
from qualify_browser_recovery import put, wait


def snapshot(root):
    return {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()
    }


def process_arguments(body):
    """Bounded fixture-only parsing, including Chromium's rewritten process title.

    Qualification paths deliberately exclude whitespace. This is not a general
    process-discovery, ownership or production argument-parsing boundary.
    """
    if not body or len(body) > 16384:
        raise ValueError("Fixture process arguments unavailable or oversized")
    values = os.fsdecode(body).rstrip("\0").split("\0")
    return values[0].split() if len(values) == 1 else values


def main():
    os.umask(0o077)
    stage, browser = map(Path, sys.argv[1:])
    assert os.geteuid() != 0
    assert all(p.is_absolute() and p.resolve() == p for p in (stage, browser))
    assert not any(c.isspace() for p in (stage, browser) for c in str(p))
    assert stage.stat().st_uid == os.geteuid() and stage.stat().st_mode & 0o777 == 0o700
    assert not list(stage.iterdir())
    for name in ("config", "cache", "data"):
        (stage / name).mkdir(mode=0o700)
    put(stage / "private-runtime-path.txt", os.environ["XDG_RUNTIME_DIR"] + "\n")
    version = subprocess.check_output([browser, "--version"], text=True).strip()

    def emit(step, **fields):
        print(json.dumps({"step": step, **fields}), flush=True)

    # Own the fictional endpoint for the entire test. Count any attempted
    # connection without accepting TLS or reading/logging a request payload.
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.settimeout(0.2)
    port = listener.getsockname()[1]
    connections = []
    done = threading.Event()

    def count():
        while not done.is_set():
            try:
                peer, _ = listener.accept()
            except TimeoutError:
                continue
            with peer:
                connections.append(1)

    x = X11()
    counter = threading.Thread(target=count)
    counter.start()
    child = keyring = None
    results = []
    try:
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
                "/CN=Fictional first-start fixture",
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
        native, bundle, directory = (stage / name for name in ("native", "bundle", "browser-data"))
        create_browser_profile(
            native,
            enrollment_file=stage / "enrollment.json",
            ca_file=stage / "ca.pem",
            origin=f"https://127.0.0.1:{port}",
            device_id="fictional",
            extension_id=key.extension_id,
        )
        create_browser_bundle(bundle, profile=native, public_key=stage / "public.pem")
        register_browser_directory(
            directory, bundle=bundle, profile=native, public_key=stage / "public.pem"
        )
        args = dict(
            root=directory,
            browser=browser,
            bundle=bundle,
            profile=native,
            public_key=stage / "public.pem",
        )
        registered = check_browser_startup(**args)
        native_before, bundle_before = snapshot(native), snapshot(bundle)
        assert inspect_browser_profile(native).revision == 1

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

        cli = [
            sys.executable,
            "-I",
            "-c",
            "from sds200.cli import main; raise SystemExit(main())",
            "browser-device-start",
            "--experimental",
            "--directory",
            str(directory),
            "--browser",
            str(browser),
            "--bundle",
            str(bundle),
            "--profile",
            str(native),
            "--public-key",
            str(stage / "public.pem"),
        ]

        def launch(name, setup=False):
            nonlocal child
            assert child is None and not connections
            command = browser_startup_command(
                directory, browser=browser, bundle=bundle, registration=registered, setup=setup
            )
            assert len(command) == 9
            expected_url = (
                registered.setup_url
                if setup
                else (f"chrome-extension://{registered.extension_id}/startup.html")
            )
            assert command[-1] == expected_url
            put(stage / (name + "-expected-argv.json"), json.dumps(command))
            emit("launch", case=name, setup=setup)
            child = subprocess.Popen(
                [*cli, *(["--setup"] if setup else [])],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            expected_title = (
                "SDSCTL experimental first-run setup" if setup else "SDSCTL managed display startup"
            )

            def window():
                assert child.poll() is None, "Foreground CLI exited before expected page"
                return next((wid for wid, title in x.windows() if expected_title in title), None)

            try:
                selected = wait(window, 40)
                # Inspect only direct children of our own still-running CLI.
                # Distribution Chromium wrappers may add their normal defaults;
                # every product argument must survive, with no test/security flags.
                children = Path(f"/proc/{child.pid}/task/{child.pid}/children").read_text().split()
                observed = []
                for pid in children:
                    try:
                        with Path(f"/proc/{int(pid)}/cmdline").open("rb") as stream:
                            body = stream.read(16385)
                    except FileNotFoundError:
                        continue
                    values = process_arguments(body)
                    if f"--user-data-dir={directory}" in values:
                        observed.append(values)
                assert len(observed) == 1
                actual = observed[0]
                assert all(arg in actual for arg in command[1:])
                assert not any(
                    arg.startswith(
                        (
                            "--no-sandbox",
                            "--disable-setuid-sandbox",
                            "--ignore-certificate",
                            "--remote-debugging",
                            "--headless",
                            "--password-store",
                            "--test-type",
                        )
                    )
                    for arg in actual
                )
                put(stage / (name + "-observed-argv.json"), json.dumps(actual))
                return selected
            except BaseException:
                emit("launch-failed", titles=[title for _, title in x.windows()])
                raise

        def read(name, window, expected):
            def matches():
                assert child.poll() is None and not connections
                text = x.text(window)
                return text if expected in text else None

            text = wait(matches, 60)
            put(stage / (name + "-visible.txt"), text)
            x.key(window, "Escape")  # Deselect copied fixture text before capture.
            x.screenshot(stage / (name + ".png"))
            assert snapshot(bundle) == bundle_before
            assert not connections
            results.append(name)
            emit("visible-state", case=name, expected=expected)

        def stop():
            nonlocal child
            assert child is not None
            child.send_signal(signal.SIGTERM)
            assert child.wait(timeout=15) == 0
            child = None
            assert not any(
                (directory / n).exists() or (directory / n).is_symlink()
                for n in ("SingletonLock", "SingletonCookie", "SingletonSocket")
            )
            check_browser_startup(**args)
            assert not connections and snapshot(bundle) == bundle_before

        for name, setup in (
            ("fresh-normal", False),
            ("setup-no-consent", True),
            ("restart-still-uninitialized", False),
        ):
            window = launch(name, setup)
            expected = (
                "Initialization saves local state" if setup else "First-run setup is required"
            )
            read(name, window, expected)
            stop()
            assert snapshot(native) == native_before
            assert inspect_browser_profile(native).revision == 1

        window = launch("explicit-setup", True)
        # Verify the page before sending real trusted confirmation keyboard events.
        read("explicit-setup-before-consent", window, "Initialization saves local state")
        x.key(window, "Tab")
        x.key(window, "space")
        x.key(window, "Tab")
        x.key(window, "Return")
        read("explicit-setup-saved", window, "Setup saved. No login was attempted.")
        stop()
        assert inspect_browser_profile(native).revision == 2
        emit("explicit-native-claim-once-no-connection")

        # Deliberate local maintenance of this FICTIONAL ledger while stopped.
        # This is not a dashboard sign-out or a guard-release test.
        config = load_browser_native_configuration(native)
        BrowserDeviceRecovery(native / "recovery.sqlite", config.identity).suspend()
        paused = snapshot(native)
        for name in ("native-pause-startup", "saved-pause-restart"):
            window = launch(name)
            read(name, window, "Automatic sign-in is paused.")
            stop()
            assert snapshot(native) == paused

        result = {
            "result": "PASS",
            "browser": version,
            "cases": results,
            "endpoint_connections": len(connections),
            "manual_refreshes": 0,
            "exact_foreground_cli": True,
            "native_pause_fixture": True,
            "production_targets": False,
        }
        put(stage / "qualification-result.json", json.dumps(result))
        emit("PASS", **result)
    except BaseException:
        x.screenshot(stage / "failed.png")
        emit(
            "failed",
            titles=[title for _, title in x.windows()],
            endpoint_connections=len(connections),
        )
        raise
    finally:
        try:
            if child is not None and child.poll() is None:
                child.send_signal(signal.SIGTERM)
                try:
                    child.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=3)
        finally:
            if keyring is not None and keyring.poll() is None:
                keyring.terminate()
                try:
                    keyring.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    keyring.kill()
                    keyring.wait(timeout=3)
            done.set()
            counter.join(timeout=3)
            listener.close()
            x.close()


if __name__ == "__main__":
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
            TMPDIR="/tmp",
        )
        for name in (
            "GNOME_KEYRING_CONTROL",
            "SSH_AUTH_SOCK",
            "SESSION_MANAGER",
            "DBUS_SESSION_BUS_ADDRESS",
            "AT_SPI_BUS_ADDRESS",
            "WAYLAND_DISPLAY",
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
