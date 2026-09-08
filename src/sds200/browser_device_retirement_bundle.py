"""Internal, inert recovery-only bundle bound to completed stopped maintenance.

Creates a NEW private bundle, never registers/launches it or releases a guard.
The same extension public key preserves identity, not proof of Chromium loading
the new worker. A future stopped-browser handoff must replace the exact normal
host with this confirmation-only host; do not register it alongside normal auth.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import shlex
import sys
from dataclasses import asdict
from importlib.resources import files
from pathlib import Path

from .browser_device_bundle import (
    NATIVE_HOST,
    BrowserExtensionIdentity,
    _json,
    _write_bundle,
    browser_extension_identity,
)
from .browser_device_native import _private_read, load_browser_native_configuration
from .browser_device_profile import _platform
from .browser_device_registration import MAINTENANCE_MARKER, _matches
from .browser_device_resume_maintenance import BrowserResumeRetirementEvidence
from .browser_device_resume_workflow import BrowserResumeWorkflow
from .browser_device_startup import _launch_lock
from .browser_device_store import BrowserDeviceStore

_MODULES = ("browser_device_recovery.mjs", "browser_device_retirement_ui.mjs",
            "browser_device_retirement_startup.mjs")


class BrowserRetirementBundleError(RuntimeError):
    def __init__(self) -> None:
        super().__init__(
            "Recovery-only bundle could not be prepared or confirmed. Retain all files "
            "and maintenance guards; do not overwrite output, register it manually, "
            "reset browser state or resume sign-in. Nothing was registered or started."
        )


def _canonical(
    root: Path, session: BrowserResumeWorkflow, *, operation_id: str, browser_intent: str,
    handoff: Path | None = None, proof: BrowserResumeRetirementEvidence | None = None,
) -> tuple[BrowserExtensionIdentity, dict[str, bytes], bytes]:
    """Caller owns launcher; handoff callers separately verify guard/registration."""
    if proof is None:
        proof = session._confirm(operation_id, browser_intent)
    key = browser_extension_identity(session._registration["public_key"])
    config = load_browser_native_configuration(session._profile)
    python = Path(sys.executable)
    if (config.identity != proof.identity
            or config.extension_origin != f"chrome-extension://{key.extension_id}/"
            or not python.is_absolute() or not python.is_file() or not os.access(python, os.X_OK)):
        raise ValueError()
    settings = json.dumps({"origin": config.origin, "identity": config.identity,
                           "nativeHost": NATIVE_HOST}, ensure_ascii=True)
    host = f"[{config.hostname}]" if ":" in config.hostname else config.hostname
    assets = files("sds200.browser_assets")
    artifacts = {f"extension/{name}": assets.joinpath(name).read_bytes() for name in _MODULES}
    artifacts.update({
        "extension/manifest.json": _json({
            "manifest_version": 3, "version": "0.0.4",
            "name": "SDSCTL experimental paused maintenance confirmation",
            "key": key.manifest_key,
            "permissions": ["nativeMessaging", "storage", "cookies", "alarms"],
            "host_permissions": [f"https://{host}:{config.port}/*"],
            "background": {"service_worker": "worker.mjs", "type": "module"},
            "incognito": "not_allowed",
            "content_security_policy": {
                "extension_pages": "script-src 'self'; object-src 'none'; connect-src 'none'",
            },
        }),
        "extension/worker.mjs": (
            "import {connectChromeRetirementRecovery} "
            "from './browser_device_retirement_startup.mjs';\n"
            f"connectChromeRetirementRecovery(chrome, {settings}"
            + (", true" if handoff is not None else "") + ");\n"
        ).encode("ascii"),
        "extension/recovery.mjs": (
            "import {connectRetirementPage} from './browser_device_retirement_ui.mjs';\n"
            "connectRetirementPage({document,window,runtime:chrome.runtime});\n"
        ).encode("ascii"),
        "extension/recovery.html": (
            "<!doctype html><html lang='en'><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>SDSCTL paused recovery confirmation</title>"
            "<link rel='stylesheet' href='recovery.css'><main>"
            "<h1>Resolve interrupted recovery</h1><p>Experimental maintenance only</p><dl>"
            f"<dt>Server</dt><dd>{html.escape(config.origin)}</dd>"
            f"<dt>Device</dt><dd>{html.escape(config.device_id)}</dd>"
            f"<dt>Extension</dt><dd>{key.extension_id}</dd></dl>"
            "<p>This page can acknowledge already-completed local maintenance. It cannot "
            "sign in, repair native errors, replace credentials or resume automatic sign-in. "
            "Do not enter a password or credential here.</p>"
            "<button id='review' type='button'>Review completed maintenance</button>"
            "<p id='reviewed'></p><form id='recovery-form'><label>"
            "<input type='checkbox' id='confirm' required disabled>"
            " I have checked this server and device. Resolve its interrupted browser record "
            "and keep automatic sign-in paused.</label><p>"
            "<button id='resolve' type='submit' disabled>Resolve and keep paused</button>"
            "</p></form><p id='notice' role='status'>Opening this page does not change "
            "the recovery record or attempt a login.</p>"
            "<p>The maintenance guard stays in place. This page is not permission to remove "
            "it or restart normal sign-in.</p></main>"
            "<script type='module' src='recovery.mjs'></script></html>\n"
        ).encode("ascii"),
        "extension/recovery.css": (
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
            "from sds200.browser_device_native import (\n"
            "    run_browser_native, BrowserRetirementSelection)\n"
            "raise SystemExit(run_browser_native(\n"
            f"    Path({str(config.root)!r}), sys.argv[1:],\n"
            "    os.fdopen(os.dup(0), 'rb', buffering=0),\n"
            "    os.fdopen(os.dup(1), 'wb', buffering=0),\n"
            f"    expected_identity={config.identity!r},\n"
            f"    retirement=BrowserRetirementSelection(Path({str(session._archives)!r}),"
            f"{operation_id!r}"
            + (f", Path({str(handoff)!r})" if handoff is not None else "") + "),\n))\n"
        ).encode(),
        NATIVE_HOST + ".json": _json({
            # Deliberately the SAME host name: a later guarded handoff must replace
            # the normal endpoint, never leave it reachable by a cached old worker.
            "name": NATIVE_HOST, "description": "SDSCTL confirmation-only recovery",
            "type": "stdio", "path": str(root / "native-host"),
            "allowed_origins": [config.extension_origin],
        }),
    })
    receipt = _json({
        "version": 1, "experimental": True, "operation": "prepare-retirement-bundle",
        **({"handoff": str(handoff)} if handoff is not None else {}),
        "targets": session._targets, "destination": str(root),
        "operation_id": operation_id, "browser_intent": browser_intent,
        "evidence": asdict(proof), "extension_id": key.extension_id,
        "public_key_sha256": key.public_key_sha256,
        "guard_sha256": hashlib.sha256(_private_read(
            session._root, MAINTENANCE_MARKER, 32768)).hexdigest(),
        "files": {name: hashlib.sha256(body).hexdigest() for name, body in artifacts.items()},
    })
    return key, artifacts, receipt


def _validate(root: Path, artifacts: dict[str, bytes], receipt: bytes) -> None:
    _matches(root / "bundle.json", receipt)
    if ({p.name for p in root.iterdir()} != {
            "bundle.json", "extension", "native-host", "native_host.py", NATIVE_HOST + ".json"}
            or {p.name for p in (root / "extension").iterdir()} != {
                name.removeprefix("extension/") for name in artifacts
                if name.startswith("extension/")}):
        raise ValueError()
    for name, body in artifacts.items():
        _matches(root / name, body, 0o700 if name == "native-host" else 0o600)


def prepare_browser_retirement_bundle(
    root: Path, *, directory: Path, profile: Path, bundle: Path, public_key: Path,
    archives: Path, operation_id: str, browser_intent: str, handoff: Path | None = None,
) -> BrowserExtensionIdentity:
    """Only a new inert output. A prepared bundle is NOT a launch or browser ACK."""
    return _prepare_or_inspect(root, directory=directory, profile=profile, bundle=bundle,
        public_key=public_key, archives=archives, operation_id=operation_id,
        browser_intent=browser_intent, create=True, handoff=handoff)


def inspect_browser_retirement_bundle(
    root: Path, *, directory: Path, profile: Path, bundle: Path, public_key: Path,
    archives: Path, operation_id: str, browser_intent: str, handoff: Path | None = None,
) -> BrowserExtensionIdentity:
    """Read-only exact reconstruction from current runtime and committed evidence."""
    return _prepare_or_inspect(root, directory=directory, profile=profile, bundle=bundle,
        public_key=public_key, archives=archives, operation_id=operation_id,
        browser_intent=browser_intent, create=False, handoff=handoff)


def _prepare_or_inspect(
    root: Path, *, directory: Path, profile: Path, bundle: Path, public_key: Path,
    archives: Path, operation_id: str, browser_intent: str, create: bool,
    handoff: Path | None = None,
) -> BrowserExtensionIdentity:
    try:
        _platform()
        BrowserDeviceStore(root)._check(database=False)
        if (not root.is_absolute() or root.resolve() != root
                or any(root == p or root.is_relative_to(p) or p.is_relative_to(root)
                       for p in (directory, profile, bundle, archives))
                or (create and (root.exists() or root.is_symlink()))):
            raise ValueError()
        session = BrowserResumeWorkflow(directory, profile=profile, bundle=bundle,
                                        public_key=public_key, archives=archives)
        if handoff is not None:
            BrowserDeviceStore(handoff)._check(database=False)
            if (handoff.resolve() != handoff or any(
                    handoff == p or handoff.is_relative_to(p) or p.is_relative_to(handoff)
                    for p in (root, directory, profile, bundle, archives))):
                raise ValueError()
        with _launch_lock(directory, create=False):
            key, artifacts, receipt = _canonical(root, session, operation_id=operation_id,
                                                 browser_intent=browser_intent, handoff=handoff)
            if create:
                _write_bundle(root, artifacts, receipt)
            _validate(root, artifacts, receipt)
            # Slow writes, changed native evidence or guard replacement must not
            # turn an old preparation snapshot into a current successful result.
            if _canonical(root, session, operation_id=operation_id, browser_intent=browser_intent,
                          handoff=handoff) != (key, artifacts, receipt):
                raise ValueError()
            return key
    except Exception:
        raise BrowserRetirementBundleError() from None
