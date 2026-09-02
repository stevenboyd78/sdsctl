# Remote daemon client/server foundation

Milestone 32.1 is building an authenticated network transport for thin CLI and
TUI clients that share one scanner-owning daemon. Local Unix-domain sockets
remain the default. This page describes the current configuration and security
preflight foundation; it does **not** announce an available remote listener.

The packaged `sdsctl daemon` command does not yet read this file, open a TCP
socket, construct TLS, accept a remote credential, publish a container port, or
add a Home Assistant App port. The current model exists so those later runtime
steps cannot define a weaker configuration boundary by accident.

## Default behavior

If `daemon-remote.toml` is absent, no remote configuration exists. The daemon
continues to use its private local sockets exactly as before.

The deterministic user path is:

```text
$XDG_CONFIG_HOME/sdsctl/daemon-remote.toml
```

When `XDG_CONFIG_HOME` is unset, the usual location is:

```text
~/.config/sdsctl/daemon-remote.toml
```

An explicitly disabled document contains only the schema version and switch:

```toml
version = 1

[listener]
enabled = false
```

A disabled document may not retain endpoint, TLS, or client settings. This
keeps “disabled” unambiguous and prevents a dormant partial configuration from
silently becoming reachable after an unrelated change.

## Configuration shape

The following is a shape example for review and preflight only. It does not
make the remote service constructible in the current release or development
branch.

```toml
version = 1

[listener]
enabled = true
bind_address = "192.168.20.10"
port = 50443
certificate_file = "/etc/sdsctl/remote-server.crt"
private_key_file = "/etc/sdsctl/remote-server.key"

[[clients]]
client_id = "pi-kiosk"
credential_file = "/etc/sdsctl/clients/pi-kiosk.secret"
scopes = ["observe"]

[[clients]]
client_id = "operator-laptop"
credential_file = "/etc/sdsctl/clients/operator-laptop.secret"
scopes = ["observe", "control"]
revoked = true
```

The default modeled port is `50443` when an enabled listener table omits
`port`. It is configurable, but the future runtime must publish only that one
selected TCP port. Scanner control UDP `50536`, scanner audio UDP `50000`, the
native HTTPS dashboard port, and Home Assistant Ingress are separate services
with separate trust boundaries.

The parser is versioned and strict. It rejects unknown top-level, listener, and
client fields instead of ignoring misspellings or future settings.

## Exact bind policy

`bind_address` must be one literal address assigned to the daemon host or its
isolated container. Hostnames, URLs, CIDR networks, DNS resolution, interface
scanning, and automatic discovery are not accepted.

The configuration model accepts only:

- RFC 1918 IPv4 addresses in `10.0.0.0/8`, `172.16.0.0/12`, or
  `192.168.0.0/16`;
- IPv4 link-local addresses in `169.254.0.0/16`;
- IPv6 unique-local addresses in `fc00::/7`; and
- IPv6 link-local addresses in `fe80::/10`, including an explicit scope such
  as `fe80::20%eth0` when the platform requires one.

The model rejects wildcard addresses (`0.0.0.0` and `::`), loopback, multicast,
documentation ranges, reserved/global/public addresses, and credentials in
URLs. A generic container may eventually bind its exact private container
address only behind an explicit orchestrator host-port mapping; host networking
does not become an exposure mechanism. Public or Internet-facing deployment
remains unsupported.

## TLS and client identity metadata

An enabled configuration requires distinct absolute certificate and private-key
paths. It also requires at least one non-revoked client identity. Configuration
stores credential **file references**, not credential contents.

Every client ID is unique and independently revocable. Every credential path
must also be unique, and a client credential may not reuse the server private
key. Setting `revoked = true` removes that identity from the active preflight;
the revoked credential file need not remain on disk. An enabled listener cannot
contain only revoked identities.

The two modeled authorization scopes are:

| Scope | Intended boundary |
| --- | --- |
| `observe` | Bounded state, ordered events, diagnostics, and demand-driven relative Waterfall data |
| `control` | The existing typed semantic scanner controls in addition to observation |

Every identity must include `observe`; `control` alone is invalid. Neither scope
grants raw scanner keys, generic MQTT commands, filesystem access, recording
contents, Favorites bytes, provider credentials, Home Assistant tokens, or
Ingress identifiers. Runtime enforcement and authenticated protocol negotiation
are later Milestone 32.1 slices and are not provided by the configuration model
alone.

## Filesystem preflight

`preflight_daemon_remote_configuration()` checks filesystem metadata before a
future listener could open a socket. It performs no network operation and does
not read certificate, private-key, or credential contents.

For an enabled configuration, preflight requires:

- the certificate, private key, and every active client credential to be
  regular non-symlink files;
- each file to be non-empty and bounded;
- certificate and private-key files to be no larger than 1 MiB;
- each active credential file to be no larger than 4 KiB; and
- on POSIX, the private key and every active credential to have exact mode
  `0600`.

The exact-mode rule deliberately fails closed for group-readable, world-readable,
or executable secret files. A future platform-specific secret-store adapter may
provide an equivalent boundary without representing a secret as a POSIX file.

Preflight reports only non-secret counts and byte sizes. Configuration
diagnostics report enabled state, address family, port, and client counts; they
do not include the private bind address or any filesystem path. Validation
errors do not echo secret contents, private endpoint values, or secret paths.

## What remains before remote use

This foundation is not sufficient for a remote connection. Milestone 32.1 must
still add and validate, in bounded slices:

1. direct TLS server identity loading and authenticated client proof;
2. transport-level scope enforcement before daemon operation dispatch;
3. protocol negotiation, rotation, revocation, replay handling, and redacted
   failure behavior;
4. bounded remote event, Waterfall, and audio leases with slow-peer isolation;
5. one shared remote client transport for CLI and TUI consumers;
6. explicit Docker/Compose and Home Assistant App port metadata; and
7. concurrent ordinary-host, container, Home Assistant OS, Raspberry Pi kiosk,
   and remote-TUI acceptance.

Until those steps are complete and released, use the private local daemon
sockets, or use the separately authenticated native HTTPS dashboard where its
documented LAN mode is appropriate.
