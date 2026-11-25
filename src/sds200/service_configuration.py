"""Strict portable configuration for long-running display services.

These documents are deliberately separate from the layered application
configuration and from daemon client profiles.  A service manifest describes
how one host launches an existing, already-authorized sdsctl client.  It never
creates credentials, changes a service, or opens a network connection while it
is being loaded or inspected.
"""

from __future__ import annotations

import json
import os
import shlex
import stat
import sys
import tempfile
import tomllib
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from importlib.resources import files
from math import isfinite
from pathlib import Path
from types import MappingProxyType
from typing import Literal, TypeAlias, cast

from .browser_kiosk import kiosk_origin, kiosk_role
from .exceptions import ConfigurationError
from .logging_config import LOG_LEVEL_NAMES

SERVICE_CONFIGURATION_VERSION = 1
SERVICE_CONFIGURATION_MAX_BYTES = 64 * 1024
SERVICE_CONFIGURATION_MODE = 0o600
DISPLAY_CLIENT_SERVICE_FILENAME = "sdsctl-display-configured.service"
BROWSER_KIOSK_SERVICE_FILENAME = "sdsctl-browser-kiosk-configured.service"

ServiceKind: TypeAlias = Literal["display-client", "browser-kiosk"]
ServiceConfigurationSource: TypeAlias = Literal["default", "manifest", "command-line"]
TuiStartupView: TypeAlias = Literal["dashboard", "mimic"]
ColorMode: TypeAlias = Literal["auto", "always", "never"]

_SERVICE_KINDS: tuple[ServiceKind, ...] = ("display-client", "browser-kiosk")
_STARTUP_VIEWS: tuple[TuiStartupView, ...] = ("dashboard", "mimic")
_COLOR_MODES: tuple[ColorMode, ...] = ("auto", "always", "never")
_MISSING = object()
_DISPLAY_OVERRIDE_FIELDS = frozenset(
    {
        "paths.config_home",
        "paths.state_home",
        "paths.cache_home",
        "connection.remote_profile",
        "connection.timeout",
        "display.terminal",
        "display.startup_view",
        "display.theme",
        "display.color",
        "recording.directory",
        "recording.metadata",
        "audio.playback",
        "audio.device",
        "audio.buffer_ms",
        "audio.history_limit",
        "logging.level",
        "logging.file",
    }
)
_BROWSER_OVERRIDE_FIELDS = frozenset(
    {
        "server.origin",
        "server.ca_file",
        "kiosk.browser",
        "kiosk.role",
        "kiosk.profile_directory",
        "logging.level",
        "logging.file",
    }
)


@dataclass(frozen=True, slots=True)
class DisplayClientServiceConfiguration:
    """Validated durable settings for one observe-only managed TUI."""

    remote_profile: str
    config_home: Path = Path("/var/lib/sdsctl-display/.config")
    state_home: Path = Path("/var/lib/sdsctl-display/.local/state")
    cache_home: Path = Path("/var/cache/sdsctl-display")
    terminal: Path = Path("/dev/tty1")
    timeout: float = 5.0
    startup_view: TuiStartupView = "dashboard"
    theme: str = "dark"
    color: ColorMode = "auto"
    recording_directory: Path = Path("/var/lib/sdsctl-display/recordings")
    recording_metadata: bool = True
    audio_playback: bool = False
    audio_device: str | int | None = None
    audio_buffer_ms: int = 250
    audio_history_limit: int = 100
    log_level: str | None = None
    log_file: Path | None = None


@dataclass(frozen=True, slots=True)
class BrowserKioskServiceConfiguration:
    """Validated durable settings for one manual-login Chromium kiosk."""

    origin: str
    ca_file: Path
    browser: Path
    role: str
    profile_directory: Path
    log_level: str | None = None
    log_file: Path | None = None


ServiceConfiguration: TypeAlias = (
    DisplayClientServiceConfiguration | BrowserKioskServiceConfiguration
)


@dataclass(frozen=True, slots=True)
class ServiceConfigurationOrigin:
    """Source of one effective service-manifest field."""

    source: ServiceConfigurationSource
    location: str | None = None


@dataclass(frozen=True, slots=True)
class ResolvedServiceConfiguration:
    """One validated service manifest plus immutable field provenance."""

    kind: ServiceKind
    configuration: ServiceConfiguration
    origins: Mapping[str, ServiceConfigurationOrigin]
    manifest: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "origins", MappingProxyType(dict(self.origins)))


@dataclass(frozen=True, slots=True)
class ServiceLaunchPlan:
    """Exact child command and environment, before process replacement."""

    argv: tuple[str, ...]
    environ: Mapping[str, str]

    def __post_init__(self) -> None:
        if not self.argv:
            raise ValueError("Service launch plan requires an executable.")
        object.__setattr__(self, "environ", MappingProxyType(dict(self.environ)))

    @property
    def command(self) -> str:
        return shlex.join(self.argv)


def service_kind(value: object) -> ServiceKind:
    """Validate one exact manifest kind."""

    if value not in _SERVICE_KINDS:
        choices = ", ".join(_SERVICE_KINDS)
        raise ConfigurationError(f"Service manifest kind must be one of: {choices}.")
    return value


def load_service_configuration(
    path: str | Path,
    *,
    overrides: Mapping[str, object] | None = None,
) -> ResolvedServiceConfiguration:
    """Load one private strict service manifest without side effects."""

    manifest, manifest_text = _read_manifest(Path(path))
    try:
        document = tomllib.loads(manifest_text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigurationError(f"Could not read service manifest {manifest}: {exc}") from exc
    if not isinstance(document, Mapping):
        raise ConfigurationError("Service manifest must be a TOML table.")

    version = document.get("version")
    if (
        isinstance(version, bool)
        or not isinstance(version, int)
        or version != SERVICE_CONFIGURATION_VERSION
    ):
        raise ConfigurationError(
            f"Service manifest version must be {SERVICE_CONFIGURATION_VERSION}; found {version!r}."
        )
    kind = service_kind(document.get("kind"))
    document = _apply_overrides(document, kind=kind, overrides=overrides)
    configuration: ServiceConfiguration
    if kind == "display-client":
        configuration, origins = _parse_display_client(document, manifest)
    else:
        configuration, origins = _parse_browser_kiosk(document, manifest)
    for field in overrides or {}:
        origins[field] = ServiceConfigurationOrigin("command-line", "command line")
    return ResolvedServiceConfiguration(kind, configuration, origins, manifest)


def service_launch_plan(
    resolved: ResolvedServiceConfiguration,
    *,
    executable: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
    preflight: bool = False,
) -> ServiceLaunchPlan:
    """Build the exact existing CLI invocation represented by a manifest."""

    program = os.fspath(executable if executable is not None else sys.argv[0])
    inherited = dict(os.environ if environ is None else environ)
    configuration = resolved.configuration
    if isinstance(configuration, DisplayClientServiceConfiguration):
        inherited.update(
            {
                "XDG_CONFIG_HOME": os.fspath(configuration.config_home),
                "XDG_STATE_HOME": os.fspath(configuration.state_home),
                "XDG_CACHE_HOME": os.fspath(configuration.cache_home),
            }
        )
        if preflight:
            argv = [
                program,
                "display-client-preflight",
                "--remote-profile",
                configuration.remote_profile,
                "--terminal",
                os.fspath(configuration.terminal),
                "--timeout",
                _number_text(configuration.timeout),
            ]
            if configuration.audio_playback or configuration.audio_device is not None:
                argv.append("--audio-playback")
            if configuration.audio_device is not None:
                argv.extend(("--audio-device", str(configuration.audio_device)))
            return ServiceLaunchPlan(tuple(argv), inherited)

        argv = [program]
        _append_logging_options(argv, configuration.log_level, configuration.log_file)
        argv.extend(("--color", configuration.color, "--theme", configuration.theme))
        argv.extend(
            (
                "tui",
                "--daemon-client",
                "--remote-profile",
                configuration.remote_profile,
                "--managed-display",
                "--startup-view",
                configuration.startup_view,
                "--daemon-timeout",
                _number_text(configuration.timeout),
                "--audio-directory",
                os.fspath(configuration.recording_directory),
                "--audio-buffer-ms",
                str(configuration.audio_buffer_ms),
                "--audio-history-limit",
                str(configuration.audio_history_limit),
            )
        )
        if configuration.recording_metadata:
            argv.append("--audio-metadata")
        if configuration.audio_playback:
            argv.append("--audio-playback")
        if configuration.audio_device is not None:
            argv.extend(("--audio-device", str(configuration.audio_device)))
        return ServiceLaunchPlan(tuple(argv), inherited)

    argv = [program]
    _append_logging_options(argv, configuration.log_level, configuration.log_file)
    argv.extend(
        (
            "browser-kiosk",
            "--origin",
            configuration.origin,
            "--ca-file",
            os.fspath(configuration.ca_file),
            "--role",
            configuration.role,
        )
    )
    if preflight:
        argv.append("--preflight-only")
    else:
        argv.extend(
            (
                "--browser",
                os.fspath(configuration.browser),
                "--profile-directory",
                os.fspath(configuration.profile_directory),
            )
        )
    return ServiceLaunchPlan(tuple(argv), inherited)


def service_configuration_inventory(
    resolved: ResolvedServiceConfiguration,
) -> dict[str, object]:
    """Return a machine-readable, credential-safe effective inventory."""

    values = _flatten_configuration(resolved.configuration)
    fields: dict[str, object] = {}
    for name in sorted(values):
        origin = resolved.origins[name]
        fields[name] = {
            "value": _json_value(values[name]),
            "source": origin.source,
            "location": origin.location,
        }
    return {
        "version": SERVICE_CONFIGURATION_VERSION,
        "kind": resolved.kind,
        "manifest": os.fspath(resolved.manifest),
        "fields": fields,
        "credentials": "not embedded",
    }


def render_service_configuration_inventory(
    resolved: ResolvedServiceConfiguration,
) -> str:
    """Render deterministic JSON provenance without reading credential bytes."""

    return json.dumps(service_configuration_inventory(resolved), indent=2, sort_keys=True) + "\n"


def service_configuration_template(kind: ServiceKind) -> str:
    """Return one valid review template; it never includes a real credential."""

    selected = service_kind(kind)
    if selected == "display-client":
        return _DISPLAY_CLIENT_TEMPLATE
    return _BROWSER_KIOSK_TEMPLATE


def write_service_configuration_template(
    path: str | Path,
    *,
    kind: ServiceKind,
) -> Path:
    """Atomically create one mode-0600 candidate without overwriting a file."""

    destination = Path(path)
    if not destination.is_absolute():
        raise ConfigurationError("Service template output must be an absolute path.")
    parent = destination.parent
    if not parent.is_dir() or parent.is_symlink() or parent.resolve() != parent:
        raise ConfigurationError(
            "Service template parent must be an existing absolute directory without symlinks."
        )
    if destination.exists() or destination.is_symlink():
        raise ConfigurationError("Service template output already exists; refusing overwrite.")

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, SERVICE_CONFIGURATION_MODE)
        payload = service_configuration_template(kind).encode("utf-8")
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, destination, follow_symlinks=False)
        temporary.unlink()
        directory_descriptor = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except FileExistsError as exc:
        raise ConfigurationError(
            "Service template output already exists; refusing overwrite."
        ) from exc
    except OSError as exc:
        raise ConfigurationError(f"Could not write service template: {exc}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        with suppress(FileNotFoundError):
            temporary.unlink()
    return destination


def configured_service_template(kind: ServiceKind) -> str:
    """Return the packaged thin systemd launcher for one service kind."""

    filename = (
        DISPLAY_CLIENT_SERVICE_FILENAME
        if service_kind(kind) == "display-client"
        else BROWSER_KIOSK_SERVICE_FILENAME
    )
    return files("sds200.service_assets").joinpath(filename).read_text(encoding="utf-8")


def _parse_display_client(
    document: Mapping[str, object], manifest: Path
) -> tuple[DisplayClientServiceConfiguration, dict[str, ServiceConfigurationOrigin]]:
    allowed_top = {
        "version",
        "kind",
        "paths",
        "connection",
        "display",
        "recording",
        "audio",
        "logging",
    }
    _reject_unknown(document, allowed_top, "display-client manifest")
    paths = _table(document, "paths", {"config_home", "state_home", "cache_home"})
    connection = _table(document, "connection", {"remote_profile", "timeout"})
    display = _table(document, "display", {"terminal", "startup_view", "theme", "color"})
    recording = _table(document, "recording", {"directory", "metadata"})
    audio = _table(document, "audio", {"playback", "device", "buffer_ms", "history_limit"})
    logging = _table(document, "logging", {"level", "file"})
    origins: dict[str, ServiceConfigurationOrigin] = {}
    location = os.fspath(manifest)

    def value(
        table: Mapping[str, object],
        key: str,
        field: str,
        default: object = _MISSING,
    ) -> object:
        if key in table:
            origins[field] = ServiceConfigurationOrigin("manifest", location)
            return table[key]
        if default is _MISSING:
            raise ConfigurationError(f"Display-client manifest requires {field}.")
        origins[field] = ServiceConfigurationOrigin("default")
        return default

    remote_profile = _nonblank_text(
        value(connection, "remote_profile", "connection.remote_profile"),
        "connection.remote_profile",
    )
    if len(remote_profile) > 128 or any(
        character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
        for character in remote_profile
    ):
        raise ConfigurationError(
            "connection.remote_profile must use 1-128 letters, digits, dot, dash or underscore."
        )
    startup_view = _choice(
        value(display, "startup_view", "display.startup_view", "dashboard"),
        _STARTUP_VIEWS,
        "display.startup_view",
    )
    color = _choice(
        value(display, "color", "display.color", "auto"),
        _COLOR_MODES,
        "display.color",
    )
    configuration = DisplayClientServiceConfiguration(
        remote_profile=remote_profile,
        config_home=_absolute_path(
            value(paths, "config_home", "paths.config_home", "/var/lib/sdsctl-display/.config"),
            "paths.config_home",
        ),
        state_home=_absolute_path(
            value(
                paths,
                "state_home",
                "paths.state_home",
                "/var/lib/sdsctl-display/.local/state",
            ),
            "paths.state_home",
        ),
        cache_home=_absolute_path(
            value(paths, "cache_home", "paths.cache_home", "/var/cache/sdsctl-display"),
            "paths.cache_home",
        ),
        terminal=_absolute_path(
            value(display, "terminal", "display.terminal", "/dev/tty1"),
            "display.terminal",
        ),
        timeout=_positive_number(
            value(connection, "timeout", "connection.timeout", 5.0),
            "connection.timeout",
        ),
        startup_view=cast(TuiStartupView, startup_view),
        theme=_identifier(value(display, "theme", "display.theme", "dark"), "display.theme"),
        color=cast(ColorMode, color),
        recording_directory=_absolute_path(
            value(
                recording,
                "directory",
                "recording.directory",
                "/var/lib/sdsctl-display/recordings",
            ),
            "recording.directory",
        ),
        recording_metadata=_boolean(
            value(recording, "metadata", "recording.metadata", True),
            "recording.metadata",
        ),
        audio_playback=_boolean(
            value(audio, "playback", "audio.playback", False), "audio.playback"
        ),
        audio_device=_audio_device(value(audio, "device", "audio.device", None), "audio.device"),
        audio_buffer_ms=_positive_integer(
            value(audio, "buffer_ms", "audio.buffer_ms", 250), "audio.buffer_ms"
        ),
        audio_history_limit=_positive_integer(
            value(audio, "history_limit", "audio.history_limit", 100),
            "audio.history_limit",
        ),
        log_level=_log_level(value(logging, "level", "logging.level", None)),
        log_file=_optional_absolute_path(
            value(logging, "file", "logging.file", None), "logging.file"
        ),
    )
    return configuration, origins


def _parse_browser_kiosk(
    document: Mapping[str, object], manifest: Path
) -> tuple[BrowserKioskServiceConfiguration, dict[str, ServiceConfigurationOrigin]]:
    _reject_unknown(
        document,
        {"version", "kind", "server", "kiosk", "logging"},
        "browser-kiosk manifest",
    )
    server = _table(document, "server", {"origin", "ca_file"})
    kiosk = _table(document, "kiosk", {"browser", "role", "profile_directory"})
    logging = _table(document, "logging", {"level", "file"})
    origins: dict[str, ServiceConfigurationOrigin] = {}
    location = os.fspath(manifest)

    def value(
        table: Mapping[str, object],
        key: str,
        field: str,
        default: object = _MISSING,
    ) -> object:
        if key in table:
            origins[field] = ServiceConfigurationOrigin("manifest", location)
            return table[key]
        if default is _MISSING:
            raise ConfigurationError(f"Browser-kiosk manifest requires {field}.")
        origins[field] = ServiceConfigurationOrigin("default")
        return default

    configuration = BrowserKioskServiceConfiguration(
        origin=kiosk_origin(
            _nonblank_text(value(server, "origin", "server.origin"), "server.origin")
        ),
        ca_file=_absolute_path(value(server, "ca_file", "server.ca_file"), "server.ca_file"),
        browser=_absolute_path(value(kiosk, "browser", "kiosk.browser"), "kiosk.browser"),
        role=kiosk_role(
            _nonblank_text(value(kiosk, "role", "kiosk.role", "display"), "kiosk.role")
        ),
        profile_directory=_absolute_path(
            value(kiosk, "profile_directory", "kiosk.profile_directory"),
            "kiosk.profile_directory",
        ),
        log_level=_log_level(value(logging, "level", "logging.level", None)),
        log_file=_optional_absolute_path(
            value(logging, "file", "logging.file", None), "logging.file"
        ),
    )
    return configuration, origins


def _read_manifest(path: Path) -> tuple[Path, str]:
    """Read one private regular manifest through a stable no-follow descriptor."""

    if not path.is_absolute():
        raise ConfigurationError("Service manifest path must be absolute.")
    descriptor: int | None = None
    try:
        if path.is_symlink() or path.resolve() != path:
            raise ConfigurationError("Service manifest path must not contain symlinks.")
        initial = path.lstat()
        _validate_manifest_metadata(initial)
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(descriptor)
        _validate_manifest_metadata(opened)
        if (initial.st_dev, initial.st_ino) != (opened.st_dev, opened.st_ino):
            raise ConfigurationError("Service manifest changed while being opened.")

        raw = bytearray()
        while len(raw) <= SERVICE_CONFIGURATION_MAX_BYTES:
            chunk = os.read(
                descriptor,
                min(8192, SERVICE_CONFIGURATION_MAX_BYTES + 1 - len(raw)),
            )
            if not chunk:
                break
            raw.extend(chunk)

        after = os.fstat(descriptor)
        final = path.lstat()
        _validate_manifest_metadata(after)
        _validate_manifest_metadata(final)
        snapshots = {_manifest_snapshot(observed) for observed in (initial, opened, after, final)}
        if len(snapshots) != 1:
            raise ConfigurationError("Service manifest changed while being read.")
        if len(raw) > SERVICE_CONFIGURATION_MAX_BYTES:
            raise ConfigurationError("Service manifest exceeds the 64 KiB size limit.")
        try:
            return path, bytes(raw).decode("utf-8")
        except UnicodeError as exc:
            raise ConfigurationError("Service manifest must be valid UTF-8.") from exc
    except ConfigurationError:
        raise
    except OSError as exc:
        raise ConfigurationError(f"Service manifest is unavailable: {exc}") from exc
    finally:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)


def _validate_manifest_metadata(observed: os.stat_result) -> None:
    if stat.S_ISLNK(observed.st_mode):
        raise ConfigurationError("Service manifest path must not contain symlinks.")
    if not stat.S_ISREG(observed.st_mode):
        raise ConfigurationError("Service manifest must be a regular file.")
    if stat.S_IMODE(observed.st_mode) != SERVICE_CONFIGURATION_MODE:
        raise ConfigurationError("Service manifest must have exact mode 0600.")
    if observed.st_size > SERVICE_CONFIGURATION_MAX_BYTES:
        raise ConfigurationError("Service manifest exceeds the 64 KiB size limit.")


def _manifest_snapshot(observed: os.stat_result) -> tuple[int, ...]:
    return (
        observed.st_dev,
        observed.st_ino,
        observed.st_mode,
        observed.st_size,
        observed.st_mtime_ns,
        observed.st_ctime_ns,
    )


def _apply_overrides(
    document: Mapping[str, object],
    *,
    kind: ServiceKind,
    overrides: Mapping[str, object] | None,
) -> dict[str, object]:
    """Apply validated flat overrides without weakening strict schema parsing."""

    updated = dict(document)
    if not overrides:
        return updated
    allowed = _DISPLAY_OVERRIDE_FIELDS if kind == "display-client" else _BROWSER_OVERRIDE_FIELDS
    for field, value in overrides.items():
        if field not in allowed:
            raise ConfigurationError(
                f"Service manifest kind {kind!r} does not support override field {field!r}."
            )
        table_name, key = field.split(".", maxsplit=1)
        raw_table = updated.get(table_name, {})
        if not isinstance(raw_table, Mapping):
            raise ConfigurationError(
                f"Service manifest [{table_name}] must be a table before overrides."
            )
        table = dict(raw_table)
        table[key] = value
        updated[table_name] = table
    return updated


def _table(document: Mapping[str, object], name: str, allowed: set[str]) -> Mapping[str, object]:
    raw = document.get(name, {})
    if not isinstance(raw, Mapping):
        raise ConfigurationError(f"Service manifest [{name}] must be a table.")
    _reject_unknown(raw, allowed, f"service manifest [{name}]")
    return raw


def _reject_unknown(values: Mapping[str, object], allowed: set[str], label: str) -> None:
    unknown = sorted(str(field) for field in values if field not in allowed)
    if unknown:
        names = ", ".join(repr(field) for field in unknown)
        raise ConfigurationError(f"{label} contains unsupported field(s): {names}.")


def _nonblank_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"{label} must be nonblank text.")
    return value.strip()


def _choice(value: object, choices: tuple[str, ...], label: str) -> str:
    normalized = _nonblank_text(value, label).lower()
    if normalized not in choices:
        raise ConfigurationError(f"{label} must be one of: {', '.join(choices)}.")
    return normalized


def _identifier(value: object, label: str) -> str:
    normalized = _nonblank_text(value, label).lower()
    if (
        any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-" for character in normalized)
        or normalized.startswith("-")
        or normalized.endswith("-")
        or "--" in normalized
    ):
        raise ConfigurationError(f"{label} must be a lowercase kebab-case identifier.")
    return normalized


def _absolute_path(value: object, label: str) -> Path:
    text = _nonblank_text(value, label)
    path = Path(text)
    if not path.is_absolute():
        raise ConfigurationError(f"{label} must be an absolute path.")
    return path


def _optional_absolute_path(value: object, label: str) -> Path | None:
    if value is None:
        return None
    return _absolute_path(value, label)


def _boolean(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigurationError(f"{label} must be true or false.")
    return value


def _positive_integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigurationError(f"{label} must be a positive integer.")
    return value


def _positive_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigurationError(f"{label} must be a positive number.")
    normalized = float(value)
    if not isfinite(normalized) or normalized <= 0:
        raise ConfigurationError(f"{label} must be a finite positive number.")
    return normalized


def _audio_device(value: object, label: str) -> str | int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ConfigurationError(f"{label} must be a device name or non-negative index.")
    if isinstance(value, int):
        if value < 0:
            raise ConfigurationError(f"{label} index must not be negative.")
        return value
    return _nonblank_text(value, label)


def _log_level(value: object) -> str | None:
    if value is None:
        return None
    normalized = _nonblank_text(value, "logging.level").upper()
    if normalized not in LOG_LEVEL_NAMES:
        raise ConfigurationError(f"logging.level must be one of: {', '.join(LOG_LEVEL_NAMES)}.")
    return normalized


def _append_logging_options(argv: list[str], level: str | None, path: Path | None) -> None:
    if level is not None:
        argv.extend(("--log-level", level))
    if path is not None:
        argv.extend(("--log-file", os.fspath(path)))


def _number_text(value: float) -> str:
    return format(value, "g")


def _flatten_configuration(configuration: ServiceConfiguration) -> dict[str, object]:
    if isinstance(configuration, DisplayClientServiceConfiguration):
        return {
            "audio.buffer_ms": configuration.audio_buffer_ms,
            "audio.device": configuration.audio_device,
            "audio.history_limit": configuration.audio_history_limit,
            "audio.playback": configuration.audio_playback,
            "connection.remote_profile": configuration.remote_profile,
            "connection.timeout": configuration.timeout,
            "display.color": configuration.color,
            "display.startup_view": configuration.startup_view,
            "display.terminal": configuration.terminal,
            "display.theme": configuration.theme,
            "logging.file": configuration.log_file,
            "logging.level": configuration.log_level,
            "paths.cache_home": configuration.cache_home,
            "paths.config_home": configuration.config_home,
            "paths.state_home": configuration.state_home,
            "recording.directory": configuration.recording_directory,
            "recording.metadata": configuration.recording_metadata,
        }
    return {
        "kiosk.browser": configuration.browser,
        "kiosk.profile_directory": configuration.profile_directory,
        "kiosk.role": configuration.role,
        "logging.file": configuration.log_file,
        "logging.level": configuration.log_level,
        "server.ca_file": configuration.ca_file,
        "server.origin": configuration.origin,
    }


def _json_value(value: object) -> object:
    if isinstance(value, Path):
        return os.fspath(value)
    return value


_DISPLAY_CLIENT_TEMPLATE = """\
# Private host manifest for one observe-only managed TUI display.
version = 1
kind = "display-client"

[paths]
config_home = "/var/lib/sdsctl-display/.config"
state_home = "/var/lib/sdsctl-display/.local/state"
cache_home = "/var/cache/sdsctl-display"

[connection]
remote_profile = "REPLACE-WITH-OBSERVE-ONLY-PROFILE"
timeout = 5.0

[display]
terminal = "/dev/tty1"
startup_view = "dashboard"
theme = "dark"
color = "auto"

[recording]
directory = "/var/lib/sdsctl-display/recordings"
metadata = true

[audio]
playback = false
# device = "REPLACE-WITH-PORTAUDIO-DEVICE-NAME"
buffer_ms = 250
history_limit = 100

[logging]
# level = "INFO"
# file = "/var/lib/sdsctl-display/sdsctl.log"
"""


_BROWSER_KIOSK_TEMPLATE = """\
# Private host manifest for one role-bound Chromium kiosk.
version = 1
kind = "browser-kiosk"

[server]
origin = "https://scanner.example:8443"
ca_file = "/absolute/path/to/server.crt"

[kiosk]
browser = "/usr/bin/chromium"
role = "display"
profile_directory = "/home/display/.sdsctl-display-kiosk"

[logging]
# level = "INFO"
# file = "/home/display/.local/state/sdsctl/kiosk.log"

# Device enrollment is intentionally absent from schema version 1. Add it only
# after the server-side per-device authentication protocol is accepted.
"""


__all__ = [
    "BROWSER_KIOSK_SERVICE_FILENAME",
    "BrowserKioskServiceConfiguration",
    "DISPLAY_CLIENT_SERVICE_FILENAME",
    "DisplayClientServiceConfiguration",
    "ResolvedServiceConfiguration",
    "SERVICE_CONFIGURATION_MAX_BYTES",
    "SERVICE_CONFIGURATION_MODE",
    "SERVICE_CONFIGURATION_VERSION",
    "ServiceConfiguration",
    "ServiceConfigurationOrigin",
    "ServiceKind",
    "ServiceLaunchPlan",
    "configured_service_template",
    "load_service_configuration",
    "render_service_configuration_inventory",
    "service_configuration_inventory",
    "service_configuration_template",
    "service_kind",
    "service_launch_plan",
    "write_service_configuration_template",
]
