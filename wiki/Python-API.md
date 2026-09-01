# Python API

The distribution and import package remain named `sds200`; the user-facing
project and executable are named `sdsctl`. New code should use `SDSScanner`.

## USB

```python
from sds200 import SDSScanner

with SDSScanner.auto(model="SDS150") as radio:
    print(radio.get_model())
    print(radio.get_firmware())
    print(radio.get_volume())
    print(radio.get_squelch())
```

## SDS200 Ethernet

```python
from sds200 import SDSScanner

with SDSScanner.network("192.168.1.25") as radio:
    info = radio.get_scanner_info()
    print(info.system)
    print(info.department)
    print(info.channel)
    print(info.frequency)
```

## Continuous state updates

```python
from sds200 import SDSScanner

with SDSScanner.network("192.168.1.25") as radio:
    radio.on_state_change(
        lambda change: print(change.fields, change.current.channel)
    )

    with radio.scanner_info_push(interval_ms=500):
        radio.wait()
```

## Reconnect and health

```python
from sds200 import ReconnectPolicy, SDSScanner

policy = ReconnectPolicy(
    initial_delay=1.0,
    multiplier=2.0,
    max_delay=30.0,
    max_attempts=8,
)

with SDSScanner.network(
    "192.168.1.25",
    reconnect_policy=policy,
) as radio:
    print(radio.health_check().as_dict())
    print(radio.health_summary().as_dict())
```

## LAN discovery

```python
from sds200 import discover_network_scanners

for scanner in discover_network_scanners(["192.168.1.0/24"]):
    print(scanner.endpoint, scanner.model, scanner.latency_ms)
```

Only probe networks you own or are authorized to scan. Close scanners and
bounded push contexts deterministically, as shown by the context managers.
Consult the repository source, type annotations, and
[transport guide](https://github.com/stevenboyd78/sdsctl/blob/main/docs/transports.md)
for advanced integration work.

