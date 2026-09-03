# Managed Raspberry Pi display

Use this workflow only after an observe-only remote TUI works interactively on
the Raspberry Pi. It gives the physical `/dev/tty1` console a boot-resilient
systemd service while one daemon on another trusted private-LAN host remains
the only scanner owner.

## Before you begin

You need:

- Raspberry Pi OS with Python 3.11 or newer;
- SSH or another console for recovery;
- one independent daemon-client identity with **Observe** access;
- that identity's downloaded profile, `server.crt`, and `client.secret`; and
- an already working private-LAN daemon-client listener.

Do not use a Control identity for an unattended display. Do not copy the
server's private key, another client's secret, or a native-dashboard password
to this service.

## What the managed mode adds

The `display-client-preflight` command verifies the physical console, TUI
dependency, API, event and audio services, runtime protocol, and observe-only
authorization. It prints terminal geometry and the responsive layout without
printing the private endpoint or identity.

The managed TUI option adds service-manager exit classes:

- `75`: temporary connection loss; the supplied unit retries after 15 seconds;
- `78`: permanent profile, TLS, authentication, authorization, or service
  configuration failure; the unit stops; and
- `2`: unexpected local dependency or device failure; the unit stops.

An intentional `Q` exits successfully and also leaves the service stopped.
This prevents a revoked credential or operator quit from becoming an infinite
restart loop.

## Install and validate

The canonical guide contains the reviewed account creation, isolated virtual
environment, exact file locations and modes, preflight, interactive test,
systemd installation, recovery, upgrade, disablement, and removal commands:

[Open the managed Raspberry Pi TUI deployment guide](https://github.com/stevenboyd78/sdsctl/blob/main/docs/managed-pi-display.md).

The installed package exports the supplied template without needing a source
checkout:

```bash
sdsctl display-client-service
```

The source repository also carries a byte-identical copy at
`contrib/systemd/sdsctl-display@.service`.

At the physically qualified 100-column by 30-row geometry, successful
preflight reports the `compact-split` layout introduced for the Raspberry Pi
display. The TUI should keep its framed panels, newest two operational log
rows, compact footer, and initial viewport without scrolling.

## Home Assistant installations

The Home Assistant App's advanced daemon-client option and TCP mapping remain
disabled by default. Enable the exact private-LAN service only after its server
identity and client registry are ready. Home Assistant Supervisor publishes an
enabled mapping on all host interfaces, so restrict reachability with the
surrounding private network or firewall and never create an Internet-facing
router port-forward.

After acceptance, stop the Pi service, revoke the temporary identity, disable
the advanced App option and mapping, restart the App, and verify the ordinary
Ingress dashboard and scanner services recover. Keep an intentionally
production-managed display identity only when its private-LAN listener is an
accepted permanent deployment.

Browser kiosks are different. They use the native HTTPS dashboard, dedicated
dashboard password, browser-trusted certificate, and memory-only session
cookie. The console service described here must not store those values.
