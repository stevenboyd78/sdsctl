# Portable display-service configuration

Long-running Raspberry Pi displays can use one strict private TOML manifest
instead of duplicating every durable option in a systemd `ExecStart` line or an
environment file. The manifest is a host deployment contract: it selects one
existing authorized client, its storage and presentation policy, and the
existing sdsctl launcher that will enforce those settings.

This feature does not install or enable a service, create a graphical session,
enroll a browser, copy a credential, open scanner hardware, or change a daemon.
The existing direct `tui`, `display-client-preflight`, and `browser-kiosk`
commands remain available for interactive diagnostics and recovery.

## Separate trust models

Use one manifest per service kind:

- `/etc/sdsctl/display-client.toml` describes an observe-only authenticated
  remote TUI and refers to one existing remote-daemon client profile;
- `/etc/sdsctl/browser-kiosk.toml` describes one display or operator Chromium
  kiosk, its exact HTTPS origin and public CA, and its role-bound browser
  profile.

Do not combine the two files. A daemon client credential and a browser session
have different authorization, storage and recovery behavior. Neither manifest
is the layered application file `/etc/sdsctl/config.toml`, and the global
`--config` option still means a scanner connection-profile document.

Schema version 1 intentionally does not accept a password or browser device
key. Unknown fields fail closed, including plausible credential field names.
Per-device automatic browser authentication requires an accepted server-side
enrollment, renewal and revocation protocol; a local setting must not pretend
that protocol exists or silently fall back to a shared password.

## Private-file contract

Every manifest must be:

- an absolute existing regular-file path;
- free of symlinks in the complete resolved path;
- exact mode `0600`;
- no larger than 64 KiB; and
- valid UTF-8 TOML using schema version 1.

Every security-sensitive path inside the document must be absolute. Parsing is
offline and read-only. It does not inspect credential bytes or contact a server.
The account that runs the service still needs read access to the manifest and
the referenced files. For a system display service, install the manifest for
the `sdsctl-display` account. For a graphical user service, install the kiosk
manifest for that dedicated desktop account even when the file lives under
`/etc/sdsctl`.

## Command lifecycle

Create a new candidate without overwriting anything:

```bash
sdsctl service-config template \
  --kind display-client \
  --output /absolute/private/staging/display-client.toml
```

or:

```bash
sdsctl service-config template \
  --kind browser-kiosk \
  --output /absolute/private/staging/browser-kiosk.toml
```

The output is created atomically with mode `0600`. It contains placeholders and
no credential. Edit and review the candidate, then use the read-only inspection
sequence:

```bash
sdsctl service-config validate --manifest /absolute/candidate.toml
sdsctl service-config show-sources --manifest /absolute/candidate.toml
sdsctl service-config show-command --manifest /absolute/candidate.toml
```

`show-sources` emits deterministic JSON containing the effective value and its
`default`, `manifest` or temporary `command-line` provenance. It never opens a
credential file or emits credential bytes. It does show operational paths,
profile names and the HTTPS origin, so retain the output as private deployment
evidence.

`show-command` prints the exact existing sdsctl CLI command represented by the
manifest. Compare it with the accepted old service command before changing a
unit. It also prints no credential bytes, but the output is host-specific.

For a temporary diagnostic, command-equivalence check or recovery action, the
manifest-backed commands accept a repeatable strict `--set FIELD=VALUE`
override. Explicit values take precedence and appear as `command-line` in
`show-sources`:

```bash
sdsctl service-config show-sources \
  --manifest /absolute/candidate.toml \
  --set display.startup_view=mimic \
  --set audio.playback=true
```

Boolean and numeric values use TOML scalar syntax; a bare value such as
`mimic` is treated as text. Duplicate, unknown, wrong-service and invalid
values fail closed through the same schema validation as manifest values.
Overrides are never written to the manifest. Do not place `--set` values in a
production systemd unit: durable behavior belongs in the reviewed manifest,
while direct underlying commands and these bounded overrides remain recovery
tools.

Run the existing bounded network/device preflight through the same manifest:

```bash
sdsctl service-config preflight --manifest /absolute/candidate.toml
```

Only after validation, command-equivalence review and preflight should a service
use:

```bash
sdsctl service-config run --manifest /absolute/installed.toml
```

`run` replaces itself with the existing validated `tui` or `browser-kiosk`
command. It does not maintain a second runtime implementation.

## Display-client schema

The minimal required display document selects one existing independently
revocable, observe-only remote profile:

```toml
version = 1
kind = "display-client"

[connection]
remote_profile = "pi4-display"
```

The complete schema is:

```toml
version = 1
kind = "display-client"

[paths]
config_home = "/var/lib/sdsctl-display/.config"
state_home = "/var/lib/sdsctl-display/.local/state"
cache_home = "/var/cache/sdsctl-display"

[connection]
remote_profile = "pi4-display"
timeout = 5.0

[display]
terminal = "/dev/tty1"
startup_view = "mimic"
theme = "dark"
color = "auto"

[recording]
directory = "/var/lib/sdsctl-display/recordings"
metadata = true

[audio]
playback = false
# device = "USB Audio Device"
buffer_ms = 250
history_limit = 100

[logging]
# level = "INFO"
# file = "/var/lib/sdsctl-display/sdsctl.log"
```

The generated launch plan sets the three XDG homes before the existing CLI
loads the named remote profile and application assets. It always uses
`--daemon-client --managed-display`, so the observe-only authorization check and
stable service-manager exit statuses remain active. `audio.playback = true`
requests automatic playback using the current TUI playback contract; an
optional device name or non-negative index selects the local output.

The packaged configured system unit still owns `/dev/tty1`; therefore
`display.terminal` must match `/dev/tty1` when that exact unit is installed.
Selecting a different terminal requires a separately reviewed unit with matching
`TTYPath` and `DeviceAllow` values. The manifest cannot weaken systemd device or
namespace isolation.

## Browser-kiosk schema

```toml
version = 1
kind = "browser-kiosk"

[server]
origin = "https://scanner.example:8443"
ca_file = "/etc/sdsctl/server.crt"

[kiosk]
browser = "/usr/bin/chromium"
role = "operator"
profile_directory = "/home/display/.sdsctl-operator-kiosk"

[logging]
# level = "INFO"
# file = "/home/display/.local/state/sdsctl/kiosk.log"
```

The role remains `display` or `operator`. Existing launcher checks still bind a
private browser profile to its first role and refuse cross-role reuse. The
manifest never contains the shared human password. Until device enrollment is
implemented, authentication remains the accepted manual login and ordinary
session lifecycle.

## Thin packaged units

Print the new manifest-backed units without installing them:

```bash
sdsctl service-config service --kind display-client
sdsctl service-config service --kind browser-kiosk
```

The display unit executes only:

```text
/opt/sdsctl-display/bin/sdsctl service-config run --manifest /etc/sdsctl/display-client.toml
```

The kiosk user unit executes only:

```text
/opt/sdsctl-kiosk/bin/sdsctl service-config run --manifest /etc/sdsctl/browser-kiosk.toml
```

The legacy service-template commands remain unchanged for existing v0.33.0
installations. Exporting a configured unit does not install, reload, enable or
start it.

## Guarded migration

Migrate one host at a time:

1. Preserve the active unit, drop-ins, manifest candidates, browser profile,
   daemon client profile, credential, certificate and last-known rollback
   command.
2. Record the current unit and its effective `ExecStart` without editing it.
3. Create the candidate in a private staging directory; replace host identity,
   profile names and paths explicitly. Never clone a daemon credential, browser
   profile or future device key to another host.
4. Run `validate`, `show-sources` and `show-command`; compare the latter with the
   accepted old behavior.
5. Run `preflight` while the old service remains unchanged.
6. Install the manifest and thin unit, perform one stopped-service start, and
   verify the exact role, view, recording/playback policy, graphical or console
   ownership, and recovery path.
7. Enable boot startup only after the bounded start passes. Retain the old unit
   and command as rollback evidence until the host has passed a reboot test.

Do not migrate both display hosts at once. A candidate file is not authority to
rotate credentials, alter Home Assistant, wake a scanner, or start audio.
