"""Manual-login Chromium kiosk preflight and foreground launcher.

This module never installs a desktop, alters a trust store, stores a password,
or enables a service. It uses the caller's already-running graphical session.
"""

from __future__ import annotations

import os
import signal
import ssl
import stat
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

from .exceptions import ConfigurationError

DISPLAY_LOGIN_PATH = "/auth/display/login"


def kiosk_origin(value: str) -> str:
    """Require an exact HTTPS origin, never a URL containing credentials."""

    try:
        parsed = urlsplit(value)
        port = parsed.port
        host = parsed.hostname
        if (
            parsed.scheme != "https"
            or not host
            or not host.isascii()
            or any(character.isspace() for character in value)
            or any(character in value for character in ("%", "\\", "?", "#"))
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or port == 0
        ):
            raise ValueError
    except (ValueError, TypeError) as error:
        raise ConfigurationError(
            "Kiosk requires one HTTPS origin without credentials or a path."
        ) from error
    authority = f"[{host}]" if ":" in host else host
    return f"https://{authority}" + (f":{port}" if port not in {None, 443} else "")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


def kiosk_preflight(origin: str, ca_file: Path | None = None) -> str:
    """Check issuer/name trust and the configured display login without authenticating."""

    normalized = kiosk_origin(origin)
    if ca_file is not None and (not ca_file.is_absolute() or not ca_file.is_file()):
        raise ConfigurationError("Kiosk CA file must be an absolute readable certificate file.")
    try:
        context = ssl.create_default_context(cafile=ca_file)
        opener = build_opener(ProxyHandler({}), HTTPSHandler(context=context), _NoRedirect())
        request = Request(normalized + DISPLAY_LOGIN_PATH, headers={"Accept": "text/html"})
        with opener.open(request, timeout=5) as response:
            if (
                response.status != 200
                or response.headers.get("X-SDSCTL-Display-Login") != "1"
                or response.headers.get_content_type() != "text/html"
                or len(response.read(65537)) > 65536
            ):
                raise ConfigurationError("Server did not provide the expected display login.")
    except HTTPError as error:
        if error.code in {408, 429, 500, 502, 503, 504}:
            raise ConnectionError("Kiosk server is temporarily unavailable.") from error
        raise ConfigurationError(
            "Display login unavailable or redirected; review server setup."
        ) from error
    except ssl.SSLError as error:
        raise ConfigurationError(
            "Kiosk certificate validation failed; review identity and trust."
        ) from error
    except URLError as error:
        if isinstance(error.reason, ssl.SSLError):
            raise ConfigurationError(
                "Kiosk certificate validation failed; review identity and trust."
            ) from error
        raise ConnectionError("Kiosk server is temporarily unreachable.") from error
    return normalized


def kiosk_command(origin: str, browser: Path, profile: Path) -> tuple[str, ...]:
    """Build fixed Chromium arguments without TLS/sandbox bypass or credentials."""

    normalized = kiosk_origin(origin)
    if not browser.is_absolute() or not profile.is_absolute():
        raise ConfigurationError("Kiosk browser and private profile paths must be absolute.")
    return (
        os.fspath(browser),
        "--kiosk",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
        f"--user-data-dir={profile}",
        normalized + DISPLAY_LOGIN_PATH,
    )


@contextmanager
def _profile_lock(profile: Path) -> Iterator[None]:
    import fcntl

    if not profile.is_absolute() or profile.is_symlink() or profile.resolve() != profile:
        raise ConfigurationError("Kiosk profile must be an absolute directory without symlinks.")
    # Only the explicitly selected profile may be created. Parents must already exist.
    profile.mkdir(mode=0o700, exist_ok=True)
    observed = profile.stat()
    if (
        not stat.S_ISDIR(observed.st_mode)
        or observed.st_uid != os.getuid()
        or stat.S_IMODE(observed.st_mode) != 0o700
    ):
        raise ConfigurationError("Kiosk profile must be owned by this user and mode 0700.")
    if not (profile / ".sdsctl-kiosk.lock").exists() and any(profile.iterdir()):
        raise ConfigurationError("Refusing to reuse a non-kiosk browser profile.")
    flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptor = os.open(profile / ".sdsctl-kiosk.lock", flags, 0o600)
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != os.getuid()
            or stat.S_IMODE(opened.st_mode) != 0o600
        ):
            raise ConfigurationError("Kiosk profile lock is not a private regular file.")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ConfigurationError("A kiosk already owns this profile.") from error
        yield
    finally:
        os.close(descriptor)


def run_browser_kiosk(origin: str, browser: Path, profile: Path, ca_file: Path | None) -> int:
    """Run in an existing Linux desktop; clean close succeeds, crash returns 75."""

    if sys.platform != "linux" or os.getuid() == 0:
        raise ConfigurationError("Browser kiosk requires a non-root Linux desktop account.")
    if not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
        raise ConfigurationError("Start the kiosk inside its graphical desktop session, not SSH.")
    if not browser.is_absolute() or not browser.is_file() or not os.access(browser, os.X_OK):
        raise ConfigurationError("Select the absolute installed Chromium executable path.")
    command = kiosk_command(kiosk_preflight(origin, ca_file), browser, profile)
    # Do not relay browser diagnostics, which can contain URLs or profile data.
    with (
        _profile_lock(profile),
        subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        ) as child,
    ):
        previous = signal.getsignal(signal.SIGTERM)

        def terminate(signum: int, frame: object) -> None:
            raise KeyboardInterrupt

        signal.signal(signal.SIGTERM, terminate)
        try:
            try:
                return 0 if child.wait() == 0 else 75
            except KeyboardInterrupt:
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
                return 0
        finally:
            signal.signal(signal.SIGTERM, previous)
