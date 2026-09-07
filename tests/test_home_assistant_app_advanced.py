from __future__ import annotations

import shutil
import subprocess
from collections.abc import Mapping

import pytest

from sds200.exceptions import SDS200Error
from sds200.home_assistant_app import (
    HOME_ASSISTANT_APP_NATIVE_DASHBOARD_PORT_KEY,
    HOME_ASSISTANT_APP_REMOTE_DAEMON_PORT_KEY,
    HOME_ASSISTANT_SUPERVISOR_TOKEN_VARIABLE,
    HomeAssistantAppAdvancedExposure,
    HomeAssistantAppOptions,
    HomeAssistantAppSupervisorInfo,
    fetch_home_assistant_app_supervisor_info,
    parse_home_assistant_app_supervisor_info_response,
    reconcile_home_assistant_app_advanced_exposure,
)
from sds200.home_assistant_app_advanced import generate_home_assistant_app_server_identity


def supervisor_info_payload(
    *,
    address: object = "172.30.33.7",
    remote_port: object = None,
    native_port: object = None,
    options: Mapping[str, object] | None = None,
) -> dict[str, object]:
    return {
        "result": "ok",
        "data": {
            "ip_address": address,
            "network": {
                "50000/udp": 50000,
                HOME_ASSISTANT_APP_REMOTE_DAEMON_PORT_KEY: remote_port,
                HOME_ASSISTANT_APP_NATIVE_DASHBOARD_PORT_KEY: native_port,
            },
            "options": {} if options is None else dict(options),
        },
    }


def test_parse_home_assistant_app_supervisor_info_accepts_disabled_ports() -> None:
    info = parse_home_assistant_app_supervisor_info_response(
        supervisor_info_payload()
    )

    assert info == HomeAssistantAppSupervisorInfo(
        container_address="172.30.33.7",
        network={
            "50000/udp": 50000,
            HOME_ASSISTANT_APP_REMOTE_DAEMON_PORT_KEY: None,
            HOME_ASSISTANT_APP_NATIVE_DASHBOARD_PORT_KEY: None,
        },
    )


def test_fetch_home_assistant_app_supervisor_info_uses_self_endpoint() -> None:
    calls: list[tuple[str, str, float]] = []

    def requester(url: str, token: str, timeout: float) -> object:
        calls.append((url, token, timeout))
        return supervisor_info_payload()

    info = fetch_home_assistant_app_supervisor_info(
        environ={HOME_ASSISTANT_SUPERVISOR_TOKEN_VARIABLE: "token-value"},
        requester=requester,
    )

    assert info.container_address == "172.30.33.7"
    assert calls == [
        ("http://supervisor/addons/self/info", "token-value", 5.0)
    ]


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"result": "error"},
        {"result": "ok", "data": []},
        supervisor_info_payload(address="0.0.0.0"),
        supervisor_info_payload(address="203.0.113.7"),
        supervisor_info_payload(remote_port=True),
        supervisor_info_payload(native_port=70000),
    ],
)
def test_parse_home_assistant_app_supervisor_info_rejects_invalid_state(
    payload: object,
) -> None:
    with pytest.raises(SDS200Error):
        parse_home_assistant_app_supervisor_info_response(payload)


def test_reconcile_home_assistant_app_advanced_exposure_preserves_defaults() -> None:
    options = HomeAssistantAppOptions(scanner_host="scanner.local")
    info = parse_home_assistant_app_supervisor_info_response(
        supervisor_info_payload()
    )

    assert reconcile_home_assistant_app_advanced_exposure(
        options,
        info,
    ) == HomeAssistantAppAdvancedExposure(
        container_address="172.30.33.7",
    )


def test_reconcile_home_assistant_app_advanced_exposure_accepts_distinct_ports() -> None:
    options = HomeAssistantAppOptions(
        scanner_host="scanner.local",
        remote_daemon_enabled=True,
        native_dashboard_enabled=True,
        advanced_access_server_name="sdsctl.home.arpa",
        advanced_access_host_address="192.168.20.15",
    )
    info = parse_home_assistant_app_supervisor_info_response(
        supervisor_info_payload(
            remote_port=50443,
            native_port=18443,
            options={
                "remote_daemon_enabled": True,
                "native_dashboard_enabled": True,
                "advanced_access_server_name": "sdsctl.home.arpa",
                "advanced_access_host_address": "192.168.20.15",
            },
        )
    )

    assert reconcile_home_assistant_app_advanced_exposure(
        options,
        info,
    ) == HomeAssistantAppAdvancedExposure(
        container_address="172.30.33.7",
        remote_daemon_host_port=50443,
        native_dashboard_host_port=18443,
    )


@pytest.mark.parametrize(
    ("options", "payload", "message"),
    [
        (
            HomeAssistantAppOptions(scanner_host="scanner.local"),
            supervisor_info_payload(remote_port=50443),
            "Network mapping must be disabled",
        ),
        (
            HomeAssistantAppOptions(
                scanner_host="scanner.local",
                remote_daemon_enabled=True,
                advanced_access_server_name="sdsctl.local",
                advanced_access_host_address="192.168.20.15",
            ),
            supervisor_info_payload(),
            "requires its Network mapping",
        ),
        (
            HomeAssistantAppOptions(
                scanner_host="scanner.local",
                remote_daemon_enabled=True,
                native_dashboard_enabled=True,
                advanced_access_server_name="sdsctl.local",
                advanced_access_host_address="192.168.20.15",
            ),
            supervisor_info_payload(remote_port=50443, native_port=50443),
            "distinct host ports",
        ),
        (
            HomeAssistantAppOptions(scanner_host="scanner.local"),
            supervisor_info_payload(
                options={"remote_daemon_enabled": True},
            ),
            "options disagree",
        ),
    ],
)
def test_reconcile_home_assistant_app_advanced_exposure_rejects_mismatch(
    options: HomeAssistantAppOptions,
    payload: object,
    message: str,
) -> None:
    info = parse_home_assistant_app_supervisor_info_response(payload)

    with pytest.raises(SDS200Error, match=message):
        reconcile_home_assistant_app_advanced_exposure(options, info)


@pytest.mark.parametrize(
    "server_name",
    [
        "https://sdsctl.local",
        "*.example.com",
        "sdsctl..example.com",
        "sdsctl.example.com:8443",
        "user@sdsctl.example.com",
        "999.999.999.999",
        "127.1",
        "203.0.113.7",
        "127.0.0.1",
        " sdsctl.local",
        "sdsctl.local/path",
    ],
)
def test_home_assistant_app_options_reject_public_ips_or_invalid_server_names(
    server_name: str,
) -> None:
    with pytest.raises(ValueError):
        HomeAssistantAppOptions(
            scanner_host="scanner.local",
            advanced_access_server_name=server_name,
        )


@pytest.mark.parametrize("name", [
    "display.example.com", "Display.Example.COM", "sdsctl.local",
    "192.168.20.15", "fd12:3456::15",
])
def test_dns_or_ip_identity_keeps_private_destination_and_disabled_exposure(name: str) -> None:
    options = HomeAssistantAppOptions(
        scanner_host="scanner.local",
        advanced_access_server_name=name,
        advanced_access_host_address="192.168.20.15",
    )
    assert options.advanced_access_server_name == name.lower()
    assert options.advanced_access_host_address == "192.168.20.15"
    exposure = reconcile_home_assistant_app_advanced_exposure(
        options, parse_home_assistant_app_supervisor_info_response(supervisor_info_payload()),
    )
    assert exposure.remote_daemon_host_port is None
    assert exposure.native_dashboard_host_port is None
    with pytest.raises(ValueError):
        HomeAssistantAppOptions(
            scanner_host="scanner.local", advanced_access_server_name=name,
            advanced_access_host_address="203.0.113.7",
        )


@pytest.mark.skipif(shutil.which("openssl") is None, reason="OpenSSL required for TLS fixture")
@pytest.mark.parametrize("identity, flag, other", [
    ("display.example.com", "-checkhost", "other.example.com"),
    ("192.168.20.15", "-checkip", "192.168.20.16"),
    ("fd12:3456::15", "-checkip", "fd12:3456::16"),
])
def test_dns_or_ip_certificate_matches_only_the_selected_identity(
    identity: str, flag: str, other: str,
) -> None:
    certificate, _private_key = generate_home_assistant_app_server_identity(identity)
    for hostname, expected in [(identity, 0), (other, 1)]:
        checked = subprocess.run(
            ["openssl", "x509", "-noout", flag, hostname],
            input=certificate, capture_output=True, timeout=10, check=False,
        )
        assert checked.returncode == expected


def test_home_assistant_app_options_requires_server_name_when_enabled() -> None:
    with pytest.raises(ValueError, match="requires an advanced-access server name"):
        HomeAssistantAppOptions(
            scanner_host="scanner.local",
            native_dashboard_enabled=True,
        )


@pytest.mark.parametrize(
    "host_address",
    [
        "sdsctl.local",
        "203.0.113.7",
        "127.0.0.1",
        "0.0.0.0",
        " 192.168.20.15",
        "192.168.020.015",
        "192.168.20.15/path",
    ],
)
def test_home_assistant_app_options_reject_invalid_host_addresses(
    host_address: str,
) -> None:
    with pytest.raises(ValueError):
        HomeAssistantAppOptions(
            scanner_host="scanner.local",
            advanced_access_host_address=host_address,
        )


def test_home_assistant_app_options_requires_host_address_for_remote() -> None:
    with pytest.raises(ValueError, match="requires an advanced-access host address"):
        HomeAssistantAppOptions(
            scanner_host="scanner.local",
            remote_daemon_enabled=True,
            advanced_access_server_name="sdsctl.local",
        )
