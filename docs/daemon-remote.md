# Remote daemon client/server foundation

Milestone 32.1 is building an authenticated network transport for thin CLI and
TUI clients that share one scanner-owning daemon. Local Unix-domain sockets
remain the default. This page describes the current configuration, security
preflight, authentication protocol, direct-TLS admission, exact-address TCP
listener, credential lifecycle, operation authorization, and bounded
observation-lease boundaries; it does **not** announce a packaged remote
service.

The packaged `sdsctl daemon` command does not yet read this file, construct the
listener or observation broker, publish a container port, or add a Home
Assistant App port. The current objects are explicit Python construction
boundaries so later startup and deployment steps cannot define weaker bind,
admission, authorization, or stream-lifecycle behavior by accident.

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

The following is the configuration shape consumed by the explicit listener
constructor. Merely creating this file does not start or publish a service.

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
`port`. It is configurable, but any packaged runtime must publish only that one
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
Ingress identifiers. Authentication returns scopes only after successful proof.
The explicit listener then enforces the authenticated scope before operation
validation or dispatch, filters advertised capabilities to that same scope, and
removes the scanner endpoint from otherwise permitted remote responses.

## Filesystem preflight

`preflight_daemon_remote_configuration()` checks filesystem metadata before the
explicit listener opens a socket. It performs no network operation and does not
read certificate, private-key, or credential contents.

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

## Authentication contract

The `sds200.daemon_remote_auth` module defines the version 1 authentication
frames and verification behavior for the direct-TLS transport. Constructing or
using those protocol objects performs no network I/O, and no packaged command
invokes them yet.

After a client has authenticated the server through validated direct TLS, the
listener creates one fresh 32-byte server nonce. The client answers
with its configured ID, a fresh 32-byte client nonce, and an HMAC-SHA256 proof
over a canonical transcript containing the exact protocol, version, algorithm,
client ID, and both nonces. Frames are strict, versioned UTF-8 JSON Lines no
larger than 4 KiB. Unknown fields, malformed values, unsupported protocol or
version values, and newline injection fail closed.

Each challenge session accepts exactly one authentication attempt. A malformed
or unsuccessful attempt consumes the challenge just as a successful attempt
does, and a proof for one challenge cannot authenticate a different challenge.
The active credential registry evaluates every configured active identity with
constant-time identifier and proof comparisons before returning a result.
Unknown, incorrect, and revoked identities share one redacted authentication
failure; scopes and identity metadata are returned only after proof succeeds.
The concluding server result is also strict and versioned. A success returns
only the authoritative scopes; a failure returns one canonical error code and
message without peer, endpoint, path, or credential data. A client must wait for
that result before sending any daemon API request.

An active credential file contains exactly one unpadded base64url value encoding
32 random bytes, optionally followed by one newline. On POSIX its exact mode is
`0600`. The credential loader rejects relative paths, symlinks, non-regular
files, malformed or oversized contents, and files whose identity or metadata
changes between path inspection, descriptor opening, reading, and final
inspection. Active credentials are loaded into an immutable registry before the
listener admits peers. A remote authentication attempt therefore cannot probe
credential-file existence or parse behavior. Exceptions and object
representations never include credential bytes or private paths.

The `sds200.daemon_remote_tls` admission object loads the preflighted server
certificate, mode-`0600` private key, and active credential registry. It requires
TLS 1.3, rechecks the certificate and private-key filesystem snapshots around
context loading, applies one absolute deadline across the TLS handshake and
authentication frame, and begins the one-use challenge only after the TLS
handshake succeeds. Plaintext, silent, byte-at-a-time, malformed,
oversized, unauthenticated, and abruptly disconnected peers are closed with
redacted failure behavior. Successful admission returns the TLS stream plus an
opaque peer carrying the authenticated ID and authoritative scopes.

The `sds200.daemon_remote_server` layer binds one exact configured address and
port. IPv6 listeners are explicitly IPv6-only. It has separate bounds for the
kernel backlog, concurrent TLS admissions, and authenticated streams awaiting
the daemon API server. A slow or silent handshake occupies only one bounded
admission slot; it does not block another slot. Capacity excess, failed
authentication, listener failure, and shutdown all close their owned streams.
Redacted snapshots include only address family, port, capacity, counts, and
stable failure classes—not the private address, peer, client ID, or secret path.

## Credential rotation and revocation

The explicit listener owns a generation-based credential authority. Initial
construction loads one complete immutable registry before the listener binds.
`reload_credentials()` then provides the operational rotation boundary for a
separately constructed listener; it is not yet exposed through a packaged CLI
command or service signal.

For one reload, the authority:

1. verifies that enabled state, bind address, port, certificate path, and
   private-key path still describe the running listener;
2. loads and validates the complete replacement client registry without
   changing the current generation;
3. atomically installs that registry and advances the generation; and
4. invalidates and closes every connection authenticated under the preceding
   generation, including unchanged identities and authenticated clients still
   waiting in the ready queue.

This all-or-nothing sequence means a malformed, missing, non-private, or
concurrently replaced active credential cannot partially alter the registry.
A failed reload reports a stable redacted failure class and leaves the
last-known-good generation and its sessions active. A successful reload always
requires every client to reconnect and complete a new challenge/proof exchange,
even when only one identity changed or the replacement bytes are identical.
That rule makes revocation immediate and avoids trying to infer which
established connection should survive a registry change.

Admission and authorized dispatch are linearized against the generation swap.
If a reload wins after an old registry verifies a proof but before the session
is registered, admission fails. If an authorized request is already executing,
the reload waits for that bounded request to finish, then advances the
generation and closes the connection. An expired request that can still reach
the API boundary receives `authentication_expired` and its connection is
closed; clients must reconnect rather than retry on the old stream.

Credential-only reload deliberately cannot change network or server-identity
settings. Changing the bind address, port, certificate, or private key requires
a separately controlled listener replacement. Credential files remain
operator-owned: this boundary reads exact mode-`0600` files but does not create,
overwrite, distribute, reveal, or delete secrets. Its diagnostics contain only
generation, configured/active/revoked/control client counts, live-session and
invalidation counts, reload totals, and stable failure classes. They contain no
client ID, endpoint, path, or credential material.

Only authenticated streams are delivered to the daemon API server, together
with a transport-owned peer context. That context invokes a distinct
authorization entry point before dispatch. An `observe` identity receives
negotiation, ping, sanitized runtime/scanner state, and audio health. The
private scanner endpoint is removed from state responses. A `control` identity
adds the existing typed scanner controls. Recording status, start, stop, and
inventory remain unavailable to both scopes because remote recording contents
are outside this milestone's authority. Negotiated capabilities are filtered to
the peer's exact operation set, and a denied operation returns
`authorization_denied` without reaching runtime dispatch.

These objects still do not enter daemon startup, create a client transport,
publish a deployment port, or activate Home Assistant configuration.

## Authenticated observation leases

The `sds200.daemon_remote_observation` module adds a transport-neutral broker
over the daemon's existing event, shared Waterfall, and accepted-PCMU
publishers. The broker does not bind a socket and does not own or create a
scanner command transport, PSI stream, Waterfall poller, RTSP session, RTP
receiver, or audio decoder. A later remote service router can therefore attach
an authenticated connection to these leases without becoming a second scanner
owner.

Every lease requires a `DaemonRemoteAuthenticatedPeer` with `observe` scope.
Acquisition is linearized with that peer's credential generation. One
credential identity may hold at most one event lease, one Waterfall lease, and
one audio lease at a time. The broker defaults to 24 leases in total and three
per identity; the lower capacity of an underlying publisher continues to apply.
Capacity, duplicate, unavailable-source, expired-generation, and closed-broker
failures use stable messages without a client ID, endpoint, path, or secret.

Credential sessions accept child invalidators. A successful credential reload
closes the authenticated socket and every event, Waterfall, and audio lease
registered beneath that session. Unchanged identities are treated exactly like
rotated or revoked identities: all old-generation leases are released and the
client must authenticate again. A normally released child lease unregisters
its invalidator without closing the parent authenticated session or another
lease.

The three lease kinds retain these source-specific contracts:

| Kind | Remote boundary |
| --- | --- |
| Events | Preserves the authoritative snapshot and global event sequence, recursively removes endpoint, path, token, credential, secret, and recording fields, and omits every `recording.state` event. |
| Waterfall | Acquires one lease on the existing demand-driven `WaterfallSession`; overlapping clients share its single GST/PWF/GWF lifecycle, and only the final lease release stops scanner publication. |
| Audio | Preserves accepted PCMU payload and RTP continuity and queue-loss metadata while replacing the scanner RTSP endpoint with the constant `sdsctl-remote-daemon`. |

Each source already supplies an independent bounded queue per subscription.
The broker adds no unbounded intermediary. A slow event, Waterfall, or audio
consumer can lose only its own oldest queued observations and cannot wait on a
healthy consumer's receive path. Closing the broker releases its child leases
but deliberately leaves all daemon-owned publishers open for local clients and
other daemon services. Its snapshot reports only capacities and aggregate
lease, rejection, expiration, and filtered-event counts; it contains no client
identity or private endpoint.

This boundary is not yet the on-wire service-selection protocol. It neither
adds remote client configuration nor changes the existing local Unix socket
formats. The next slice can build one shared local/remote client transport on
top of these source-preserving leases.

## What remains before remote use

This foundation is not sufficient for a supported packaged remote deployment.
Milestone 32.1 must still add and validate, in bounded slices:

1. one shared remote client transport for CLI and TUI consumers;
2. explicit opt-in daemon startup and diagnostics;
3. explicit Docker/Compose and Home Assistant App port metadata; and
4. concurrent ordinary-host, container, Home Assistant OS, Raspberry Pi kiosk,
   and remote-TUI acceptance.

Until those steps are complete and released, use the private local daemon
sockets, or use the separately authenticated native HTTPS dashboard where its
documented LAN mode is appropriate.
