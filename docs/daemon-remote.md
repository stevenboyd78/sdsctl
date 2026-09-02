# Authenticated remote daemon clients

Milestone 32.2 packages the authenticated transport foundation for ordinary
Python installations. Milestone 32.3 adds one separate, isolated native-Linux
Docker Engine deployment without changing the ordinary local Compose paths.
One `sdsctl daemon` process can own the scanner, PSI, Waterfall, and SDS200
audio sessions while explicitly configured CLI and TUI clients connect from
another private or link-local host. Local Unix-domain sockets remain enabled
and remain the default for same-host clients.

The remote listener is opt-in. `sdsctl daemon` reads one strict
`daemon-remote.toml` document and opens the configured TLS listener only when
that document explicitly enables it. `sdsctl daemon-client` and
`sdsctl tui --daemon-client` continue to use local sockets unless the operator
selects one exact named remote profile with `--remote-profile`.

The ordinary-host setup in this document publishes no Docker, systemd, Home
Assistant App, Ingress, or native-dashboard port. The opt-in Docker boundary is
documented separately in
[Remote daemon container deployment](remote-container-deployment.md). The
authenticated daemon-client port is not a browser dashboard port in either
deployment.

## Default behavior

If `daemon-remote.toml` is absent, no remote configuration exists and no TCP
listener is constructed. The daemon continues to use its private local sockets
exactly as before. Merely creating a client-profile document does not enable a
listener and does not change client selection.

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

The following is the server configuration consumed by `sdsctl daemon`. An
enabled document starts the listener on the next daemon start after every
preflight check passes.

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
using those protocol objects performs no network I/O. The packaged daemon and
explicit remote-profile clients invoke them only when their opt-in settings are
selected.

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
`reload_credentials()` provides the operational rotation boundary used by the
packaged daemon's controlled `SIGHUP` lifecycle.

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

The explicit `DaemonRemoteServiceRouter` receives authenticated TLS streams and
requires one strict, bounded service selection. Only an authorized `api`
selection is delivered to the daemon API server, together with a
transport-owned peer context; the existing event, Waterfall, and PCMU protocols
use their dedicated observation leases. The router preserves the peer context
and aggregate client cap through API handoff, so API shutdown or credential
invalidation releases the exact remote client without affecting another
service. That context invokes a distinct authorization entry point before
dispatch. An `observe` identity receives
negotiation, ping, sanitized runtime/scanner state, and audio health. The
private scanner endpoint is removed from state responses. A `control` identity
adds the existing typed scanner controls. Recording status, start, stop, and
inventory remain unavailable to both scopes because remote recording contents
are outside this milestone's authority. Negotiated capabilities are filtered to
the peer's exact operation set, and a denied operation returns
`authorization_denied` without reaching runtime dispatch.

The packaged daemon now constructs these objects after local services and the
single scanner-owning runtime have been created. It still publishes no
container or Home Assistant port metadata.

## Authenticated observation leases

The `sds200.daemon_remote_observation` module provides a transport-neutral broker
over the daemon's existing event, shared Waterfall, and accepted-PCMU
publishers. The broker does not bind a socket and does not own or create a
scanner command transport, PSI stream, Waterfall poller, RTSP session, RTP
receiver, or audio decoder. The remote service router attaches authenticated
connections to these leases without becoming a second scanner owner.

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

## Service selection and shared client transport

After TLS 1.3 server-identity validation and successful challenge/proof
authentication, a client sends exactly one bounded, versioned JSON Lines
service request. Protocol `sdsctl.daemon.service`, version 1, accepts exactly
one of `api`, `events`, `waterfall`, or `audio`. The server returns one strict
success or canonical redacted failure before emitting any selected-service
bytes. Unknown fields, extra newlines, malformed or oversized frames,
unsupported versions or services, missing authority, unavailable sources, and
capacity excess fail closed.

One connection carries one selected service. There is no in-band switching or
multiplexing. After a successful selection, framing is deliberately unchanged:

| Service | Bytes after selection |
| --- | --- |
| `api` | Existing versioned daemon API JSON Lines request/response frames, with peer authorization and remote response sanitization |
| `events` | Existing event JSON Lines envelopes, beginning with the authoritative remote-filtered snapshot |
| `waterfall` | Existing checkpoint, transition, and delivery JSON Lines records from the shared `WaterfallSession` |
| `audio` | Existing binary PCMU header and delivery frames from the daemon-owned accepted-packet publisher |

`DaemonRemoteServiceRouter` consumes an already authenticated listener, applies
an aggregate client cap and a selection deadline, and hands an authorized API
connection to the existing `DaemonApiServer`. Event, Waterfall, and audio
workers hold exactly one observation lease with bounded source queues and send
deadlines. A malformed, stalled, expired, or disconnected client releases only
its own stream and lease. API handoff retains the authenticated peer context;
credential reload invalidates both API and observation connections from the old
generation. Router diagnostics contain only aggregate counts, capacities, and
stable failure classes.

`DaemonRemoteClientConfiguration` models one literal private or link-local
server address, one port, an expected TLS server hostname, an absolute CA
certificate path, and an independently provisioned client ID and mode-`0600`
credential path. It rejects wildcard, loopback, public, hostname-as-address,
URL, and relative-path inputs. Its representation and failures do not disclose
the address, hostname, client ID, or filesystem paths.

`DaemonRemoteClientTransport` uses one absolute deadline to load and recheck
trust material, connect, validate TLS 1.3 and the expected server identity,
complete challenge/proof authentication, receive authoritative scopes, and
select its exact service. It has no plaintext or certificate-verification
fallback. `for_service()` creates another immutable service transport using the
same client configuration, allowing API, event, Waterfall, and audio clients to
connect independently and reconnect without sharing a mutable stream.

The existing `DaemonApiClient`, `DaemonEventClient`, `DaemonWaterfallClient`,
and `DaemonPcmuClient` now accept either their original Unix socket location or
a `DaemonClientTransport`. Local construction preserves the exact private
socket formats and diagnostics. A remote transport declares that private state
has already been sanitized: API and event validation then requires the scanner
endpoint to be absent, and event validation also requires the private
`last_error` field to be absent. API results recursively remove scanner and
audio endpoint fields, including nested control snapshots. Audio reports only
the constant `sdsctl-remote-daemon`; the TUI uses that same label and never
caches or renders the scanner address. A purported sanitized transport that
leaks a private endpoint fails closed.

## Named client profiles

Remote clients use a separate strict document. Its default path is:

```text
$XDG_CONFIG_HOME/sdsctl/daemon-remote-clients.toml
```

When `XDG_CONFIG_HOME` is unset, the usual location is:

```text
~/.config/sdsctl/daemon-remote-clients.toml
```

Each table is selected by its exact name. The document may contain several
independent client identities, but one command selects only one:

```toml
version = 1

[profiles.pi-display]
address = "192.168.20.10"
port = 50443
server_hostname = "sdsctl-daemon.lan"
certificate_file = "/home/pi/.config/sdsctl/remote-server.crt"
client_id = "pi-display"
credential_file = "/home/pi/.config/sdsctl/pi-display.secret"
```

`address` is the literal private or link-local address used for the TCP
connection. `server_hostname` is the DNS identity encoded in the server
certificate and is checked during TLS; it is not used to discover or replace
the literal address. The certificate path contains the public CA or server
certificate used for trust. The credential path contains that client's exact
32-byte base64url secret and must have mode `0600` on POSIX.

The profile parser rejects unknown fields, unsafe names, public or wildcard
addresses, relative file paths, and invalid values. Selection is explicit:

```bash
sdsctl daemon-client --remote-profile pi-display status
sdsctl tui --daemon-client --remote-profile pi-display
```

Do not put a credential value in TOML, an environment variable, a command-line
argument, a process-manager unit, or a screenshot. Profile names and files are
never selected automatically. `--remote-profile` cannot be combined with local
daemon socket overrides.

## Beginner setup: one daemon host and one Raspberry Pi TUI

The following order keeps the scanner-owning host private until trust and one
client identity are ready. Replace the documentation addresses and names with
values from your own private network.

### 1. Install the two hosts

On the daemon host, install the interfaces the scanner owner needs. On the Pi,
install the TUI and optional local playback support:

```bash
python -m pip install "sds200[all]"
python -m pip install "sds200[tui,playback]"
```

Use separate virtual environments when that is how Python applications are
managed on the hosts. Raspberry Pi OS also normally needs `libportaudio2` for
speaker or headphone playback.

### 2. Create server identity and one independent client credential

Create a TLS certificate whose subject alternative name contains the exact
`server_hostname` that the Pi profile will expect. Keep the certificate's
private key only on the daemon host with mode `0600`. Create one independent
random 32-byte base64url credential for each client, store each in a distinct
mode-`0600` file, and transfer only that client's credential through a trusted
out-of-band method.

The Pi needs the public certificate and its own client credential. It never
needs, and must never receive, the server private key or another client's
credential. After provisioning, verify secret permissions on both hosts:

```bash
chmod 600 /etc/sdsctl/remote-server.key
chmod 600 /etc/sdsctl/clients/pi-display.secret
chmod 600 ~/.config/sdsctl/pi-display.secret
```

The containing directories should be writable only by their intended owner.
Certificate files may be readable without exposing the server private key.

### 3. Configure the daemon host

Create `daemon-remote.toml` at the default path shown above, or pass an absolute
path with `sdsctl daemon --remote-config PATH`. Start with an `observe`-only Pi:

```toml
version = 1

[listener]
enabled = true
bind_address = "192.168.20.10"
port = 50443
certificate_file = "/etc/sdsctl/remote-server.crt"
private_key_file = "/etc/sdsctl/remote-server.key"

[[clients]]
client_id = "pi-display"
credential_file = "/etc/sdsctl/clients/pi-display.secret"
scopes = ["observe"]
```

An observe-only display can read sanitized state, events, Waterfall data, and
accepted PCMU audio. It cannot issue scanner controls. Add `"control"` only for
an identity whose operator is meant to use the existing typed hold,
next/previous, volume, squelch, and reconnect controls:

```toml
scopes = ["observe", "control"]
```

Start the daemon with the same scanner selector used for local operation. The
remote service reuses that daemon's existing sources; it does not create a
second scanner command, PSI, Waterfall, RTSP, or RTP session:

```bash
sdsctl --host SCANNER_PRIVATE_IP daemon
```

If preflight or listener startup fails, fix the reported stable failure class.
The daemon does not fall back to plaintext, a wildcard address, another port,
or a weaker credential registry.

### 4. Allow only the intended firewall direction

Permit TCP traffic to the exact configured port from the Pi or its dedicated
private client subnet to the daemon host. No inbound daemon-client port is
needed on the Pi. Do not expose scanner UDP control, scanner RTP audio, the
daemon-client port, or this direct-TLS service to the public Internet.

The intended path is:

```text
Raspberry Pi client  -- TCP/TLS 50443 -->  daemon host  -->  scanner
```

Keep the daemon host's local Unix sockets private; local and authenticated
remote clients can operate concurrently without sharing a client connection.

### 5. Configure and verify the Pi

Create the Pi's `daemon-remote-clients.toml` using the profile example above.
Then verify read-only status before opening the TUI:

```bash
sdsctl daemon-client --remote-profile pi-display status
sdsctl daemon-client --remote-profile pi-display events --count 2 --json
sdsctl tui --daemon-client --remote-profile pi-display --audio-playback
```

Successful output uses only the fixed endpoint label
`sdsctl-remote-daemon`. It must not print the private address, TLS hostname,
client ID, certificate path, credential path, credential bytes, scanner
endpoint, or private daemon error detail.

Long-lived remote event, Waterfall, and accepted-PCMU consumers use a finite
exponential reconnect policy for connection loss. A recovered event stream
must begin with a new authoritative snapshot, and a recovered Waterfall stream
must begin with a new session checkpoint. Audio continuity state is reset at
reconnection. Protocol, TLS identity, authentication, authorization, service,
and local configuration failures stop immediately instead of entering a hidden
retry loop.

### 6. Rotate or revoke a client

Provision the complete replacement set of credential files and update the full
server document first. Send `SIGHUP` to the foreground daemon through the
process manager or shell that owns it. A successful reload atomically advances
the credential generation and disconnects every previous-generation API,
event, Waterfall, and audio connection. Each remote client then authenticates
again and resynchronizes.

A failed reload keeps the last-known-good registry and its generation. Review
the redacted daemon log, correct the complete replacement set, and retry. Bind
address, port, certificate, and server-private-key changes require a daemon
restart; `SIGHUP` never partially changes listener identity.

To revoke one client, set `revoked = true` on that server-side `[[clients]]`
entry while retaining at least one active identity, then perform the same
controlled reload. Delete retired secret files only after the new generation
is active and rollback is no longer required.

### 7. Disable remote access completely

Stop the daemon, replace the remote document with the minimal disabled form
shown under [Default behavior](#default-behavior), and restart the daemon. Verify
that local `sdsctl daemon-client status` still works and that no TCP listener
exists on the previously selected port. Removing the file entirely has the same
disabled result on the next daemon start. Disablement is a restart operation,
not a credential-only reload.

## Browser kiosks and other deployment surfaces

A Raspberry Pi TUI uses the authenticated daemon-client profile and port. A
browser kiosk does not: it opens the daemon host's separately configured
authenticated native HTTPS dashboard. Never place daemon client credentials in
browser storage or JavaScript.

The standalone native-Linux Docker Engine listener publication is defined in
[Remote daemon container deployment](remote-container-deployment.md). Home
Assistant App options, Ingress interaction, managed service units, and physical
one-daemon/multiple-display acceptance remain later Milestone 32 slices. Until
those surfaces explicitly publish and validate their own settings, neither the
ordinary-host nor container configuration implies that they are exposed.
