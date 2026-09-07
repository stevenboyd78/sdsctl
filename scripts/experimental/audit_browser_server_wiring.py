"""Installed server-CLI acceptance in an empty, isolated Linux network namespace.

No browser or scanner. Synthetic authority only. Retains private evidence and
never touches host interfaces, installed services, credentials or trust stores.
Run with the explicit outer namespace inode; see the adjacent README.
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import signal
import socket
import ssl
import subprocess
import time
from pathlib import Path
from urllib.parse import urlencode

import sds200
from sds200.browser_device_http import BROWSER_DEVICE_COOKIE, BROWSER_DEVICE_EXCHANGE_PATH
from sds200.browser_device_ingress import BROWSER_ADMIN_PATH
from sds200.browser_device_store import BrowserDeviceStore

ADMIN_ORIGIN = "https://ha.example.test"
UID = "a" * 32
ADDRESS = "192.168.250.1"
IPV6 = "fd12:3456::1"


def private_write(path: Path, body: bytes) -> None:
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(body)


def stop(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
            raise AssertionError("Fixture child did not stop within its deadline") from None


def scenario(root: Path, executable: Path, mode: str) -> dict[str, object]:
    root.mkdir(mode=0o700)
    host = {"ip": ADDRESS, "dns": "scanner.example.test", "ipv6": IPV6}[mode]
    address = IPV6 if mode == "ipv6" else ADDRESS
    origin = f"https://{'[' + host + ']' if ':' in host else host}:8443"
    subprocess.run([
        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
        "-subj", "/CN=" + host, "-addext",
        "subjectAltName=" + ("DNS:" if mode == "dns" else "IP:") + host,
        "-keyout", str(root / "tls.key"), "-out", str(root / "tls.pem"),
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
    (root / "tls.key").chmod(0o600)
    store = BrowserDeviceStore.initialize(root / "authority.sqlite")
    config = root / "server.json"
    private_write(config, json.dumps({
        "version": 1, "authority_path": str(store.path), "native_origin": origin,
        "ingress_admin": {"origin": ADMIN_ORIGIN, "user_ids": [UID]},
    }).encode())
    private_write(root / "operator.secret", b"fictional operator fixture password")
    common = [str(executable), "web", "--experimental-browser-devices",
              "--browser-device-config", str(config), "--no-access-log",
              "--daemon-socket-path", str(root / "no-scanner.sock")]
    native_command = [*common, "--authenticated-lan", "--lan-listen-address", address,
                      "--lan-origin", origin, "--listen-port", "8443",
                      "--lan-password-file", str(root / "operator.secret"),
                      "--lan-tls-certfile", str(root / "tls.pem"),
                      "--lan-tls-keyfile", str(root / "tls.key")]
    context = ssl.create_default_context(cafile=str(root / "tls.pem"))
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    children = []

    def launch(command, label):
        descriptor = os.open(root / (label + ".log"), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            child = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                     stdout=stream, stderr=stream, start_new_session=True)
        children.append(child)
        return child

    def native(path, *, method="GET", body=None, headers=None):
        # Route the fictional DNS name entirely within the namespace while
        # retaining ordinary certificate/hostname verification and exact Host.
        connection = http.client.HTTPSConnection(host, 8443, context=context, timeout=2)
        raw = socket.create_connection((address, 8443), timeout=2)
        try:
            connection.sock = context.wrap_socket(raw, server_hostname=host)
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read(256000)
        finally:
            connection.close()
            raw.close()

    def admin(*, method="GET", body=None, origin=ADMIN_ORIGIN, peer="172.30.32.2"):
        connection = http.client.HTTPConnection(ADDRESS, 8099, timeout=2,
                                                source_address=(peer, 0))
        try:
            connection.request(method, BROWSER_ADMIN_PATH, body=body, headers={
                "X-Remote-User-Id": UID, "Origin": origin, "Content-Type":
                "application/x-www-form-urlencoded", "X-Forwarded-For": "172.30.32.2",
            })
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read(256000)
        finally:
            connection.close()

    def wait_ready(child, probe, expected):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            assert child.poll() is None, "Fixture child exited before readiness"
            try:
                if probe()[0] == expected:
                    return
            except (OSError, http.client.HTTPException):
                pass
            time.sleep(0.1)
        raise AssertionError("Fixture readiness deadline exceeded")

    def exchange(credential):
        code, headers, body = native(BROWSER_DEVICE_EXCHANGE_PATH, method="POST",
                                    body=b'{"device_id":"display"}', headers={
                                        "Authorization": "Bearer " + credential,
                                        "Content-Type": "application/json",
                                    })
        assert "set-cookie" not in {key.lower() for key in headers}
        return code, json.loads(body)

    try:
        ingress = launch([*common, "--home-assistant-ingress", "--listen-port", "8099"], "ingress")
        wait_ready(ingress, admin, 200)
        assert admin(peer=ADDRESS)[0] == 403, "Forwarded peer granted administration"
        page = admin()[2].decode()
        nonce = re.search(r'name="csrf" value="([a-f0-9]{64})"', page).group(1)
        form = urlencode({"csrf": nonce, "action": "enroll", "device_id": "display",
                          "confirm": "display"})
        assert admin(method="POST", body=form, origin=origin)[0] == 403
        code, headers, body = admin(method="POST", body=form)
        assert code == 200 and "attachment" in headers.get("content-disposition", "")
        issued = json.loads(body)
        credential = issued["credential"]
        assert issued["outcome"]["completed"] is False  # Issuance, not a running client.
        assert credential.encode() not in admin()[2]
        assert admin(method="POST", body=form)[0] == 400, "Unexpected enrollment replay outcome"

        first = launch(native_command, "native-first")
        wait_ready(first, lambda: native("/device-display"), 401)
        assert native("/auth/login")[0] == 200
        code, session = exchange(credential)
        assert code == 200
        old_cookie = {"Cookie": BROWSER_DEVICE_COOKIE + "=" + session["token"]}
        code, _, page = native("/device-display", headers=old_cookie)
        assert code == 200 and b'data-access-mode="display"' in page
        for path in (BROWSER_ADMIN_PATH, "/api/v1/recordings", "/api/v1/control"):
            assert native(path, headers=old_cookie)[0] == 403
        duplicate_command = list(native_command)
        duplicate_command[duplicate_command.index("--listen-port") + 1] = "8444"
        duplicate = launch([*duplicate_command, "--lan-public-port", "8443"], "duplicate-owner")
        assert duplicate.wait(timeout=20) != 0, "Duplicate owner reported successful startup"
        assert native("/device-display", headers=old_cookie)[0] == 200
        stop(first)
        # Uvicorn re-raises a captured SIGTERM after completing ASGI shutdown.
        assert first.returncode in (0, -signal.SIGTERM)
        replacement = launch(native_command, "native-restarted")
        wait_ready(replacement, lambda: native("/device-display"), 401)
        assert native("/device-display", headers=old_cookie)[0] == 401
        code, renewed = exchange(credential)
        assert code == 200
        current_cookie = {"Cookie": BROWSER_DEVICE_COOKIE + "=" + renewed["token"]}
        assert native("/device-display", headers=current_cookie)[0] == 200
        nonce = re.search(r'name="csrf" value="([a-f0-9]{64})"', admin()[2].decode()).group(1)
        code, _, body = admin(method="POST", body=urlencode({
            "csrf": nonce, "action": "revoke", "device_id": "display", "confirm": "display",
            "generation": "1", "state": "active",
        }))
        assert code == 200
        assert b"Outcome: confirmed" in body, "Cross-process revoke was not acknowledged"
        assert native("/device-display", headers=current_cookie)[0] == 401
        assert exchange(credential)[0] == 401
        assert credential.encode() not in store.path.read_bytes()
        stop(replacement)
        stop(ingress)
        assert replacement.returncode in (0, -signal.SIGTERM)
        assert ingress.returncode in (0, -signal.SIGTERM)
        for log in root.glob("*.log"):
            assert credential.encode() not in log.read_bytes(), "Credential appeared in server log"
            if log.name != "duplicate-owner.log":
                assert b"Exception" not in log.read_bytes(), "Unexpected server exception"
        return {"mode": mode, "passed": True, "tls_verified": True,
                "native_restart": True, "old_session_denied": True,
                "duplicate_owner_nonzero_exit": True, "cross_process_revoke_confirmed": True}
    finally:
        for child in reversed(children):
            stop(child)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", type=Path)
    parser.add_argument("installed", type=Path)
    parser.add_argument("host_netns", type=int)
    args = parser.parse_args()
    assert os.stat("/proc/self/ns/net").st_ino != args.host_netns, "Refuse host network namespace"
    links = json.loads(subprocess.check_output(["ip", "-j", "link", "show"], timeout=5))
    assert len(links) == 1 and links[0]["ifname"] == "lo" and "UP" not in links[0]["flags"], (
        "Require an empty new network namespace"
    )
    assert args.installed.is_absolute() and Path(sds200.__file__).is_relative_to(args.installed)
    assert args.stage.is_absolute() and args.stage.stat().st_mode & 0o777 == 0o700
    subprocess.run(["ip", "link", "set", "lo", "up"], check=True, timeout=5)
    for address in (ADDRESS, "172.30.32.2"):
        subprocess.run(["ip", "addr", "add", address + "/32", "dev", "lo"], check=True, timeout=5)
    subprocess.run(["ip", "-6", "addr", "add", IPV6 + "/128", "dev", "lo", "nodad"],
                   check=True, timeout=5)
    result = {"installed_runtime": str(Path(sds200.__file__).parent), "network_isolated": True,
              "browser_tested": False, "scanner_tested": False, "cases": []}
    for mode in ("ip", "dns", "ipv6"):
        result["cases"].append(scenario(args.stage / mode, args.installed / "bin/sdsctl", mode))
        print(mode + " installed server wiring passed", flush=True)
    private_write(args.stage / "result.json", json.dumps(result, indent=2).encode())


if __name__ == "__main__":
    main()
