"""Role-independent worker graph and fixed-wrapper native context selection.

Build identity detects stale cached code, not hostile same-account software.
Neither browser messages nor storage select a filesystem path or authority role.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .browser_device_native import BrowserNativeConfiguration, BrowserRetirementSelection

MODULES = (
    "browser_device_recovery.mjs", "browser_device_logout.mjs", "browser_device_setup.mjs",
    "browser_device_startup.mjs", "browser_device_resume.mjs", "browser_device_retirement_ui.mjs",
    "browser_device_retirement_startup.mjs", "browser_device_launch.mjs",
    "browser_device_worker.mjs", "browser_device_worker_gate.mjs",
    "browser_device_paused.mjs",
    "browser_device_continuation_state.mjs",
    "browser_device_continuation_probe.mjs",
    "browser_device_continuation_context.mjs",
    "browser_device_continuation_cookie.mjs",
    "browser_device_continuation_worker.mjs",
)
_ENTRY = ("import {startBrowserWorker} from './browser_device_worker.mjs';\n"
          "void startBrowserWorker(chrome, BUILD).catch(()=>{});\n")


def worker_graph() -> tuple[str, dict[str, bytes]]:
    """Digest the entire fixed graph and entry template, excluding only its digest literal."""
    assets = files("sds200.browser_assets")
    graph = {name: assets.joinpath(name).read_bytes() for name in MODULES}
    native = {item.name: hashlib.sha256(item.read_bytes()).hexdigest()
              for item in files("sds200").iterdir()
              if item.name.startswith("browser_device") and item.name.endswith(".py")}
    digest = hashlib.sha256(json.dumps({
        **native,
        **{name: hashlib.sha256(body).hexdigest() for name, body in graph.items()},
        "worker.mjs": hashlib.sha256(_ENTRY.encode("ascii")).hexdigest(),
    }, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()
    graph["worker.mjs"] = _ENTRY.replace("BUILD", json.dumps(digest)).encode("ascii")
    return digest, {"extension/" + name: body for name, body in graph.items()}


@dataclass(frozen=True, slots=True)
class BrowserWorkerSelection:
    """Canonical wrapper arguments; never supplied by browser/native messages."""

    bundle: Path
    public_key: Path
    directory: Path | None = None
    normal_bundle: Path | None = None
    intent: str | None = None


def _selected_directory(raw: bytes, bundle: Path, extension_origin: str) -> Path:
    """Match the fixed managed command tail, including Chromium's flattened title.

    Never shlex/split a flattened pathname on whitespace. The known bundle and
    exact extension entry delimit the directory. Ambiguous switch-like names
    or any earlier nonempty extension/root override are refused.
    """
    if not raw or len(raw) > 32768 or not raw.endswith(b"\0"):
        raise ValueError()
    argv = raw[:-1].decode("utf-8").split("\0")
    entries = [extension_origin + page for page in ("setup.html", "startup.html")]
    load = "--load-extension=" + str(bundle / "extension")
    only = "--disable-extensions-except=" + str(bundle / "extension")
    if len(argv) == 1:
        title = argv[0]
        if title.count(" --user-data-dir=") != 1:
            raise ValueError()
        prefix, tail = title.split(" --user-data-dir=", 1)
        matches = [tail.removesuffix(suffix) for page in entries
                   if tail.endswith(suffix := " " + load + " " + only + " " + page)]
        if len(matches) != 1:
            raise ValueError()
        selected = matches[0]
    else:
        if (len(argv) < 5 or argv[-3:-1] != [load, only] or argv[-1] not in entries
                or not argv[-4].startswith("--user-data-dir=")):
            raise ValueError()
        selected = argv[-4].split("=", 1)[1]
        prefix = " ".join(argv[:-4])
    if (any(ord(c) < 32 or ord(c) == 127 for c in selected + str(bundle))
            or " --" in selected or " --" in str(bundle)
            or any(value for value in re.findall(
                r"(?:^| )--(?:user-data-dir|load-extension|disable-extensions-except)=(\S+)",
                prefix))):
        raise ValueError()
    root = Path(selected)
    if not root.is_absolute() or root.resolve() != root:
        raise ValueError()
    return root


def _browser_directory(bundle: Path, extension_origin: str) -> Path:
    """Bounded live ancestor inspection, not browser-supplied request/environment paths.

    The native child is descended from the selected Chromium. Reject ambiguous
    roots, mismatched extension flags and incomplete process snapshots. This is
    an ownership check, not a sandbox against trusted same-account/root code.
    """
    pid = os.getppid()
    roots: list[Path] = []
    for _ in range(32):
        if pid <= 1:
            break
        proc = Path("/proc") / str(pid)
        if proc.stat().st_uid != os.geteuid():
            break
        with (proc / "stat").open("rb") as stream:
            before = stream.read(4097)
        with (proc / "cmdline").open("rb") as stream:
            raw = stream.read(32769)
        if len(before) > 4096 or len(raw) > 32768 or not raw.endswith(b"\0"):
            raise ValueError()
        parts = before.rsplit(b")", 1)[1].split()
        if b"--user-data-dir=" in raw and (proc / "exe").resolve(strict=True).name in {
                "chromium", "chromium-browser"}:
            roots.append(_selected_directory(raw, bundle, extension_origin))
        with (proc / "stat").open("rb") as stream:
            after = stream.read(4097).rsplit(b")", 1)[1].split()
        if parts[1] != after[1] or parts[19] != after[19]:
            raise ValueError()
        pid = int(parts[1])
    else:
        raise ValueError()
    if len(roots) != 1 or not roots[0].is_absolute() or roots[0].resolve() != roots[0]:
        raise ValueError()
    return roots[0]


def normal_worker_paused_only(
    configuration: BrowserNativeConfiguration, selection: BrowserWorkerSelection,
) -> bool:
    """Revalidate the live normal owner for each request, not only worker startup.

    A released installation is bound to unchanged paused evidence. It permits
    read-only status, not a new revision, credential operation or authentication.
    Same-account/root callers remain trusted; this is not an OS security boundary.
    """
    from .browser_device_continuation_intent import has_continuation_intent
    from .browser_device_guard_release import _check_worker_guard_release, has_guard_release
    from .browser_device_handoff import _busy, _lock_file
    from .browser_device_registration import MAINTENANCE_MARKER, _inspect_registration_files

    if any(value is not None for value in (
            selection.directory, selection.normal_bundle, selection.intent)):
        raise ValueError()
    directory = _browser_directory(selection.bundle, configuration.extension_origin)
    # Recheck on every request, including already-started workers. No completed
    # intent may be interpreted as activation or an unguarded normal profile.
    if has_continuation_intent(directory):
        raise ValueError()
    lock = directory / ".sdsctl-device-launch.lock"
    fd, binding = _lock_file(lock)
    os.close(fd)
    _busy(lock, binding)
    paused_only = ((directory / MAINTENANCE_MARKER).exists()
        or (directory / MAINTENANCE_MARKER).is_symlink() or has_guard_release(directory))
    if paused_only:
        _check_worker_guard_release(directory, bundle=selection.bundle,
            profile=configuration.root, public_key=selection.public_key)
    _inspect_registration_files(directory, bundle=selection.bundle,
        profile=configuration.root, public_key=selection.public_key)
    _busy(lock, binding)
    return paused_only


def continuation_worker_selected(
    configuration: BrowserNativeConfiguration, selection: BrowserWorkerSelection,
) -> bool:
    """Select from the fixed wrapper and actual ancestor, never a requested role.

    A marker selects strict continuation validation, not permission. Missing or
    invalid history/activation must fail there and never fall back to normal.
    """
    from .browser_device_continuation_intent import has_continuation_intent
    from .browser_device_native import BrowserNativeConfiguration, load_browser_native_configuration

    if (type(configuration) is not BrowserNativeConfiguration
            or type(selection) is not BrowserWorkerSelection
            or any(v is not None for v in (
                selection.directory, selection.normal_bundle, selection.intent))
            or configuration != load_browser_native_configuration(configuration.root)):
        raise ValueError()
    return has_continuation_intent(_browser_directory(
        selection.bundle, configuration.extension_origin))


def worker_context(
    configuration: BrowserNativeConfiguration, selection: BrowserWorkerSelection,
    retirement: BrowserRetirementSelection | None,
) -> dict[str, object]:
    """Read-only canonical validation; no claim, status tick, readiness ACK or login."""
    from .browser_device_handoff import handoff_worker_context
    from .browser_device_retirement_bundle import inspect_browser_retirement_bundle

    if retirement is None:
        if continuation_worker_selected(configuration, selection):
            from .browser_device_continuation_context import _continuation_worker_context

            return _continuation_worker_context(configuration, selection)
        paused_only = normal_worker_paused_only(configuration, selection)
        role, acknowledge, launch = "paused" if paused_only else "normal", False, None
    else:
        if (selection.directory is None or selection.normal_bundle is None
                or selection.intent is None):
            raise ValueError()
        if retirement.handoff is not None:
            return handoff_worker_context(configuration, selection, retirement)
        inspect_browser_retirement_bundle(selection.bundle, directory=selection.directory,
            profile=configuration.root, bundle=selection.normal_bundle,
            public_key=selection.public_key, archives=retirement.archives,
            operation_id=retirement.operation_id, browser_intent=selection.intent)
        role, acknowledge, launch = "recovery", False, None
    return context_document(configuration, role=role, acknowledge=acknowledge, launch=launch)


def context_document(
    configuration: BrowserNativeConfiguration, *, role: str, acknowledge: bool,
    launch: dict[str, object] | None,
) -> dict[str, object]:
    return {"version": 1, "ok": True, "build": worker_graph()[0], "role": role,
        "config": {"origin": configuration.origin, "identity": configuration.identity,
                   "nativeHost": "org.sdsctl.browser_device"},
        "extensionId": configuration.extension_origin.split("/")[2],
        "acknowledge": acknowledge, "launch": launch}
