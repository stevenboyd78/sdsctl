# Containers

Use the published generic image when a Linux Docker or Podman host should run
the daemon and optional web client without installing the Python package on the
host. Home Assistant OS users should install the [Home Assistant App](Home-Assistant)
instead.

## Pull the image

Use an exact release tag for controlled deployments:

```bash
docker pull theboyd78/sdsctl:0.29.2
```

`theboyd78/sdsctl:latest` follows the newest successfully published release.
Registry tags are mutable; verify the reviewed manifest digest when
cryptographic immutability is required.

## Repository Compose deployment

The repository-root Compose files build the checked-out source rather than
selecting the published image:

```bash
git clone https://github.com/stevenboyd78/sdsctl.git
cd sdsctl
cp .env.example .env
docker compose up --detach --build daemon
docker compose run --rm daemon-client status --json
```

Start the optional loopback web sidecar explicitly:

```bash
docker compose --profile web up --detach --build web-dashboard
```

Open `http://127.0.0.1:8000/` on the Docker host. The default host publication
is loopback-only. Do not change it into an unauthenticated LAN or public
listener.

Stop the services without deleting named data volumes:

```bash
docker compose down
```

Do not add `--volumes` unless permanent deletion of the deployment's named
volumes is intended.

## Remote daemon and thin clients

Use the separate `compose.remote.yaml` path when one native Linux Docker Engine
host should own an SDS200 and an explicitly authenticated Raspberry Pi or other
private-LAN machine should run `sdsctl daemon-client` or the TUI. The ordinary
Compose files above stay local-only.

This advanced path uses a fixed isolated bridge address and publishes exactly
the authenticated daemon-client TCP port plus scanner-to-host RTP UDP `50000`
on one operator-selected literal private host address. It does not use host
networking, publish a browser dashboard, or expose scanner control UDP `50536`.
The TCP port is direct TLS for sdsctl clients; it is not HTTP.

The safe order is:

1. copy `.env.remote.example` to the ignored `.env.remote` file;
2. create the operator-owned TLS and per-client credential files beneath one
   read-only server configuration tree;
3. resolve the Compose model and run its non-networked preflight service;
4. start the one scanner-owning daemon;
5. provision only the public certificate and that client's credential to the
   Pi; and
6. select the exact named profile with `--remote-profile`.

The complete beginner procedure, permission commands, firewall direction,
verification sequence, rotation, revocation, rollback, and shutdown checks are
in the
[remote daemon container deployment guide](https://github.com/stevenboyd78/sdsctl/blob/main/docs/remote-container-deployment.md).
Do not invent secret values in Compose or expose this service to the Internet.

## Linux USB commands

The opt-in `compose.usb.yaml` path supports explicitly mapped scanner serial
devices on native Linux without privileged mode or networking. It requires a
stable device path and numeric device group. Validate the exact command in the
[container deployment guide](https://github.com/stevenboyd78/sdsctl/blob/main/docs/container-deployment.md)
before attaching hardware.

Remote Podman clients, Docker Desktop USB/IP, and physical Windows or macOS
scanner attachment are not claimed as supported equivalents.

## Detailed reference

The [generic container deployment guide](https://github.com/stevenboyd78/sdsctl/blob/main/docs/container-deployment.md)
is authoritative for Docker Compose, rootless Podman, USB mapping, readiness,
networking, persistence, upgrades, recovery, and the validated portability
matrix.
