# First Connection

Use this page after installing the Python package or starting a supported
container. It proves that `sdsctl` can identify the scanner before you add a
TUI, daemon, dashboard, audio, or automation.

## Before you begin

- Put an SDS100, SDS150, or SDS200 USB connection into serial mode.
- For SDS200 Ethernet, keep the scanner and computer on a trusted local
  network.
- Do not probe a network you do not own or have permission to scan.

## Find a USB scanner

Connect the scanner and run:

```bash
sdsctl discover
sdsctl info
```

A successful `info` command reports the detected model and firmware. Start the
terminal monitor only after that succeeds:

```bash
sdsctl monitor
```

Stop the monitor with `Ctrl+C`.

If more than one USB scanner is connected, select a model:

```bash
sdsctl --model SDS100 info
sdsctl --model SDS150 info
sdsctl --model SDS200 info
```

For a stable Linux device path, inspect `/dev/serial/by-id/` and pass the exact
path explicitly:

```bash
ls -l /dev/serial/by-id/
sdsctl --port /dev/serial/by-id/SCANNER_SERIAL_DEVICE info
```

Use the [Linux udev guide](https://github.com/stevenboyd78/sdsctl/blob/main/docs/udev.md)
if the device exists but the current user cannot open it.

## Find an SDS200 on Ethernet

Search one authorized local network:

```bash
sdsctl discover --network 192.168.1.0/24 --network-only
```

Then use the reported scanner address:

```bash
sdsctl --host SCANNER_IP info
sdsctl --host SCANNER_IP scanner-info
sdsctl --host SCANNER_IP monitor
```

The SDS200 control service uses unauthenticated, unencrypted UDP port `50536`.
Keep it on a trusted LAN or behind a secured VPN; never forward it directly to
the public Internet.

## Save a connection profile

Profiles are useful after the direct connection works. Discover and save a
preferred SDS200 endpoint with:

```bash
sdsctl profile discover home \
  --network 192.168.1.0/24 \
  --prefer network
sdsctl --profile home info
```

A profile can retain both USB and Ethernet endpoints and use bounded fallback.
See [discovery and profiles](https://github.com/stevenboyd78/sdsctl/blob/main/docs/discovery-and-profiles.md)
and [fallback profiles](https://github.com/stevenboyd78/sdsctl/blob/main/docs/fallback-profiles.md)
before enabling automatic preferred-endpoint recovery.

## If it does not work

Open [Troubleshooting](Troubleshooting) for USB permissions, serial-mode,
Ethernet discovery, firewall, and stale-path checks. When asking for help,
include sanitized output from:

```bash
sdsctl --version
python --version
sdsctl discover
```

Do not publish scanner addresses, credentials, Favorites data, recordings, or
unreviewed capture files.

## Next steps

- [Choose an everyday interface](Using-sdsctl)
- [Play or record SDS200 audio](Audio-and-Recordings)
- [Run in Home Assistant](Home-Assistant)
- [Configure and diagnose a service](Operations-and-Diagnostics)
