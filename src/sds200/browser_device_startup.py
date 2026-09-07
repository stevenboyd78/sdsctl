"""Opt-in foreground Chromium launch for a registered experimental device.

No setup, repair, browser trust/policy changes, service installation or secret
arguments. Existing manual kiosk and TUI entrypoints are deliberately separate.
"""

from __future__ import annotations

import os
import re
import signal
import stat
import subprocess
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .browser_device_profile import _platform
from .browser_device_registration import BrowserRegistration, inspect_browser_registration
from .exceptions import ConfigurationError


class BrowserStartupError(ConfigurationError):
    """Fixed, redacted diagnostics only."""


def browser_startup_command(
    root: Path, *, browser: Path, bundle: Path, registration: BrowserRegistration,
    setup: bool = False,
) -> tuple[str, ...]:
    """Fixed argv; caller must validate and lock before running it."""
    return (str(browser), "--kiosk", "--no-first-run", "--no-default-browser-check",
            "--disable-background-networking", f"--user-data-dir={root}",
            f"--load-extension={bundle / 'extension'}",
            f"--disable-extensions-except={bundle / 'extension'}",
            registration.setup_url if setup else
            f"chrome-extension://{registration.extension_id}/startup.html")


def check_browser_startup(
    root: Path, *, browser: Path, bundle: Path, profile: Path, public_key: Path,
) -> BrowserRegistration:
    """Read-only offline check, including used/paused state; not live login proof."""
    try:
        _platform()
        if not browser.is_absolute() or not browser.is_file() or not os.access(browser, os.X_OK):
            raise ValueError()
        # Chromium's comma-separated extension flags cannot represent a comma in a path.
        if "," in str(bundle):
            raise ValueError()
        return inspect_browser_registration(root, bundle=bundle, profile=profile,
                                            public_key=public_key)
    except Exception:
        raise BrowserStartupError(
            "Managed browser setup is invalid or unsafe; nothing was changed. "
            "Review the registered directory, canonical bundle, runtime and private profile."
        ) from None


@contextmanager
def _launch_lock(root: Path) -> Iterator[None]:
    import fcntl

    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    directory = os.open(root, flags)
    descriptor = None
    try:
        info = os.fstat(directory)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError()
        name = ".sdsctl-device-launch.lock"
        descriptor = os.open(name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC
                             | os.O_NONBLOCK,
                             0o600, dir_fd=directory)
        opened = os.fstat(descriptor)
        if (not stat.S_ISREG(opened.st_mode) or opened.st_uid != os.geteuid()
                or stat.S_IMODE(opened.st_mode) != 0o600 or opened.st_nlink != 1):
            raise ValueError()
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        named = os.stat(name, dir_fd=directory, follow_symlinks=False)
        if (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino):
            raise ValueError()
        if (info.st_dev, info.st_ino) != (root.stat().st_dev, root.stat().st_ino):
            raise ValueError()
        # Even stale Chromium markers require review in this first foreground
        # implementation. Never delete locks or take over an unrelated browser.
        if any((root / name).exists() or (root / name).is_symlink()
               for name in ("SingletonLock", "SingletonSocket", "SingletonCookie")):
            raise ValueError()
        yield
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(directory)


def run_browser_startup(
    root: Path, *, browser: Path, bundle: Path, profile: Path, public_key: Path,
    setup: bool = False,
) -> int:
    """Run only this child; clean user close/signal returns 0, crash returns 75."""
    try:
        check_browser_startup(root, browser=browser, bundle=bundle, profile=profile,
                              public_key=public_key)
        if (threading.current_thread() is not threading.main_thread()
                or not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))):
            raise ValueError()
        # --load-extension is not supported by Chrome-branded builds. Never add
        # policy/feature overrides to make an unsupported browser load this bundle.
        version = subprocess.run([str(browser), "--version"], stdin=subprocess.DEVNULL,
                                 capture_output=True, timeout=5, check=False)
        match = re.match(rb"Chromium ([0-9]{1,4})\.", version.stdout)
        if version.returncode != 0 or match is None or int(match[1]) < 120:
            raise ValueError()
        with _launch_lock(root):
            registered = check_browser_startup(root, browser=browser, bundle=bundle,
                                                profile=profile, public_key=public_key)
            command = browser_startup_command(root, browser=browser, bundle=bundle,
                                              registration=registered, setup=setup)
            previous = signal.getsignal(signal.SIGTERM)
            child = None

            def terminate(signum: int, frame: object) -> None:
                raise KeyboardInterrupt

            def stop_child() -> None:
                if child is not None:
                    # Linux Chromium treats SIGTERM as fast session ending,
                    # which can retain Singleton markers even after exit 0.
                    # SIGINT requests normal browser/profile cleanup instead.
                    # Signal only our own child; never delete Chromium's locks.
                    child.send_signal(signal.SIGINT)
                    try:
                        child.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait()

            signal.signal(signal.SIGTERM, terminate)
            try:
                child = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                         start_new_session=True)
                return 0 if child.wait() == 0 else 75
            except KeyboardInterrupt:
                stop_child()
                return 0
            except Exception:
                stop_child()
                raise
            finally:
                signal.signal(signal.SIGTERM, previous)
    except KeyboardInterrupt:
        return 0
    except Exception:
        raise BrowserStartupError(
            "Managed browser launch could not be confirmed. Review Chromium, graphical "
            "session and profile ownership; no state was reset or locks removed."
        ) from None
