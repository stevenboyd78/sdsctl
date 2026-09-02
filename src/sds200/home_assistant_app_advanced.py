"""Private lifecycle state for advanced Home Assistant App access."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import stat
import subprocess
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from ipaddress import ip_address
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Final, TypeAlias

from .daemon_remote import (
    DAEMON_REMOTE_CONFIG_VERSION,
    DAEMON_REMOTE_DEFAULT_PORT,
    DAEMON_REMOTE_PRIVATE_FILE_MODE,
    DaemonRemoteAuthorizationScope,
    load_daemon_remote_configuration,
    preflight_daemon_remote_configuration,
)
from .daemon_remote_auth import DAEMON_REMOTE_AUTH_CREDENTIAL_BYTES
from .exceptions import ConfigurationError, SDS200Error
from .home_assistant_app import (
    HOME_ASSISTANT_APP_NATIVE_DASHBOARD_PORT_KEY,
    HOME_ASSISTANT_APP_REMOTE_DAEMON_PORT_KEY,
    HomeAssistantAppAdvancedExposure,
    HomeAssistantAppOptions,
    HomeAssistantAppSupervisorInfo,
)

HOME_ASSISTANT_APP_ADVANCED_ACCESS_VERSION: Final = 1
HOME_ASSISTANT_APP_ADVANCED_ACCESS_DIRECTORY = Path("/data/advanced-access")
HOME_ASSISTANT_APP_ADVANCED_ACCESS_STATE_FILENAME = "state.json"
HOME_ASSISTANT_APP_ADVANCED_ACCESS_IDENTITIES_DIRECTORY = "identities"
HOME_ASSISTANT_APP_ADVANCED_ACCESS_CLIENTS_DIRECTORY = "clients"
HOME_ASSISTANT_APP_ADVANCED_ACCESS_DASHBOARD_PASSWORD_FILENAME = (
    "native-dashboard-password.secret"
)
HOME_ASSISTANT_APP_ADVANCED_ACCESS_RUNTIME_CONFIG_FILENAME = "daemon-remote.toml"
HOME_ASSISTANT_APP_ADVANCED_ACCESS_CONTEXT_FILENAME = (
    "home-assistant-advanced-context.json"
)
HOME_ASSISTANT_APP_ADVANCED_ACCESS_DIRECTORY_MODE = 0o700
HOME_ASSISTANT_APP_ADVANCED_ACCESS_PUBLIC_FILE_MODE = 0o644
HOME_ASSISTANT_APP_ADVANCED_ACCESS_MAX_STATE_BYTES = 65_536
HOME_ASSISTANT_APP_ADVANCED_ACCESS_MAX_CONTEXT_BYTES = 65_536
HOME_ASSISTANT_APP_ADVANCED_ACCESS_MAX_CLIENTS = 64
HOME_ASSISTANT_APP_ADVANCED_ACCESS_MAX_TLS_BYTES = 1_048_576
HOME_ASSISTANT_APP_ADVANCED_ACCESS_CERTIFICATE_DAYS = 825

CertificateGenerator: TypeAlias = Callable[[str], tuple[bytes, bytes]]
AtomicWriteValidator: TypeAlias = Callable[[Path], None]

logger = logging.getLogger(__name__)


def _client_id(value: object) -> str:
    if (
        type(value) is not str
        or not value
        or len(value) > 64
        or not value.isascii()
        or not value[0].isalnum()
        or any(
            not (character.isalnum() or character in "._-")
            for character in value
        )
    ):
        raise ValueError(
            "Advanced-access client ID must start with an ASCII letter or digit "
            "and contain only ASCII letters, digits, '.', '_', or '-'."
        )
    return value


def _identity_generation(value: object) -> str:
    if (
        type(value) is not str
        or len(value) != 32
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError("Advanced-access identity generation is invalid.")
    return value


def _require_absolute_path(value: object, *, label: str) -> Path:
    if not isinstance(value, Path) or not value.is_absolute():
        raise ValueError(f"{label} must be an absolute pathlib.Path.")
    return value


def _require_direct_child(path: Path, parent: Path, *, label: str) -> Path:
    if path.parent != parent:
        raise ValueError(f"{label} must be directly inside its private directory.")
    return path


@dataclass(frozen=True, slots=True)
class HomeAssistantAppAdvancedAccessPaths:
    """Exact persistent and runtime paths for App-owned access material."""

    root: Path
    state: Path
    identities: Path
    clients: Path
    dashboard_password: Path = field(repr=False)
    runtime_remote_configuration: Path

    def __post_init__(self) -> None:
        root = _require_absolute_path(
            self.root,
            label="Advanced-access root",
        )
        object.__setattr__(self, "root", root)
        for field_name, label in (
            ("state", "Advanced-access state"),
            ("identities", "Advanced-access identities directory"),
            ("clients", "Advanced-access clients directory"),
            ("dashboard_password", "Advanced-access dashboard password"),
        ):
            selected = _require_absolute_path(
                getattr(self, field_name),
                label=label,
            )
            _require_direct_child(selected, root, label=label)
            object.__setattr__(self, field_name, selected)
        runtime = _require_absolute_path(
            self.runtime_remote_configuration,
            label="Advanced-access runtime configuration",
        )
        object.__setattr__(self, "runtime_remote_configuration", runtime)

    def identity_directory(self, generation: str) -> Path:
        return self.identities / _identity_generation(generation)

    def certificate(self, generation: str) -> Path:
        return self.identity_directory(generation) / "server.crt"

    def private_key(self, generation: str) -> Path:
        return self.identity_directory(generation) / "server.key"

    def credential(self, client_id: str) -> Path:
        return self.clients / f"{_client_id(client_id)}.secret"


def default_home_assistant_app_advanced_access_paths(
    *,
    root: Path = HOME_ASSISTANT_APP_ADVANCED_ACCESS_DIRECTORY,
    runtime_directory: Path = Path("/run/sdsctl"),
) -> HomeAssistantAppAdvancedAccessPaths:
    """Return deterministic App-private advanced-access paths."""

    return HomeAssistantAppAdvancedAccessPaths(
        root=root,
        state=root / HOME_ASSISTANT_APP_ADVANCED_ACCESS_STATE_FILENAME,
        identities=(
            root / HOME_ASSISTANT_APP_ADVANCED_ACCESS_IDENTITIES_DIRECTORY
        ),
        clients=root / HOME_ASSISTANT_APP_ADVANCED_ACCESS_CLIENTS_DIRECTORY,
        dashboard_password=(
            root / HOME_ASSISTANT_APP_ADVANCED_ACCESS_DASHBOARD_PASSWORD_FILENAME
        ),
        runtime_remote_configuration=(
            runtime_directory
            / HOME_ASSISTANT_APP_ADVANCED_ACCESS_RUNTIME_CONFIG_FILENAME
        ),
    )


def home_assistant_app_advanced_access_context_path(
    paths: HomeAssistantAppAdvancedAccessPaths,
) -> Path:
    """Return the exact non-secret parent-to-Ingress runtime context path."""

    if not isinstance(paths, HomeAssistantAppAdvancedAccessPaths):
        raise TypeError("Advanced-access context requires App-private paths.")
    return (
        paths.runtime_remote_configuration.parent
        / HOME_ASSISTANT_APP_ADVANCED_ACCESS_CONTEXT_FILENAME
    )


@dataclass(frozen=True, slots=True)
class HomeAssistantAppAdvancedClient:
    """Secret-free metadata for one independently managed remote client."""

    client_id: str
    scopes: tuple[DaemonRemoteAuthorizationScope, ...] = (
        DaemonRemoteAuthorizationScope.OBSERVE,
    )
    revoked: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "client_id", _client_id(self.client_id))
        if type(self.scopes) is not tuple or not self.scopes:
            raise TypeError("Advanced-access client scopes must be a non-empty tuple.")
        if any(
            not isinstance(scope, DaemonRemoteAuthorizationScope)
            for scope in self.scopes
        ):
            raise TypeError("Advanced-access client scopes are invalid.")
        if len(set(self.scopes)) != len(self.scopes):
            raise ValueError("Advanced-access client scopes must not contain duplicates.")
        if DaemonRemoteAuthorizationScope.OBSERVE not in self.scopes:
            raise ValueError("Advanced-access clients require the observe scope.")
        object.__setattr__(
            self,
            "scopes",
            tuple(sorted(self.scopes, key=lambda scope: scope.value)),
        )
        if type(self.revoked) is not bool:
            raise TypeError("Advanced-access client revoked state must be boolean.")

    def as_dict(self) -> dict[str, object]:
        return {
            "client_id": self.client_id,
            "scopes": [scope.value for scope in self.scopes],
            "revoked": self.revoked,
        }


@dataclass(frozen=True, slots=True)
class HomeAssistantAppAdvancedAccessState:
    """Non-secret persistent selection and client registry."""

    identity_generation: str | None = None
    clients: tuple[HomeAssistantAppAdvancedClient, ...] = ()

    def __post_init__(self) -> None:
        if self.identity_generation is not None:
            object.__setattr__(
                self,
                "identity_generation",
                _identity_generation(self.identity_generation),
            )
        if type(self.clients) is not tuple:
            raise TypeError("Advanced-access clients must be a tuple.")
        identifiers = tuple(client.client_id for client in self.clients)
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("Advanced-access client IDs must be unique.")
        if len(self.clients) > HOME_ASSISTANT_APP_ADVANCED_ACCESS_MAX_CLIENTS:
            raise ValueError("Advanced-access client limit exceeded.")
        object.__setattr__(
            self,
            "clients",
            tuple(sorted(self.clients, key=lambda client: client.client_id)),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "version": HOME_ASSISTANT_APP_ADVANCED_ACCESS_VERSION,
            "identity_generation": self.identity_generation,
            "clients": [client.as_dict() for client in self.clients],
        }


@dataclass(frozen=True, slots=True)
class HomeAssistantAppAdvancedAccessSnapshot:
    """Redacted lifecycle status safe for the Ingress workspace."""

    identity_present: bool
    certificate_sha256: str | None
    dashboard_password_present: bool
    clients: tuple[HomeAssistantAppAdvancedClient, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "identity_present": self.identity_present,
            "certificate_sha256": self.certificate_sha256,
            "dashboard_password_present": self.dashboard_password_present,
            "clients": [client.as_dict() for client in self.clients],
        }


@dataclass(frozen=True, slots=True, repr=False)
class HomeAssistantAppOneTimeClientEnrollment:
    """One-time client material returned only by issue or rotation."""

    client_id: str
    credential: str = field(repr=False)
    certificate: str
    profile: str = field(repr=False)

    def __repr__(self) -> str:
        return "HomeAssistantAppOneTimeClientEnrollment(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class HomeAssistantAppOneTimeDashboardPassword:
    """One-time native-dashboard password returned only after rotation."""

    password: str = field(repr=False)

    def __repr__(self) -> str:
        return "HomeAssistantAppOneTimeDashboardPassword(<redacted>)"


def _prepare_private_directories(paths: HomeAssistantAppAdvancedAccessPaths) -> None:
    for directory in (paths.root, paths.identities, paths.clients):
        directory.mkdir(
            parents=True,
            exist_ok=True,
            mode=HOME_ASSISTANT_APP_ADVANCED_ACCESS_DIRECTORY_MODE,
        )
        if directory.is_symlink() or not directory.is_dir():
            raise SDS200Error("Advanced-access private directory is unsafe.")
        directory.chmod(HOME_ASSISTANT_APP_ADVANCED_ACCESS_DIRECTORY_MODE)


def _atomic_write(
    path: Path,
    content: bytes,
    *,
    mode: int,
    validator: AtomicWriteValidator | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.parent / f".{path.name}.{secrets.token_hex(8)}.tmp"
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            mode,
        )
        written = 0
        while written < len(content):
            count = os.write(descriptor, content[written:])
            if count <= 0:
                raise OSError("Advanced-access atomic write made no progress.")
            written += count
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        temporary.chmod(mode)
        if validator is not None:
            validator(temporary)
        os.replace(temporary, path)
    finally:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)
        with suppress(FileNotFoundError):
            temporary.unlink()


def load_home_assistant_app_advanced_access_state(
    paths: HomeAssistantAppAdvancedAccessPaths,
) -> HomeAssistantAppAdvancedAccessState:
    """Load the strict non-secret App-owned lifecycle document."""

    if not paths.state.exists():
        return HomeAssistantAppAdvancedAccessState()
    try:
        observed = paths.state.lstat()
        if (
            stat.S_ISLNK(observed.st_mode)
            or not stat.S_ISREG(observed.st_mode)
            or not 1 <= observed.st_size <= HOME_ASSISTANT_APP_ADVANCED_ACCESS_MAX_STATE_BYTES
            or (
                os.name == "posix"
                and stat.S_IMODE(observed.st_mode) != DAEMON_REMOTE_PRIVATE_FILE_MODE
            )
        ):
            raise SDS200Error("Advanced-access lifecycle state is unsafe.")
        payload = json.loads(paths.state.read_text(encoding="utf-8"))
    except SDS200Error:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SDS200Error("Advanced-access lifecycle state is invalid.") from error
    if not isinstance(payload, Mapping) or set(payload) != {
        "version",
        "identity_generation",
        "clients",
    }:
        raise SDS200Error("Advanced-access lifecycle state is invalid.")
    if payload.get("version") != HOME_ASSISTANT_APP_ADVANCED_ACCESS_VERSION:
        raise SDS200Error("Advanced-access lifecycle state version is unsupported.")
    raw_clients = payload.get("clients")
    if not isinstance(raw_clients, Sequence) or isinstance(
        raw_clients,
        (str, bytes, bytearray),
    ):
        raise SDS200Error("Advanced-access lifecycle client state is invalid.")
    try:
        clients = tuple(_parse_client(item) for item in raw_clients)
        return HomeAssistantAppAdvancedAccessState(
            identity_generation=payload.get("identity_generation"),
            clients=clients,
        )
    except (TypeError, ValueError) as error:
        raise SDS200Error("Advanced-access lifecycle state is invalid.") from error


def _parse_client(value: object) -> HomeAssistantAppAdvancedClient:
    if not isinstance(value, Mapping) or set(value) != {
        "client_id",
        "scopes",
        "revoked",
    }:
        raise ValueError("Advanced-access client state is invalid.")
    raw_scopes = value.get("scopes")
    if not isinstance(raw_scopes, Sequence) or isinstance(
        raw_scopes,
        (str, bytes, bytearray),
    ):
        raise ValueError("Advanced-access client scopes are invalid.")
    raw_client_id = value.get("client_id")
    raw_revoked = value.get("revoked")
    if not isinstance(raw_client_id, str) or type(raw_revoked) is not bool:
        raise ValueError("Advanced-access client state is invalid.")
    return HomeAssistantAppAdvancedClient(
        client_id=raw_client_id,
        scopes=tuple(DaemonRemoteAuthorizationScope(scope) for scope in raw_scopes),
        revoked=raw_revoked,
    )


def write_home_assistant_app_advanced_access_state(
    paths: HomeAssistantAppAdvancedAccessPaths,
    state: HomeAssistantAppAdvancedAccessState,
) -> None:
    """Atomically replace the non-secret lifecycle state."""

    _prepare_private_directories(paths)
    payload = json.dumps(
        state.as_dict(),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii") + b"\n"
    _atomic_write(
        paths.state,
        payload,
        mode=DAEMON_REMOTE_PRIVATE_FILE_MODE,
    )


def write_home_assistant_app_advanced_access_context(
    paths: HomeAssistantAppAdvancedAccessPaths,
    info: HomeAssistantAppSupervisorInfo,
) -> Path:
    """Write strict non-secret Supervisor state for the tokenless Ingress child."""

    if not isinstance(info, HomeAssistantAppSupervisorInfo):
        raise TypeError("Advanced-access context requires Supervisor App state.")
    advanced_option_names = (
        "remote_daemon_enabled",
        "native_dashboard_enabled",
        "advanced_access_server_name",
        "advanced_access_host_address",
    )
    document = {
        "version": HOME_ASSISTANT_APP_ADVANCED_ACCESS_VERSION,
        "container_address": info.container_address,
        "network": {
            HOME_ASSISTANT_APP_REMOTE_DAEMON_PORT_KEY: info.network.get(
                HOME_ASSISTANT_APP_REMOTE_DAEMON_PORT_KEY
            ),
            HOME_ASSISTANT_APP_NATIVE_DASHBOARD_PORT_KEY: info.network.get(
                HOME_ASSISTANT_APP_NATIVE_DASHBOARD_PORT_KEY
            ),
        },
        "options": {
            name: info.options[name]
            for name in advanced_option_names
            if name in info.options
        },
    }
    try:
        payload = json.dumps(
            document,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii") + b"\n"
    except (TypeError, ValueError) as error:
        raise SDS200Error("Advanced-access runtime context is invalid.") from error
    if len(payload) > HOME_ASSISTANT_APP_ADVANCED_ACCESS_MAX_CONTEXT_BYTES:
        raise SDS200Error("Advanced-access runtime context is too large.")
    target = home_assistant_app_advanced_access_context_path(paths)
    _atomic_write(
        target,
        payload,
        mode=DAEMON_REMOTE_PRIVATE_FILE_MODE,
    )
    return target


def load_home_assistant_app_advanced_access_context(
    paths: HomeAssistantAppAdvancedAccessPaths,
) -> HomeAssistantAppSupervisorInfo:
    """Load the parent-prepared Supervisor state without its capability token."""

    target = home_assistant_app_advanced_access_context_path(paths)
    try:
        observed = target.lstat()
        if (
            stat.S_ISLNK(observed.st_mode)
            or not stat.S_ISREG(observed.st_mode)
            or not 1 <= observed.st_size <= HOME_ASSISTANT_APP_ADVANCED_ACCESS_MAX_CONTEXT_BYTES
            or (
                os.name == "posix"
                and stat.S_IMODE(observed.st_mode) != DAEMON_REMOTE_PRIVATE_FILE_MODE
            )
        ):
            raise SDS200Error("Advanced-access runtime context is unsafe.")
        payload = json.loads(target.read_text(encoding="ascii"))
    except SDS200Error:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SDS200Error("Advanced-access runtime context is invalid.") from error
    if not isinstance(payload, Mapping) or set(payload) != {
        "version",
        "container_address",
        "network",
        "options",
    }:
        raise SDS200Error("Advanced-access runtime context is invalid.")
    if payload.get("version") != HOME_ASSISTANT_APP_ADVANCED_ACCESS_VERSION:
        raise SDS200Error("Advanced-access runtime context version is unsupported.")
    network = payload.get("network")
    options = payload.get("options")
    container_address = payload.get("container_address")
    if (
        type(container_address) is not str
        or not isinstance(network, Mapping)
        or set(network)
        != {
            HOME_ASSISTANT_APP_REMOTE_DAEMON_PORT_KEY,
            HOME_ASSISTANT_APP_NATIVE_DASHBOARD_PORT_KEY,
        }
        or not isinstance(options, Mapping)
        or not set(options).issubset(
            {
                "remote_daemon_enabled",
                "native_dashboard_enabled",
                "advanced_access_server_name",
                "advanced_access_host_address",
            }
        )
        or any(
            type(value) is not bool
            for name, value in options.items()
            if name in {"remote_daemon_enabled", "native_dashboard_enabled"}
        )
        or any(
            type(value) is not str
            for name, value in options.items()
            if name in {
                "advanced_access_server_name",
                "advanced_access_host_address",
            }
        )
    ):
        raise SDS200Error("Advanced-access runtime context is invalid.")
    try:
        return HomeAssistantAppSupervisorInfo(
            container_address=container_address,
            network=network,
            options=options,
        )
    except (TypeError, ValueError) as error:
        raise SDS200Error("Advanced-access runtime context is invalid.") from error


def inspect_home_assistant_app_advanced_access(
    paths: HomeAssistantAppAdvancedAccessPaths,
) -> HomeAssistantAppAdvancedAccessSnapshot:
    """Return status without private paths, names, or secret values."""

    state = load_home_assistant_app_advanced_access_state(paths)
    certificate_digest: str | None = None
    identity_present = False
    if state.identity_generation is not None:
        certificate = paths.certificate(state.identity_generation)
        private_key = paths.private_key(state.identity_generation)
        _validate_material_file(certificate, private=False)
        _validate_material_file(private_key, private=True)
        certificate_digest = hashlib.sha256(certificate.read_bytes()).hexdigest()
        identity_present = True
    password_present = paths.dashboard_password.exists()
    if password_present:
        _validate_secret_file(paths.dashboard_password)
    for client in state.clients:
        _validate_secret_file(paths.credential(client.client_id))
    return HomeAssistantAppAdvancedAccessSnapshot(
        identity_present=identity_present,
        certificate_sha256=certificate_digest,
        dashboard_password_present=password_present,
        clients=state.clients,
    )


def _validate_material_file(path: Path, *, private: bool) -> None:
    try:
        observed = path.lstat()
    except OSError as error:
        raise SDS200Error("Advanced-access identity material is unavailable.") from error
    expected_mode = (
        DAEMON_REMOTE_PRIVATE_FILE_MODE
        if private
        else HOME_ASSISTANT_APP_ADVANCED_ACCESS_PUBLIC_FILE_MODE
    )
    if (
        stat.S_ISLNK(observed.st_mode)
        or not stat.S_ISREG(observed.st_mode)
        or not 1 <= observed.st_size <= HOME_ASSISTANT_APP_ADVANCED_ACCESS_MAX_TLS_BYTES
        or (os.name == "posix" and stat.S_IMODE(observed.st_mode) != expected_mode)
    ):
        raise SDS200Error("Advanced-access identity material is unsafe.")


def _validate_secret_file(path: Path) -> None:
    try:
        observed = path.lstat()
    except OSError as error:
        raise SDS200Error("Advanced-access secret material is unavailable.") from error
    if (
        stat.S_ISLNK(observed.st_mode)
        or not stat.S_ISREG(observed.st_mode)
        or not 43 <= observed.st_size <= 512
        or (
            os.name == "posix"
            and stat.S_IMODE(observed.st_mode) != DAEMON_REMOTE_PRIVATE_FILE_MODE
        )
    ):
        raise SDS200Error("Advanced-access secret material is unsafe.")


def generate_home_assistant_app_server_identity(server_name: str) -> tuple[bytes, bytes]:
    """Generate one App-owned self-signed TLS identity without printing its key."""

    normalized_name = HomeAssistantAppOptions(
        scanner_host="validation.invalid",
        advanced_access_server_name=server_name,
    ).advanced_access_server_name
    if not normalized_name:
        raise ValueError("Advanced-access server name is not configured.")
    subject_kind = "IP" if _is_ip_address(normalized_name) else "DNS"
    with TemporaryDirectory(prefix="sdsctl-ha-identity-") as temporary:
        root = Path(temporary)
        certificate = root / "server.crt"
        private_key = root / "server.key"
        try:
            subprocess.run(
                (
                    "openssl",
                    "req",
                    "-x509",
                    "-newkey",
                    "rsa:3072",
                    "-sha256",
                    "-days",
                    str(HOME_ASSISTANT_APP_ADVANCED_ACCESS_CERTIFICATE_DAYS),
                    "-nodes",
                    "-keyout",
                    os.fspath(private_key),
                    "-out",
                    os.fspath(certificate),
                    "-subj",
                    f"/CN={normalized_name}",
                    "-addext",
                    f"subjectAltName={subject_kind}:{normalized_name}",
                ),
                check=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30.0,
            )
            certificate_bytes = certificate.read_bytes()
            private_key_bytes = private_key.read_bytes()
        except (OSError, subprocess.SubprocessError) as error:
            raise SDS200Error(
                "Advanced-access TLS identity generation failed."
            ) from error
    if (
        not certificate_bytes.startswith(b"-----BEGIN CERTIFICATE-----")
        or b"PRIVATE KEY-----" not in private_key_bytes
        or len(certificate_bytes) > HOME_ASSISTANT_APP_ADVANCED_ACCESS_MAX_TLS_BYTES
        or len(private_key_bytes) > HOME_ASSISTANT_APP_ADVANCED_ACCESS_MAX_TLS_BYTES
    ):
        raise SDS200Error("Advanced-access TLS identity generation failed.")
    return certificate_bytes, private_key_bytes


def _is_ip_address(value: str) -> bool:
    try:
        ip_address(value)
    except ValueError:
        return False
    return True


def rotate_home_assistant_app_server_identity(
    paths: HomeAssistantAppAdvancedAccessPaths,
    server_name: str,
    *,
    generator: CertificateGenerator = generate_home_assistant_app_server_identity,
) -> HomeAssistantAppAdvancedAccessSnapshot:
    """Install one complete new identity generation and select it atomically."""

    if not callable(generator):
        raise TypeError("Advanced-access certificate generator must be callable.")
    normalized_server_name = HomeAssistantAppOptions(
        scanner_host="validation.invalid",
        advanced_access_server_name=server_name,
    ).advanced_access_server_name
    if not normalized_server_name:
        raise ValueError("Advanced-access server name is not configured.")
    state = load_home_assistant_app_advanced_access_state(paths)
    generation = secrets.token_hex(16)
    directory = paths.identity_directory(generation)
    _prepare_private_directories(paths)
    directory.mkdir(mode=HOME_ASSISTANT_APP_ADVANCED_ACCESS_DIRECTORY_MODE)
    committed = False
    try:
        certificate, private_key = generator(normalized_server_name)
        if type(certificate) is not bytes or type(private_key) is not bytes:
            raise TypeError("Advanced-access certificate generator must return bytes.")
        _atomic_write(
            paths.certificate(generation),
            certificate,
            mode=HOME_ASSISTANT_APP_ADVANCED_ACCESS_PUBLIC_FILE_MODE,
        )
        _atomic_write(
            paths.private_key(generation),
            private_key,
            mode=DAEMON_REMOTE_PRIVATE_FILE_MODE,
        )
        _validate_material_file(paths.certificate(generation), private=False)
        _validate_material_file(paths.private_key(generation), private=True)
        write_home_assistant_app_advanced_access_state(
            paths,
            HomeAssistantAppAdvancedAccessState(
                identity_generation=generation,
                clients=state.clients,
            ),
        )
        committed = True
    except BaseException:
        if not committed:
            _remove_identity_directory(directory)
        raise
    _retire_unselected_identity_directories(paths, selected=generation)
    return inspect_home_assistant_app_advanced_access(paths)


def _remove_identity_directory(directory: Path) -> bool:
    try:
        if directory.is_symlink() or not directory.is_dir():
            return False
        children = tuple(directory.iterdir())
        if any(
            child.name not in {"server.crt", "server.key"}
            or child.is_symlink()
            or not child.is_file()
            for child in children
        ):
            return False
        for child in children:
            child.unlink()
        directory.rmdir()
    except OSError:
        return False
    return True


def _retire_unselected_identity_directories(
    paths: HomeAssistantAppAdvancedAccessPaths,
    *,
    selected: str,
) -> None:
    try:
        directories = tuple(paths.identities.iterdir())
    except OSError:
        logger.warning(
            "Unselected advanced-access identity directories could not be "
            "enumerated for retirement."
        )
        return
    for directory in directories:
        if directory.name == selected:
            continue
        if not _remove_identity_directory(directory):
            logger.warning(
                "An unselected advanced-access identity directory could not "
                "be retired."
            )


def rotate_home_assistant_app_dashboard_password(
    paths: HomeAssistantAppAdvancedAccessPaths,
) -> HomeAssistantAppOneTimeDashboardPassword:
    """Replace the native-dashboard password and return it exactly once."""

    _prepare_private_directories(paths)
    password = secrets.token_urlsafe(32)
    _atomic_write(
        paths.dashboard_password,
        (password + "\n").encode("ascii"),
        mode=DAEMON_REMOTE_PRIVATE_FILE_MODE,
    )
    return HomeAssistantAppOneTimeDashboardPassword(password=password)


def issue_home_assistant_app_remote_client(
    paths: HomeAssistantAppAdvancedAccessPaths,
    *,
    client_id: str,
    control: bool,
    host_address: str,
    server_name: str,
    host_port: int,
    replace: bool = False,
) -> HomeAssistantAppOneTimeClientEnrollment:
    """Issue or rotate one independent client credential exactly once."""

    normalized_id = _client_id(client_id)
    if type(control) is not bool or type(replace) is not bool:
        raise TypeError("Advanced-access client flags must be boolean.")
    if type(host_port) is not int or not 1 <= host_port <= 65535:
        raise ValueError("Advanced-access remote host port is invalid.")
    normalized_options = HomeAssistantAppOptions(
        scanner_host="validation.invalid",
        advanced_access_host_address=host_address,
        advanced_access_server_name=server_name,
    )
    normalized_host_address = normalized_options.advanced_access_host_address
    normalized_server_name = normalized_options.advanced_access_server_name
    if not normalized_host_address:
        raise ValueError("Advanced-access host address is not configured.")
    if not normalized_server_name:
        raise ValueError("Advanced-access server name is not configured.")
    state = load_home_assistant_app_advanced_access_state(paths)
    if state.identity_generation is None:
        raise SDS200Error("Advanced-access server identity is not initialized.")
    existing = next(
        (client for client in state.clients if client.client_id == normalized_id),
        None,
    )
    if existing is not None and not replace:
        raise SDS200Error("Advanced-access client already exists; rotate it explicitly.")
    if existing is None and replace:
        raise SDS200Error("Advanced-access client is unavailable for rotation.")

    scopes: tuple[DaemonRemoteAuthorizationScope, ...] = (
        DaemonRemoteAuthorizationScope.OBSERVE,
    )
    if control:
        scopes += (DaemonRemoteAuthorizationScope.CONTROL,)
    replacement = HomeAssistantAppAdvancedClient(
        client_id=normalized_id,
        scopes=scopes,
    )
    clients = tuple(
        replacement if client.client_id == normalized_id else client
        for client in state.clients
    )
    if existing is None:
        clients += (replacement,)
    candidate = HomeAssistantAppAdvancedAccessState(
        identity_generation=state.identity_generation,
        clients=clients,
    )
    certificate_path = paths.certificate(state.identity_generation)
    _validate_material_file(certificate_path, private=False)
    certificate = certificate_path.read_text(encoding="ascii")
    profile = render_home_assistant_app_remote_client_profile(
        client_id=normalized_id,
        host_address=normalized_host_address,
        server_name=normalized_server_name,
        host_port=host_port,
    )
    credential = secrets.token_urlsafe(DAEMON_REMOTE_AUTH_CREDENTIAL_BYTES)
    _prepare_private_directories(paths)
    credential_path = paths.credential(normalized_id)
    previous_credential: bytes | None = None
    if existing is None:
        if credential_path.exists():
            raise SDS200Error(
                "An unregistered advanced-access client credential already exists."
            )
    else:
        _validate_secret_file(credential_path)
        previous_credential = credential_path.read_bytes()
    try:
        _atomic_write(
            credential_path,
            (credential + "\n").encode("ascii"),
            mode=DAEMON_REMOTE_PRIVATE_FILE_MODE,
        )
        write_home_assistant_app_advanced_access_state(paths, candidate)
    except BaseException:
        if previous_credential is None:
            with suppress(FileNotFoundError):
                credential_path.unlink()
        else:
            _atomic_write(
                credential_path,
                previous_credential,
                mode=DAEMON_REMOTE_PRIVATE_FILE_MODE,
            )
        raise
    return HomeAssistantAppOneTimeClientEnrollment(
        client_id=normalized_id,
        credential=credential,
        certificate=certificate,
        profile=profile,
    )


def set_home_assistant_app_remote_client_revoked(
    paths: HomeAssistantAppAdvancedAccessPaths,
    *,
    client_id: str,
    revoked: bool,
) -> HomeAssistantAppAdvancedAccessSnapshot:
    """Selectively revoke or restore one existing client identity."""

    normalized_id = _client_id(client_id)
    if type(revoked) is not bool:
        raise TypeError("Advanced-access client revoked setting must be boolean.")
    state = load_home_assistant_app_advanced_access_state(paths)
    if not any(client.client_id == normalized_id for client in state.clients):
        raise SDS200Error("Advanced-access client is unavailable.")
    clients = tuple(
        HomeAssistantAppAdvancedClient(
            client_id=client.client_id,
            scopes=client.scopes,
            revoked=revoked,
        )
        if client.client_id == normalized_id
        else client
        for client in state.clients
    )
    write_home_assistant_app_advanced_access_state(
        paths,
        HomeAssistantAppAdvancedAccessState(
            identity_generation=state.identity_generation,
            clients=clients,
        ),
    )
    return inspect_home_assistant_app_advanced_access(paths)


def render_home_assistant_app_remote_client_profile(
    *,
    client_id: str,
    host_address: str,
    server_name: str,
    host_port: int,
) -> str:
    """Render a secret-free client profile using deterministic local paths."""

    normalized_id = _client_id(client_id)
    if type(host_port) is not int or not 1 <= host_port <= 65535:
        raise ValueError("Advanced-access remote host port is invalid.")
    normalized_options = HomeAssistantAppOptions(
        scanner_host="validation.invalid",
        advanced_access_host_address=host_address,
        advanced_access_server_name=server_name,
    )
    normalized_host_address = normalized_options.advanced_access_host_address
    normalized_name = normalized_options.advanced_access_server_name
    if not normalized_host_address:
        raise ValueError("Advanced-access host address is not configured.")
    if not normalized_name:
        raise ValueError("Advanced-access server name is not configured.")
    client_root = f"/etc/sdsctl/remote/{normalized_id}"
    return (
        "# Save the downloaded certificate and credential at the absolute paths "
        "below, or replace both paths before use.\n"
        "version = 1\n\n"
        f"[profiles.{json.dumps(normalized_id)}]\n"
        f"address = {json.dumps(normalized_host_address)}\n"
        f"port = {host_port}\n"
        f"server_hostname = {json.dumps(normalized_name)}\n"
        f"certificate_file = {json.dumps(client_root + '/server.crt')}\n"
        f"client_id = {json.dumps(normalized_id)}\n"
        f"credential_file = {json.dumps(client_root + '/client.secret')}\n"
    )


def write_home_assistant_app_remote_daemon_configuration(
    paths: HomeAssistantAppAdvancedAccessPaths,
    exposure: HomeAssistantAppAdvancedExposure,
) -> Path:
    """Materialize and preflight the enabled daemon listener configuration."""

    if exposure.remote_daemon_host_port is None:
        raise ValueError("Advanced-access remote daemon is not enabled.")
    state = load_home_assistant_app_advanced_access_state(paths)
    if state.identity_generation is None:
        raise SDS200Error("Advanced-access server identity is not initialized.")
    active = tuple(client for client in state.clients if not client.revoked)
    if not active:
        raise SDS200Error("Advanced-access remote daemon requires one active client.")
    certificate = paths.certificate(state.identity_generation)
    private_key = paths.private_key(state.identity_generation)
    lines = [
        f"version = {DAEMON_REMOTE_CONFIG_VERSION}",
        "",
        "[listener]",
        "enabled = true",
        f"bind_address = {json.dumps(exposure.container_address)}",
        f"port = {DAEMON_REMOTE_DEFAULT_PORT}",
        f"certificate_file = {json.dumps(os.fspath(certificate))}",
        f"private_key_file = {json.dumps(os.fspath(private_key))}",
    ]
    for client in state.clients:
        lines.extend(
            (
                "",
                "[[clients]]",
                f"client_id = {json.dumps(client.client_id)}",
                f"credential_file = {json.dumps(os.fspath(paths.credential(client.client_id)))}",
                "scopes = ["
                + ", ".join(json.dumps(scope.value) for scope in client.scopes)
                + "]",
                f"revoked = {str(client.revoked).lower()}",
            )
        )
    rendered = ("\n".join(lines) + "\n").encode("utf-8")
    def validate(candidate: Path) -> None:
        configuration = load_daemon_remote_configuration(candidate)
        if configuration is None:
            raise ConfigurationError(
                "Advanced-access daemon configuration disappeared."
            )
        preflight_daemon_remote_configuration(configuration)

    _atomic_write(
        paths.runtime_remote_configuration,
        rendered,
        mode=DAEMON_REMOTE_PRIVATE_FILE_MODE,
        validator=validate,
    )
    return paths.runtime_remote_configuration


__all__ = [
    "HOME_ASSISTANT_APP_ADVANCED_ACCESS_CERTIFICATE_DAYS",
    "HOME_ASSISTANT_APP_ADVANCED_ACCESS_CLIENTS_DIRECTORY",
    "HOME_ASSISTANT_APP_ADVANCED_ACCESS_CONTEXT_FILENAME",
    "HOME_ASSISTANT_APP_ADVANCED_ACCESS_DASHBOARD_PASSWORD_FILENAME",
    "HOME_ASSISTANT_APP_ADVANCED_ACCESS_DIRECTORY",
    "HOME_ASSISTANT_APP_ADVANCED_ACCESS_DIRECTORY_MODE",
    "HOME_ASSISTANT_APP_ADVANCED_ACCESS_IDENTITIES_DIRECTORY",
    "HOME_ASSISTANT_APP_ADVANCED_ACCESS_MAX_CLIENTS",
    "HOME_ASSISTANT_APP_ADVANCED_ACCESS_MAX_CONTEXT_BYTES",
    "HOME_ASSISTANT_APP_ADVANCED_ACCESS_PUBLIC_FILE_MODE",
    "HOME_ASSISTANT_APP_ADVANCED_ACCESS_RUNTIME_CONFIG_FILENAME",
    "HOME_ASSISTANT_APP_ADVANCED_ACCESS_STATE_FILENAME",
    "HOME_ASSISTANT_APP_ADVANCED_ACCESS_VERSION",
    "HomeAssistantAppAdvancedAccessPaths",
    "HomeAssistantAppAdvancedAccessSnapshot",
    "HomeAssistantAppAdvancedAccessState",
    "HomeAssistantAppAdvancedClient",
    "HomeAssistantAppOneTimeClientEnrollment",
    "HomeAssistantAppOneTimeDashboardPassword",
    "default_home_assistant_app_advanced_access_paths",
    "generate_home_assistant_app_server_identity",
    "home_assistant_app_advanced_access_context_path",
    "inspect_home_assistant_app_advanced_access",
    "issue_home_assistant_app_remote_client",
    "load_home_assistant_app_advanced_access_context",
    "load_home_assistant_app_advanced_access_state",
    "render_home_assistant_app_remote_client_profile",
    "rotate_home_assistant_app_dashboard_password",
    "rotate_home_assistant_app_server_identity",
    "set_home_assistant_app_remote_client_revoked",
    "write_home_assistant_app_advanced_access_state",
    "write_home_assistant_app_advanced_access_context",
    "write_home_assistant_app_remote_daemon_configuration",
]
