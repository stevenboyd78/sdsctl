from __future__ import annotations

from pathlib import Path

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_COMPOSE = _REPOSITORY_ROOT / "compose.yaml"
_USB_COMPOSE = _REPOSITORY_ROOT / "compose.usb.yaml"
_REMOTE_COMPOSE = _REPOSITORY_ROOT / "compose.remote.yaml"
_REMOTE_ENV_EXAMPLE = _REPOSITORY_ROOT / ".env.remote.example"
_DOCKERFILE = _REPOSITORY_ROOT / "Dockerfile"
_DOCKERIGNORE = _REPOSITORY_ROOT / ".dockerignore"
_GITIGNORE = _REPOSITORY_ROOT / ".gitignore"
_SERVER_EXAMPLE = (
    _REPOSITORY_ROOT / "examples" / "remote-compose" /
    "daemon-remote.toml.example"
)
_CLIENT_EXAMPLE = (
    _REPOSITORY_ROOT / "examples" / "remote-compose" /
    "pi-daemon-remote-clients.toml.example"
)
_REMOTE_DOC = _REPOSITORY_ROOT / "docs" / "remote-container-deployment.md"
_CONTAINER_DOC = _REPOSITORY_ROOT / "docs" / "container-deployment.md"
_DAEMON_REMOTE_DOC = _REPOSITORY_ROOT / "docs" / "daemon-remote.md"
_README = _REPOSITORY_ROOT / "README.md"
_WIKI_CONTAINERS = _REPOSITORY_ROOT / "wiki" / "Containers.md"


def _service(document: str, name: str, next_name: str | None) -> str:
    content = document.split(f"\n  {name}:\n", 1)[1]
    if next_name is None:
        return content.split("\nnetworks:\n", 1)[0]
    return content.split(f"\n  {next_name}:\n", 1)[0]


def test_remote_compose_is_standalone_and_preserves_existing_manifests() -> None:
    standard = _COMPOSE.read_text(encoding="utf-8")
    usb = _USB_COMPOSE.read_text(encoding="utf-8")
    remote = _REMOTE_COMPOSE.read_text(encoding="utf-8")

    assert remote.startswith("name: sdsctl-remote\n\nservices:\n")
    for existing in (standard, usb):
        assert "daemon-remote-preflight" not in existing
        assert "SDSCTL_REMOTE_HOST_ADDRESS" not in existing
        assert "SDSCTL_REMOTE_CONFIG_DIRECTORY" not in existing
        assert "172.30.32.2" not in existing
    assert "extends:" not in remote
    assert "include:" not in remote


def test_remote_compose_preflight_is_nonnetworked_read_only_and_bounded() -> None:
    compose = _REMOTE_COMPOSE.read_text(encoding="utf-8")
    preflight = _service(compose, "remote-preflight", "daemon")

    for required in (
        "      - daemon-remote-preflight\n",
        "      - --published-address\n",
        "      - --expected-port\n",
        "      - /config/sdsctl/daemon-remote.toml\n",
        "    network_mode: none\n",
        "    read_only: true\n",
        "    healthcheck:\n      disable: true\n",
        "    cap_drop:\n      - ALL\n",
        "      - no-new-privileges:true\n",
        "        target: /config/sdsctl\n",
        "        read_only: true\n",
        "          create_host_path: false\n",
    ):
        assert required in preflight
    for forbidden in (
        "ports:",
        "state:/state",
        "cache:/cache",
        "runtime:/run/sdsctl",
        "restart:",
        "devices:",
        "privileged:",
        "cap_add:",
    ):
        assert forbidden not in preflight


def test_remote_compose_daemon_uses_exact_bridge_and_two_mappings() -> None:
    compose = _REMOTE_COMPOSE.read_text(encoding="utf-8")
    daemon = _service(compose, "daemon", "daemon-client")

    for required in (
        '${SDS200_HOST:?Set SDS200_HOST to the scanner IPv4 address}',
        "      - --remote-config\n",
        "      - /config/sdsctl/daemon-remote.toml\n",
        "      - --rtp-bind-port\n      - \"50000\"\n",
        "        condition: service_completed_successfully\n",
        "        ipv4_address: 172.30.32.2\n",
        "      - name: daemon-client\n",
        "      - name: scanner-rtp\n",
        "        protocol: tcp\n",
        "        protocol: udp\n",
        "    read_only: true\n",
        "    restart: unless-stopped\n",
        "    cap_drop:\n      - ALL\n",
        "      - state:/state\n",
        "      - cache:/cache\n",
        "      - runtime:/run/sdsctl\n",
    ):
        assert required in daemon
    assert daemon.count("        host_ip:") == 2
    assert daemon.count("        target:") == 3
    assert daemon.count("        published:") == 2
    for forbidden in (
        "network_mode: host",
        "network_mode: none",
        "privileged:",
        "devices:",
        "cap_add:",
        "50536",
        "554",
        "8099",
    ):
        assert forbidden not in daemon


def test_remote_compose_local_sidecars_do_not_receive_server_secrets() -> None:
    compose = _REMOTE_COMPOSE.read_text(encoding="utf-8")
    client = _service(compose, "daemon-client", "web-dashboard")
    web = _service(compose, "web-dashboard", None)

    assert "    network_mode: none\n" in client
    assert "      - runtime:/run/sdsctl\n" in client
    assert '      - "127.0.0.1:${SDSCTL_WEB_PORT:-8000}:8000"\n' in web
    assert "    read_only: true\n" in web
    assert "      - runtime:/run/sdsctl\n" in web
    for sidecar in (client, web):
        assert "SDSCTL_REMOTE_CONFIG_DIRECTORY" not in sidecar
        assert "/config/sdsctl" not in sidecar
        assert "daemon-remote.toml" not in sidecar
        assert "50000" not in sidecar
        assert "50443" not in sidecar
        assert "privileged:" not in sidecar
        assert "devices:" not in sidecar
        assert "cap_add:" not in sidecar


def test_remote_compose_network_is_fixed_private_and_image_stays_unexposed() -> None:
    compose = _REMOTE_COMPOSE.read_text(encoding="utf-8")
    dockerfile = _DOCKERFILE.read_text(encoding="utf-8")

    assert "        - subnet: 172.30.32.0/24\n" in compose
    assert compose.count("        ipv4_address: 172.30.32.2\n") == 1
    assert "network_mode: host" not in compose
    assert "EXPOSE " not in dockerfile


def test_remote_compose_examples_are_non_secret_and_locally_ignored() -> None:
    environment = _REMOTE_ENV_EXAMPLE.read_text(encoding="utf-8")
    server = _SERVER_EXAMPLE.read_text(encoding="utf-8")
    client = _CLIENT_EXAMPLE.read_text(encoding="utf-8")
    gitignore = _GITIGNORE.read_text(encoding="utf-8")
    dockerignore = _DOCKERIGNORE.read_text(encoding="utf-8")

    assert environment == (
        "SDS200_HOST=192.168.20.20\n"
        "SDS200_LOG_LEVEL=INFO\n"
        "SDSCTL_REMOTE_HOST_ADDRESS=192.168.20.10\n"
        "SDSCTL_REMOTE_PORT=50443\n"
        "SDSCTL_REMOTE_CONFIG_DIRECTORY=/srv/sdsctl/remote-config\n"
        "SDSCTL_WEB_PORT=8000\n"
    )
    assert ".env.remote\n" in gitignore
    assert dockerignore.startswith("*\n")
    assert 'bind_address = "172.30.32.2"' in server
    assert 'credential_file = "/config/sdsctl/clients/pi-display.secret"' in server
    assert 'address = "192.168.20.10"' in client
    assert 'server_hostname = "sdsctl-daemon.home.arpa"' in client
    combined = "\n".join((environment, server, client)).lower()
    for forbidden in (
        "begin private key",
        "private_key =",
        "credential =",
        "password =",
        "token =",
        "secret =",
    ):
        assert forbidden not in combined


def test_remote_container_documentation_preserves_beginner_security_contract() -> None:
    document = _REMOTE_DOC.read_text(encoding="utf-8")
    normalized = " ".join(document.split())

    for required in (
        "native-Linux Docker Engine",
        "compose.remote.yaml",
        "172.30.32.0/24",
        "172.30.32.2",
        "exactly two ports",
        "TCP `50443`",
        "UDP `50000`",
        "not HTTP",
        "Scanner control UDP `50536`",
        "UID/GID `10001:10001`",
        "cp .env.remote.example .env.remote",
        "SDSCTL_REMOTE_HOST_ADDRESS",
        "SDSCTL_REMOTE_CONFIG_DIRECTORY",
        "sudo chmod 0600 /srv/sdsctl/remote-config/tls/server.key",
        "openssl rand -base64 32",
        "daemon-remote.toml.example",
        "scopes = [\"observe\", \"control\"]",
        "config --quiet",
        "run --rm remote-preflight",
        "Remote daemon container deployment preflight passed.",
        "from PI_PRIVATE_IP to HOST_PRIVATE_IP",
        "up --detach --build daemon",
        "run --rm daemon-client status --json",
        "http://127.0.0.1:8000/",
        "python -m pip install \"sds200[tui,playback]\"",
        "--remote-profile docker-host status --json",
        "--remote-profile docker-host snapshot",
        "--remote-profile docker-host events --count 2 --json",
        "--daemon-client --remote-profile docker-host --audio-playback",
        "kill --signal SIGHUP daemon",
        "restart daemon",
        "revoked = true",
        "Do not add `--volumes`",
        "remote Compose preflight intentionally rejects a disabled document",
        "reserved for Milestone 32.4",
    ):
        assert required in document or required in normalized

    lower_document = document.lower()
    for forbidden in (
        "network_mode: host",
        "0.0.0.0",
        "begin private key",
        "credential =",
        "private_key =",
    ):
        assert forbidden not in lower_document


def test_remote_container_guide_is_linked_from_beginner_and_advanced_routes() -> None:
    assert "remote-container-deployment.md" in _README.read_text(encoding="utf-8")
    assert "remote-container-deployment.md" in _CONTAINER_DOC.read_text(
        encoding="utf-8"
    )
    assert "remote-container-deployment.md" in _DAEMON_REMOTE_DOC.read_text(
        encoding="utf-8"
    )
    assert "remote-container-deployment.md" in _WIKI_CONTAINERS.read_text(
        encoding="utf-8"
    )
