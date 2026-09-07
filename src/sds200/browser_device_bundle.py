"""Prepare an inert experimental extension/native-host review bundle on Linux.

No browser registration, state initialization, network access or secret copying.
The selected profile is inspected offline, including its private credential file.
Same-account/root processes and the installed Python/OpenSSL runtime are trusted.
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from .browser_device_native import BrowserNativeConfiguration, _private_read
from .browser_device_native import load_browser_native_configuration as load_configuration
from .browser_device_profile import _platform, _write, inspect_browser_profile
from .browser_device_store import BrowserDeviceStore
from .exceptions import ConfigurationError

NATIVE_HOST = "org.sdsctl.browser_device"
MODULES = ("browser_device_recovery.mjs", "browser_device_logout.mjs", "browser_device_setup.mjs",
           "browser_device_startup.mjs")


class BrowserBundleError(ConfigurationError):
    """Fixed diagnostics only: no input fields, paths, key material or subprocess output."""


@dataclass(frozen=True, slots=True)
class BrowserExtensionIdentity:
    extension_id: str
    public_key_sha256: str
    manifest_key: str


def browser_extension_identity(public_key: Path) -> BrowserExtensionIdentity:
    """Validate one public SPKI PEM with OpenSSL, derive Chromium's key-based ID.

    Public identity is not proof of signing-key ownership or trusted distribution.
    OpenSSL is used only during preparation, with bounded input and wall time.
    """
    try:
        _platform()
        body = _private_read(public_key.parent, public_key.name, 4096)
        match = re.fullmatch(
            rb"-----BEGIN PUBLIC KEY-----\r?\n([A-Za-z0-9+/=\r\n]+)"
            rb"-----END PUBLIC KEY-----\r?\n?", body,
        )
        if match is None:
            raise ValueError()
        encoded = match[1].replace(b"\r", b"").replace(b"\n", b"")
        der = base64.b64decode(encoded, validate=True)
        if not der or base64.b64encode(der) != encoded:
            raise ValueError()
        openssl = shutil.which("openssl", path=os.defpath)
        if openssl is None:
            raise ValueError()
        checked = subprocess.run(
            [openssl, "pkey", "-pubin", "-inform", "DER", "-noout", "-pubcheck"],
            input=der, capture_output=True, timeout=5, check=False,
            env={"PATH": os.defpath, "OPENSSL_CONF": os.devnull, "LC_ALL": "C"},
        )
        # Validate separately so pubcheck's diagnostic is never part of the DER.
        if checked.returncode != 0:
            raise ValueError()
        normalized = subprocess.run(
            [openssl, "pkey", "-pubin", "-inform", "DER", "-outform", "DER"],
            input=der, capture_output=True, timeout=5, check=False,
            env={"PATH": os.defpath, "OPENSSL_CONF": os.devnull, "LC_ALL": "C"},
        )
        if normalized.returncode != 0 or normalized.stdout != der:
            raise ValueError()
        digest = hashlib.sha256(der).hexdigest()
        identity = "".join(chr(ord("a") + int(nibble, 16)) for nibble in digest[:32])
        return BrowserExtensionIdentity(identity, digest, encoded.decode("ascii"))
    except Exception:
        raise BrowserBundleError(
            "Extension public key is invalid or unsafe; use one protected public SPKI PEM "
            "file and an installed OpenSSL executable."
        ) from None


def _json(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + "\n").encode("ascii")


def _artifacts(
    root: Path, config: BrowserNativeConfiguration, key: BrowserExtensionIdentity,
) -> dict[str, bytes]:
    python = Path(sys.executable)
    if not python.is_absolute() or not python.is_file() or not os.access(python, os.X_OK):
        raise ValueError()
    # Keep the venv interpreter path; resolving its symlink would lose that venv.
    settings = json.dumps({"origin": config.origin, "identity": config.identity,
                           "nativeHost": NATIVE_HOST}, ensure_ascii=True)
    origin = json.dumps(config.origin)
    host = f"[{config.hostname}]" if ":" in config.hostname else config.hostname
    pattern = f"https://{host}:{config.port}/"
    assets = files("sds200.browser_assets")
    result = {f"extension/{name}": assets.joinpath(name).read_bytes() for name in MODULES}
    logout = result["extension/browser_device_logout.mjs"].decode("utf-8")
    # The packaged logout module has no imports; build a classic isolated-world
    # entrypoint without exposing its symbols in the page or requiring WAR access.
    content = "(() => {\n" + re.sub(r"^export (?=(?:async )?function )", "", logout,
                                    flags=re.MULTILINE)
    content += ("\nif (window === window.top && ['/', '/device-display'].some(p => "
                f"location.href === {origin} + p)) "
                "connectLogoutContent({document, window, runtime: chrome.runtime, "
                f"fetcher: fetch.bind(globalThis)}}, {origin});\n}})();\n")
    result.update({
        "extension/manifest.json": _json({
            "manifest_version": 3, "version": "0.0.3",
            "name": "SDSCTL experimental device recovery review",
            "key": key.manifest_key,
            "permissions": ["nativeMessaging", "storage", "cookies", "alarms", "tabs"],
            "host_permissions": [pattern + "*"],
            "background": {"service_worker": "worker.mjs", "type": "module"},
            "content_scripts": [{"matches": [pattern, pattern + "device-display"],
                                 "js": ["content.js"],
                                 "run_at": "document_start", "all_frames": False,
                                 "world": "ISOLATED"}],
            "incognito": "not_allowed",
            "content_security_policy": {
                "extension_pages": "script-src 'self'; object-src 'none'; connect-src 'none'",
            },
        }),
        "extension/worker.mjs": (
            "import {connectChromeRecovery} from './browser_device_recovery.mjs';\n"
            "import {connectLogoutWorker} from './browser_device_logout.mjs';\n"
            "import {connectBrowserEntry} from './browser_device_startup.mjs';\n"
            f"const config = {settings};\n"
            "const controller = connectChromeRecovery(chrome, config);\n"
            "connectLogoutWorker(chrome, controller, config.origin);\n"
            "connectBrowserEntry(chrome);\n"
        ).encode("ascii"),
        "extension/content.js": content.encode("utf-8"),
        "extension/control.html": (
            "<!doctype html><meta charset='utf-8'><title>SDSCTL review bundle</title>"
            "<h1>Experimental review bundle</h1><p>Preparation does not enable login. "
            "Trusted browser-state provisioning and deployment acceptance are still required."
            "</p><p>No password or credential should be entered here.</p>\n"
        ).encode("ascii"),
        "extension/setup.mjs": (
            "import {connectBrowserSetupPage} from './browser_device_setup.mjs';\n"
            "connectBrowserSetupPage({document, window, runtime: chrome.runtime});\n"
        ).encode("ascii"),
        "extension/startup.mjs": (
            "import {connectBrowserStartupPage} from './browser_device_startup.mjs';\n"
            f"connectBrowserStartupPage({{document, window, runtime: chrome.runtime}}, {origin});\n"
        ).encode("ascii"),
        "extension/startup.html": (
            "<!doctype html><html lang='en'><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>SDSCTL managed display startup</title><link rel='stylesheet' href='setup.css'>"
            "<main><h1>SDSCTL managed display</h1><p>Experimental startup</p><dl>"
            f"<dt>Server</dt><dd>{html.escape(config.origin)}</dd>"
            f"<dt>Device</dt><dd>{html.escape(config.device_id)}</dd></dl>"
            "<p id='notice' role='status'>Starting managed display…</p>"
            "<p>This page never initializes, repairs or resumes a profile automatically.</p>"
            "</main><script type='module' src='startup.mjs'></script></html>\n"
        ).encode(),
        "extension/setup.html": (
            "<!doctype html><html lang='en'><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>SDSCTL experimental first-run setup</title>"
            "<link rel='stylesheet' href='setup.css'><main><h1>Initialize this display</h1>"
            "<p>Experimental setup only. This is not a password sign-in page.</p><dl>"
            f"<dt>Server</dt><dd>{html.escape(config.origin)}</dd>"
            f"<dt>Device</dt><dd>{html.escape(config.device_id)}</dd>"
            f"<dt>Extension</dt><dd>{key.extension_id}</dd></dl>"
            "<p>Only an empty browser state and a fresh native profile can initialize. "
            "Existing pauses, errors and previous setup attempts will not be reset.</p>"
            "<form id='setup-form'><label><input type='checkbox' id='confirm' required>"
            " I have verified this server and device. I want automatic sign-in on future "
            "browser or extension starts.</label><p><button id='initialize' type='submit'>"
            "Initialize automatic sign-in</button></p></form>"
            "<p id='notice' role='status'>This page does not read a password or credential. "
            "Initialization saves local state; it does not contact the dashboard.</p></main>"
            "<script type='module' src='setup.mjs'></script></html>\n"
        ).encode("ascii"),
        "extension/setup.css": (
            ":root{color-scheme:light dark;font:18px/1.5 system-ui}"
            "body{margin:0;padding:1.5rem}main{max-width:42rem;margin:auto}"
            "h1{line-height:1.2}dt{font-weight:600}dd{margin:0 0 .8rem;overflow-wrap:anywhere}"
            "label{display:block}button{font:inherit;padding:.6rem 1rem}"
            "#notice{border:1px solid;padding:1rem}input{width:1.1rem;height:1.1rem}\n"
        ).encode("ascii"),
        "native-host": (
            "#!/bin/sh\nexec " + shlex.quote(str(python)) + " -I "
            + shlex.quote(str(root / "native_host.py")) + ' "$@"\n'
        ).encode("utf-8"),
        "native_host.py": (
            "import os, sys\nfrom pathlib import Path\n"
            "from sds200.browser_device_native import run_browser_native\n"
            "raise SystemExit(run_browser_native(\n"
            f"    Path({str(config.root)!r}), sys.argv[1:],\n"
            "    os.fdopen(os.dup(0), 'rb', buffering=0),\n"
            "    os.fdopen(os.dup(1), 'wb', buffering=0),\n"
            f"    expected_identity={config.identity!r},\n))\n"
        ).encode(),
        NATIVE_HOST + ".json": _json({
            "name": NATIVE_HOST, "description": "SDSCTL experimental device recovery",
            "type": "stdio", "path": str(root / "native-host"),
            "allowed_origins": [config.extension_origin],
        }),
    })
    return result


def create_browser_bundle(
    root: Path, *, profile: Path, public_key: Path,
) -> BrowserExtensionIdentity:
    """Write only to a new private bundle; never register, launch, repair or overwrite."""
    try:
        _platform()
        BrowserDeviceStore(root)._check(database=False)
        if root.exists() or root.is_symlink():
            raise FileExistsError()
        key = browser_extension_identity(public_key)
        inspected = inspect_browser_profile(profile)
        config = load_configuration(profile)
        if (config.extension_origin != f"chrome-extension://{key.extension_id}/"
                or inspected.identity != config.identity):
            raise ValueError()
        artifacts = _artifacts(root, config, key)
        receipt = _json({
            "version": 1, "experimental": True, "extension_id": key.extension_id,
            "identity": config.identity, "public_key_sha256": key.public_key_sha256,
            "trust_sha256": inspected.trust_sha256,
            "files": {name: hashlib.sha256(body).hexdigest() for name, body in artifacts.items()},
        })
    except FileExistsError:
        raise BrowserBundleError("Browser bundle already exists; refusing to overwrite.") from None
    except Exception:
        raise BrowserBundleError(
            "Browser bundle inputs are invalid or unsafe; no bundle was created. "
            "Check the private profile, matching extension public key and destination."
        ) from None

    parent_fd = root_fd = extension_fd = None
    try:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        parent_fd = os.open(root.parent, flags)
        parent = os.fstat(parent_fd)
        if parent.st_uid != os.geteuid() or stat.S_IMODE(parent.st_mode) != 0o700:
            raise ValueError()
        os.mkdir(root.name, 0o700, dir_fd=parent_fd)
        os.fsync(parent_fd)
        root_fd = os.open(root.name, flags, dir_fd=parent_fd)
        opened = os.fstat(root_fd)
        if (opened.st_dev, opened.st_ino) != (root.stat().st_dev, root.stat().st_ino):
            raise ValueError()
        os.mkdir("extension", 0o700, dir_fd=root_fd)
        extension_fd = os.open("extension", flags, dir_fd=root_fd)
        for name, body in artifacts.items():
            if name.startswith("extension/"):
                _write(extension_fd, name.removeprefix("extension/"), body)
            else:
                _write(root_fd, name, body)
        launcher_fd = os.open("native-host", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=root_fd)
        try:
            os.fchmod(launcher_fd, 0o700)
            os.fsync(launcher_fd)
        finally:
            os.close(launcher_fd)
        os.fsync(extension_fd)
        _write(root_fd, "bundle.json", receipt)  # Completion marker, not a signature.
        os.fsync(root_fd)
        return key
    except Exception:
        raise BrowserBundleError(
            "Browser bundle creation could not be confirmed. Retain any created directory "
            "for inspection; do not retry over it. Nothing was registered or started."
        ) from None
    finally:
        for descriptor in (extension_fd, root_fd, parent_fd):
            if descriptor is not None:
                os.close(descriptor)
