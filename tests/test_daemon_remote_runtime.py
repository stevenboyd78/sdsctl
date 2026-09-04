from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from sds200 import (
    ConfigurationError,
    DaemonRemoteClientIdentity,
    DaemonRemoteConfigurationPreflight,
    DaemonRemoteListenerConfiguration,
    PackagedDaemonRemoteService,
)
from sds200 import daemon_remote_runtime as runtime_module


@dataclass
class FakeSnapshot:
    values: dict[str, object]
    port: int = 50443

    def as_dict(self) -> dict[str, object]:
        return dict(self.values)


class FakeListener:
    def __init__(self) -> None:
        self.reload_calls: list[DaemonRemoteListenerConfiguration] = []
        self.stop_calls = 0

    def snapshot(self) -> FakeSnapshot:
        return FakeSnapshot({"active": True, "port": 50443})

    def credential_snapshot(self) -> FakeSnapshot:
        return FakeSnapshot({"generation": 2})

    def reload_credentials(
        self,
        configuration: DaemonRemoteListenerConfiguration,
    ) -> None:
        self.reload_calls.append(configuration)

    def stop(self) -> None:
        self.stop_calls += 1


class FakeComponent:
    def __init__(self, values: dict[str, object] | None = None) -> None:
        self.values = values or {"active": True}
        self.close_calls = 0
        self.stop_calls = 0

    def snapshot(self) -> FakeSnapshot:
        return FakeSnapshot(self.values)

    def close(self) -> None:
        self.close_calls += 1

    def stop(self) -> None:
        self.stop_calls += 1


class FakeApiServer(FakeComponent):
    def __init__(self, *, start_error: BaseException | None = None) -> None:
        super().__init__()
        self.start_error = start_error
        self.start_calls = 0
        self.active = False

    def start(self) -> None:
        self.start_calls += 1
        if self.start_error is not None:
            raise self.start_error
        self.active = True

    def stop(self) -> None:
        super().stop()
        self.active = False


def _preflight(*, active_credentials: int = 1) -> DaemonRemoteConfigurationPreflight:
    return DaemonRemoteConfigurationPreflight(
        enabled=True,
        certificate_bytes=100,
        private_key_bytes=200,
        active_credentials=active_credentials,
        revoked_credentials=0,
    )


def _configuration(tmp_path: Path) -> DaemonRemoteListenerConfiguration:
    return DaemonRemoteListenerConfiguration(
        enabled=True,
        bind_address="192.168.20.10",
        port=50443,
        certificate_file=tmp_path / "server.crt",
        private_key_file=tmp_path / "server.key",
        clients=(
            DaemonRemoteClientIdentity(
                "pi-display",
                tmp_path / "pi-display.secret",
            ),
        ),
    )


def _service(
    tmp_path: Path,
    *,
    api_server: FakeApiServer | None = None,
) -> tuple[
    PackagedDaemonRemoteService,
    FakeListener,
    FakeComponent,
    FakeComponent,
    FakeApiServer,
]:
    listener = FakeListener()
    observations = FakeComponent({"leases": 0})
    router = FakeComponent({"clients": 0})
    server = api_server or FakeApiServer()
    service = PackagedDaemonRemoteService(
        configuration_path=tmp_path / "private-remote.toml",
        preflight=_preflight(),
        listener=listener,  # type: ignore[arg-type]
        observations=observations,  # type: ignore[arg-type]
        router=router,  # type: ignore[arg-type]
        api_server=server,  # type: ignore[arg-type]
    )
    return service, listener, observations, router, server


def test_packaged_remote_service_starts_snapshots_and_stops_exact_graph(
    tmp_path: Path,
) -> None:
    service, _, observations, _, api_server = _service(tmp_path)

    service.start()
    service.start()
    snapshot = service.snapshot().as_dict()

    assert service.active
    assert api_server.start_calls == 1
    assert snapshot["active"] is True
    assert snapshot["preflight"] == {
        "enabled": True,
        "certificate_bytes": 100,
        "private_key_bytes": 200,
        "active_credentials": 1,
        "revoked_credentials": 0,
    }
    rendered = repr(service)
    assert "private-remote.toml" not in rendered
    assert "192.168.20.10" not in rendered

    service.stop()
    service.stop()
    assert not service.active
    assert api_server.stop_calls == 1
    assert observations.close_calls == 1
    with pytest.raises(RuntimeError):
        service.start()


def test_packaged_remote_service_start_failure_closes_partial_graph(
    tmp_path: Path,
) -> None:
    failure = RuntimeError("private startup detail")
    service, _, observations, _, api_server = _service(
        tmp_path,
        api_server=FakeApiServer(start_error=failure),
    )

    with pytest.raises(RuntimeError) as raised:
        service.start()

    assert raised.value is failure
    assert not service.active
    assert api_server.stop_calls == 1
    assert observations.close_calls == 1


def test_packaged_remote_service_reload_commits_only_after_listener_reload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, listener, _, _, _ = _service(tmp_path)
    replacement = _configuration(tmp_path)
    replacement_preflight = _preflight(active_credentials=2)
    monkeypatch.setattr(
        runtime_module,
        "load_daemon_remote_configuration",
        lambda path: replacement,
    )
    monkeypatch.setattr(
        runtime_module,
        "preflight_daemon_remote_configuration",
        lambda configuration: replacement_preflight,
    )
    service.start()

    snapshot = service.reload()

    assert listener.reload_calls == [replacement]
    assert service.preflight is replacement_preflight
    assert snapshot.preflight.active_credentials == 2


def test_packaged_remote_service_reload_failure_preserves_last_known_good(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, listener, _, _, _ = _service(tmp_path)
    original = service.preflight
    monkeypatch.setattr(
        runtime_module,
        "load_daemon_remote_configuration",
        lambda path: None,
    )
    service.start()

    with pytest.raises(ConfigurationError) as raised:
        service.reload()

    assert service.preflight is original
    assert listener.reload_calls == []
    assert str(service.configuration_path) not in str(raised.value)


def test_packaged_remote_service_requires_enabled_validated_preflight(
    tmp_path: Path,
) -> None:
    _, listener, observations, router, server = _service(tmp_path)

    with pytest.raises(ValueError, match="enabled preflight"):
        PackagedDaemonRemoteService(
            configuration_path=tmp_path / "remote.toml",
            preflight=DaemonRemoteConfigurationPreflight(enabled=False),
            listener=listener,  # type: ignore[arg-type]
            observations=observations,  # type: ignore[arg-type]
            router=router,  # type: ignore[arg-type]
            api_server=server,  # type: ignore[arg-type]
        )


def test_packaged_remote_service_factory_reuses_exact_daemon_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configuration = _configuration(tmp_path)
    configuration_path = tmp_path / "private-remote.toml"
    preflight = _preflight()
    daemon_api = SimpleNamespace(remote_clients_provider=None)
    event_stream = object()
    waterfall_session = object()
    pcmu_stream = object()
    constructed: dict[str, object] = {}

    class ConstructedListener(FakeListener):
        def __init__(self, selected: object) -> None:
            super().__init__()
            constructed["listener_configuration"] = selected

    class ConstructedObservations(FakeComponent):
        def __init__(self, **kwargs: object) -> None:
            super().__init__()
            constructed["observation_options"] = kwargs

    class ConstructedRouter(FakeComponent):
        def connected_clients_snapshot(self) -> dict[str, object]:
            return {"active": True, "clients": []}

        def __init__(
            self,
            listener: object,
            observations: object,
            **kwargs: object,
        ) -> None:
            super().__init__()
            constructed["router_listener"] = listener
            constructed["router_observations"] = observations
            constructed["router_options"] = kwargs

    class ConstructedApiServer(FakeApiServer):
        def __init__(
            self,
            listener: object,
            api: object,
            **kwargs: object,
        ) -> None:
            super().__init__()
            constructed["api_listener"] = listener
            constructed["api"] = api
            constructed["api_options"] = kwargs

    monkeypatch.setattr(
        runtime_module,
        "preflight_daemon_remote_configuration",
        lambda selected: preflight,
    )
    monkeypatch.setattr(
        runtime_module,
        "DaemonRemoteTcpListener",
        ConstructedListener,
    )
    monkeypatch.setattr(
        runtime_module,
        "DaemonRemoteObservationBroker",
        ConstructedObservations,
    )
    monkeypatch.setattr(
        runtime_module,
        "DaemonRemoteServiceRouter",
        ConstructedRouter,
    )
    monkeypatch.setattr(runtime_module, "DaemonApiServer", ConstructedApiServer)

    service = runtime_module.build_packaged_daemon_remote_service(
        configuration_path,
        configuration,
        api=daemon_api,  # type: ignore[arg-type]
        event_stream=event_stream,  # type: ignore[arg-type]
        waterfall_session=waterfall_session,  # type: ignore[arg-type]
        pcmu_stream=pcmu_stream,  # type: ignore[arg-type]
        api_max_clients=3,
        max_remote_clients=4,
        max_observation_leases=5,
        max_observation_leases_per_client=2,
    )
    assert daemon_api.remote_clients_provider() == {"active": True, "clients": []}

    assert service.configuration_path == configuration_path
    assert service.preflight is preflight
    assert constructed["listener_configuration"] is configuration
    assert constructed["observation_options"] == {
        "event_stream": event_stream,
        "waterfall_session": waterfall_session,
        "pcmu_stream": pcmu_stream,
        "max_leases": 5,
        "max_leases_per_client": 2,
    }
    assert constructed["router_listener"] is service.listener
    assert constructed["router_observations"] is service.observations
    assert constructed["router_options"]["max_clients"] == 4  # type: ignore[index]
    assert constructed["api_listener"] is service.router
    assert constructed["api"] is daemon_api
    assert constructed["api_options"]["max_clients"] == 3  # type: ignore[index]


def test_packaged_remote_service_factory_closes_partial_construction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    listener = FakeListener()
    observations = FakeComponent()
    failure = RuntimeError("private constructor detail")

    monkeypatch.setattr(
        runtime_module,
        "preflight_daemon_remote_configuration",
        lambda selected: _preflight(),
    )
    monkeypatch.setattr(
        runtime_module,
        "DaemonRemoteTcpListener",
        lambda selected: listener,
    )
    monkeypatch.setattr(
        runtime_module,
        "DaemonRemoteObservationBroker",
        lambda **kwargs: observations,
    )

    def fail_router(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise failure

    monkeypatch.setattr(runtime_module, "DaemonRemoteServiceRouter", fail_router)

    with pytest.raises(RuntimeError) as raised:
        runtime_module.build_packaged_daemon_remote_service(
            tmp_path / "private-remote.toml",
            _configuration(tmp_path),
            api=object(),  # type: ignore[arg-type]
            event_stream=object(),  # type: ignore[arg-type]
            waterfall_session=None,
            pcmu_stream=None,
        )

    assert raised.value is failure
    assert listener.stop_calls == 1
    assert observations.close_calls == 1
