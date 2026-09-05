# Managed Raspberry Pi TUI display

This guide turns an interactively verified Raspberry Pi remote TUI into an
opt-in, boot-resilient physical-console display. The Pi remains a thin client:
one daemon on another trusted private-LAN host owns the scanner, and the Pi
uses its own independently revocable `observe` credential.

This is not a browser-kiosk guide. A browser uses the native HTTPS dashboard,
its dedicated password, and its own session cookie. Never put a daemon-client
credential in browser storage or JavaScript.

## Safety boundary

The managed display:

- connects only through one explicitly selected authenticated remote profile;
- refuses an identity that advertises any scanner-control operation;
- never discovers a daemon or falls back to a direct scanner connection;
- never enables a listener, opens a Home Assistant App port, or changes a
  firewall;
- keeps the credential in an absolute, mode-`0600` regular file;
- reports only fixed endpoint and failure classes, not private addresses,
  identities, certificate paths, credential paths, or credential bytes; and
- retries only temporary connection loss. Configuration, certificate,
  authentication, authorization, and unexpected local failures stop the unit.

The supplied unit owns `/dev/tty1`. Starting it conflicts with the login prompt
on that console. Other consoles and SSH remain separate recovery paths.

## 1. Prepare Raspberry Pi OS

These commands use a dedicated service account and virtual environment. Run
them from an administrator account on the Pi:

```bash
sudo apt update
sudo apt install python3-venv libportaudio2
sudo adduser --system --group \
  --home /var/lib/sdsctl-display \
  --shell /usr/sbin/nologin \
  sdsctl-display
sudo chmod 0700 /var/lib/sdsctl-display
sudo usermod --append --groups tty,audio sdsctl-display
sudo python3 -m venv /opt/sdsctl-display
sudo /opt/sdsctl-display/bin/python -m pip install --upgrade pip
sudo /opt/sdsctl-display/bin/python -m pip install "sds200[tui,playback]"
```

`libportaudio2`, the `audio` group, and the playback extra are needed only when
the physical display will play live or saved audio. Local WAV recording does
not require an output device.

## 2. Install only this Pi's client material

Create private configuration and recording directories:

```bash
sudo install -d -o sdsctl-display -g sdsctl-display -m 0700 \
  /var/lib/sdsctl-display/.config/sdsctl \
  /var/lib/sdsctl-display/recordings
```

Copy the downloaded profile, public server certificate, and this Pi's client
credential into that directory. Use descriptive staging names appropriate for
your own trusted transfer method, then install them with these final names and
modes:

```bash
sudo install -o sdsctl-display -g sdsctl-display -m 0600 \
  DAEMON_REMOTE_CLIENTS_STAGING_FILE \
  /var/lib/sdsctl-display/.config/sdsctl/daemon-remote-clients.toml
sudo install -o sdsctl-display -g sdsctl-display -m 0644 \
  SERVER_CERTIFICATE_STAGING_FILE \
  /var/lib/sdsctl-display/.config/sdsctl/server.crt
sudo install -o sdsctl-display -g sdsctl-display -m 0600 \
  CLIENT_CREDENTIAL_STAGING_FILE \
  /var/lib/sdsctl-display/.config/sdsctl/client.secret
```

Edit the installed profile so its `certificate_file` and `credential_file`
values are these exact absolute paths. The profile name must match the
independent observe-only client ID created on the daemon host or in the Home
Assistant App. Do not give an unattended display the `control` scope.

Delete the three staging copies only after preflight and one interactive test
have passed. Never copy the daemon's private TLS key or another client's secret
to the Pi.

## 3. Run the managed-display preflight

Keep the daemon's private-LAN listener disabled until its complete server
identity and client registry are ready. When the listener is deliberately
enabled, keep a separate SSH recovery session open and release the physical
login prompt before inspecting the console:

```bash
sudo systemctl stop getty@tty1.service
sudo env \
  XDG_CONFIG_HOME=/var/lib/sdsctl-display/.config \
  XDG_STATE_HOME=/var/lib/sdsctl-display/.local/state \
  XDG_CACHE_HOME=/var/cache/sdsctl-display \
  /opt/sdsctl-display/bin/sdsctl \
  display-client-preflight \
  --remote-profile CLIENT_ID \
  --terminal /dev/tty1
```

Raspberry Pi OS returns the released virtual console to root ownership and mode
`0600`. Only this bounded, read-only preflight runs through `sudo` so it can
inspect that exact character device. It uses the service account's explicit
configuration paths and prints only sanitized evidence. The long-running TUI
still runs as the unprivileged `sdsctl-display` account. If preflight does not
pass and you are not continuing immediately, restore the login prompt with
`sudo systemctl start getty@tty1.service`.

Add `--audio-playback` to require a locally discoverable PortAudio output
device, and optionally select an exact name or index with `--audio-device`.
Preflight authenticates independently to the API, event, and audio services,
verifies `runtime.snapshot`, rejects control-capable identities, and prints
only sanitized evidence. Scanner readiness is reported by the TUI and does not
prevent a disconnected display from starting.

A successful 100-column by 30-row Pi console reports:

```text
Managed display preflight passed.
Terminal geometry: 100 columns x 30 rows
Responsive layout: compact-split
Remote services: API, events, audio
Authorization: observe only
Audio playback: not requested
```

Exit status `75` means the private daemon connection is temporarily
unavailable. Exit status `78` means configuration, certificate,
authentication, authorization, or service negotiation must be corrected. An
unexpected local dependency or device error exits `2`.

## 4. Install the template and perform one interactive start

Export the reviewed template from the installed Python package, install it,
remove the non-secret staging copy, and validate it:

```bash
/opt/sdsctl-display/bin/sdsctl display-client-service \
  > sdsctl-display@.service
sudo install -o root -g root -m 0644 \
  sdsctl-display@.service \
  /etc/systemd/system/sdsctl-display@.service
rm sdsctl-display@.service
sudo systemctl daemon-reload
sudo systemd-analyze verify /etc/systemd/system/sdsctl-display@.service
```

Start the instance once without enabling boot startup:

```bash
sudo systemctl start sdsctl-display@CLIENT_ID.service
sudo systemctl status sdsctl-display@CLIENT_ID.service
```

Confirm the physical display, then press `Q`. An intentional quit leaves the
disabled service inactive. If playback was installed, press `A` only after the
local audio backend and selected physical output have been verified. The TUI
derives its compact, split, standard, or wide layout from terminal geometry; do
not add a device-model layout override. On the normal 100-by-30 dashboard,
Connection sits beside Channel Details, System / Site / Channel spans the next
row, Scanner State sits beside Live PSI / Controls, and Network Audio spans the
lower row when available. A named remote profile also shows its resolved
`host:port` as `Target` in Connection.

On that compact layout, press `G` to replace Network Audio with a bounded
full-width Operational Logs drawer showing the newest four single-line records.
Logs continue buffering while hidden. Press `G` again to return to the normal
dashboard. `?` opens the Keyboard
Reference instead; the two drawers are mutually exclusive so they cannot compete
for the physical viewport.

On a larger terminal with at least 120 columns and 32 rows, Live PSI / Controls
sits directly below Scanner State, beside Network Audio. Operational Logs uses
a separate full-width row below them. `?` and `G` toggle the reference and logs
independently, so both may remain open. Opening the full reference may require
scrolling even though the ordinary dashboard fits.

The top header shows the `sdsctl` application version. Model and firmware are
separate values in the Scanner panel, not the installed application version.
On short terminals that panel uses a single content line; the 100-by-30
dashboard gives it a compact full-width row below Audio or Logs.

Choose a readable console font before starting the display. If you want to
change it later, stop the exact display instance first, apply the font using
your operating system's console settings, and then start the instance again.
Changing between 256- and 512-character fonts while a full-screen application
is running can leave its saved screen using the old character/color encoding,
which may appear as repeated symbols after quitting. Do not change the TUI's
restart policy or erase permanent-failure messages to work around that artifact.

That warning is specific to the Linux text console used by this service, such
as `/dev/tty1`. A desktop terminal window uses its own font preferences or zoom
controls; `setfont` and `/etc/default/console-setup` do not set its font. GUI
terminal font changes normally do not require quitting the TUI. The application
adapts to the resulting rows and columns, so a larger font or smaller window may
select a more compact layout. With SSH, the local terminal controls the font;
changing it does not change the remote Pi's HDMI-console font. Run `stty size`
in the terminal being used to check its current dimensions.

### Enlarge the HDMI console font (1080p example)

The readability improvement on the tested 1920×1080 HDMI display used
**Terminus, 12 pixels wide × 24 pixels high**, with the `Uni2` character set.
The installed font file was
`/usr/share/consolefonts/Uni2-Terminus24x12.psf.gz`. Its filename uses **height
then width** (`24x12`); the console configuration uses **width then height**
(`12x24`). This is a pixel size, not a GUI terminal's point size.

That font produced **160 columns × 45 rows**, keeping the wide TUI layout while
making the text larger. `stty size` prints the reverse order: `45 160` (rows,
then columns). The previous 8×16-sized console geometry was `67 240`.
Resolution, framebuffer and font support can vary; verify your own result.
Do not force this font on the smaller Pi display simply to match the example.

#### Preview safely before making it persistent

Use a separate SSH session to administer the Pi, leaving a recovery route open.
Replace `CLIENT_ID` with the profile name of the **one display instance** you
intend to change. For a manually launched TUI, quit it normally instead of
using the service commands. These commands change the physical Linux text
console `/dev/tty1`, not the SSH terminal. Do not use them on a console currently
owned by a graphical desktop or browser kiosk.

Confirm the tools and font are available before stopping the display:

```bash
command -v setfont
command -v setupcon
ls /usr/share/consolefonts/Uni2-Terminus24x12.psf.gz
```

On Debian/Raspberry Pi OS, install `console-setup` and `kbd` if these are
missing. Font packages and paths may differ on other distributions; do not
continue with a missing font file.

```bash
sudo systemctl stop sdsctl-display@CLIENT_ID.service
sdsctl_font_backup=$(mktemp -d "$HOME/sdsctl-font-backup.XXXXXX")
sudo cp /etc/default/console-setup "$sdsctl_font_backup/console-setup.before"
sudo setfont -C /dev/tty1 -O "$sdsctl_font_backup/original-font.psf" \
  /usr/share/consolefonts/Uni2-Terminus24x12.psf.gz
sudo stty -F /dev/tty1 size
printf 'Keep this font backup directory: %s\n' "$sdsctl_font_backup"
sudo systemctl start sdsctl-display@CLIENT_ID.service
```

`setfont -O` saves the previous font **and its Unicode map** while loading the
new font. Keep the printed backup path. If a command fails, stop and resolve it
before continuing. At 1920×1080, check for `45 160` and confirm readability on
the actual display. Do not use `stty rows`/`cols` to fake the expected size.
This preview alone does **not** persist the font across boot. See Debian's
[setfont manual](https://manpages.debian.org/trixie/kbd/setfont.8.en.html).

#### Save the setting and apply it before each TUI start

After accepting the preview, edit the existing configuration:

```bash
sudoedit /etc/default/console-setup
```

Set these existing font/encoding entries, preserving unrelated settings:

```ini
CHARMAP="UTF-8"
CODESET="Uni2"
FONTFACE="Terminus"
FONTSIZE="12x24"
```

Regenerate the boot font cache without changing the live console:

```bash
sudo setupcon --font-only --save-only
sudo install -d /etc/systemd/system/sdsctl-display@CLIENT_ID.service.d
sudoedit /etc/systemd/system/sdsctl-display@CLIENT_ID.service.d/console-font.conf
```

Add this instance-specific ordering, preserving any existing drop-in content:

```ini
[Unit]
Wants=console-setup.service
After=console-setup.service
```

The ordering above is useful, but was not sufficient by itself on the HDMI
installation: a later boot returned to `67 240` despite the saved font and a
successful `console-setup.service`. Add a separate, optional **HDMI-instance**
drop-in to apply the font immediately before each TUI start:

```bash
sudoedit /etc/systemd/system/sdsctl-display@CLIENT_ID.service.d/console-font-prestart.conf
```

```ini
[Service]
ExecStartPre=+/usr/bin/setfont -C /dev/tty1 /usr/share/consolefonts/Uni2-Terminus24x12.psf.gz
```

Keep this file root-owned and readable, with no user-writable executable or
font file. The `+` deliberately elevates **only this fixed preparatory command**;
the TUI retains its unprivileged account, empty capability set and other
restrictions. The service-wide device policy still applies and already allows
`/dev/tty1`. Do not add `sudo`, a shell wrapper, a user-supplied font argument,
or a `-` prefix that would ignore font-loading failure. Do not change the shared
template or automatically apply the HDMI font to other displays. See
[systemd's command-prefix documentation](https://manpages.debian.org/trixie/systemd/systemd.service.5.en.html#COMMAND_LINES).

Check the instance configuration, then stop and start the display cleanly:

```bash
sudo systemctl daemon-reload
sudo systemd-analyze verify sdsctl-display@CLIENT_ID.service
sudo systemctl stop sdsctl-display@CLIENT_ID.service
sudo systemctl start sdsctl-display@CLIENT_ID.service
systemctl show sdsctl-display@CLIENT_ID.service \
  -p ActiveState -p ExecStartPre -p NRestarts
sudo stty -F /dev/tty1 size
```

Expect a successful pre-start command (`status=0`), an active TUI, and `45 160`
at the tested resolution. If the command fails, inspect the journal and correct
the font path/access or roll back the added drop-in; do not bypass the error.
The pre-start command runs at service startup, never as an in-place font reset
underneath an existing TUI.

`--save-only` prepares cached files but does not apply the font immediately.
The pre-start command explicitly loads the selected font independently of that
cache. See the Debian manuals for
[console-setup configuration](https://manpages.debian.org/trixie/console-setup/console-setup.5.en.html)
and [setupcon](https://manpages.debian.org/trixie/console-setup/setupcon.1.en.html).

**Verify after the next planned reboot.** From SSH, run
`sudo stty -F /dev/tty1 size` and inspect the physical display. An active/exited
`console-setup.service` alone is not proof that the larger font is still loaded.
The added pre-start command passed a controlled service start and one planned
HDMI Pi reboot: `setfont` exited successfully, the enabled TUI reconnected at
`45 160`, and its main process retained zero capabilities and
`NoNewPrivileges`. This verifies those observed starts, not every graphical
stack or display hot-plug scenario. A later console-driver replacement could
still reset the font after startup. If it reverts, inspect the display service's
`ExecStartPre` result, `journalctl -b -u console-setup.service`, and framebuffer
startup ordering. Do not add a blind delay or repeatedly reset a font under a
running TUI. Keep the original backup rather than overwriting it with another
preview. The operator also confirmed the larger font remained readable and
the physical HDMI TUI was updating after this reboot.

#### Restore the previous font

In the same shell, the saved `sdsctl_font_backup` variable identifies your
backup. In a new session, set it to the exact directory printed earlier first.
Stop the TUI before restoring. If you added `console-font-prestart.conf`, remove
only that exact drop-in after reviewing it (or remove only your added line if
it contains other customizations), then run `sudo systemctl daemon-reload`
**before starting again**. Otherwise the pre-start hook would immediately
reapply the larger font and undo your restoration.

```bash
sudo systemctl stop sdsctl-display@CLIENT_ID.service
sudo setfont -C /dev/tty1 "$sdsctl_font_backup/original-font.psf"
sudo systemctl start sdsctl-display@CLIENT_ID.service
```

If you also made persistent changes, restore the saved `console-setup.before`
configuration and regenerate its cache with `sudo setupcon --font-only --save-only`.
Undo only the ordering lines you added to the instance drop-in, preserving any
pre-existing customization, then run `sudo systemctl daemon-reload`. Keep the
backup until both the appearance and the next planned boot are verified.

For **GUI terminals**, enlarge the terminal application's font or use its zoom
controls instead; Terminus 12×24 is a console bitmap font, not a required GUI
font or point size. For **SSH**, change the local terminal's font. For the
**browser WebUI**, use browser zoom/display scaling. None of those changes sets
the remote HDMI text-console font.

### Managed service template details

The source repository carries a byte-identical template at
`contrib/systemd/sdsctl-display@.service`. Automated tests require the source
and packaged copies to remain identical.

The template passes the systemd instance name to `--remote-profile`. Use
`systemd-escape` before starting or enabling the unit if an existing profile
name requires systemd instance escaping. A simple identifier containing
letters, digits, and hyphens needs no conversion.

## 5. Enable boot startup

Only after the one-start physical test passes, enable the exact instance:

```bash
sudo systemctl enable --now sdsctl-display@CLIENT_ID.service
sudo systemctl status sdsctl-display@CLIENT_ID.service
```

The unit deliberately:

- waits for `network-online.target`;
- starts after Plymouth and cloud-init when those boot-console producers are
  present, so their final status line cannot overwrite the TUI;
- opts out of the normal `multi-user.target` ordering edge, while explicitly
  retaining basic-system and clean-shutdown ordering, so waiting for a
  post-`multi-user.target` cloud-init target cannot create a boot cycle;
- binds standard input, output, and error to the physical console because
  Textual renders terminal frames through standard error;
- claims `/dev/tty1` and stops the competing `getty@tty1` login prompt;
- uses the exact `/opt/sdsctl-display` virtual environment;
- cannot open an SDS USB serial device;
- permits only the console and optional sound character devices;
- writes recordings only beneath its private state directory;
- retries temporary connection failure every 15 seconds without a busy loop;
- does not restart after exit `2`, exit `78`, or an intentional `Q`; and
- leaves the last fixed failure message visible when a permanent failure stops
  the service.

Operational failure classes are available over a separate SSH session:

```bash
journalctl -u sdsctl-display@CLIENT_ID.service --since today
journalctl -u sdsctl-display@CLIENT_ID.service -f
```

## 6. Recovery and credential changes

An ordinary daemon or Home Assistant App restart is a temporary transport
failure. The TUI first uses its bounded in-process reconnect sequence; if the
process exits with status `75`, systemd waits 15 seconds and starts a fresh
session. A fresh session begins from a new authoritative snapshot.

Changing any client in the Home Assistant App reloads its credential registry
and closes all existing remote-client sessions. An unchanged, still-authorized
display can reconnect using its existing files through the normal temporary-loss
recovery path. Its connection is not preserved uninterrupted through the reload.

Revocation, a replaced certificate, or an invalid credential is permanent from
the Pi's perspective. The display stops instead of repeatedly authenticating.
Restoring an unrotated client makes its retained credential valid again; rotation
requires replacing the affected client files. After deliberately restoring access
or installing the replacement files, rerun the bounded preflight from section 3
with `getty@tty1` stopped, then restart the unit:

```bash
sudo systemctl restart sdsctl-display@CLIENT_ID.service
```

Do not change the unit to `Restart=always`; that would turn permanent
authentication failures and an operator's intentional quit into a retry loop.

## 7. Upgrade, disable, or remove

Upgrade only after reviewing the target version. Stop the display before
replacing its package, install the exact published release, check dependencies,
then start it again. For v0.29.2:

```bash
sudo systemctl stop sdsctl-display@CLIENT_ID.service
sudo /opt/sdsctl-display/bin/python -m pip install --upgrade \
  "sds200[tui,playback]==0.29.2"
sudo /opt/sdsctl-display/bin/python -m pip check
sudo systemctl start sdsctl-display@CLIENT_ID.service
```

If installation or the dependency check fails, keep the service stopped and
restore the previously reviewed package version before starting it. The existing
client profile, credential, certificate, recording directory, console font,
and service enablement do not need to change for this presentation-only update.

To return `/dev/tty1` to its ordinary login prompt:

```bash
sudo systemctl disable --now sdsctl-display@CLIENT_ID.service
sudo systemctl start getty@tty1.service
```

For permanent removal, first revoke the exact client identity on the server.
Then remove only this display's unit, virtual environment, and state directory:

```bash
sudo rm /etc/systemd/system/sdsctl-display@.service
sudo systemctl daemon-reload
sudo rm -rf /opt/sdsctl-display
sudo rm -rf /var/lib/sdsctl-display
sudo rm -rf /var/cache/sdsctl-display
sudo deluser sdsctl-display
```

Those paths are deliberately exact. Do not recursively remove a parent such as
`/opt`, `/var/lib`, `/var/cache`, `/etc`, or a home directory.

## Acceptance checklist

- Reboot the Pi and confirm the TUI appears without a typed command.
- Confirm the expected terminal geometry and responsive layout.
- Confirm the footer remains visible and the normal dashboard does not scroll.
- On the compact 100-by-30 display, press `G` and confirm the full-width
  Operational Logs drawer opens without scrolling; press `G` again and confirm
  the normal lower panel returns.
- On that compact display, press `?` and confirm the Keyboard Reference replaces
  any open log drawer. The full reference may require scrolling.
- On a wide, tall display, confirm Logs spans the full width and `?` and `G`
  toggle their panels independently; both may remain open while scrolling.
- For a named remote profile, confirm Connection shows the expected resolved
  private-LAN `Target` without exposing credential material.
- Restart the daemon or Home Assistant App and confirm automatic recovery.
- Interrupt and restore the private-LAN path and confirm bounded recovery.
- Revoke the Pi identity and confirm a sanitized permanent failure without a
  retry storm; restore or rotate it, rerun preflight, and restart the unit.
- Confirm the remote daemon remains the only scanner owner.
- When selected, validate local playback, local recording finalization, and
  metadata without disclosing the private daemon address.
- Disable the temporary listener or Home Assistant mapping and remove the exact
  acceptance credential and files when testing is complete.
