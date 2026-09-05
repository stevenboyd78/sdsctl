"""Bounded browser authentication for future LAN dashboard access."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import secrets
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass, field
from hmac import compare_digest
from html import escape
from urllib.parse import parse_qs, urlsplit

from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .exceptions import ConfigurationError

WEB_DASHBOARD_AUTH_COOKIE = "__Host-sdsctl-session"
WEB_DASHBOARD_LOGIN_PATH = "/auth/login"
WEB_DASHBOARD_DISPLAY_LOGIN_PATH = "/auth/display/login"
WEB_DASHBOARD_SESSION_PATH = "/auth/session"
WEB_DASHBOARD_LOGOUT_PATH = "/auth/logout"
WEB_DASHBOARD_MINIMUM_PASSWORD_CHARACTERS = 16
WEB_DASHBOARD_DEFAULT_SESSION_IDLE_SECONDS = 30 * 60
WEB_DASHBOARD_DEFAULT_SESSION_ABSOLUTE_SECONDS = 8 * 60 * 60
WEB_DASHBOARD_DEFAULT_MAX_SESSIONS = 64
WEB_DASHBOARD_DEFAULT_MAX_REQUESTS_PER_SESSION = 16
WEB_DASHBOARD_MINIMUM_SESSION_TOKEN_CHARACTERS = 43
WEB_DASHBOARD_MAXIMUM_SESSION_TOKEN_CHARACTERS = 128
WEB_DASHBOARD_MAX_LOGIN_BODY_BYTES = 4096
WEB_DASHBOARD_LOGIN_BODY_TIMEOUT_SECONDS = 5
WEB_DASHBOARD_LOGIN_FAILURE_LIMIT = 5
WEB_DASHBOARD_GLOBAL_LOGIN_FAILURE_LIMIT = 100
WEB_DASHBOARD_LOGIN_FAILURE_WINDOW_SECONDS = 60
WEB_DASHBOARD_GLOBAL_LOGIN_RECOVERY_SECONDS = 5
WEB_DASHBOARD_MAX_LOGIN_PEERS = 256
WEB_DASHBOARD_AUTHENTICATION_REQUIRED_DETAIL = "Authentication is required."
WEB_DASHBOARD_AUTHENTICATION_FAILED_DETAIL = "Authentication failed."
WEB_DASHBOARD_ORIGIN_REQUIRED_DETAIL = "The configured dashboard origin is required."

_PASSWORD_SALT_BYTES = 16
_PASSWORD_SCRYPT_N = 2**14
_PASSWORD_SCRYPT_R = 8
_PASSWORD_SCRYPT_P = 5
_PASSWORD_SCRYPT_MAX_MEMORY_BYTES = 32 * 1024 * 1024
_PASSWORD_DERIVED_KEY_BYTES = 32

_Clock = Callable[[], float]
_TokenFactory = Callable[[], str]
_SESSION_TOKEN_CHARACTERS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
)
_DISPLAY_READ_PATHS = frozenset({
    "/", "/healthz", "/api/v1/status", "/api/v1/snapshot",
    "/api/v1/events", "/api/v1/waterfall", WEB_DASHBOARD_SESSION_PATH,
    "/assets/dashboard.css", "/assets/dashboard-viewport.css",
    "/assets/system-palettes.css", "/assets/theme-bootstrap.js",
    "/assets/dashboard.js", "/assets/favicon.svg",
})


@dataclass(frozen=True, slots=True)
class _SessionWatcher:
    loop: asyncio.AbstractEventLoop
    revoked: asyncio.Event


@dataclass(slots=True)
class _Session:
    absolute_expires_at: float
    idle_expires_at: float
    display_only: bool = False
    watchers: dict[int, _SessionWatcher] = field(default_factory=dict)


@dataclass(slots=True)
class _SessionLease:
    authentication: WebDashboardAuthentication
    digest: bytes
    watcher_id: int
    revoked: asyncio.Event
    remaining_seconds: float
    display_only: bool
    released: bool = False

    def release(self) -> None:
        if self.released:
            return
        self.released = True
        self.authentication._release_session(self.digest, self.watcher_id)


class WebDashboardAuthentication:
    """Validate one password and own bounded, process-local browser sessions."""

    def __init__(
        self,
        password: str,
        origin: str,
        *,
        display_password: str | None = None,
        idle_seconds: int = WEB_DASHBOARD_DEFAULT_SESSION_IDLE_SECONDS,
        absolute_seconds: int = WEB_DASHBOARD_DEFAULT_SESSION_ABSOLUTE_SECONDS,
        max_sessions: int = WEB_DASHBOARD_DEFAULT_MAX_SESSIONS,
        max_requests_per_session: int = WEB_DASHBOARD_DEFAULT_MAX_REQUESTS_PER_SESSION,
        clock: _Clock = time.monotonic,
        token_factory: _TokenFactory | None = None,
    ) -> None:
        if not isinstance(password, str):
            raise TypeError("Web dashboard password must be a string.")
        if len(password) < WEB_DASHBOARD_MINIMUM_PASSWORD_CHARACTERS:
            raise ValueError(
                "Web dashboard password must contain at least "
                f"{WEB_DASHBOARD_MINIMUM_PASSWORD_CHARACTERS} characters."
            )
        if type(idle_seconds) is not int or idle_seconds <= 0:
            raise ValueError("Web dashboard session idle lifetime must be positive.")
        if type(absolute_seconds) is not int or absolute_seconds <= 0:
            raise ValueError("Web dashboard session absolute lifetime must be positive.")
        if idle_seconds > absolute_seconds:
            raise ValueError(
                "Web dashboard session idle lifetime must not exceed its absolute lifetime."
            )
        if type(max_sessions) is not int or max_sessions <= 0:
            raise ValueError("Web dashboard maximum session count must be positive.")
        if type(max_requests_per_session) is not int or max_requests_per_session <= 0:
            raise ValueError("Web dashboard maximum requests per session must be positive.")
        if not callable(clock):
            raise TypeError("Web dashboard session clock must be callable.")
        if token_factory is not None and not callable(token_factory):
            raise TypeError("Web dashboard session token factory must be callable or None.")

        self._origin = _normalize_origin(origin)
        self._password_salt = _new_password_salt()
        self._password_digest = _derive_password_key(
            password.encode("utf-8"),
            self._password_salt,
        )
        if display_password is not None:
            if not isinstance(display_password, str) or len(display_password) < 16:
                raise ValueError("Display password must contain at least 16 characters.")
            if compare_digest(display_password.encode("utf-8"), password.encode("utf-8")):
                raise ValueError("Display and operator passwords must be different.")
        self._display_password_salt = (
            _new_password_salt() if display_password is not None else self._password_salt
        )
        self._display_password_digest = (
            None if display_password is None else _derive_password_key(
                display_password.encode("utf-8"), self._display_password_salt,
            )
        )
        self._idle_seconds = idle_seconds
        self._absolute_seconds = absolute_seconds
        self._max_sessions = max_sessions
        self._max_requests_per_session = max_requests_per_session
        self._clock = clock
        self._token_factory = token_factory or _default_token_factory
        self._sessions: dict[bytes, _Session] = {}
        self._login_failures: dict[str, list[float]] = {}
        self._global_login_failures: list[float] = []
        self._next_global_password_derivation_at = 0.0
        self._password_derivation_peer: str | None = None
        self._password_executor: ThreadPoolExecutor | None = None
        self._watcher_id = 0
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(origin={self._origin!r}, "
            f"idle_seconds={self._idle_seconds!r}, "
            f"absolute_seconds={self._absolute_seconds!r}, "
            f"max_sessions={self._max_sessions!r}, "
            f"max_requests_per_session={self._max_requests_per_session!r})"
        )

    @property
    def origin(self) -> str:
        """Return the one accepted HTTPS browser origin."""

        return self._origin

    @property
    def absolute_seconds(self) -> int:
        """Return the cookie and absolute session lifetime."""

        return self._absolute_seconds

    def password_matches(self, candidate: str) -> bool:
        """Compare one submitted password without exposing either value."""

        if not isinstance(candidate, str):
            return False
        candidate_digest = _derive_password_key(
            candidate.encode("utf-8"),
            self._password_salt,
        )
        return compare_digest(self._password_digest, candidate_digest)

    @property
    def display_enabled(self) -> bool:
        return self._display_password_digest is not None

    def _display_password_matches(self, candidate: str) -> bool:
        derived = _derive_password_key(candidate.encode("utf-8"), self._display_password_salt)
        return self._display_password_digest is not None and compare_digest(
            self._display_password_digest, derived,
        )

    async def authenticate_password(
        self, candidate: str | None, peer: str, *, display_only: bool = False,
    ) -> bool:
        """Apply one bounded password attempt without blocking the event loop."""

        if not isinstance(peer, str) or not peer:
            peer = "unknown"
        if not isinstance(candidate, str):
            return False
        if not self._reserve_password_derivation(peer):
            return False

        try:
            submitted = (
                self._submit_password_derivation(candidate, display_only=True)
                if display_only else self._submit_password_derivation(candidate)
            )
        except BaseException:
            self.close()
            self._release_password_derivation(peer)
            raise
        try:
            verification = asyncio.wrap_future(submitted)
        except BaseException:
            submitted.add_done_callback(lambda _completed: self._release_password_derivation(peer))
            raise
        cancellation: asyncio.CancelledError | None = None
        try:
            while not verification.done():
                try:
                    await asyncio.shield(verification)
                except asyncio.CancelledError as error:
                    cancellation = cancellation or error
            candidate_matches = verification.result()
        except BaseException:
            self._release_password_derivation(peer)
            raise
        authenticated = self._complete_password_derivation(
            peer,
            candidate_matches=candidate_matches,
        )
        if cancellation is not None:
            raise cancellation
        return authenticated

    def _reserve_password_derivation(self, peer: str) -> bool:
        now = self._clock()
        with self._lock:
            failures = self._prune_login_failures_locked(peer, now)
            if (
                len(failures) >= WEB_DASHBOARD_LOGIN_FAILURE_LIMIT
                or self._password_derivation_peer is not None
            ):
                return False
            if len(self._global_login_failures) >= WEB_DASHBOARD_GLOBAL_LOGIN_FAILURE_LIMIT:
                if now < self._next_global_password_derivation_at:
                    return False
                self._next_global_password_derivation_at = (
                    now + WEB_DASHBOARD_GLOBAL_LOGIN_RECOVERY_SECONDS
                )
            self._password_derivation_peer = peer
            return True

    def _submit_password_derivation(
        self, candidate: str, *, display_only: bool = False,
    ) -> Future[bool]:
        with self._lock:
            if self._password_executor is None:
                self._password_executor = ThreadPoolExecutor(
                    max_workers=1,
                    thread_name_prefix="sdsctl-web-password",
                )
            verifier = self._display_password_matches if display_only else self.password_matches
            return self._password_executor.submit(verifier, candidate)

    def _complete_password_derivation(
        self,
        peer: str,
        *,
        candidate_matches: bool,
    ) -> bool:
        now = self._clock()
        with self._lock:
            self._password_derivation_peer = None
            failures = self._prune_login_failures_locked(peer, now)
            if candidate_matches:
                self._login_failures.pop(peer, None)
                self._global_login_failures.clear()
                self._next_global_password_derivation_at = 0.0
                return True
            if len(failures) >= WEB_DASHBOARD_LOGIN_FAILURE_LIMIT:
                return False
            failures.append(now)
            self._login_failures[peer] = failures
            global_was_below_limit = (
                len(self._global_login_failures) < WEB_DASHBOARD_GLOBAL_LOGIN_FAILURE_LIMIT
            )
            self._global_login_failures.append(now)
            if len(self._global_login_failures) > WEB_DASHBOARD_GLOBAL_LOGIN_FAILURE_LIMIT:
                del self._global_login_failures[
                    : len(self._global_login_failures) - WEB_DASHBOARD_GLOBAL_LOGIN_FAILURE_LIMIT
                ]
            if (
                global_was_below_limit
                and len(self._global_login_failures) == WEB_DASHBOARD_GLOBAL_LOGIN_FAILURE_LIMIT
            ):
                self._next_global_password_derivation_at = (
                    now + WEB_DASHBOARD_GLOBAL_LOGIN_RECOVERY_SECONDS
                )
            while len(self._login_failures) > WEB_DASHBOARD_MAX_LOGIN_PEERS:
                oldest_peer = min(
                    self._login_failures,
                    key=lambda item: self._login_failures[item][-1],
                )
                del self._login_failures[oldest_peer]
            return False

    def _release_password_derivation(self, peer: str) -> None:
        with self._lock:
            if self._password_derivation_peer == peer:
                self._password_derivation_peer = None

    def close(self) -> None:
        """Stop accepting password work and let any active derivation finish."""

        with self._lock:
            executor = self._password_executor
            self._password_executor = None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)

    def _prune_login_failures_locked(self, peer: str, now: float) -> list[float]:
        cutoff = now - WEB_DASHBOARD_LOGIN_FAILURE_WINDOW_SECONDS
        failures = [
            attempted_at
            for attempted_at in self._login_failures.get(peer, [])
            if attempted_at > cutoff
        ]
        if failures:
            self._login_failures[peer] = failures
        else:
            self._login_failures.pop(peer, None)
        self._global_login_failures = [
            attempted_at for attempted_at in self._global_login_failures if attempted_at > cutoff
        ]
        if len(self._global_login_failures) < WEB_DASHBOARD_GLOBAL_LOGIN_FAILURE_LIMIT:
            self._next_global_password_derivation_at = 0.0
        return failures

    def issue_session(self, *, display_only: bool = False) -> str:
        """Create one opaque session and retain only its SHA-256 digest."""

        if type(display_only) is not bool or (display_only and not self.display_enabled):
            raise ValueError("Display sessions require a configured display password.")
        token = self._token_factory()
        if (
            not isinstance(token, str)
            or len(token) < WEB_DASHBOARD_MINIMUM_SESSION_TOKEN_CHARACTERS
            or len(token) > WEB_DASHBOARD_MAXIMUM_SESSION_TOKEN_CHARACTERS
            or not set(token) <= _SESSION_TOKEN_CHARACTERS
        ):
            raise ValueError("Web dashboard session token factory returned an invalid token.")
        digest = _session_digest(token)
        now = self._clock()
        session = _Session(
            absolute_expires_at=now + self._absolute_seconds,
            idle_expires_at=now + self._idle_seconds,
            display_only=display_only,
        )
        with self._lock:
            self._prune_locked(now)
            if digest in self._sessions:
                raise ValueError("Web dashboard session token factory returned a duplicate token.")
            while len(self._sessions) >= self._max_sessions:
                oldest = min(
                    self._sessions,
                    key=lambda item: self._sessions[item].absolute_expires_at,
                )
                self._remove_session_locked(oldest)
            self._sessions[digest] = session
        return token

    def authorize_session(self, token: str | None) -> float | None:
        """Refresh one valid session and return seconds until its next deadline."""

        if not _valid_session_token(token):
            return None
        assert token is not None
        digest = _session_digest(token)
        now = self._clock()
        with self._lock:
            session = self._refresh_session_locked(digest, now)
            if session is None:
                return None
            return (
                min(
                    session.absolute_expires_at,
                    session.idle_expires_at,
                )
                - now
            )

    def acquire_session(self, token: str | None) -> _SessionLease | None:
        """Authorize one request and register it for revocation or eviction."""

        if not _valid_session_token(token):
            return None
        assert token is not None
        digest = _session_digest(token)
        now = self._clock()
        loop = asyncio.get_running_loop()
        revoked = asyncio.Event()
        with self._lock:
            session = self._refresh_session_locked(digest, now)
            if session is None:
                return None
            if len(session.watchers) >= self._max_requests_per_session:
                return None
            self._watcher_id += 1
            watcher_id = self._watcher_id
            session.watchers[watcher_id] = _SessionWatcher(loop, revoked)
            remaining_seconds = session.absolute_expires_at - now
            display_only = session.display_only
        return _SessionLease(
            authentication=self,
            digest=digest,
            watcher_id=watcher_id,
            revoked=revoked,
            remaining_seconds=remaining_seconds,
            display_only=display_only,
        )

    def revoke_session(self, token: str | None) -> None:
        """Revoke one opaque session without disclosing whether it existed."""

        if not _valid_session_token(token):
            return
        assert token is not None
        with self._lock:
            self._remove_session_locked(_session_digest(token))

    def _release_session(self, digest: bytes, watcher_id: int) -> None:
        with self._lock:
            session = self._sessions.get(digest)
            if session is not None:
                session.watchers.pop(watcher_id, None)
                session.idle_expires_at = min(
                    session.absolute_expires_at,
                    self._clock() + self._idle_seconds,
                )

    def _refresh_session_locked(self, digest: bytes, now: float) -> _Session | None:
        self._prune_locked(now)
        session = self._sessions.get(digest)
        if session is None:
            return None
        session.idle_expires_at = min(
            session.absolute_expires_at,
            now + self._idle_seconds,
        )
        return session

    def _prune_locked(self, now: float) -> None:
        expired = [
            digest
            for digest, session in self._sessions.items()
            if session.absolute_expires_at <= now
            or (not session.watchers and session.idle_expires_at <= now)
        ]
        for digest in expired:
            self._remove_session_locked(digest)

    def _remove_session_locked(self, digest: bytes) -> None:
        session = self._sessions.pop(digest, None)
        if session is None:
            return
        for watcher in session.watchers.values():
            with suppress(RuntimeError):
                watcher.loop.call_soon_threadsafe(watcher.revoked.set)


class WebDashboardAuthenticationMiddleware:
    """Authenticate every dashboard HTTP route and enforce one HTTPS origin."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        authentication: WebDashboardAuthentication,
        display_theme_paths: frozenset[str] = frozenset(),
    ) -> None:
        if not isinstance(authentication, WebDashboardAuthentication):
            raise TypeError("Web dashboard authentication middleware requires a valid policy.")
        self._app = app
        self._authentication = authentication
        self._display_paths = _DISPLAY_READ_PATHS | display_theme_paths

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] == "lifespan":
            try:
                await self._app(scope, receive, send)
            finally:
                self._authentication.close()
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        if scope["type"] != "http":
            return

        headers = Headers(scope=scope)
        if _request_origin(scope, headers) != self._authentication.origin:
            await _json_error(
                WEB_DASHBOARD_ORIGIN_REQUIRED_DETAIL,
                status_code=400,
            )(scope, receive, send)
            return

        method = scope["method"].upper()
        path = scope["path"]
        display_login = path == WEB_DASHBOARD_DISPLAY_LOGIN_PATH
        if display_login and not self._authentication.display_enabled:
            await _json_error("Display login is not configured.", status_code=404)(
                scope, receive, send,
            )
            return
        if path == WEB_DASHBOARD_LOGIN_PATH or display_login:
            if method == "GET":
                await _login_response(display_only=display_login)(scope, receive, send)
                return
            if method == "POST":
                await self._login(scope, receive, send, headers, display_only=display_login)
                return

        if not _fetch_site_allowed(headers):
            await _json_error(
                WEB_DASHBOARD_ORIGIN_REQUIRED_DETAIL,
                status_code=403,
            )(scope, receive, send)
            return
        if method not in {"GET", "HEAD", "OPTIONS"} and not _origin_matches(
            headers,
            self._authentication.origin,
        ):
            await _json_error(
                WEB_DASHBOARD_ORIGIN_REQUIRED_DETAIL,
                status_code=403,
            )(scope, receive, send)
            return

        token = _cookie_value(headers, WEB_DASHBOARD_AUTH_COOKIE)
        if path == WEB_DASHBOARD_LOGOUT_PATH and method == "POST":
            logout_lease = self._authentication.acquire_session(token)
            if logout_lease is None:
                await _json_error(
                    WEB_DASHBOARD_AUTHENTICATION_REQUIRED_DETAIL,
                    status_code=401,
                )(scope, receive, send)
                return
            logout_path = (
                WEB_DASHBOARD_DISPLAY_LOGIN_PATH if logout_lease.display_only
                else WEB_DASHBOARD_LOGIN_PATH
            )
            logout_lease.release()
            self._authentication.revoke_session(token)
            logout_response = RedirectResponse(
                logout_path,
                status_code=303,
            )
            logout_response.delete_cookie(
                WEB_DASHBOARD_AUTH_COOKIE,
                path="/",
                secure=True,
                httponly=True,
                samesite="strict",
            )
            _secure_response(logout_response)
            await logout_response(scope, receive, send)
            return

        lease = self._authentication.acquire_session(token)
        if lease is None:
            unauthorized_response = (
                RedirectResponse(WEB_DASHBOARD_LOGIN_PATH, status_code=302)
                if method == "GET" and path == "/"
                else _json_error(
                    WEB_DASHBOARD_AUTHENTICATION_REQUIRED_DETAIL,
                    status_code=401,
                )
            )
            _secure_response(unauthorized_response)
            await unauthorized_response(scope, receive, send)
            return

        if lease.display_only and (method != "GET" or path not in self._display_paths):
            lease.release()
            await _json_error(
                "Display-only access does not permit this operation.", status_code=403,
            )(
                scope, receive, send,
            )
            return
        scope.setdefault("state", {})["sdsctl_display_only"] = lease.display_only
        if path == WEB_DASHBOARD_SESSION_PATH and method == "GET":
            response = JSONResponse({
                "display_only": lease.display_only,
                "remaining_seconds": lease.remaining_seconds,
            })
            _secure_response(response)
            try:
                await response(scope, receive, send)
            finally:
                lease.release()
            return

        response_started = False
        response_complete = False

        async def tracked_send(message: Message) -> None:
            nonlocal response_complete, response_started
            if message["type"] == "http.response.start":
                response_started = True
                _secure_message(message)
            elif message["type"] == "http.response.body" and not message.get("more_body", False):
                response_complete = True
            await send(message)

        async def run_app() -> None:
            await self._app(scope, receive, tracked_send)

        app_task = asyncio.create_task(run_app())
        revocation_task = asyncio.create_task(lease.revoked.wait())
        try:
            done, _ = await asyncio.wait(
                {app_task, revocation_task},
                timeout=lease.remaining_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if app_task in done:
                await app_task
                return
            app_task.cancel()
            await asyncio.gather(app_task, return_exceptions=True)
            if response_started and not response_complete:
                await send(
                    {
                        "type": "http.response.body",
                        "body": b"",
                        "more_body": False,
                    }
                )
            elif not response_started:
                await _json_error(
                    WEB_DASHBOARD_AUTHENTICATION_REQUIRED_DETAIL,
                    status_code=401,
                )(scope, receive, send)
        finally:
            lease.release()
            for task in (app_task, revocation_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(
                app_task,
                revocation_task,
                return_exceptions=True,
            )

    async def _login(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        headers: Headers,
        *,
        display_only: bool = False,
    ) -> None:
        if not _origin_matches(headers, self._authentication.origin):
            await _json_error(
                WEB_DASHBOARD_ORIGIN_REQUIRED_DETAIL,
                status_code=403,
            )(scope, receive, send)
            return
        content_type = headers.get("content-type", "").partition(";")[0].strip().lower()
        if content_type != "application/x-www-form-urlencoded":
            await _json_error(
                WEB_DASHBOARD_AUTHENTICATION_FAILED_DETAIL,
                status_code=400,
            )(scope, receive, send)
            return

        try:
            async with asyncio.timeout(WEB_DASHBOARD_LOGIN_BODY_TIMEOUT_SECONDS):
                body = await _read_body(receive)
        except TimeoutError:
            body = None
        candidate = _submitted_password(body)
        client = scope.get("client")
        peer = "unknown" if client is None else str(client[0])
        if not await self._authentication.authenticate_password(
            candidate, peer, display_only=display_only,
        ):
            await _login_response(failed=True, status_code=401, display_only=display_only)(
                scope, receive, send,
            )
            return

        previous_token = _cookie_value(headers, WEB_DASHBOARD_AUTH_COOKIE)
        self._authentication.revoke_session(previous_token)
        token = self._authentication.issue_session(display_only=display_only)
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            WEB_DASHBOARD_AUTH_COOKIE,
            token,
            max_age=self._authentication.absolute_seconds,
            path="/",
            secure=True,
            httponly=True,
            samesite="strict",
        )
        _secure_response(response)
        await response(scope, receive, send)


def _normalize_origin(origin: str) -> str:
    if not isinstance(origin, str):
        raise TypeError("Web dashboard origin must be a string.")
    if not origin or origin.strip() != origin:
        raise ValueError("Web dashboard origin must not be empty or padded.")
    try:
        parsed = urlsplit(origin)
        _ = parsed.port
    except ValueError as error:
        raise ValueError("Web dashboard origin is invalid.") from error
    port = parsed.port
    hostname = parsed.hostname
    if (
        parsed.scheme.lower() != "https"
        or hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or port == 0
        or not _valid_origin_hostname(hostname)
    ):
        raise ValueError(
            "Web dashboard origin must be one HTTPS origin without credentials, "
            "a path, a query, or a fragment."
        )
    normalized_hostname = hostname.lower()
    authority = f"[{normalized_hostname}]" if ":" in normalized_hostname else normalized_hostname
    if port not in {None, 443}:
        authority = f"{authority}:{port}"
    return f"https://{authority}"


def _valid_origin_hostname(hostname: str) -> bool:
    if "%" in hostname:
        return False
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        if len(hostname) > 253 or not hostname.isascii():
            return False
        labels = hostname.split(".")
        return all(
            label
            and len(label) <= 63
            and label[0].isalnum()
            and label[-1].isalnum()
            and all(character.isalnum() or character == "-" for character in label)
            for label in labels
        )
    return True


def _request_origin(scope: Scope, headers: Headers) -> str | None:
    hosts = headers.getlist("host")
    if len(hosts) != 1:
        return None
    try:
        return _normalize_origin(f"{scope.get('scheme', '').lower()}://{hosts[0].lower()}")
    except (TypeError, ValueError):
        return None


def _origin_matches(headers: Headers, expected: str) -> bool:
    supplied = headers.getlist("origin")
    return len(supplied) == 1 and compare_digest(supplied[0], expected)


def _fetch_site_allowed(headers: Headers) -> bool:
    values = headers.getlist("sec-fetch-site")
    return not values or (len(values) == 1 and values[0] in {"none", "same-origin"})


def _cookie_value(headers: Headers, name: str) -> str | None:
    matches: list[str] = []
    for header in headers.getlist("cookie"):
        for pair in header.split(";"):
            key, separator, value = pair.strip().partition("=")
            if separator and key == name:
                matches.append(value)
    return matches[0] if len(matches) == 1 else None


async def _read_body(receive: Receive) -> bytes | None:
    body = bytearray()
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            return None
        if message["type"] != "http.request":
            continue
        body.extend(message.get("body", b""))
        if len(body) > WEB_DASHBOARD_MAX_LOGIN_BODY_BYTES:
            return None
        if not message.get("more_body", False):
            return bytes(body)


def _submitted_password(body: bytes | None) -> str | None:
    if body is None:
        return None
    try:
        values = parse_qs(
            body.decode("utf-8"),
            keep_blank_values=True,
            max_num_fields=4,
        )
    except (UnicodeDecodeError, ValueError):
        return None
    passwords = values.get("password")
    if passwords is None or len(passwords) != 1:
        return None
    return passwords[0]


def _session_digest(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


def _new_password_salt() -> bytes:
    return secrets.token_bytes(_PASSWORD_SALT_BYTES)


def _derive_password_key(password: bytes, salt: bytes) -> bytes:
    try:
        return hashlib.scrypt(
            password,
            salt=salt,
            n=_PASSWORD_SCRYPT_N,
            r=_PASSWORD_SCRYPT_R,
            p=_PASSWORD_SCRYPT_P,
            maxmem=_PASSWORD_SCRYPT_MAX_MEMORY_BYTES,
            dklen=_PASSWORD_DERIVED_KEY_BYTES,
        )
    except (AttributeError, MemoryError, ValueError) as error:
        raise ConfigurationError(
            "Secure web dashboard password derivation is unavailable."
        ) from error


def _valid_session_token(token: str | None) -> bool:
    return (
        isinstance(token, str)
        and len(token) >= WEB_DASHBOARD_MINIMUM_SESSION_TOKEN_CHARACTERS
        and len(token) <= WEB_DASHBOARD_MAXIMUM_SESSION_TOKEN_CHARACTERS
        and set(token) <= _SESSION_TOKEN_CHARACTERS
    )


def _default_token_factory() -> str:
    return secrets.token_urlsafe(32)


_LOGIN_SCRIPT = """\
(() => {
  const password = document.getElementById('password');
  const toggle = document.getElementById('password-visibility');
  const hide = () => {
    password.type = 'password';
    toggle.textContent = 'Show password';
    toggle.setAttribute('aria-pressed', 'false');
  };
  toggle.hidden = false;
  toggle.addEventListener('click', () => {
    if (password.type === 'password') {
      password.type = 'text';
      toggle.textContent = 'Hide password';
      toggle.setAttribute('aria-pressed', 'true');
    } else {
      hide();
    }
  });
  password.form.addEventListener('submit', hide);
  window.addEventListener('pagehide', hide);
})();
"""
_LOGIN_STYLE = """\
:root { color-scheme: light dark; font-family: system-ui, sans-serif; }
* { box-sizing: border-box; }
body { margin: 0; padding: 1rem; }
main { max-width: 32rem; margin: 2rem auto; }
form { display: grid; gap: .75rem; }
input, button { font: inherit; min-height: 2.75rem; padding: .6rem .75rem; }
input { width: 100%; min-width: 0; }
button { cursor: pointer; }
[hidden] { display: none !important; }
:focus-visible { outline: 3px solid Highlight; outline-offset: 3px; }
"""


def _login_response(
    *, failed: bool = False, status_code: int = 200, display_only: bool = False,
) -> HTMLResponse:
    failure = (
        f'<p role="alert">{escape(WEB_DASHBOARD_AUTHENTICATION_FAILED_DETAIL)}</p>'
        if failed
        else ""
    )
    login_path = WEB_DASHBOARD_DISPLAY_LOGIN_PATH if display_only else WEB_DASHBOARD_LOGIN_PATH
    description = (
        "Display-only sign in. Scanner controls, audio and recordings are not available."
        if display_only else "Operator sign in."
    )
    response = HTMLResponse(
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>Sign in | sdsctl</title><style>{_LOGIN_STYLE}</style>"
        "</head><body><main><h1>sdsctl</h1>"
        f'<p>{description}</p>{failure}<form method="post" action="{login_path}">'
        '<label for="password">Password</label>'
        '<input id="password" name="password" type="password" '
        'autocomplete="current-password" autocapitalize="off" spellcheck="false" '
        'required autofocus>'
        '<button id="password-visibility" type="button" aria-controls="password" '
        'aria-pressed="false" hidden>Show password</button>'
        '<button type="submit">Sign in</button></form></main>'
        f'<script>{_LOGIN_SCRIPT}</script></body></html>',
        status_code=status_code,
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": (
                "default-src 'none'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'; "
                "script-src 'sha256-"
                + base64.b64encode(hashlib.sha256(_LOGIN_SCRIPT.encode()).digest()).decode()
                + "'; style-src 'sha256-"
                + base64.b64encode(hashlib.sha256(_LOGIN_STYLE.encode()).digest()).decode()
                + "'"
            ),
            # A basic same-origin form POST derives its Origin header from the
            # referrer policy.  ``no-referrer`` can serialize that Origin as
            # ``null``, which would make the exact-origin CSRF check reject the
            # dashboard's own login form before password authentication.
            "Referrer-Policy": "same-origin",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
        },
    )
    _secure_response(response)
    if display_only:
        response.headers["X-SDSCTL-Display-Login"] = "1"
    return response


def _json_error(detail: str, *, status_code: int) -> JSONResponse:
    response = JSONResponse(
        {"detail": detail},
        status_code=status_code,
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
    _secure_response(response)
    return response


def _secure_message(message: Message) -> None:
    headers = MutableHeaders(raw=message["headers"])
    if "content-length" in headers:
        del headers["content-length"]
    headers["Cache-Control"] = "no-store"
    headers["Strict-Transport-Security"] = "max-age=31536000"
    headers["X-Content-Type-Options"] = "nosniff"


def _secure_response(response: HTMLResponse | JSONResponse | RedirectResponse) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Strict-Transport-Security"] = "max-age=31536000"
    response.headers["X-Content-Type-Options"] = "nosniff"


__all__ = [
    "WEB_DASHBOARD_AUTH_COOKIE",
    "WEB_DASHBOARD_AUTHENTICATION_FAILED_DETAIL",
    "WEB_DASHBOARD_AUTHENTICATION_REQUIRED_DETAIL",
    "WEB_DASHBOARD_DEFAULT_MAX_REQUESTS_PER_SESSION",
    "WEB_DASHBOARD_DEFAULT_MAX_SESSIONS",
    "WEB_DASHBOARD_DEFAULT_SESSION_ABSOLUTE_SECONDS",
    "WEB_DASHBOARD_DEFAULT_SESSION_IDLE_SECONDS",
    "WEB_DASHBOARD_LOGIN_PATH",
    "WEB_DASHBOARD_LOGIN_BODY_TIMEOUT_SECONDS",
    "WEB_DASHBOARD_LOGIN_FAILURE_LIMIT",
    "WEB_DASHBOARD_LOGIN_FAILURE_WINDOW_SECONDS",
    "WEB_DASHBOARD_GLOBAL_LOGIN_FAILURE_LIMIT",
    "WEB_DASHBOARD_GLOBAL_LOGIN_RECOVERY_SECONDS",
    "WEB_DASHBOARD_LOGOUT_PATH",
    "WEB_DASHBOARD_MAX_LOGIN_BODY_BYTES",
    "WEB_DASHBOARD_MAXIMUM_SESSION_TOKEN_CHARACTERS",
    "WEB_DASHBOARD_MINIMUM_PASSWORD_CHARACTERS",
    "WEB_DASHBOARD_MINIMUM_SESSION_TOKEN_CHARACTERS",
    "WEB_DASHBOARD_ORIGIN_REQUIRED_DETAIL",
    "WebDashboardAuthentication",
    "WebDashboardAuthenticationMiddleware",
]
