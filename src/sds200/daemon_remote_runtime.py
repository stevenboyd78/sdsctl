"""Packaged lifecycle owner for the authenticated remote daemon service."""

from __future__ import annotations

import threading
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

from .daemon_api import DaemonReadOnlyApi
from .daemon_event_stream import DaemonEventStream
from .daemon_events import DAEMON_EVENT_DEFAULT_MAX_BYTES
from .daemon_remote import (
    DaemonRemoteConfigurationPreflight,
    DaemonRemoteListenerConfiguration,
    load_daemon_remote_configuration,
    preflight_daemon_remote_configuration,
)
from .daemon_remote_observation import (
    DAEMON_REMOTE_OBSERVATION_DEFAULT_MAX_LEASES,
    DAEMON_REMOTE_OBSERVATION_DEFAULT_MAX_LEASES_PER_CLIENT,
    DaemonRemoteObservationBroker,
)
from .daemon_remote_server import DaemonRemoteTcpListener
from .daemon_remote_service_server import (
    DAEMON_REMOTE_SERVICE_DEFAULT_MAX_CLIENTS,
    DaemonRemoteServiceRouter,
)
from .daemon_server import (
    DAEMON_API_DEFAULT_CLIENT_TIMEOUT,
    DAEMON_API_DEFAULT_MAX_CLIENTS,
    DAEMON_API_DEFAULT_MAX_REQUEST_BYTES,
    DAEMON_API_DEFAULT_MAX_RESPONSE_BYTES,
    DAEMON_API_DEFAULT_SHUTDOWN_TIMEOUT,
    DaemonApiServer,
)
from .daemon_waterfall_protocol import DAEMON_WATERFALL_DEFAULT_MAX_RECORD_BYTES
from .exceptions import ConfigurationError
from .pcmu_protocol import (
    PCMU_STREAM_DEFAULT_MAX_ENDPOINT_BYTES,
    PCMU_STREAM_DEFAULT_MAX_FRAME_BYTES,
)
from .pcmu_stream import PcmuStream
from .waterfall_session import WaterfallSession


@dataclass(frozen=True, slots=True)
class PackagedDaemonRemoteServiceSnapshot:
    """Redacted aggregate state for one packaged remote service."""

    active: bool
    preflight: DaemonRemoteConfigurationPreflight
    listener: dict[str, object]
    credentials: dict[str, object] | None
    router: dict[str, object]
    observations: dict[str, object]
    api: dict[str, object]

    def as_dict(self) -> dict[str, object]:
        return {
            "active": self.active,
            "preflight": {
                "enabled": self.preflight.enabled,
                "certificate_bytes": self.preflight.certificate_bytes,
                "private_key_bytes": self.preflight.private_key_bytes,
                "active_credentials": self.preflight.active_credentials,
                "revoked_credentials": self.preflight.revoked_credentials,
            },
            "listener": dict(self.listener),
            "credentials": (
                None if self.credentials is None else dict(self.credentials)
            ),
            "router": dict(self.router),
            "observations": dict(self.observations),
            "api": dict(self.api),
        }


@dataclass(slots=True, repr=False)
class PackagedDaemonRemoteService:
    """Start, stop, diagnose, and reload one existing remote service graph."""

    configuration_path: Path = field(repr=False)
    preflight: DaemonRemoteConfigurationPreflight
    listener: DaemonRemoteTcpListener = field(repr=False)
    observations: DaemonRemoteObservationBroker = field(repr=False)
    router: DaemonRemoteServiceRouter = field(repr=False)
    api_server: DaemonApiServer = field(repr=False)
    _lock: threading.RLock = field(
        default_factory=threading.RLock,
        init=False,
        repr=False,
    )
    _started: bool = field(default=False, init=False, repr=False)
    _stopped: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.configuration_path, Path):
            raise TypeError(
                "Packaged remote daemon configuration path must be a pathlib.Path."
            )
        if not isinstance(self.preflight, DaemonRemoteConfigurationPreflight):
            raise TypeError(
                "Packaged remote daemon service requires validated preflight."
            )
        if not self.preflight.enabled:
            raise ValueError(
                "Packaged remote daemon service requires enabled preflight."
            )

    def __repr__(self) -> str:
        return (
            "PackagedDaemonRemoteService("
            f"active={self.active}, port={self.listener.snapshot().port})"
        )

    @property
    def active(self) -> bool:
        with self._lock:
            return self._started and not self._stopped and self.api_server.active

    def start(self) -> None:
        with self._lock:
            if self._started:
                if self._stopped:
                    raise RuntimeError(
                        "Packaged remote daemon services cannot restart after shutdown."
                    )
                return
            self._started = True
        try:
            self.api_server.start()
        except BaseException:
            with self._lock:
                self._stopped = True
            with suppress(BaseException):
                self.api_server.stop()
            with suppress(BaseException):
                self.observations.close()
            raise

    def stop(self) -> None:
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
        failures: list[BaseException] = []
        try:
            self.api_server.stop()
        except BaseException as error:
            failures.append(error)
        try:
            self.observations.close()
        except BaseException as error:
            failures.append(error)
        if failures:
            raise failures[0]

    def reload(self) -> PackagedDaemonRemoteServiceSnapshot:
        """Reload only client credentials while retaining listener identity."""

        with self._lock:
            if not self._started or self._stopped:
                raise RuntimeError(
                    "Packaged remote daemon service is not active."
                )
            configuration = load_daemon_remote_configuration(
                self.configuration_path
            )
            if configuration is None or not configuration.enabled:
                raise ConfigurationError(
                    "Remote daemon credential reload requires an enabled "
                    "configuration."
                )
            preflight = preflight_daemon_remote_configuration(configuration)
            self.listener.reload_credentials(configuration)
            self.preflight = preflight
            return self.snapshot()

    def snapshot(self) -> PackagedDaemonRemoteServiceSnapshot:
        listener = self.listener.snapshot()
        credentials = self.listener.credential_snapshot()
        return PackagedDaemonRemoteServiceSnapshot(
            active=self.active,
            preflight=self.preflight,
            listener=listener.as_dict(),
            credentials=(
                None if credentials is None else credentials.as_dict()
            ),
            router=self.router.snapshot().as_dict(),
            observations=self.observations.snapshot().as_dict(),
            api=self.api_server.snapshot().as_dict(),
        )


def build_packaged_daemon_remote_service(
    configuration_path: Path,
    configuration: DaemonRemoteListenerConfiguration,
    *,
    api: DaemonReadOnlyApi,
    event_stream: DaemonEventStream,
    waterfall_session: WaterfallSession | None,
    pcmu_stream: PcmuStream | None,
    api_max_clients: int = DAEMON_API_DEFAULT_MAX_CLIENTS,
    api_max_request_bytes: int = DAEMON_API_DEFAULT_MAX_REQUEST_BYTES,
    api_max_response_bytes: int = DAEMON_API_DEFAULT_MAX_RESPONSE_BYTES,
    api_client_timeout: float = DAEMON_API_DEFAULT_CLIENT_TIMEOUT,
    api_shutdown_timeout: float = DAEMON_API_DEFAULT_SHUTDOWN_TIMEOUT,
    max_remote_clients: int = DAEMON_REMOTE_SERVICE_DEFAULT_MAX_CLIENTS,
    max_event_bytes: int = DAEMON_EVENT_DEFAULT_MAX_BYTES,
    max_waterfall_record_bytes: int = DAEMON_WATERFALL_DEFAULT_MAX_RECORD_BYTES,
    max_audio_endpoint_bytes: int = PCMU_STREAM_DEFAULT_MAX_ENDPOINT_BYTES,
    max_audio_frame_bytes: int = PCMU_STREAM_DEFAULT_MAX_FRAME_BYTES,
    max_observation_leases: int = DAEMON_REMOTE_OBSERVATION_DEFAULT_MAX_LEASES,
    max_observation_leases_per_client: int = (
        DAEMON_REMOTE_OBSERVATION_DEFAULT_MAX_LEASES_PER_CLIENT
    ),
) -> PackagedDaemonRemoteService:
    """Build one unstarted service graph from an enabled strict configuration."""

    if not isinstance(configuration_path, Path):
        raise TypeError(
            "Packaged remote daemon configuration path must be a pathlib.Path."
        )
    if not isinstance(configuration, DaemonRemoteListenerConfiguration):
        raise TypeError(
            "Packaged remote daemon service requires remote listener configuration."
        )
    if not configuration.enabled:
        raise ValueError(
            "Packaged remote daemon service requires enabled configuration."
        )

    preflight = preflight_daemon_remote_configuration(configuration)
    listener: DaemonRemoteTcpListener | None = None
    observations: DaemonRemoteObservationBroker | None = None
    router: DaemonRemoteServiceRouter | None = None
    api_server: DaemonApiServer | None = None
    try:
        listener = DaemonRemoteTcpListener(configuration)
        observations = DaemonRemoteObservationBroker(
            event_stream=event_stream,
            waterfall_session=waterfall_session,
            pcmu_stream=pcmu_stream,
            max_leases=max_observation_leases,
            max_leases_per_client=max_observation_leases_per_client,
        )
        router = DaemonRemoteServiceRouter(
            listener,
            observations,
            max_clients=max_remote_clients,
            max_event_bytes=max_event_bytes,
            max_waterfall_record_bytes=max_waterfall_record_bytes,
            max_audio_endpoint_bytes=max_audio_endpoint_bytes,
            max_audio_frame_bytes=max_audio_frame_bytes,
        )
        api_server = DaemonApiServer(
            router,
            api,
            max_clients=api_max_clients,
            max_request_bytes=api_max_request_bytes,
            max_response_bytes=api_max_response_bytes,
            client_timeout=api_client_timeout,
            shutdown_timeout=api_shutdown_timeout,
        )
    except BaseException:
        if api_server is not None:
            with suppress(BaseException):
                api_server.stop()
        elif router is not None:
            with suppress(BaseException):
                router.stop()
        elif listener is not None:
            with suppress(BaseException):
                listener.stop()
        if observations is not None:
            with suppress(BaseException):
                observations.close()
        raise

    assert listener is not None
    assert observations is not None
    assert router is not None
    assert api_server is not None
    return PackagedDaemonRemoteService(
        configuration_path=configuration_path,
        preflight=preflight,
        listener=listener,
        observations=observations,
        router=router,
        api_server=api_server,
    )


__all__ = [
    "PackagedDaemonRemoteService",
    "PackagedDaemonRemoteServiceSnapshot",
    "build_packaged_daemon_remote_service",
]
