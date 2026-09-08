"""Internal stopped-browser host handoff and durable paused acknowledgement.

No CLI, browser launcher, service control or guard release. A trusted local
callback will eventually supervise a browser; its return/exit status is NOT ACK.
Only the selected native endpoint can record the browser's explicit paused ACK.
Same-account/root code is trusted. Advisory ownership does not fence manual edits.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import secrets
import select
import stat
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .browser_device_bundle import NATIVE_HOST, _json
from .browser_device_native import BrowserRetirementSelection, _private_read
from .browser_device_profile import _platform, _write
from .browser_device_profile_access import browser_profile_access
from .browser_device_protocol import (
    BrowserRecoveryLaunchRequest,
    BrowserRetirementAcknowledgement,
    BrowserRetirementRequest,
)
from .browser_device_recovery import _object
from .browser_device_registration import MAINTENANCE_MARKER, _matches, _receipt, _validated_bundle
from .browser_device_resume import _hex
from .browser_device_resume_maintenance import BrowserResumeRetirementEvidence
from .browser_device_resume_workflow import BrowserResumeWorkflow
from .browser_device_retirement_bundle import _canonical, _validate
from .browser_device_startup import _launch_lock
from .browser_device_store import BrowserDeviceStore

_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_HOST = NATIVE_HOST + ".json"
_RECEIPT = ".sdsctl-browser-registration.json"
_OWNER = "owner.lock"
_ACK = "browser-acknowledgement.json"
_LAUNCH = "browser-launch-ready.json"


class BrowserHandoffError(RuntimeError):
    def __init__(self) -> None:
        super().__init__(
            "Recovery handoff could not be confirmed. Retain all files, receipts and guards. "
            "Do not replay a switch, restore files manually, reset browser state or resume "
            "sign-in. Stop the selected browser before exact read-only confirmation."
        )


@dataclass(frozen=True, slots=True)
class BrowserHandoffAcknowledgement:
    identity: str = field(repr=False)
    operation_id: str = field(repr=False)
    receipt_sha256: str = field(repr=False)
    mode: str
    revision: int


def _digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _write_file(root: Path, name: str, body: bytes) -> None:
    fd = os.open(root, _FLAGS)
    try:
        _write(fd, name, body)  # Exclusive. Partial output is never overwritten.
        os.fsync(fd)
    finally:
        os.close(fd)
    _matches(root / name, body)


def _lock_file(path: Path) -> tuple[int, list[int]]:
    BrowserDeviceStore(path)._check(database=False)
    fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or info.st_size != 0):
            raise ValueError()
        named = path.lstat()
        if (named.st_dev, named.st_ino) != (info.st_dev, info.st_ino):
            raise ValueError()
        return fd, [info.st_dev, info.st_ino]
    except BaseException:
        os.close(fd)
        raise


def _busy(path: Path, expected: list[int]) -> None:
    fd, binding = _lock_file(path)
    try:
        if binding != expected:
            raise ValueError()
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        raise ValueError()  # No live cooperating owner; never adopt a stale lease.
    finally:
        os.close(fd)


def _process_identity(pid: int) -> list[int]:
    """Linux PID plus start ticks; a fork-inherited lock is not a live supervisor.

    The comm field may contain spaces and parentheses; fields after its final
    closing parenthesis begin at proc field 3. Field 22 is the start tick count.
    This is kernel metadata, never a caller-selected path or a diagnostic dump.
    """
    if type(pid) is not int or not 0 < pid < 2**31:
        raise ValueError()
    with (Path("/proc") / str(pid) / "stat").open("rb") as stream:
        body = stream.read(4097)
    fields = body.rsplit(b")", 1)[1].split()
    if (len(body) > 4096 or body.split(b" ", 1)[0] != str(pid).encode("ascii")
            or len(fields) < 20 or fields[0] in {b"Z", b"X", b"x"}
            or not fields[19].isdigit()):
        raise ValueError()
    return [pid, int(fields[19])]


class BrowserRecoveryHandoff:
    def __init__(self, root: Path, *, directory: Path, profile: Path, bundle: Path,
                 public_key: Path, archives: Path, recovery_bundle: Path,
                 operation_id: str, browser_intent: str, supervised: bool = False) -> None:
        try:
            _platform()
            roots = (root, directory, profile, bundle, archives, recovery_bundle)
            if (any(not p.is_absolute() or p.resolve() != p for p in roots)
                    or any(a == b or a.is_relative_to(b) or b.is_relative_to(a)
                           for i, a in enumerate(roots) for b in roots[i + 1:])
                    or not _hex(operation_id) or not _hex(browser_intent)
                    or type(supervised) is not bool):
                raise ValueError()
            BrowserDeviceStore(root)._check(database=False)
            self._root, self._recovery = root, recovery_bundle
            self._session = BrowserResumeWorkflow(directory, profile=profile, bundle=bundle,
                                                  public_key=public_key, archives=archives)
            self._operation, self._intent = operation_id, browser_intent
            self._supervised = supervised
            self._targets = {**self._session._targets, "handoff": str(root),
                             "recovery_bundle": str(recovery_bundle)}
            self._attempted = False
        except Exception:
            raise BrowserHandoffError() from None

    def _material(self, nonce: str, owner: list[int], process: list[int], *, stopped: bool,
                  ) -> tuple[bytes, bytes, bytes, bytes, bytes, BrowserResumeRetirementEvidence]:
        session = self._session
        proof = session._confirm_guard(self._operation, self._intent, stopped=stopped)
        registration, original_host = _validated_bundle(**session._registration, fresh=False)
        if registration.identity != proof.identity:
            raise ValueError()
        original_receipt = _receipt(registration, session._registration["bundle"], session._profile)
        _matches(session._root / _RECEIPT, original_receipt)
        key, artifacts, receipt = _canonical(self._recovery, session,
            operation_id=self._operation, browser_intent=self._intent,
            handoff=self._root, proof=proof, supervised=self._supervised)
        _validate(self._recovery, artifacts, receipt)
        guard = _private_read(session._root, MAINTENANCE_MARKER, 32768)
        info = self._root.lstat()
        record = _json({
            "version": 1, "operation": "paused-browser-handoff", "targets": self._targets,
            "supervised": self._supervised,
            "operation_id": self._operation, "browser_intent": self._intent, "nonce": nonce,
            "identity": proof.identity, "extension_id": key.extension_id, "evidence": asdict(proof),
            "handoff_binding": [info.st_dev, info.st_ino], "owner_binding": owner,
            "owner_process": process,
            "browser_binding": session._browser_binding(stopped=stopped),
            "guard_sha256": _digest(guard), "original_host_sha256": _digest(original_host),
            "original_receipt_sha256": _digest(original_receipt),
            "recovery_receipt_sha256": _digest(receipt),
            "recovery_host_sha256": _digest(artifacts[_HOST]),
        })
        return record, original_receipt, original_host, artifacts[_HOST], guard, proof

    def _checked(self, *, stopped: bool, restored: bool = False, live: bool = False,
                 ) -> tuple[bytes, bytes, BrowserResumeRetirementEvidence]:
        raw = _private_read(self._root, "operation.json", 32768)
        data = json.loads(raw, object_pairs_hook=_object)
        nonce, owner, process = data["nonce"], data["owner_binding"], data["owner_process"]
        if (not _hex(nonce) or type(owner) is not list or len(owner) != 2
                or any(type(n) is not int or n < 0 for n in owner)
                or type(process) is not list or len(process) != 2
                or any(type(n) is not int or n <= 0 for n in process)):
            raise ValueError()
        if not stopped and not live:
            raise ValueError()
        if live:
            if _process_identity(process[0]) != process:
                raise ValueError()
            _busy(self._root / _OWNER, owner)
            _busy(self._session._root / ".sdsctl-device-launch.lock",
                  self._session._browser_binding(stopped=stopped)[1])
        material = self._material(nonce, owner, process, stopped=stopped)
        record, receipt, original, replacement, guard, proof = material
        if raw != record:
            raise ValueError()
        fd, actual_owner = _lock_file(self._root / _OWNER)
        os.close(fd)
        if actual_owner != owner:
            raise ValueError()
        for name, body in (("registration.before.json", receipt),
                           ("native-host.before.json", original), ("guard.before.json", guard),
                           ("switch-started.json", record), ("ready.json", record)):
            _matches(self._root / name, body)
        _matches(self._session._root / "NativeMessagingHosts" / _HOST,
                 original if restored else replacement)
        if {p.name for p in (self._session._root / "NativeMessagingHosts").iterdir()} != {_HOST}:
            raise ValueError()
        if live and any((self._root / name).exists() or (self._root / name).is_symlink()
                        for name in ("restoration-started.json", "restored.json")):
            raise ValueError()
        if self._session._confirm_guard(self._operation, self._intent, stopped=stopped) != proof:
            raise ValueError()
        if live and _process_identity(process[0]) != process:
            raise ValueError()
        _matches(self._root / "operation.json", record)
        return record, original, proof

    @staticmethod
    def _ack_body(record: bytes, proof: BrowserResumeRetirementEvidence) -> bytes:
        return _json({"version": 1, "operation": "browser-paused-acknowledgement",
                      "handoff_sha256": _digest(record), "evidence": asdict(proof),
                      "local_pause_saved": True, "session_ready": False})

    def _ack(self, record: bytes, proof: BrowserResumeRetirementEvidence,
             *, stopped: bool = False,
             ) -> BrowserHandoffAcknowledgement:
        if self._supervised:
            _matches(self._root / _LAUNCH, self._launch_body(record, live=False))
            if stopped:
                self._supervisor_stopped()
        body = self._ack_body(record, proof)
        _matches(self._root / _ACK, body)
        return BrowserHandoffAcknowledgement(proof.identity, self._operation, _digest(body),
                                               proof.mode, proof.revision)

    def activate(self, callback: Callable[[], None]) -> BrowserHandoffAcknowledgement:
        """One local session; callback return is not consent or acknowledgement.

        Holds launcher/profile/owner coordination through the trusted callback.
        Does not launch a browser itself or restore the host automatically. No
        browser may remain running when callback returns. Every outcome retains
        the guard and operation files. An existing/partial operation is refused.
        """
        owner_fd = None
        try:
            if self._attempted or self._root.exists() or self._root.is_symlink():
                raise ValueError()
            self._attempted = True
            session = self._session
            with _launch_lock(session._root, create=False), browser_profile_access(
                    session._profile, exclusive=False):
                key, artifacts, receipt = _canonical(self._recovery, session,
                    operation_id=self._operation, browser_intent=self._intent, handoff=self._root,
                    supervised=self._supervised)
                _validate(self._recovery, artifacts, receipt)
                parent = os.open(self._root.parent, _FLAGS)
                try:
                    info = os.fstat(parent)
                    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
                        raise ValueError()
                    os.mkdir(self._root.name, mode=0o700, dir_fd=parent)
                    os.fsync(parent)
                finally:
                    os.close(parent)
                _write_file(self._root, _OWNER, b"")
                owner_fd, owner = _lock_file(self._root / _OWNER)
                fcntl.flock(owner_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                nonce = secrets.token_hex(32)
                process = _process_identity(os.getpid())
                material = self._material(nonce, owner, process, stopped=True)
                record, original_receipt, original_host, replacement, guard, _ = material
                for name, body in (("registration.before.json", original_receipt),
                    ("native-host.before.json", original_host), ("guard.before.json", guard),
                    ("operation.json", record)):
                    _write_file(self._root, name, body)
                if self._material(nonce, owner, process, stopped=True) != material:
                    raise ValueError()
                hosts = session._root / "NativeMessagingHosts"
                _matches(hosts / _HOST, original_host)
                _write_file(self._root, "switch-started.json", record)
                self._switch_host(original_host, replacement)
                _write_file(self._root, "ready.json", record)
                self._checked(stopped=True, live=True)
                callback()  # Explicit trusted local adapter; never a page-provided command.
                checked, _, proof = self._checked(stopped=True, live=True)
                return self._ack(checked, proof, stopped=True)
        except Exception:
            raise BrowserHandoffError() from None
        finally:
            if owner_fd is not None:
                os.close(owner_fd)

    def _switch_host(self, before: bytes, after: bytes) -> None:
        hosts = self._session._root / "NativeMessagingHosts"
        _matches(hosts / _HOST, before)
        if {p.name for p in hosts.iterdir()} != {_HOST}:
            raise ValueError()
        fd = os.open(hosts, _FLAGS)
        try:
            os.unlink(_HOST, dir_fd=fd)
            os.fsync(fd)
            _write(fd, _HOST, after)
            os.fsync(fd)
        finally:
            os.close(fd)
        _matches(hosts / _HOST, after)

    def _launch_body(self, record: bytes, *, live: bool) -> bytes:
        """Bind page/worker readiness to this exact isolated supervisor; not consent."""
        if not self._supervised:
            raise ValueError()
        scope = _private_read(self._root, "supervisor.json", 8192)
        value = json.loads(scope, object_pairs_hook=_object)
        if (type(value) is not dict or set(value) != {
                "version", "record_sha256", "process", "namespace", "owner_namespace",
                "expires_at", "command_sha256"}
                or type(value["version"]) is not int or value["version"] != 1
                or value["record_sha256"] != _digest(record)
                or not _hex(value["command_sha256"])
                or type(value["expires_at"]) not in (int, float)
                or not math.isfinite(value["expires_at"]) or value["expires_at"] <= 0
                or any(type(value[key]) is not list or len(value[key]) != 2
                       or any(type(n) is not int or n <= 0 for n in value[key])
                       for key in ("process", "namespace", "owner_namespace"))
                or scope != _json(value)):
            raise ValueError()
        if live:
            if (_process_identity(value["process"][0]) != value["process"]
                    or time.monotonic() >= value["expires_at"]):
                raise ValueError()
            ns = (Path("/proc") / str(value["process"][0]) / "ns/pid").stat()
            # The outer launcher verified its own namespace before writing this
            # bound scope. Nested user namespaces cannot dereference an outer
            # owner's ns link; do not weaken ptrace/proc protections to do so.
            if ([ns.st_dev, ns.st_ino] != value["namespace"]
                    or value["namespace"] == value["owner_namespace"]):
                raise ValueError()
        bundle = json.loads(_private_read(self._recovery, "bundle.json", 32768),
                            object_pairs_hook=_object)
        if not _hex(bundle["launch_binding"]):
            raise ValueError()
        return _json({"version": 1, "operation": "recovery-extension-ready",
                      "handoff_sha256": _digest(record), "supervisor_sha256": _digest(scope),
                      "binding": bundle["launch_binding"], "browser_consent": False})

    def _supervisor_stopped(self) -> None:
        """Read-only PID-handle proof: absent Singleton files alone are not enough."""
        process = json.loads(_private_read(self._root, "supervisor.json", 8192))["process"]
        try:
            fd = os.pidfd_open(process[0])
        except ProcessLookupError:
            return
        try:
            if select.select([fd], [], [], 0)[0]:
                return  # Kernel reports exit, including an unreaped zombie.
            try:
                current = _process_identity(process[0])
            except (FileNotFoundError, ProcessLookupError):
                return
            if current == process:
                raise ValueError()
            # The old process exited and its PID was reused; never signal the new one.
        finally:
            os.close(fd)

    def confirm(self, *, restored: bool = False) -> BrowserHandoffAcknowledgement:
        """Read-only stopped confirmation after callback/process/reply loss."""
        try:
            with _launch_lock(self._session._root, create=False), browser_profile_access(
                    self._session._profile, exclusive=False):
                record, _, proof = self._checked(stopped=True, restored=restored)
                ack = self._ack(record, proof, stopped=True)
                if restored:
                    body = self._restoration(record, ack)
                    _matches(self._root / "restoration-started.json", body)
                    _matches(self._root / "restored.json", body)
                elif ((self._root / "restoration-started.json").exists()
                      or (self._root / "restoration-started.json").is_symlink()):
                    raise ValueError()
                return ack
        except Exception:
            raise BrowserHandoffError() from None

    @staticmethod
    def _restoration(record: bytes, ack: BrowserHandoffAcknowledgement) -> bytes:
        return _json({"version": 1, "operation": "restore-paused-browser-host",
                      "handoff_sha256": _digest(record), "ack_sha256": ack.receipt_sha256})

    def restore(self) -> BrowserHandoffAcknowledgement:
        """Explicit exact host restoration after ACK; NEVER release the guard.

        Interrupted restoration is not automatically replayed. Its completed
        result may only be confirmed read-only with restored=True.
        """
        try:
            with _launch_lock(self._session._root, create=False), browser_profile_access(
                    self._session._profile, exclusive=False):
                record, original, proof = self._checked(stopped=True)
                ack = self._ack(record, proof, stopped=True)
                body = self._restoration(record, ack)
                _write_file(self._root, "restoration-started.json", body)
                replacement = _private_read(self._recovery, _HOST, 16384)
                self._switch_host(replacement, original)
                self._checked(stopped=True, restored=True)
                _write_file(self._root, "restored.json", body)
                return ack
        except Exception:
            raise BrowserHandoffError() from None


def handoff_native_request(
    profile: Path, selection: BrowserRetirementSelection,
    request: (BrowserRetirementRequest | BrowserRetirementAcknowledgement
              | BrowserRecoveryLaunchRequest),
) -> dict[str, object]:
    """Fixed wrapper-selected context; guarded native supervisor owns the deadline.

    Browser attestation comes from the same-identity trusted extension after exact
    read-back, not an independent inspection of opaque Chromium databases. Local
    same-account/root code remains trusted; a receipt is not server permission.
    """
    if selection.handoff is None:
        raise ValueError()
    raw = _private_read(selection.handoff, "operation.json", 32768)
    data = json.loads(raw, object_pairs_hook=_object)
    targets = data["targets"]
    if (type(targets) is not dict or set(targets) != {
            "handoff", "directory", "profile", "bundle", "public_key", "archives",
            "recovery_bundle"} or any(type(v) is not str for v in targets.values())
            or targets["handoff"] != str(selection.handoff)
            or targets["profile"] != str(profile) or targets["archives"] != str(selection.archives)
            or data["operation_id"] != selection.operation_id
            or data["browser_intent"] != request.intent or data["identity"] != request.identity):
        raise ValueError()
    handoff = BrowserRecoveryHandoff(selection.handoff,
        **{key: Path(value) for key, value in targets.items() if key != "handoff"},
        operation_id=selection.operation_id, browser_intent=request.intent,
        supervised=data["supervised"])
    with browser_profile_access(selection.handoff, exclusive=True):
        record, _, proof = handoff._checked(stopped=False, live=True)
        if record != raw:
            raise ValueError()
        if isinstance(request, BrowserRecoveryLaunchRequest):
            body = handoff._launch_body(record, live=True)
            if request.binding != json.loads(body)["binding"]:
                raise ValueError()
            path = selection.handoff / _LAUNCH
            if path.exists() or path.is_symlink():
                _matches(path, body)  # Worker recreation only confirms exact completed readiness.
            else:
                _write_file(selection.handoff, _LAUNCH, body)
            checked_record, _, _ = handoff._checked(stopped=False, live=True)
            if checked_record != record or handoff._launch_body(record, live=True) != body:
                raise ValueError()
            return {"version": 1, "ok": True, "binding": request.binding}
        if handoff._supervised:
            _matches(selection.handoff / _LAUNCH, handoff._launch_body(record, live=True))
        if isinstance(request, BrowserRetirementRequest):
            return {"version": 1, "ok": True, "evidence": asdict(proof)}
        if _json(asdict(request)) != _json(asdict(proof)):
            raise ValueError()
        _write_file(selection.handoff, _ACK, handoff._ack_body(record, proof))
        checked, _, current = handoff._checked(stopped=False, live=True)
        if checked != record or current != proof:
            raise ValueError()
        handoff._ack(record, proof)
        return {"version": 1, "ok": True, "mode": "retired_paused", "acknowledged": True}
