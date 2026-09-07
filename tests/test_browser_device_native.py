from __future__ import annotations

import json
import os
import shutil
import ssl
import struct
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from sds200.browser_device_native import exchange_browser_device, load_browser_native_configuration
from sds200.browser_device_recovery import BrowserDeviceRecovery, ExchangeFailure, RecoveryMode

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux native runner")
EXTENSION = "chrome-extension://" + "a" * 32 + "/"
CREDENTIAL = "sdsctl-browser-v1." + "b" * 64
TOKEN = "sdsctl-browser-session-v1." + "c" * 64
RUNNER = """
import os, sys
from pathlib import Path
import sds200.browser_device_native as native
raise SystemExit(native.run_browser_native(Path(sys.argv[1]), sys.argv[2:],
    os.fdopen(os.dup(0), 'rb', buffering=0), os.fdopen(os.dup(1), 'wb', buffering=0)))
"""


def private(path, value):
    path.write_bytes(value if isinstance(value, bytes) else value.encode())
    path.chmod(0o600)


def configure(root, origin="https://localhost", **changes):
    document = {"version": 1, "origin": origin, "device_id": "display",
                "extension_origin": EXTENSION, **changes}
    private(root / "client.json", json.dumps(document))


@pytest.fixture
def root(tmp_path):
    root = tmp_path / "installation"
    root.mkdir(mode=0o700)
    configure(root)
    private(root / "device.secret", CREDENTIAL)
    return root


def initialize(root):
    configuration = load_browser_native_configuration(root)
    BrowserDeviceRecovery.initialize(root / "recovery.sqlite", configuration.identity)
    return configuration


def frame(document):
    body = json.dumps(document).encode()
    return struct.pack("=I", len(body)) + body


def invoke(root, action="authenticate", *, args=None, document=None, script=RUNNER):
    result = subprocess.run([sys.executable, "-c", script, str(root),
                             *(args if args is not None else [EXTENSION])],
                            input=frame(document or {"version": 1, "action": action}),
                            capture_output=True, timeout=13)
    assert result.stderr == b""
    assert CREDENTIAL.encode() not in result.stdout
    assert result.returncode == 0
    size = struct.unpack("=I", result.stdout[:4])[0]
    assert len(result.stdout) == size + 4 and size <= 4096
    return json.loads(result.stdout[4:])


@pytest.fixture(scope="module")
def certificates(tmp_path_factory):
    if shutil.which("openssl") is None:
        pytest.skip("OpenSSL TLS fixture")
    root = tmp_path_factory.mktemp("native-tls")
    pairs = []
    for name, san in [("right", "DNS:localhost,IP:127.0.0.1"),
                      ("wrong", "DNS:not-the-server.invalid")]:
        cert, key = root / f"{name}.pem", root / f"{name}.key"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                        "-days", "1", "-subj", "/CN=fixture", "-addext", "subjectAltName=" + san,
                        "-addext", "basicConstraints=critical,CA:TRUE", "-keyout", str(key),
                        "-out", str(cert)], check=True, capture_output=True)
        key.chmod(0o600)
        pairs.append((cert, key))
    return pairs


@pytest.fixture
def server(root, certificates, request):
    observed = []
    response = {"status": 200, "body": json.dumps({"token": TOKEN, "expires_in": 300}).encode(),
                "headers": [], "slow": False, "type": "application/json"}
    stopped = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            observed.append((self.path, dict(self.headers),
                             self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(response["status"])
            if response["type"] is not None:
                self.send_header("Content-Type", response["type"])
            for key, value in response["headers"]:
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(response["body"])))
            self.end_headers()
            try:
                if response["slow"]:
                    for byte in response["body"]:
                        if stopped.wait(0.1):
                            break
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                else:
                    self.wfile.write(response["body"])
            except (OSError, ssl.SSLError):
                pass

        def log_message(self, *args):
            pass

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    selected = certificates[getattr(request, "param", 0)]
    context.load_cert_chain(*selected)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    configure(root, f"https://localhost:{httpd.server_port}")
    private(root / "ca.pem", selected[0].read_bytes())
    configuration = initialize(root)
    try:
        yield configuration, response, observed
    finally:
        stopped.set()
        httpd.shutdown()
        httpd.server_close()
        thread.join(3)


@pytest.mark.parametrize("origin", ["https://ha.example.com", "https://192.168.0.18:8443",
                                    "https://[fd00::18]:8443"])
def test_dns_and_ip_configuration(root, origin):
    configure(root, origin)
    configuration = load_browser_native_configuration(root)
    assert configuration.origin == origin and len(configuration.identity) == 64


@pytest.mark.parametrize("origin", ["http://localhost", "https://localhost/path", "https://localhost/",
    "https://localhost?", "https://user:password@localhost", "https://localhost:0",
    "https://[fe80::1%eth0]", "https://2130706433", "https://LOCALHOST", "https://localhost\n"])
def test_bad_origins_are_redacted(root, origin):
    configure(root, origin)
    with pytest.raises(ExchangeFailure) as error:
        load_browser_native_configuration(root)
    assert error.value.mode is RecoveryMode.SETUP_ERROR
    assert origin not in str(error.value)


@pytest.mark.parametrize("change", [{"version": True}, {"device_id": "../secret"},
                                    {"extension_origin": "chrome-extension://bad/"},
                                    {"role": "operator"}])
def test_bad_configuration_fields(root, change):
    configure(root, **change)
    with pytest.raises(ExchangeFailure):
        load_browser_native_configuration(root)


@pytest.mark.parametrize("kind", ["mode", "symlink", "hardlink", "fifo", "duplicate"])
def test_unsafe_config_is_refused_without_fixing_permissions(root, kind):
    path = root / "client.json"
    if kind == "mode":
        path.chmod(0o644)
    elif kind == "symlink":
        path.rename(root / "saved.json")
        path.symlink_to(root / "saved.json")
    elif kind == "hardlink":
        os.link(path, root / "linked.json")
    elif kind == "fifo":
        path.rename(root / "saved.json")
        os.mkfifo(path, 0o600)
    else:
        private(path, b'{"version":1,"version":1}')
    start = time.monotonic()
    with pytest.raises(ExchangeFailure):
        load_browser_native_configuration(root)
    assert time.monotonic() - start < 1
    if kind == "mode":
        assert path.stat().st_mode & 0o777 == 0o644


def test_native_verified_exchange_ignores_proxy_env(server, monkeypatch):
    configuration, response, observed = server
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("SSL_CERT_FILE", "/fictional/unreadable")
    result = invoke(configuration.root)
    assert result["ok"] and result["session"]["token"] == TOKEN
    assert 0 < result["renew_after"] < result["session"]["expires_in"] <= 300
    assert len(observed) == 1
    path, headers, body = observed[0]
    assert path == "/auth/device/session"
    assert headers["Authorization"] == "Bearer " + CREDENTIAL
    assert json.loads(body) == {"device_id": "display"}
    assert not {"Origin", "Cookie"} & headers.keys()
    assert TOKEN.encode() not in (configuration.root / "recovery.sqlite").read_bytes()


def test_untrusted_tls_is_terminal_without_sending_credential(server, certificates):
    configuration, response, observed = server
    private(configuration.root / "ca.pem", certificates[1][0].read_bytes())
    with pytest.raises(ExchangeFailure) as error:
        exchange_browser_device(configuration)
    assert error.value.mode is RecoveryMode.TLS_ERROR
    assert observed == []


@pytest.mark.parametrize("server", [1], indirect=True)
def test_trusted_issuer_with_wrong_hostname_is_rejected(server):
    configuration, response, observed = server
    result = invoke(configuration.root)
    assert result["mode"] == "tls_error" and "session" not in result
    assert observed == []


@pytest.mark.parametrize("kind", ["mode", "symlink", "hardlink", "fifo", "invalid", "trust-mode"])
def test_unsafe_secret_or_trust_file_never_reaches_server(server, kind):
    configuration, response, observed = server
    path = configuration.root / ("ca.pem" if kind == "trust-mode" else "device.secret")
    if kind in {"mode", "trust-mode"}:
        path.chmod(0o644)
    elif kind == "symlink":
        path.rename(configuration.root / "saved.secret")
        path.symlink_to(configuration.root / "saved.secret")
    elif kind == "hardlink":
        os.link(path, configuration.root / "linked.secret")
    elif kind == "fifo":
        path.rename(configuration.root / "saved.secret")
        os.mkfifo(path, 0o600)
    else:
        private(path, "operator password is not a device credential")
    result = invoke(configuration.root)
    assert result["mode"] == "setup_error" and "session" not in result
    assert observed == []


@pytest.mark.parametrize("headers,status", [([("Location", "https://evil.invalid/")], 302),
    ([("Set-Cookie", "operator=secret")], 200), ([("Content-Type", "text/plain")], 200),
    ([("Content-Encoding", "gzip")], 200)])
def test_redirect_cookie_and_duplicate_response_headers_refused(server, headers, status):
    configuration, response, observed = server
    response.update(headers=headers, status=status)
    result = invoke(configuration.root)
    assert result["mode"] == "protocol_error" and "session" not in result
    assert len(observed) == 1


@pytest.mark.parametrize("status,mode", [(401, "credential_rejected"), (503, "active")])
def test_exchange_failure_persists_recovery_state(server, status, mode):
    configuration, response, observed = server
    response.update(status=status, headers=[("Retry-After", "60")])
    first = invoke(configuration.root)
    second = invoke(configuration.root)
    assert first["mode"] == second["mode"] == mode
    assert "session" not in first and "session" not in second
    assert len(observed) == 1


def test_status_and_offline_suspend_need_no_secret_or_ca(root):
    initialize(root)
    (root / "device.secret").rename(root / "not-loaded.secret")
    assert invoke(root, "status")["mode"] == "active"
    assert invoke(root, "suspend")["mode"] == "paused"
    assert invoke(root)["mode"] == "paused"


@pytest.mark.parametrize("args", [[], ["chrome-extension://" + "b" * 32 + "/"],
                                 [EXTENSION, "--origin=https://evil.invalid"]])
def test_unexpected_caller_refused(root, args):
    initialize(root)
    assert invoke(root, "suspend", args=args) == {"version": 1, "ok": False, "mode": "setup_error"}
    assert invoke(root, "status")["mode"] == "active"


def test_native_request_cannot_choose_path_or_server(root):
    initialize(root)
    result = invoke(root, document={"version": 1, "action": "authenticate", "path": "/secret"})
    assert result == {"version": 1, "ok": False, "mode": "setup_error"}


def test_default_total_deadline_terminates_stalled_native_input(root):
    initialize(root)
    start = time.monotonic()
    with subprocess.Popen(
        [sys.executable, "-c", RUNNER, str(root), EXTENSION],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ) as process:
        process.wait(timeout=12)  # Keep stdin open without sending a frame.
        assert process.returncode == 2
        assert process.stdout.read() == b"" and process.stderr.read() == b""
    assert 9.5 <= time.monotonic() - start < 12


def test_total_deadline_bounds_slow_https_body(server):
    configuration, response, observed = server
    response["slow"] = True
    script = RUNNER.replace("raise SystemExit", "native._TOTAL_SECONDS = 0.4\nraise SystemExit")
    result = subprocess.run([sys.executable, "-c", script, str(configuration.root), EXTENSION],
                            input=frame({"version": 1, "action": "authenticate"}),
                            capture_output=True, timeout=3)
    assert result.returncode == 2
    assert result.stdout == result.stderr == b""
    assert len(observed) == 1
    after = invoke(configuration.root, "status")
    assert after["retry_after"] > 0  # Saved claim survives a killed exchange; no immediate retry.


def test_parent_death_kills_stalled_worker(root):
    initialize(root)
    with subprocess.Popen(
        [sys.executable, "-c", RUNNER, str(root), EXTENSION],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ) as process:
        child = None
        until = time.monotonic() + 3
        while time.monotonic() < until:
            children = Path(f"/proc/{process.pid}/task/{process.pid}/children").read_text().split()
            if children:
                child = int(children[0])
                break
            time.sleep(0.02)
        assert child is not None
        process.kill()
        process.wait(timeout=2)
        until = time.monotonic() + 2
        while time.monotonic() < until:
            try:
                state = Path(f"/proc/{child}/stat").read_text().split(")", 1)[1].split()[0]
            except FileNotFoundError:
                break
            if state == "Z":
                break
            time.sleep(0.02)
        else:
            pytest.fail("Native child remained live after parent death")
        assert process.stdout.read() == process.stderr.read() == b""


def test_total_deadline_bounds_blocked_exchange_independently_of_socket_timeout(root):
    initialize(root)
    script = RUNNER.replace("raise SystemExit", """
native._TOTAL_SECONDS = 0.3
native.exchange_browser_device = lambda configuration: __import__('time').sleep(60)
raise SystemExit""")
    result = subprocess.run([sys.executable, "-c", script, str(root), EXTENSION],
                            input=frame({"version": 1, "action": "authenticate"}),
                            capture_output=True, timeout=3)
    assert result.returncode == 2 and result.stdout == result.stderr == b""


def test_total_deadline_bounds_blocked_native_output(root):
    script = RUNNER.replace("raise SystemExit", """
native._TOTAL_SECONDS = 0.3
def blocked(*args):
    while True:
        os.write(1, b'x' * 65536)
native._native_request = blocked
raise SystemExit""")
    with subprocess.Popen(
        [sys.executable, "-c", script, str(root), EXTENSION],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ) as process:
        process.wait(timeout=3)  # Deliberately do not drain the child's output pipe.
        assert process.returncode == 2 and process.stderr.read() == b""
        assert process.stdout.read()  # Partial output must be discarded by the extension.


def test_temporary_failure_without_content_type_remains_retryable(server):
    configuration, response, observed = server
    response.update(status=503, type=None)
    result = invoke(configuration.root)
    assert result["mode"] == "active" and result["retry_after"] > 0
    assert "session" not in result
