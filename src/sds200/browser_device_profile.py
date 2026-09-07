"""Explicit first-time experimental browser profile import and offline inspection.

No enrollment request, extension registration, browser launch, trust-store change
or service configuration occurs here. Same-account/root processes are trusted.
Never log/serialize input documents: they contain a one-time credential.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import ssl
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

from .browser_device_native import (
    _private_read,
    load_browser_native_configuration,
    parse_browser_native_configuration,
)
from .browser_device_recovery import BrowserDeviceRecovery, RecoveryMode, _object
from .browser_device_store import BrowserDeviceStore
from .exceptions import ConfigurationError


class BrowserProfileError(ConfigurationError):
    """Fixed diagnostics only, never source paths, document fields or secrets."""


@dataclass(frozen=True, slots=True)
class BrowserProfileInspection:
    identity: str
    trust_sha256: str
    mode: RecoveryMode
    revision: int


def _platform() -> None:
    if sys.platform != "linux" or os.geteuid() == 0:
        raise ValueError()


def _credential(body: bytes) -> str:
    value = body.decode("ascii").removesuffix("\n")
    if re.fullmatch(r"sdsctl-browser-v1\.[a-f0-9]{64}", value) is None:
        raise ValueError()
    return value


def _trust(body: bytes) -> str:
    # Reject mixed certificate/private-key files and ignored trailing material.
    text = body.decode("ascii")
    pattern = r"-----BEGIN CERTIFICATE-----\s+[A-Za-z0-9+/=\s]+-----END CERTIFICATE-----"
    if not re.search(pattern, text) or re.sub(pattern, "", text).strip():
        raise ValueError()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_verify_locations(cadata=text)
    if context.cert_store_stats()["x509"] < 1:
        raise ValueError()
    return hashlib.sha256(body).hexdigest()


def inspect_browser_profile(root: Path) -> BrowserProfileInspection:
    """Offline and read-only: validity is not server reachability or authentication."""
    try:
        _platform()
        config = load_browser_native_configuration(root)
        _credential(_private_read(root, "device.secret", 128))
        trust_sha256 = _trust(_private_read(root, "ca.pem", 128 * 1024))
        status = BrowserDeviceRecovery(root / "recovery.sqlite", config.identity).inspect()
        return BrowserProfileInspection(config.identity, trust_sha256, status.mode, status.revision)
    except Exception:
        raise BrowserProfileError(
            "Browser profile is invalid or unsafe; check private files, trust and recovery state."
        ) from None


def _enrollment(path: Path, device_id: str) -> str:
    body = _private_read(path.parent, path.name, 4096)
    value = json.loads(body.decode("utf-8"), object_pairs_hook=_object)
    if (
        type(value) is not dict
        or set(value) != {"version", "device_id", "generation", "credential", "outcome"}
        or type(value["version"]) is not int
        or value["version"] != 1
        or value["device_id"] != device_id
        or type(value["generation"]) is not int
        or value["generation"] != 1
        or type(value["credential"]) is not str
        or type(value["outcome"]) is not dict
        or set(value["outcome"]) != {"status", "completed"}
        or value["outcome"]["status"] != "issued"
        or value["outcome"]["completed"] is not False
    ):
        raise ValueError()
    # Rotation/replacement, including unconfirmed outcomes, needs a separate workflow.
    credential = value["credential"]
    if "\n" in credential:
        raise ValueError()
    return _credential(credential.encode("ascii"))


def _write(directory_fd: int, name: str, value: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptor = os.open(name, flags, 0o600, dir_fd=directory_fd)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())


def create_browser_profile(
    root: Path,
    *,
    enrollment_file: Path,
    ca_file: Path,
    origin: str,
    device_id: str,
    extension_id: str,
) -> BrowserProfileInspection:
    """Import a fresh issuance into one new private directory; never overwrite.

    Validate all inputs first. Write credentials/trust and initialize the durable
    ledger before publishing client.json last. Interrupted creation is retained
    for inspection, never automatically deleted or retried over an existing path.
    The source credential file is preserved; the caller must manage extra copies.
    """
    try:
        _platform()
        BrowserDeviceStore(root)._check(database=False)
        if root.exists() or root.is_symlink():
            raise FileExistsError()
        document = json.dumps(
            {
                "version": 1,
                "origin": origin,
                "device_id": device_id,
                "extension_origin": f"chrome-extension://{extension_id}/",
            },
            separators=(",", ":"),
        ).encode("ascii")
        config = parse_browser_native_configuration(root, document)
        credential = _enrollment(enrollment_file, device_id)
        trust = _private_read(ca_file.parent, ca_file.name, 128 * 1024)
        _trust(trust)
    except FileExistsError:
        raise BrowserProfileError(
            "Browser profile already exists; refusing to overwrite or reset recovery state."
        ) from None
    except Exception:
        raise BrowserProfileError(
            "Browser profile inputs are invalid or unsafe; no profile was created. "
            "Use a fresh issuance and private files in existing private directories."
        ) from None

    parent_fd = directory_fd = None
    try:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        parent_fd = os.open(root.parent, flags)
        parent = os.fstat(parent_fd)
        if parent.st_uid != os.geteuid() or stat.S_IMODE(parent.st_mode) != 0o700:
            raise ValueError()
        os.mkdir(root.name, 0o700, dir_fd=parent_fd)  # Exclusive even against another importer.
        os.fsync(parent_fd)
        directory_fd = os.open(root.name, flags, dir_fd=parent_fd)
        opened = os.fstat(directory_fd)
        if (opened.st_dev, opened.st_ino) != (root.stat().st_dev, root.stat().st_ino):
            raise ValueError()
        _write(directory_fd, "device.secret", credential.encode("ascii") + b"\n")
        _write(directory_fd, "ca.pem", trust)
        BrowserDeviceRecovery.initialize(root / "recovery.sqlite", config.identity)
        _write(directory_fd, "client.json", document)
        os.fsync(directory_fd)
        return inspect_browser_profile(root)
    except Exception:
        raise BrowserProfileError(
            "Browser profile creation could not be confirmed. Retain any created directory for "
            "inspection; do not retry over it or reset recovery state."
        ) from None
    finally:
        if directory_fd is not None:
            os.close(directory_fd)
        if parent_fd is not None:
            os.close(parent_fd)
