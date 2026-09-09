"""Experimental protected-config/HTTPS/native-pipe runner; not registered or launched.

An eventual trusted wrapper must fix the installation directory in code, pass
the browser-supplied extension origin separately, and use unbuffered native
pipes. Never accept a configuration path or server URL in a native request.
"""

from __future__ import annotations

import ctypes
import hashlib
import http.client
import io
import ipaddress
import json
import os
import re
import signal
import ssl
import stat
import struct
import sys
import threading
import time
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import BinaryIO
from urllib.parse import urlsplit

from .browser_device_profile_access import browser_profile_access
from .browser_device_protocol import (
    BrowserDeviceAction,
    BrowserDeviceRequest,
    BrowserRecoveryLaunchRequest,
    BrowserResumeRequest,
    BrowserRetirementAcknowledgement,
    BrowserRetirementRequest,
    BrowserWorkerContextRequest,
    BrowserWorkerRequest,
    read_browser_device_request,
)
from .browser_device_recovery import (
    BrowserDeviceRecovery,
    ExchangeFailure,
    ExchangeSession,
    RecoveryMode,
    RecoveryResult,
    _object,
    parse_exchange_response,
)
from .browser_device_store import BrowserDeviceStore
from .browser_device_worker import (
    BrowserWorkerSelection,
    normal_worker_paused_only,
    worker_context,
    worker_graph,
)

_TOTAL_SECONDS = 10


@dataclass(frozen=True, slots=True)
class BrowserRetirementSelection:
    """Trusted wrapper-only selection; never enables authentication/resume.

    Only an explicit handoff also enables a durable paused acknowledgement.
    No field may come from native messages or browser-controlled storage.
    """

    archives: Path = field(repr=False)
    operation_id: str = field(repr=False)
    handoff: Path | None = field(default=None, repr=False)


def _private_read(root: Path, name: str, maximum: int) -> bytes:
    """Bounded regular-file snapshot; reject unsafe input rather than chmod it."""
    BrowserDeviceStore(root / name)._check(database=False)
    fd = os.open(root / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb", buffering=0) as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1
                or not 0 < info.st_size <= maximum):
            raise ValueError()
        value = stream.read(maximum + 1)
        if not 0 < len(value) <= maximum:
            raise ValueError()
        return value


@dataclass(frozen=True, slots=True)
class BrowserNativeConfiguration:
    root: Path
    origin: str
    hostname: str
    port: int
    device_id: str
    extension_origin: str

    @property
    def identity(self) -> str:
        value = json.dumps([self.origin, self.device_id, self.extension_origin],
                           separators=(",", ":"))
        return hashlib.sha256(value.encode("ascii")).hexdigest()


def load_browser_native_configuration(root: Path) -> BrowserNativeConfiguration:
    """Load only fixed filenames from an explicitly provisioned private installation."""
    try:
        return parse_browser_native_configuration(root, _private_read(root, "client.json", 4096))
    except Exception:
        raise ExchangeFailure(RecoveryMode.SETUP_ERROR) from None


def parse_browser_native_configuration(root: Path, body: bytes) -> BrowserNativeConfiguration:
    """Validate the same bounded configuration before a new profile is written."""
    try:
        if type(body) is not bytes or not 0 < len(body) <= 4096:
            raise ValueError()
        value = json.loads(body.decode("utf-8"), object_pairs_hook=_object)
        if (type(value) is not dict
                or set(value) != {"version", "origin", "device_id", "extension_origin"}
                or type(value["version"]) is not int or value["version"] != 1
                or any(type(value[key]) is not str
                       for key in ("origin", "device_id", "extension_origin"))
                or re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", value["device_id"]) is None
                or re.fullmatch(r"chrome-extension://[a-p]{32}/",
                                value["extension_origin"]) is None):
            raise ValueError()
        origin = value["origin"]
        if not origin.isascii() or any(character.isspace() for character in origin):
            raise ValueError()
        parsed = urlsplit(origin)
        host, port = parsed.hostname, parsed.port or 443
        if (parsed.scheme != "https" or host is None or parsed.username is not None
                or parsed.password is not None or parsed.path or parsed.query or parsed.fragment
                or parsed.port == 0 or "%" in host):
            raise ValueError()
        try:
            address = ipaddress.ip_address(host)
            host = str(address)
        except ValueError:
            if (len(host) > 253 or host.endswith(".") or host.split(".")[-1].isdigit()
                    or any(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) is None
                           for label in host.split("."))):
                raise ValueError() from None
        authority = f"[{host}]" if ":" in host else host
        if port != 443:
            authority += f":{port}"
        if origin != "https://" + authority:
            raise ValueError()
        return BrowserNativeConfiguration(root, origin, host, port,
                                          value["device_id"], value["extension_origin"])
    except Exception:
        raise ExchangeFailure(RecoveryMode.SETUP_ERROR) from None


def exchange_browser_device(configuration: BrowserNativeConfiguration) -> ExchangeSession:
    status, body, content_type, retry = _post_browser_device(
        configuration, "/auth/device/session", {"device_id": configuration.device_id},
    )
    return parse_exchange_response(status, body, content_type=content_type, retry_after=retry)


def _post_browser_device(
    configuration: BrowserNativeConfiguration, path: str, payload: dict[str, object],
) -> tuple[int, bytes, str, str | None]:
    """Exactly one verified HTTPS request, no redirects, proxy env or cookie jar.

    Call under the native runner's total deadline; the per-I/O timeout alone
    cannot bound DNS resolution or a peer slowly dripping a response.
    """
    connection = None
    try:
        try:
            credential = _private_read(configuration.root, "device.secret", 128).decode("ascii")
            credential = credential.removesuffix("\n")
            if re.fullmatch(r"sdsctl-browser-v1\.[a-f0-9]{64}", credential) is None:
                raise ValueError()
            ca = _private_read(configuration.root, "ca.pem", 128 * 1024).decode("ascii")
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_verify_locations(cadata=ca)
            assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
        except Exception:
            raise ExchangeFailure(RecoveryMode.SETUP_ERROR) from None
        connection = http.client.HTTPSConnection(configuration.hostname, configuration.port,
                                                  context=context, timeout=3)
        connection.request("POST", path, json.dumps(payload).encode("ascii"),
                           {"Authorization": "Bearer " + credential,
                            "Content-Type": "application/json", "Accept": "application/json"})
        response = connection.getresponse()
        types = response.headers.get_all("Content-Type", [])
        retries = response.headers.get_all("Retry-After", [])
        if (len(types) > 1 or (response.status == 200 and len(types) != 1)
                or len(retries) > 1 or response.headers.get_all("Set-Cookie")
                or response.headers.get_all("Content-Encoding")):
            raise ExchangeFailure(RecoveryMode.PROTOCOL_ERROR)
        body = b""
        if response.status == 200:
            lengths = response.headers.get_all("Content-Length", [])
            encodings = response.headers.get_all("Transfer-Encoding", [])
            if (len(lengths) > 1 or len(encodings) > 1 or (lengths and encodings)
                    or (encodings and encodings[0].lower() != "chunked")
                    or (lengths and (re.fullmatch(r"[0-9]{1,10}", lengths[0]) is None
                                     or int(lengths[0]) > 4096))):
                raise ExchangeFailure(RecoveryMode.PROTOCOL_ERROR)
            body = response.read(4097)
            # read(amt) may silently return a short Content-Length body at EOF.
            # Do not persist a protocol error for a server interrupted mid-response.
            if lengths and len(body) < int(lengths[0]):
                raise ExchangeFailure()
        return response.status, body, types[0] if types else "", retries[0] if retries else None
    except ExchangeFailure:
        raise
    except (ssl.SSLEOFError, ssl.SSLZeroReturnError, http.client.IncompleteRead):
        raise ExchangeFailure() from None
    except ssl.SSLError:
        raise ExchangeFailure(RecoveryMode.TLS_ERROR) from None
    except (OSError, TimeoutError):
        raise ExchangeFailure() from None
    except Exception:
        raise ExchangeFailure(RecoveryMode.PROTOCOL_ERROR) from None
    finally:
        if connection is not None:
            connection.close()


def _result_document(result: RecoveryResult) -> dict[str, object]:
    document: dict[str, object] = {
        "version": 1, "ok": True, "mode": result.status.mode.value,
        "revision": result.status.revision, "retry_after": result.status.retry_after,
        "renew_after": result.renew_after,
    }
    if result.session is not None:
        document["session"] = {"token": result.session.token,
                               "expires_in": result.session.expires_in}
    return document


def _resume_request(
    root: Path, request: BrowserResumeRequest, configuration: BrowserNativeConfiguration,
    recovery: BrowserDeviceRecovery,
) -> dict[str, object]:
    # Lazy import: resume's verified transport shares this native module.
    from .browser_device_resume import BrowserDeviceResume, BrowserResumeApproval
    from .browser_device_verification import verify_browser_device

    if request.action == "review-resume":
        before = recovery.inspect()
        if before.mode is RecoveryMode.ACTIVE:
            raise ValueError()
        proof = verify_browser_device(configuration)
        after = recovery.inspect()
        if (before.revision, before.mode) != (after.revision, after.mode):
            raise ValueError()
        return {"version": 1, "ok": True, "mode": after.mode.value,
                "revision": after.revision, "generation": proof.record.generation}
    engine = BrowserDeviceResume(root)
    assert request.intent is not None and request.revision is not None
    if request.action == "prepare-resume":
        assert request.generation is not None
        approval = engine.prepare_verified(expected_revision=request.revision,
            browser_intent=request.intent, expected_generation=request.generation)
        return {"version": 1, "ok": True, "approval": {"ticket": approval.ticket,
            "revision": approval.revision, "expires_at": approval.expires_at}}
    assert request.action == "commit-resume"
    assert request.ticket is not None and request.expires_at is not None
    return _result_document(engine.commit_session(
        BrowserResumeApproval(request.ticket, request.revision, request.expires_at),
        browser_intent=request.intent))


def _native_request(
    root: Path, caller_arguments: list[str], source: BinaryIO, destination: BinaryIO,
    *, expected_identity: str | None = None,
    retirement: BrowserRetirementSelection | None = None,
    worker: BrowserWorkerSelection | None = None,
) -> int:
    try:
        try:
            # Held across input parsing, private reads and dispatch/network work.
            # A busy maintenance/input-writer owner fails closed without waiting.
            with browser_profile_access(root, exclusive=False):
                configuration = load_browser_native_configuration(root)
                if (caller_arguments != [configuration.extension_origin]
                        or (expected_identity is not None
                            and configuration.identity != expected_identity)):
                    raise ValueError()
                request = read_browser_device_request(source)
                if request is None:
                    raise ValueError()
                paused_only = False
                if worker is not None:
                    if (expected_identity is None or not isinstance(request,
                            (BrowserWorkerContextRequest, BrowserWorkerRequest))
                            or request.build != worker_graph()[0]):
                        raise ValueError()
                    if isinstance(request, BrowserWorkerRequest):
                        if retirement is None:
                            paused_only = normal_worker_paused_only(configuration, worker)
                        request = request.request
                elif isinstance(request, (BrowserWorkerContextRequest, BrowserWorkerRequest)):
                    raise ValueError()
                if isinstance(request, BrowserWorkerContextRequest):
                    assert worker is not None
                    document = worker_context(configuration, worker, retirement)
                elif paused_only:
                    if (not isinstance(request, BrowserDeviceRequest)
                            or request.action is not BrowserDeviceAction.STATUS):
                        raise ValueError()
                    recovery = BrowserDeviceRecovery(
                        root / "recovery.sqlite", configuration.identity)
                    # Even a normal status tick may persist a clock correction.
                    # Released paused evidence must remain byte-identical.
                    status = recovery.inspect()
                    if status.mode is not RecoveryMode.PAUSED:
                        raise ValueError()
                    document = _result_document(RecoveryResult(status))
                elif retirement is not None or isinstance(
                        request, (BrowserRetirementRequest, BrowserRetirementAcknowledgement,
                                  BrowserRecoveryLaunchRequest)):
                    # Separate recovery endpoint. It can confirm evidence and,
                    # only with a live handoff, acknowledge a saved local pause.
                    # It cannot execute a review or fall through to authentication.
                    if (not isinstance(retirement, BrowserRetirementSelection)
                            or not isinstance(request, (BrowserRetirementRequest,
                                                        BrowserRetirementAcknowledgement,
                                                        BrowserRecoveryLaunchRequest))
                            or expected_identity is None or request.identity != expected_identity):
                        raise ValueError()
                    if retirement.handoff is not None:
                        from .browser_device_handoff import handoff_native_request

                        document = handoff_native_request(root, retirement, request)
                    else:
                        if not isinstance(request, BrowserRetirementRequest):
                            raise ValueError()
                        from .browser_device_resume_boundary import BrowserResumeBoundary

                        proof = BrowserResumeBoundary(root, archives=retirement.archives).confirm(
                            operation_id=retirement.operation_id, browser_intent=request.intent)
                        document = {"version": 1, "ok": True, "evidence": asdict(proof)}
                elif isinstance(request, BrowserResumeRequest):
                    recovery = BrowserDeviceRecovery(
                        root / "recovery.sqlite", configuration.identity)
                    if expected_identity is None:
                        # Only a fixed identity-bound installed wrapper enables resume.
                        raise ValueError()
                    document = _resume_request(root, request, configuration, recovery)
                else:
                    recovery = BrowserDeviceRecovery(
                        root / "recovery.sqlite", configuration.identity)
                    result = recovery.handle(
                        request, lambda: exchange_browser_device(configuration))
                    document = _result_document(result)
        except Exception:
            document = {"version": 1, "ok": False, "mode": "setup_error"}
        body = json.dumps(document, allow_nan=False, separators=(",", ":")).encode("ascii")
        if len(body) > 4096:
            raise ValueError()
        framed = struct.pack("=I", len(body)) + body
        while framed:
            written = destination.write(framed)
            if type(written) is not int or not 0 < written <= len(framed):
                raise ValueError()
            framed = framed[written:]
        destination.flush()
        return 0
    except Exception:
        return 1


def run_browser_native(
    root: Path, caller_arguments: list[str], source: BinaryIO, destination: BinaryIO,
    *, expected_identity: str | None = None,
    retirement: BrowserRetirementSelection | None = None,
    worker: BrowserWorkerSelection | None = None,
) -> int:
    """Dedicated Linux native process: supervise one child and reap it on timeout.

    Requires single-threaded main process and unbuffered FileIO native pipes.
    Root, optional identity and confirmation-only retirement selection are fixed
    by a trusted wrapper, never browser/native message parameters.
    Exit 2 means ten-second total deadline (partial/no response must be discarded).
    Parent never loads secrets. Bundle preparation does not register or launch it.
    """
    if (not sys.platform.startswith("linux") or threading.active_count() != 1
            or threading.current_thread() is not threading.main_thread()
            or signal.getsignal(signal.SIGCHLD) != signal.SIG_DFL
            or not isinstance(source, io.FileIO) or not isinstance(destination, io.FileIO)):
        return 1
    parent = os.getpid()
    started = time.monotonic()
    try:
        child = os.fork()
    except OSError:
        return 1
    if child == 0:
        code = 1
        try:
            # PR_SET_PDEATHSIG; close the race where the supervisor died before prctl.
            libc = ctypes.CDLL(None)
            libc.prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong,
                                   ctypes.c_ulong, ctypes.c_ulong]
            libc.prctl.restype = ctypes.c_int
            if libc.prctl(1, signal.SIGKILL, 0, 0, 0) == 0 and os.getppid() == parent:
                code = _native_request(root, caller_arguments, source, destination,
                    expected_identity=expected_identity, retirement=retirement, worker=worker)
        except BaseException:
            pass  # Dedicated child exits without exception/secret output or buffered flushing.
        os._exit(code)
    reaped = False
    try:
        while time.monotonic() - started < _TOTAL_SECONDS:
            try:
                result, status = os.waitpid(child, os.WNOHANG)
            except ChildProcessError:
                reaped = True  # Never signal a possibly reused PID if ownership was lost.
                return 1
            if result == child:
                reaped = True
                return os.waitstatus_to_exitcode(status) if os.WIFEXITED(status) else 1
            time.sleep(0.02)
        return 2
    except Exception:
        return 1
    finally:
        if not reaped:
            with suppress(ProcessLookupError):
                os.kill(child, signal.SIGKILL)
            with suppress(ChildProcessError):
                os.waitpid(child, 0)
