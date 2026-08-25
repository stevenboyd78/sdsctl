# Local daemon waterfall stream

Milestone 27.2 provides demand-driven text waterfall data through a private
Unix-domain socket. The daemon remains the only scanner owner. Local consumers
never open another scanner transport and never send `GST`, `PWF`, or `GWF`
directly.

This is the renderer-neutral data plane for the future web waterfall workspace.
It does not add a browser waterfall, scanner tuning, mode navigation, MQTT FFT
state, public TCP access, or binary `GW2` handling.

## Scanner protocol boundary

The implementation follows the official SDS Series Remote Command
Specification V2.00 text forms:

- `GST` retrieves the Waterfall-oriented scanner status;
- `PWF,1,ON` and `PWF,1,OFF` control type-1 text PWF publication; and
- `GWF,1,ON` and `GWF,1,OFF` control type-1 text GWF publication.

`GST` is promoted to a typed response only when its display-form length, line
pairs, and twelve documented trailing fields form one exact specification
shape. Other shapes remain generic lossless packets. Typed GST values remain raw
strings. In particular, frequency units, FFT magnitude, color, and cadence are
not inferred.

PWF records preserve all fields and empty positions. GWF becomes typed only for
exactly 240 comma-separated values. Binary `GW2` is excluded because the current
line-oriented transports cannot preserve its framing safely and the V2.00 text
contains unresolved command details.

## Shared session lifecycle

The first local waterfall client obtains a shared session lease. Session startup
retrieves one GST checkpoint, starts PWF, waits for its first typed record, then
starts GWF and waits for its first 240-value record. Failure after either start
attempts both stop wires in this order:

1. `GWF,1,OFF`
2. `PWF,1,OFF`

Later clients receive independent bounded queues without sending another scanner
start. Closing a client affects only that lease. The final client departure sends
both stop wires. Explicit shutdown, partial startup, transport interruption,
recovery, and cleanup failure are represented by immutable ordered session
transitions and snapshots.

When the owned scanner transport reconnects automatically, the receive callback
only marks the session interrupted. The daemon poll loop performs the blocking
GST/PWF/GWF restoration later under the shared control lock. This keeps scanner
commands off the receive thread, avoids competing with browser/API controls, and
emits an explicit running or failed session transition.

One slow client drops only its own oldest unread records. Each delivered record
includes that lease's cumulative `responses_dropped` and `overflows` counters.
Scanner receive dispatch and other consumers do not block behind a slow client.

## Socket location and permissions

Waterfall socket resolution uses this precedence:

1. an explicit absolute `--waterfall-socket-path`;
2. `$XDG_RUNTIME_DIR/sdsctl/waterfall.sock`; or
3. the resolved user state directory followed by `waterfall.sock`.

The managed parent directory uses mode `0700`; the socket uses mode `0600`.
Active sockets, symlinks, non-socket entries, and filesystem identity changes
receive the same guarded treatment as the other daemon sockets.

The Home Assistant App explicitly places the socket at
`/run/sdsctl/waterfall.sock`. It remains private inside the App container. The
future Ingress consumer will access it through the existing web process rather
than exposing the socket or scanner protocol to the LAN.

## JSON Lines protocol

The socket is `AF_UNIX` plus `SOCK_STREAM`. Each UTF-8 JSON record ends with one
newline and carries:

- `protocol`: `sdsctl.waterfall`;
- `version`: `1`;
- a positive per-client `sequence`;
- an aware ISO 8601 `observed_at` timestamp;
- a stable `kind`; and
- a JSON-object `payload`.

Every connection begins with `session.checkpoint`. Its payload contains the
authoritative shared-session snapshot, latest typed GST metadata, publisher
counts, consumer count, lifecycle timestamps, and last failure. Later records
are `waterfall.pwf`, `waterfall.gwf`, or `session.transition`.

PWF and GWF payloads include the radio-owned `source_sequence`, raw `values`,
source receive timestamp, and cumulative lease loss counters. The validating
client rejects an absent initial checkpoint, repeated checkpoint, unsupported
protocol/version/kind, oversized record, malformed JSON or UTF-8, naive
timestamp, and any per-client sequence gap.

The default encoded-record limit is 64 KiB and the default server limit is eight
concurrent local clients. These are framing and resource bounds, not claims
about scanner cadence or FFT semantics.

## Bounded diagnostic client

The daemon client can validate and print the private stream without opening
scanner hardware:

```console
sdsctl daemon-client waterfall --duration 10 --count 100 --json
```

`--duration` is a wall-clock deadline and `--count` is a record ceiling; the
first boundary reached closes the connection. Closing the final connection
releases demand and sends `GWF,1,OFF` followed by `PWF,1,OFF`. The command
defaults to ten seconds and 100 records. `--waterfall-socket-path` selects an
explicit socket, while `--max-record-bytes` can lower the 64 KiB client framing
limit for negative tests.

## Current validation status

Host-independent tests cover exact commands, strict GST/PWF/GWF parsing, first-
record confirmation, partial-start rollback, both-stop cleanup after a failure,
shared demand, retry, interruption and recovery, per-client overflow isolation,
canonical JSON, client ordering, socket mode, multi-client fanout, final-client
stop, and socket removal.

Physical SDS200 qualification remains required before Milestone 27.2 closes. It
must use a time- and record-bounded session while the scanner is manually placed
in Waterfall mode, record exact observed response shapes and cadence, and restore
normal scanner operation during cleanup.
