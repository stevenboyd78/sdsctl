# Remote daemon container deployment

Milestone 32.3 provides one explicit native-Linux Docker Engine deployment for
a scanner-owning daemon and authenticated private-LAN clients. Use this path
when one Linux host should own the SDS200 while another machine, such as a
Raspberry Pi display, runs the CLI or TUI.

This is an advanced, opt-in deployment. The ordinary `compose.yaml` and
`compose.usb.yaml` workflows remain local-only and publish no daemon-client
port. Home Assistant OS uses its separately managed App boundary.

## What this deployment opens

`compose.remote.yaml` creates an isolated `172.30.32.0/24` bridge and gives the
daemon the fixed container address `172.30.32.2`. It publishes exactly two
ports on one literal private address selected by the operator:

| Host mapping | Direction | Purpose |
| --- | --- | --- |
| TCP `50443` by default | intended client to Docker host | Authenticated TLS daemon-client services |
| UDP `50000` | scanner to Docker host | The SDS200's existing RTP audio input |

The TCP port is not HTTP and must not be opened in a browser. UDP `50000` is
not a client service. Scanner control UDP `50536`, RTSP TCP `554`, local Unix
sockets, and web or Home Assistant listeners are not published by this
manifest. The daemon still owns one scanner control transport, one PSI loop,
one RTSP/RTP audio input, and one demand-driven Waterfall source.

The manifest does not use host networking, privileged mode, host devices,
added capabilities, or a broad host mount. The daemon runs as numeric UID/GID
`10001:10001`, has a read-only root filesystem, and receives only its
operator-owned configuration tree plus named state, cache, and runtime
volumes. The image deliberately has no Dockerfile `EXPOSE` instruction.

## Requirements

Use all of the following:

- a native Linux host running Docker Engine and the Docker Compose plugin;
- an SDS200 reachable from that host on an authorized private network;
- one exact private IPv4 address assigned to the Docker host;
- a checkout of this repository at the reviewed version to deploy;
- a non-conflicting `172.30.32.0/24` Docker subnet; and
- a separate intended client, such as Raspberry Pi OS with Python 3.11 or
  newer.

Rootless Podman, remote Podman clients, Docker Desktop, public or wildcard
publication, automatic interface selection, and Internet-facing deployment are
not validated by this contract. Stop if the fixed bridge subnet conflicts with
an existing host or routed network; do not silently substitute another address
because the listener, preflight, and manifest must agree exactly.

All addresses below are documentation examples. Replace them with addresses
from the private network you administer. Never commit the completed `.env.remote`
file, TLS private key, or client credential.

## 1. Prepare the environment file

From the repository root, copy the non-secret template:

```bash
cp .env.remote.example .env.remote
```

Edit `.env.remote` and set:

- `SDS200_HOST` to the scanner's private IPv4 address;
- `SDSCTL_REMOTE_HOST_ADDRESS` to the one exact private IPv4 address assigned
  to the Docker host;
- `SDSCTL_REMOTE_PORT` to the selected daemon-client TCP port, normally
  `50443`; and
- `SDSCTL_REMOTE_CONFIG_DIRECTORY` to the absolute host configuration tree,
  normally `/srv/sdsctl/remote-config`.

Do not place credentials, private keys, passwords, tokens, or Home Assistant
secrets in `.env.remote`. The file is ignored by Git, but it is still operator
configuration and should not be shared.

## 2. Create the server configuration tree

Create only the required directories with ownership matching the unprivileged
container user:

```bash
sudo install -d -o 10001 -g 10001 -m 0700 \
  /srv/sdsctl/remote-config \
  /srv/sdsctl/remote-config/tls \
  /srv/sdsctl/remote-config/clients
```

Create a server certificate whose subject alternative name contains the exact
hostname that clients will verify. The example profile uses
`sdsctl-daemon.home.arpa`. A locally issued certificate or a carefully managed
self-signed certificate is suitable for a private deployment. Its public
certificate may be copied to clients; its private key never leaves this host.

For a temporary private-network acceptance certificate, OpenSSL can create a
self-signed identity without printing private material:

```bash
sudo openssl req -x509 -newkey rsa:3072 -sha256 -days 365 -nodes \
  -keyout /srv/sdsctl/remote-config/tls/server.key \
  -out /srv/sdsctl/remote-config/tls/server.crt \
  -subj '/CN=sdsctl-daemon.home.arpa' \
  -addext 'subjectAltName=DNS:sdsctl-daemon.home.arpa'
sudo chown 10001:10001 \
  /srv/sdsctl/remote-config/tls/server.key \
  /srv/sdsctl/remote-config/tls/server.crt
sudo chmod 0600 /srv/sdsctl/remote-config/tls/server.key
```

Replace short-lived acceptance material with certificates managed according to
your local security policy. Do not reuse a public web certificate's private
key merely for convenience.

## 3. Issue one independent client credential

Each client gets a separate random 32-byte, unpadded base64url credential. The
following sequence writes the value directly to its protected server file and
does not print it:

```bash
sudo install -o 10001 -g 10001 -m 0600 /dev/null \
  /srv/sdsctl/remote-config/clients/pi-display.secret
openssl rand -base64 32 \
  | tr '+/' '-_' \
  | tr -d '=\n' \
  | sudo tee /srv/sdsctl/remote-config/clients/pi-display.secret >/dev/null
sudo chown 10001:10001 \
  /srv/sdsctl/remote-config/clients/pi-display.secret
sudo chmod 0600 \
  /srv/sdsctl/remote-config/clients/pi-display.secret
```

Provision the public certificate and only this credential to the Pi through an
operator-controlled out-of-band channel. Never copy `server.key` or another
client's credential. Avoid clipboard history, screenshots, shell tracing, and
commands that echo the value.

## 4. Configure the listener

Copy the committed non-secret example into the configuration tree:

```bash
sudo install -o 10001 -g 10001 -m 0600 \
  examples/remote-compose/daemon-remote.toml.example \
  /srv/sdsctl/remote-config/daemon-remote.toml
```

The listener address inside this document must remain `172.30.32.2`. Its port
must exactly match `SDSCTL_REMOTE_PORT`. The certificate, private-key, and
credential paths are fixed container paths beneath `/config/sdsctl`; do not
replace them with host paths.

The example grants `observe` only. That scope allows sanitized status, events,
Waterfall, and accepted PCMU audio. Add `"control"` only for an identity whose
operator is meant to issue the existing typed hold, navigation, volume,
squelch, and reconnect controls:

```toml
scopes = ["observe", "control"]
```

The server private key and every active credential must be regular,
non-symlink, non-empty files with exact mode `0600` and readable by UID/GID
`10001:10001`. Secret bytes belong only in those files, never in TOML.

## 5. Validate before opening a listener

First resolve the Compose model. Missing environment values fail here, before
a container is started:

```bash
docker compose --env-file .env.remote \
  -f compose.remote.yaml config --quiet
```

Then build and run the isolated, non-networked, read-only preflight service:

```bash
docker compose --env-file .env.remote \
  -f compose.remote.yaml build remote-preflight daemon
docker compose --env-file .env.remote \
  -f compose.remote.yaml run --rm remote-preflight
```

Expected output is exactly:

```text
Remote daemon container deployment preflight passed.
```

Any invalid host address, container address, port, listener setting, TLS file,
permission, or credential produces one redacted failure. Correct the private
configuration locally; do not add a wildcard bind or weaken file permissions.
The daemon repeats its authoritative preflight during startup even after this
one-shot check passes.

## 6. Apply the narrow firewall policy

Allow the selected TCP port only from the intended Pi address or dedicated
private client subnet to the exact Docker-host address. For an uncomplicated
UFW host, a single-client rule has this form:

```bash
sudo ufw allow from PI_PRIVATE_IP to HOST_PRIVATE_IP port 50443 proto tcp
```

If the host firewall blocks inbound scanner audio, separately allow UDP
`50000` only from `SDS200_HOST` to the same host address. That datagram path is
scanner-to-host RTP input; it is not available to thin clients and must not be
opened broadly. Do not forward either port from an Internet gateway.

## 7. Start and verify the daemon

Start only the scanner-owning daemon. Compose requires a new successful
preflight before it starts:

```bash
docker compose --env-file .env.remote \
  -f compose.remote.yaml up --detach --build daemon
docker compose --env-file .env.remote \
  -f compose.remote.yaml ps
```

Verify the private local socket path without using the network listener:

```bash
docker compose --env-file .env.remote \
  -f compose.remote.yaml run --rm daemon-client status --json
docker compose --env-file .env.remote \
  -f compose.remote.yaml run --rm daemon-client snapshot
```

The optional web sidecar is still host-loopback-only and uses the shared Unix
socket volume. It receives no remote TLS or credential tree:

```bash
docker compose --env-file .env.remote \
  -f compose.remote.yaml --profile web \
  up --detach --build web-dashboard
```

Open `http://127.0.0.1:8000/` only on the Docker host. This is not a LAN kiosk
publication. A remote browser requires the separately authenticated native
HTTPS dashboard deployment.

## 8. Install and configure the Raspberry Pi client

On Raspberry Pi OS, use an ordinary virtual environment and install the TUI
plus optional local playback support:

```bash
python3 -m venv ~/.venvs/sdsctl
source ~/.venvs/sdsctl/bin/activate
python -m pip install --upgrade pip
python -m pip install "sds200[tui,playback]"
```

Raspberry Pi OS normally also needs `libportaudio2` for speaker or headphone
playback. Create the client configuration directory, then place the public
certificate and that Pi's credential at the exact paths in the example:

```bash
install -d -m 0700 ~/.config/sdsctl
chmod 0600 ~/.config/sdsctl/pi-display.secret
install -m 0600 \
  examples/remote-compose/pi-daemon-remote-clients.toml.example \
  ~/.config/sdsctl/daemon-remote-clients.toml
```

When the repository is not present on the Pi, copy the example document through
the same trusted provisioning channel and edit it locally. Set `address` to the
Docker host's literal private address. Keep `server_hostname` equal to the TLS
certificate identity. `certificate_file` is public trust material;
`credential_file` is the Pi's mode-`0600` secret.

Verify each boundary before opening the TUI:

```bash
sdsctl daemon-client --remote-profile docker-host status --json
sdsctl daemon-client --remote-profile docker-host snapshot
sdsctl daemon-client --remote-profile docker-host events --count 2 --json
sdsctl tui --daemon-client --remote-profile docker-host --audio-playback
```

An observe-only profile must be unable to issue controls. A deliberately
control-capable identity may use only the typed controls authorized by the
daemon. Successful remote diagnostics use the fixed label
`sdsctl-remote-daemon`; they do not disclose the private server or scanner
address, TLS hostname, client ID, path, or credential.

## Rotation, revocation, and recovery

For rotation, create a complete replacement credential set, distribute the new
client file out of band, update the full listener document if needed, and run
the isolated preflight again. Then request the daemon's controlled reload:

```bash
docker compose --env-file .env.remote \
  -f compose.remote.yaml kill --signal SIGHUP daemon
```

A successful reload atomically advances the credential generation and closes
all old-generation API, event, Waterfall, and audio connections. Clients must
reauthenticate and resynchronize. A failed reload retains the last-known-good
generation. Bind address, port, certificate, or private-key changes require a
full daemon restart:

```bash
docker compose --env-file .env.remote \
  -f compose.remote.yaml restart daemon
```

To revoke one client, set `revoked = true` on its server `[[clients]]` entry,
retain at least one active identity, preflight the complete configuration, and
send the same controlled reload. Delete retired credentials only after the new
generation and client recovery are verified.

For a configuration rollback, restore the complete previously reviewed
configuration tree as one unit, reapply exact ownership and secret modes, run
the isolated preflight, and restart the daemon. Do not mix one generation's
listener document with another generation's credential files.

Long-lived remote event, Waterfall, and accepted-PCMU consumers use bounded
reconnect and fresh authoritative resynchronization. After a daemon restart,
confirm that status returns, events begin with a fresh snapshot, Waterfall
starts a new session checkpoint, audio continuity is reset, and the local
daemon-client remains usable. A TLS identity, authentication, authorization,
protocol, or configuration failure stops rather than silently falling back.

## Stop or disable the deployment

Stop all project containers and remove the dedicated network while preserving
named state, cache, and runtime volumes:

```bash
docker compose --env-file .env.remote \
  -f compose.remote.yaml down
```

After `down` returns, the daemon-client listener and scanner ownership must no
longer exist. Do not add `--volumes` during normal shutdown; it permanently
deletes the deployment's named data volumes.

To keep the checkout but disable remote startup, stop the daemon and replace
the listener document with the minimal `enabled = false` form documented in
[Authenticated remote daemon clients](daemon-remote.md#default-behavior). The
remote Compose preflight intentionally rejects a disabled document, preventing
this manifest from reopening the listener. Restore and preflight an explicit
enabled document before a later restart.

Remove firewall rules that are no longer needed and retire credentials through
the same controlled process that provisioned them. Permanently deleting the
configuration tree, named volumes, or credentials is a separate destructive
operation and should occur only after exact operator review.

## Support boundary

This deployment publishes an authenticated daemon-client service, not the
native web dashboard. A Raspberry Pi browser display still uses the separate
password-authenticated native HTTPS dashboard, while a Pi TUI uses the named
remote profile above. Home Assistant App options for a native-dashboard port or
daemon-client port, Supervisor-facing validation, and one daemon serving
multiple physical displays are reserved for Milestone 32.4.

For protocol, authentication, authorization, sanitization, and profile details,
read [Authenticated remote daemon clients](daemon-remote.md). For the existing
local, USB, Docker Desktop, and Podman paths, read
[Generic container deployment](container-deployment.md).
