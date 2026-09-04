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
display. The TUI should keep its framed panels, compact footer, and normal
dashboard viewport without scrolling. Press `G` to open the bounded Operational
Logs drawer with the newest four single-line records; `?` opens Keyboard
Reference instead. The two drawers are mutually exclusive.

A larger HDMI monitor does not need the small display's terminal settings.
The TUI chooses its layout from terminal **rows and columns**, not pixel
resolution. Run `stty size` in the terminal on the physical display (an SSH
terminal may have a different size), then check the preflight's reported geometry.
On a 1080p display, the console font or graphical terminal's font and window size
determine the available text area. Keep a readable font and let the TUI select
its responsive layout; do not force the earlier 100-by-30 configuration.

At 120 or more columns and at least 32 rows, Live PSI / Controls sits directly
below Scanner State, beside Network Audio. Operational Logs occupies a separate
full-width row. You can leave it open while viewing Keyboard Reference: `?`
toggles only the reference and `G` toggles only Logs. The complete reference may
require scrolling; the compact display's mutually exclusive drawers are unchanged.

## Credential recovery

Credential registry changes close all existing remote sessions, even for other
authorized clients. An unchanged display can reconnect using its existing
credential. A revoked display instead stops on authentication failure. After
restoring its access, rerun preflight and explicitly restart its service as
described in the canonical guide. Restore reuses an unrotated credential;
rotation requires installing the new client files first.

## Home Assistant installations

The Home Assistant App's advanced daemon-client option and TCP mapping remain
disabled by default. Enable the exact private-LAN service only after its server
identity and client registry are ready. Home Assistant Supervisor publishes an
enabled mapping on all host interfaces, so restrict reachability with the
surrounding private network or firewall and never create an Internet-facing
router port-forward.

For a **temporary acceptance installation**, stop the Pi service after testing,
revoke the temporary identity, and return the App to its previous configuration.
If no production clients depend on advanced access, disable its option and
mapping, restart the App, and verify the ordinary Ingress dashboard and scanner
services recover. Do not disable a listener used by other production clients.

For a **production installation**, keep its independent production identity and
accepted private-LAN listener enabled. Do not perform the temporary-test cleanup
above. Enable boot startup only after the interactive display test passes.

Browser kiosks are different. They use the native HTTPS dashboard, dedicated
dashboard password, browser-trusted certificate, and memory-only session
cookie. The console service described here must not store those values.
