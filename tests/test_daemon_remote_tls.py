from __future__ import annotations

import base64
import os
import shutil
import socket
import ssl
import subprocess
import threading
from pathlib import Path
from queue import Queue
from time import monotonic, sleep
from typing import cast

import pytest

import sds200.daemon_remote_tls as remote_tls
from sds200 import (
    DAEMON_REMOTE_AUTH_MAX_FRAME_BYTES,
    DAEMON_REMOTE_TLS_DEFAULT_HANDSHAKE_TIMEOUT,
    DAEMON_REMOTE_TLS_VERSION,
    DaemonRemoteAuthenticatedIdentity,
    DaemonRemoteAuthenticatedPeer,
    DaemonRemoteAuthenticationError,
    DaemonRemoteAuthenticationErrorReason,
    DaemonRemoteAuthenticationResult,
    DaemonRemoteAuthorizationScope,
    DaemonRemoteChallenge,
    DaemonRemoteClientIdentity,
    DaemonRemoteCredential,
    DaemonRemoteListenerConfiguration,
    DaemonRemoteServerTlsAdmission,
    DaemonRemoteTlsError,
    DaemonRemoteTlsErrorReason,
    build_daemon_remote_authentication_request,
)

CLIENT_ID = "pi-kiosk"
CREDENTIAL_KEY = bytes(range(32))


class ScriptedTlsStream:
    def __init__(
        self,
        *,
        tls_version: str = DAEMON_REMOTE_TLS_VERSION,
        handshake_error: OSError | None = None,
        send_error: OSError | None = None,
        received: bytes = b"",
    ) -> None:
        self.tls_version = tls_version
        self.handshake_error = handshake_error
        self.send_error = send_error
        self.received = bytearray(received)
        self.timeout: float | None = None
        self.closed = False

    def settimeout(self, value: float) -> None:
        self.timeout = value

    def do_handshake(self) -> None:
        if self.handshake_error is not None:
            raise self.handshake_error

    def version(self) -> str:
        return self.tls_version

    def sendall(self, data: bytes) -> None:
        del data
        if self.send_error is not None:
            raise self.send_error

    def recv(self, size: int) -> bytes:
        if not self.received:
            return b""
        value = bytes(self.received[:size])
        del self.received[:size]
        return value

    def shutdown(self, how: int) -> None:
        del how

    def close(self) -> None:
        self.closed = True


def _encoded(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _write_credential(path: Path, key: bytes = CREDENTIAL_KEY) -> None:
    path.write_text(_encoded(key) + "\n", encoding="ascii")
    path.chmod(0o600)


def _generate_tls_identity(tmp_path: Path) -> tuple[Path, Path]:
    executable = shutil.which("openssl")
    if executable is None:
        pytest.skip("OpenSSL command is required for direct-TLS integration coverage.")
    certificate = tmp_path / "server.crt"
    private_key = tmp_path / "server.key"
    subprocess.run(
        [
            executable,
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-sha256",
            "-nodes",
            "-keyout",
            os.fspath(private_key),
            "-out",
            os.fspath(certificate),
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=DNS:localhost",
            "-days",
            "1",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    private_key.chmod(0o600)
    return certificate, private_key


def _configuration(
    tmp_path: Path,
    *,
    key: bytes = CREDENTIAL_KEY,
    scopes: tuple[DaemonRemoteAuthorizationScope, ...] = (
        DaemonRemoteAuthorizationScope.OBSERVE,
    ),
) -> tuple[DaemonRemoteListenerConfiguration, Path]:
    certificate, private_key = _generate_tls_identity(tmp_path)
    credential = tmp_path / "client.secret"
    _write_credential(credential, key)
    return (
        DaemonRemoteListenerConfiguration(
            enabled=True,
            bind_address="192.168.20.10",
            port=50443,
            certificate_file=certificate,
            private_key_file=private_key,
            clients=(
                DaemonRemoteClientIdentity(
                    CLIENT_ID,
                    credential,
                    scopes=scopes,
                ),
            ),
        ),
        certificate,
    )


def _client_context(certificate: Path) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.maximum_version = ssl.TLSVersion.TLSv1_3
    context.load_verify_locations(cafile=certificate)
    return context


def _receive_line(stream: ssl.SSLSocket) -> bytes:
    frame = bytearray()
    while not frame.endswith(b"\n"):
        chunk = stream.recv(1)
        if not chunk:
            raise AssertionError("TLS peer closed before the expected frame.")
        frame.extend(chunk)
    return bytes(frame)


def _start_admission(
    admission: DaemonRemoteServerTlsAdmission,
    server_stream: socket.socket,
) -> tuple[threading.Thread, Queue[object]]:
    outcome: Queue[object] = Queue()

    def admit() -> None:
        try:
            outcome.put(admission.admit(server_stream))
        except BaseException as error:
            outcome.put(error)

    thread = threading.Thread(target=admit, daemon=True)
    thread.start()
    return thread, outcome


def _open_client(
    context: ssl.SSLContext,
    stream: socket.socket,
) -> ssl.SSLSocket:
    secured = context.wrap_socket(
        stream,
        server_hostname="localhost",
        do_handshake_on_connect=False,
    )
    secured.settimeout(2.0)
    secured.do_handshake()
    return secured


def test_direct_tls_admission_authenticates_and_returns_authoritative_scopes(
    tmp_path: Path,
) -> None:
    scopes = (
        DaemonRemoteAuthorizationScope.OBSERVE,
        DaemonRemoteAuthorizationScope.CONTROL,
    )
    configuration, certificate = _configuration(tmp_path, scopes=scopes)
    admission = DaemonRemoteServerTlsAdmission.from_configuration(configuration)
    client_raw, server_raw = socket.socketpair()
    thread, outcome = _start_admission(admission, server_raw)

    client = _open_client(_client_context(certificate), client_raw)
    try:
        challenge = DaemonRemoteChallenge.from_json_line(_receive_line(client))
        request = build_daemon_remote_authentication_request(
            challenge,
            client_id=CLIENT_ID,
            credential=DaemonRemoteCredential(CREDENTIAL_KEY),
        )
        client.sendall(request.to_json_line())
        result = DaemonRemoteAuthenticationResult.from_json_line(_receive_line(client))
        assert result.ok is True
        assert result.scopes == (
            DaemonRemoteAuthorizationScope.CONTROL,
            DaemonRemoteAuthorizationScope.OBSERVE,
        )
        assert client.version() == DAEMON_REMOTE_TLS_VERSION
    finally:
        client.close()

    thread.join(2.0)
    assert thread.is_alive() is False
    accepted = outcome.get_nowait()
    assert isinstance(accepted, tuple)
    server, peer = accepted
    assert isinstance(server, ssl.SSLSocket)
    assert isinstance(peer, DaemonRemoteAuthenticatedPeer)
    assert peer.client_id == CLIENT_ID
    assert peer.scopes == result.scopes
    assert peer.allows(DaemonRemoteAuthorizationScope.CONTROL) is True
    assert peer.tls_version == DAEMON_REMOTE_TLS_VERSION
    server.close()
    assert admission.context.minimum_version is ssl.TLSVersion.TLSv1_3
    assert admission.context.maximum_version is ssl.TLSVersion.TLSv1_3
    assert admission.registry.active_credentials == 1
    assert admission.handshake_timeout == DAEMON_REMOTE_TLS_DEFAULT_HANDSHAKE_TIMEOUT


@pytest.mark.parametrize(
    ("request_kind", "expected_reason"),
    [
        ("wrong-key", DaemonRemoteAuthenticationErrorReason.AUTHENTICATION_FAILED),
        ("malformed", DaemonRemoteAuthenticationErrorReason.INVALID_FRAME),
        ("oversized", DaemonRemoteAuthenticationErrorReason.INVALID_FRAME),
    ],
)
def test_authenticated_tls_rejects_bad_peers_with_redacted_result(
    tmp_path: Path,
    request_kind: str,
    expected_reason: DaemonRemoteAuthenticationErrorReason,
) -> None:
    configuration, certificate = _configuration(tmp_path)
    admission = DaemonRemoteServerTlsAdmission.from_configuration(configuration)
    client_raw, server_raw = socket.socketpair()
    thread, outcome = _start_admission(admission, server_raw)

    client = _open_client(_client_context(certificate), client_raw)
    challenge = DaemonRemoteChallenge.from_json_line(_receive_line(client))
    if request_kind == "wrong-key":
        request = build_daemon_remote_authentication_request(
            challenge,
            client_id=CLIENT_ID,
            credential=DaemonRemoteCredential(b"x" * 32),
        ).to_json_line()
    elif request_kind == "malformed":
        request = b"not-json\n"
    else:
        request = b"x" * (DAEMON_REMOTE_AUTH_MAX_FRAME_BYTES + 1) + b"\n"
    client.sendall(request)
    result = DaemonRemoteAuthenticationResult.from_json_line(_receive_line(client))
    client.close()

    assert result.ok is False
    assert result.error is expected_reason
    thread.join(2.0)
    failure = outcome.get_nowait()
    assert isinstance(failure, DaemonRemoteTlsError)
    assert failure.reason is DaemonRemoteTlsErrorReason.AUTHENTICATION_FAILED
    assert CLIENT_ID not in str(failure)
    assert _encoded(CREDENTIAL_KEY) not in str(failure)


def test_plaintext_and_silent_peers_never_reach_authentication(
    tmp_path: Path,
) -> None:
    configuration, _ = _configuration(tmp_path)
    admission = DaemonRemoteServerTlsAdmission.from_configuration(
        configuration,
        handshake_timeout=0.05,
    )

    for payload in (b"GET / HTTP/1.1\r\n\r\n", b""):
        client, server = socket.socketpair()
        thread, outcome = _start_admission(admission, server)
        if payload:
            client.sendall(payload)
        thread.join(1.0)
        client.close()

        assert thread.is_alive() is False
        failure = outcome.get_nowait()
        assert isinstance(failure, DaemonRemoteTlsError)
        assert failure.reason is DaemonRemoteTlsErrorReason.TLS_HANDSHAKE_FAILED
        assert "GET" not in str(failure)


def test_tls_admission_uses_one_deadline_for_handshake_and_authentication(
    tmp_path: Path,
) -> None:
    configuration, certificate = _configuration(tmp_path)
    admission = DaemonRemoteServerTlsAdmission.from_configuration(
        configuration,
        handshake_timeout=0.12,
    )
    client_raw, server_raw = socket.socketpair()
    started = monotonic()
    thread, outcome = _start_admission(admission, server_raw)
    client = _open_client(_client_context(certificate), client_raw)
    _receive_line(client)

    for chunk in (b"{", b'"', b"c", b"l", b"i", b"e"):
        sleep(0.04)
        try:
            client.sendall(chunk)
        except OSError:
            break
    client.close()
    thread.join(0.5)

    assert thread.is_alive() is False
    assert monotonic() - started < 0.4
    failure = outcome.get_nowait()
    assert isinstance(failure, DaemonRemoteTlsError)
    assert failure.reason is DaemonRemoteTlsErrorReason.AUTHENTICATION_FAILED


def test_tls_configuration_errors_are_uniform_and_redacted(tmp_path: Path) -> None:
    credential = tmp_path / "private-client-name.secret"
    _write_credential(credential)
    certificate = tmp_path / "private-server-name.crt"
    certificate.write_text("not a certificate", encoding="ascii")
    private_key = tmp_path / "private-server-name.key"
    private_key.write_text("not a private key", encoding="ascii")
    private_key.chmod(0o600)
    configuration = DaemonRemoteListenerConfiguration(
        enabled=True,
        bind_address="192.168.20.10",
        port=50443,
        certificate_file=certificate,
        private_key_file=private_key,
        clients=(DaemonRemoteClientIdentity(CLIENT_ID, credential),),
    )

    with pytest.raises(DaemonRemoteTlsError) as exc_info:
        DaemonRemoteServerTlsAdmission.from_configuration(configuration)

    assert exc_info.value.reason is DaemonRemoteTlsErrorReason.CONFIGURATION_FAILED
    assert str(exc_info.value) == "Remote daemon TLS configuration is unavailable."
    assert "private-server-name" not in str(exc_info.value)
    assert "private-client-name" not in str(exc_info.value)


def test_tls_loader_detects_identity_mutation_after_context_loading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configuration, _ = _configuration(tmp_path)
    original = remote_tls._tls_file_snapshot
    calls = 0

    def changing_snapshot(path: Path, *, private: bool) -> tuple[int, int, int, int, int, int]:
        nonlocal calls
        calls += 1
        observed = original(path, private=private)
        if calls == 3:
            return (*observed[:-1], observed[-1] + 1)
        return observed

    monkeypatch.setattr(remote_tls, "_tls_file_snapshot", changing_snapshot)

    with pytest.raises(DaemonRemoteTlsError) as exc_info:
        DaemonRemoteServerTlsAdmission.from_configuration(configuration)
    assert exc_info.value.reason is DaemonRemoteTlsErrorReason.CONFIGURATION_FAILED


@pytest.mark.parametrize(
    ("scripted", "reason"),
    [
        (
            ScriptedTlsStream(tls_version="TLSv1.2"),
            DaemonRemoteTlsErrorReason.TLS_HANDSHAKE_FAILED,
        ),
        (
            ScriptedTlsStream(handshake_error=OSError("private handshake detail")),
            DaemonRemoteTlsErrorReason.TLS_HANDSHAKE_FAILED,
        ),
        (
            ScriptedTlsStream(send_error=OSError("private transport detail")),
            DaemonRemoteTlsErrorReason.TRANSPORT_FAILED,
        ),
    ],
)
def test_tls_admission_maps_internal_transport_failures_without_detail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scripted: ScriptedTlsStream,
    reason: DaemonRemoteTlsErrorReason,
) -> None:
    configuration, _ = _configuration(tmp_path)
    admission = DaemonRemoteServerTlsAdmission.from_configuration(configuration)
    monkeypatch.setattr(
        admission.context,
        "wrap_socket",
        lambda *args, **kwargs: cast(ssl.SSLSocket, scripted),
    )
    client, server = socket.socketpair()

    with pytest.raises(DaemonRemoteTlsError) as exc_info:
        admission.admit(server)
    client.close()

    assert exc_info.value.reason is reason
    assert "private" not in str(exc_info.value)
    assert scripted.closed is True


@pytest.mark.parametrize("payload", [b"", b"\n", b"bad\rframe\n"])
def test_authentication_frame_reader_rejects_eof_empty_and_carriage_return(
    payload: bytes,
) -> None:
    stream = ScriptedTlsStream(received=payload)

    with pytest.raises(DaemonRemoteAuthenticationError, match="authentication frame is invalid"):
        remote_tls._receive_authentication_frame(cast(ssl.SSLSocket, stream))


def test_tls_admission_and_peer_constructors_are_strict(tmp_path: Path) -> None:
    configuration, _ = _configuration(tmp_path)
    admission = DaemonRemoteServerTlsAdmission.from_configuration(configuration)
    identity = DaemonRemoteAuthenticatedIdentity(
        CLIENT_ID,
        (DaemonRemoteAuthorizationScope.OBSERVE,),
    )

    with pytest.raises(TypeError, match="SSLContext"):
        DaemonRemoteServerTlsAdmission(object(), admission.registry)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="credential registry"):
        DaemonRemoteServerTlsAdmission(admission.context, object())  # type: ignore[arg-type]
    for timeout in (True, 0, -1, float("inf")):
        with pytest.raises((TypeError, ValueError), match="handshake timeout"):
            DaemonRemoteServerTlsAdmission(
                admission.context,
                admission.registry,
                handshake_timeout=timeout,
            )
    with pytest.raises(TypeError, match="unwrapped socket"):
        admission.admit(object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="authenticated identity"):
        DaemonRemoteAuthenticatedPeer(object())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="version is unsupported"):
        DaemonRemoteAuthenticatedPeer(identity, tls_version="TLSv1.2")
    with pytest.raises(TypeError, match="TLS error reason"):
        DaemonRemoteTlsError("tls_handshake_failed")  # type: ignore[arg-type]


def test_tls_loader_requires_exact_enabled_configuration() -> None:
    with pytest.raises(TypeError, match="requires DaemonRemoteListenerConfiguration"):
        DaemonRemoteServerTlsAdmission.from_configuration(object())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="enabled configuration"):
        DaemonRemoteServerTlsAdmission.from_configuration(
            DaemonRemoteListenerConfiguration()
        )


def test_tls_file_snapshot_rejects_symlink_and_nonprivate_key(tmp_path: Path) -> None:
    target = tmp_path / "target.key"
    target.write_text("private", encoding="ascii")
    target.chmod(0o600)
    linked = tmp_path / "linked.key"
    linked.symlink_to(target)

    with pytest.raises(DaemonRemoteTlsError):
        remote_tls._tls_file_snapshot(linked, private=True)
    target.chmod(0o640)
    with pytest.raises(DaemonRemoteTlsError):
        remote_tls._tls_file_snapshot(target, private=True)


def test_tls_constants_are_explicit() -> None:
    assert DAEMON_REMOTE_TLS_VERSION == "TLSv1.3"
    assert DAEMON_REMOTE_TLS_DEFAULT_HANDSHAKE_TIMEOUT == 5.0


def test_expired_total_admission_deadline_is_rejected() -> None:
    with pytest.raises(TimeoutError, match="deadline expired"):
        remote_tls._remaining_admission_seconds(monotonic() - 1.0)
