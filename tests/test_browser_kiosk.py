from __future__ import annotations

import os
import shutil
import ssl
import subprocess
import threading
from contextlib import contextmanager
from email.message import Message
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

from sds200 import browser_kiosk as kiosk
from sds200 import cli
from sds200.exceptions import ConfigurationError


@pytest.mark.skipif(shutil.which("openssl") is None, reason="OpenSSL TLS fixture")
def test_preflight_real_tls_trust_and_hostname_validation(tmp_path: Path) -> None:
    certificate = tmp_path / "server.crt"
    key = tmp_path / "server.key"
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
            "/CN=localhost",
            "-addext",
            "subjectAltName=DNS:localhost",
            "-addext",
            "basicConstraints=critical,CA:TRUE",
            "-keyout",
            str(key),
            "-out",
            str(certificate),
        ],
        check=True,
        capture_output=True,
    )

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            assert self.path == "/auth/display/login"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("X-SDSCTL-Display-Login", "1")
            self.end_headers()
            self.wfile.write(b"<html>Fictional display login</html>")

        def log_message(self, format: str, *args: object) -> None:
            pass

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certificate, key)
    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = f"https://localhost:{server.server_port}"
        try:
            assert kiosk.kiosk_preflight(origin, certificate) == origin
            with pytest.raises(ConfigurationError, match="certificate"):
                kiosk.kiosk_preflight(origin)
            with pytest.raises(ConfigurationError, match="certificate"):
                kiosk.kiosk_preflight(f"https://127.0.0.1:{server.server_port}", certificate)
        finally:
            server.shutdown()
            thread.join(timeout=5)


@pytest.mark.parametrize("ending", ["close", "crash", "interrupt"])
def test_launcher_clean_close_crash_and_interrupt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    ending: str,
) -> None:
    browser = tmp_path / "chromium"
    browser.write_text("fictional executable")
    browser.chmod(0o700)
    monkeypatch.setenv("DISPLAY", ":99")
    monkeypatch.setattr(kiosk, "kiosk_preflight", lambda origin, ca: origin)

    class Process:
        def __init__(self, command: tuple[str, ...], **kwargs: object) -> None:
            assert command[0] == str(browser)
            assert kwargs["stderr"] == subprocess.DEVNULL
            self.stopped = False

        def __enter__(self):  # type: ignore[no-untyped-def]
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def wait(self, timeout: float | None = None) -> int:
            if ending == "interrupt" and not self.stopped:
                raise KeyboardInterrupt
            return 1 if ending == "crash" else 0

        def terminate(self) -> None:
            self.stopped = True

    monkeypatch.setattr(kiosk.subprocess, "Popen", Process)
    result = kiosk.run_browser_kiosk("https://display.example", browser, tmp_path / "profile", None)
    assert result == (75 if ending == "crash" else 0)


@pytest.mark.parametrize(
    "origin",
    [
        "http://display.example",
        "https://user:secret@display.example",
        "https://display.example/path",
        "https://display.example?secret=x",
        "https://display.example#x",
        "https://display.example:0",
        "https://display.example:99999",
        "https://display.example\\other",
        "https:// display.example",
    ],
)
def test_kiosk_origin_rejects_unsafe_or_ambiguous_urls(origin: str) -> None:
    with pytest.raises(ConfigurationError):
        kiosk.kiosk_origin(origin)


def test_kiosk_command_contains_no_credential_or_verification_bypass() -> None:
    command = kiosk.kiosk_command(
        "https://display.example:8443/",
        Path("/usr/bin/chromium"),
        Path("/home/display/private-profile"),
    )
    assert command[-1] == "https://display.example:8443/auth/display/login"
    assert "--kiosk" in command
    assert "--user-data-dir=/home/display/private-profile" in command
    assert not any(
        "password" in arg or "ignore-certificate" in arg or "no-sandbox" in arg for arg in command
    )
    with pytest.raises(ConfigurationError):
        kiosk.kiosk_command("https://display.example", Path("chromium"), Path("relative"))


@pytest.mark.parametrize(
    "failure", [None, "redirect", "certificate", "offline", "unavailable", "wrong-service"]
)
def test_preflight_verifies_tls_and_display_contract(
    monkeypatch: pytest.MonkeyPatch,
    failure: str | None,
) -> None:
    class Response:
        status = 200
        headers = Message()
        headers["Content-Type"] = "text/html"
        headers["X-SDSCTL-Display-Login"] = "0" if failure == "wrong-service" else "1"

        def read(self, limit: int) -> bytes:
            assert limit == 65537
            return b"<html>fictional display login</html>"

    class Opener:
        @contextmanager
        def open(self, request: object, timeout: float):  # type: ignore[no-untyped-def]
            assert timeout == 5
            assert request.full_url == "https://display.example/auth/display/login"  # type: ignore[attr-defined]
            if failure == "redirect":
                raise HTTPError("https://display.example", 302, "redirect", Message(), None)
            if failure == "certificate":
                raise URLError(ssl.SSLCertVerificationError("fictional trust failure"))
            if failure == "offline":
                raise URLError("offline")
            if failure == "unavailable":
                raise HTTPError("https://display.example", 503, "unavailable", Message(), None)
            yield Response()

    def build(*handlers: object) -> Opener:
        https = next(handler for handler in handlers if isinstance(handler, kiosk.HTTPSHandler))
        assert https._context.verify_mode == ssl.CERT_REQUIRED
        assert https._context.check_hostname is True
        assert any(isinstance(handler, kiosk._NoRedirect) for handler in handlers)
        return Opener()

    monkeypatch.setattr(kiosk, "build_opener", build)
    if failure is None:
        assert kiosk.kiosk_preflight("https://display.example") == "https://display.example"
    else:
        with pytest.raises(
            ConnectionError if failure in {"offline", "unavailable"} else ConfigurationError
        ):
            kiosk.kiosk_preflight("https://display.example")


@pytest.mark.skipif(os.name != "posix", reason="Linux private browser profile")
def test_profile_is_private_exclusive_and_does_not_adopt_personal_data(tmp_path: Path) -> None:
    profile = tmp_path / "kiosk"
    with kiosk._profile_lock(profile):
        assert profile.stat().st_mode & 0o777 == 0o700
        with pytest.raises(ConfigurationError, match="already owns"), kiosk._profile_lock(profile):
            pytest.fail("duplicate acquired")
    with kiosk._profile_lock(profile):
        pass
    profile.chmod(0o755)
    with pytest.raises(ConfigurationError, match="0700"), kiosk._profile_lock(profile):
        pytest.fail("public profile accepted")
    personal = tmp_path / "personal"
    personal.mkdir(mode=0o700)
    (personal / "Bookmarks").write_text("preserve me")
    with pytest.raises(ConfigurationError, match="non-kiosk"), kiosk._profile_lock(personal):
        pytest.fail("adopted existing profile")
    assert (personal / "Bookmarks").read_text() == "preserve me"
    link = tmp_path / "link"
    link.symlink_to(personal)
    with pytest.raises(ConfigurationError, match="symlinks"), kiosk._profile_lock(link):
        pytest.fail("followed symlink")


def test_kiosk_cli_and_service_do_not_enable_or_publish_anything(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(kiosk, "kiosk_preflight", lambda origin, ca: calls.append(origin))
    assert (
        cli.main(["browser-kiosk", "--origin", "https://display.example", "--preflight-only"]) == 0
    )
    assert calls == ["https://display.example"]
    assert cli.main(["browser-kiosk", "--origin", "https://display.example"]) == 78
    assert cli.main(["browser-kiosk-service"]) == 0
    output = capsys.readouterr().out
    assert "RestartPreventExitStatus=78" in output
    assert "StartLimitBurst=3" in output
    assert "graphical-session.target" in output
    assert "tty1" not in output
    assert "--no-sandbox" not in output
