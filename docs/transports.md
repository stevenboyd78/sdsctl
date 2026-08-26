# Control transports

The radio protocol is separated from the connection mechanism. `SDSScanner`
depends on the `ControlTransport` protocol, while `SerialTransport` provides
USB control for the SDS100, SDS150, and SDS200 and `UdpTransport` provides
SDS200 Ethernet control.

## USB serial

USB serial is supported by all three scanner models. Pass `model` to narrow
automatic discovery and verify the `MDL` response:

```python
from sds200 import SDSScanner

with SDSScanner.auto(model="SDS150") as radio:
    print(radio.get_model())
```

An optional [Linux udev rule](udev.md) is available for systems that do not
grant the active desktop user access to the scanner serial port.

An explicit path can also be used:

```python
from sds200 import SDSScanner

radio = SDSScanner("/dev/serial/by-id/usb-UNIDEN_...")
```

## SDS200 Ethernet control

The scanner's virtual-serial network protocol sends ordinary remote commands
as CR-terminated UDP datagrams. It uses scanner port `50536` by default and
does not require negotiation or a protocol header.

```python
from sds200 import SDSScanner

with SDSScanner.network("192.168.1.50") as radio:
    print(radio.get_model())
    print(radio.get_firmware())
```

The same high-level API works over either transport:

```python
with SDSScanner.network("192.168.1.50") as radio:
    radio.on_state_change(
        lambda change: print(change.fields, change.current.channel)
    )
    with radio.scanner_info_push(500):
        radio.wait()
```

Advanced socket options are available when a specific local interface or port
is required:

```python
radio = SDSScanner.network(
    "scanner.local",
    remote_port=50536,
    local_host="192.168.1.10",
    local_port=42000,
)
```

With the default local address and port, the connected UDP socket lets the
operating system select the route-specific interface and an ephemeral port. A
port-only override resolves and binds the route-selected local IPv4 address.
Explicit `0.0.0.0` binds are rejected.

The UDP transport reassembles numbered XML datagrams using the network
`Footer` node's `No` and `EOT` attributes. Production reconstruction accepts at
most 256 fragments, 10,000 retained XML elements including nested descendants,
64 levels of document depth, 4 MiB of aggregate UTF-8 fragment source, and ten
seconds from fragment 1 on a monotonic clock. The removed `Foot` or `Footer`
marker is not part of the retained-element count. Exact-limit sequences are
accepted; a sequence that exceeds any limit is discarded rather than being
passed to the protocol parser.

After transport framing has delivered a CR-delimited line, the shared
`XmlResponseAssembler` applies an independent boundary to XML responses: 10,000
lines, 4 MiB of UTF-8 source, 10,000 parsed elements, 64 levels of nesting, and
ten seconds from the recognized command header by default. These are assembler
bounds; they do not claim to bound bytes retained by a transport before it finds
a line delimiter. Synchronous incremental XML parser callbacks establish
structural completion without retaining a second parsed tree, including
self-closing roots and closing tags with legal whitespace; text that merely
resembles a closing-tag suffix cannot complete a document.

At most one daemon watchdog timer serves an assembler. It clears an idle partial
document at the deadline even when no later line arrives. A malformed, expired,
over-limit, or disconnected document resets the assembler and emits at most one
payload-free `protocol_error`; a late XML-looking continuation is discarded,
while the first ordinary scanner packet and any later valid command header remain
available for immediate recovery.

UDP is connectionless. `radio.connected` means the local UDP socket is open; it
does not prove that the scanner is powered on or reachable. A command timeout
is the authoritative indication that no response arrived.

The SDS200 network-control protocol has no authentication or encryption layer.
Keep it on a trusted LAN or access it through a VPN. Do not forward UDP port
50536 directly from the public Internet.

Network audio is a separate protocol and is not part of `UdpTransport`.

SDS100 and SDS150 do not use this native UDP control transport.

## Capture and replay transports

`RecordingTransport` wraps USB, UDP, fallback, or custom control transports
and records connection changes, transmitted commands, and received CR-delimited
lines as JSON Lines. `ReplayTransport` consumes those files through the same
`ControlTransport` contract.

```python
from sds200 import SDSScanner

with SDSScanner.replay("captures/sds100-info.jsonl") as radio:
    print(radio.get_model())
```

Replay is strict and immediate by default. See
[Session capture and replay](replay-and-capture.md) for CLI usage, timing,
fixture construction, and redaction guidance.

## Custom transports

A custom transport must expose an endpoint, connection state, CR-delimited
incoming lines, command writes, and lifecycle methods:

```python
from sds200 import SDSScanner

radio = SDSScanner.from_transport(my_transport)
```

This contract allows future connection types to reuse commands, parsing, state,
events, tracing, and monitoring without duplicating radio logic.

## UDP resilience and statistics

Numbered XML fragments are validated using their `Footer` sequence number.
Missing-first, invalid-footer, sequence-gap, limit, and expiry failures emit a
`TransportDiagnostic` and discard the complete in-progress sequence. Lifetime
expiry is checked on every decoder feed and on every UDP receive timeout. A later
valid fragment 1 can resynchronize reconstruction. Within the configured retry
budget, the transport retries the most recent tracked GSI or PSI request, exact
`GLT,FL` request, or exact MSI request. The default retry limit is two:

```python
radio = SDSScanner.network("192.168.0.251", max_xml_retries=3)
```

Transport counters are available through a radio health check:

```python
with SDSScanner.network("192.168.0.251") as radio:
    health = radio.health_check()
    print(health.latency_ms)
    print(health.statistics)
```

An exception raised by the decoded-line application handler discards only that
line. It increments the `handler_errors` statistic, updates `last_diagnostic`,
and emits a `handler_error` diagnostic containing the exception class and
endpoint without the decoded line or exception message. Later lines in the same
datagram and later datagrams continue through the reader. UDP receive debug logs
record datagram byte counts and decoded-line character counts rather than packet
contents.

A UDP socket being open does not establish remote liveness. The health check's
successful command round trip is the meaningful reachability test.


## Fallback transport

`FallbackTransport` composes ordered serial and UDP candidates while preserving
the `ControlTransport` contract. Candidate reconnect loops are disabled because
the coordinator owns activation, retry, and switching. Transport diagnostics and
active-transport statistics are forwarded through the existing radio events.

Audio does not use `ControlTransport`; see [Audio subsystem architecture](audio.md).
