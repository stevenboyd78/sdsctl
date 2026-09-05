# Display-only browser kiosk — candidate setup and testing

**Unreleased candidate. These commands are not in v0.29.2.** Use only a reviewed
candidate checkout/build for now. No Pi graphical stack or cold boot has yet
been physically accepted for this feature. This guide does not replace the
[production TUI guide](managed-pi-display.md).

## What this screen can do

The browser shows the scanner, Waterfall and read-only diagnostics from one
existing daemon. It cannot hold or navigate the scanner, reconnect it, play
audio, record, browse/download recordings, manage Home Assistant, or change
credentials. The server enforces this even if someone changes the page's HTML.
Theme selection and Waterfall pause/history/pointer controls affect only the
local display. Visible Waterfall still acquires shared scanner demand through
the daemon; it is not a separate scanner owner.

This first version uses **manual sign-in**. A session lasts at most eight hours;
inactive sessions can expire after thirty minutes. Restarting the web service
also requires a fresh login. The screen marks old data stale and stops its
consumers when authentication ends. Relaunching a browser is not automatic
login. Each server has one shared display password, separate from its operator
password; independently revocable browser devices are deferred.

## 1. Choose a test machine

Use a separate Linux desktop account and a dedicated test display. Keep SSH or
another recovery path available. Record the OS, browser version, compositor,
resolution and input devices before installation. A Pi with console auto-login
does not necessarily have a graphical desktop running.

Raspberry Pi's [official kiosk tutorial](https://www.raspberrypi.com/tutorials/how-to-use-a-raspberry-pi-in-kiosk-mode/)
uses Chromium in a graphical session and a labwc autostart file. Do not overwrite
that file or assume the same setup on a different OS. This candidate's launcher
works inside an existing graphical session; it does not install a desktop,
change auto-login, claim a console, alter its font, or stop a TUI.

The client needs the base Python package and an installed Chromium-family
browser, not a scanner USB connection or the Python `tui`/`playback` extras.
Install the candidate into an isolated environment, keeping its path separate
from `/opt/sdsctl-display`. The example service expects `/opt/sdsctl-kiosk`;
adjust it to the reviewed installation path before testing.

## 2. Prepare the server and certificate

For a Home Assistant candidate App, open **Home Assistant → Advanced access** in
the sdsctl Ingress dashboard. Under native dashboard passwords, select **Create
or rotate display password**, review the confirmation, and save its one-time
value privately. It clears from that page after sixty seconds. Do not use or
rotate the operator password for the display. Restart the App to load the new
display password. The old password remains active until that restart.

Use the existing [native HTTPS setup](home-assistant-advanced-access.md), not the
remote-daemon port or an exposed Ingress listener. Native access is off by
default. Creating a display password does not enable it. Review any required
host-wide port mapping and coordinate an App restart with existing clients.

For a standalone candidate server, add this to its existing authenticated-LAN
`sdsctl web` command:

```text
--lan-display-password-file /absolute/private/display-password
```

The file must meet the same private-file checks as the operator password file,
contain a different password of at least sixteen characters, and never be
committed. Restart the web process after changing it. To disable display login,
remove this optional argument and restart. For an App, remove only its exact
`/data/advanced-access/display-password` file through the approved private App
maintenance workflow, then restart; do not delete other access material.

Verify the server certificate identity and fingerprint through a trusted route.
Configure trust in the dedicated browser account and test the exact HTTPS URL
interactively. The preflight CA file and the browser's trust store are separate;
preflight success alone does not prove Chromium trusts the certificate. Never
copy the server private key or bypass a certificate warning.

## 3. Preflight, then launch interactively

Replace the fictional hostname and example paths below with the reviewed values.
The public certificate file contains no server private key.

```bash
/opt/sdsctl-kiosk/bin/sdsctl browser-kiosk \
  --origin https://scanner.example:8443 \
  --ca-file /home/display/server.crt \
  --preflight-only
```

This checks issuer/name trust and the enabled display-login endpoint without
submitting a password. It rejects redirects and uses a five-second network
timeout without inheriting an environment proxy.

Run the next command **from the dedicated account's graphical terminal**:

```bash
/opt/sdsctl-kiosk/bin/sdsctl browser-kiosk \
  --origin https://scanner.example:8443 \
  --ca-file /home/display/server.crt \
  --browser /usr/bin/chromium \
  --profile-directory /home/display/.sdsctl-kiosk
```

Confirm the actual browser executable first. The launcher refuses root and
requires `DISPLAY` or `WAYLAND_DISPLAY`. It creates only the exact profile
directory, requires private ownership/mode `0700`, locks it against a second
launcher, and refuses an existing non-kiosk profile. Parents must already exist.
The browser remains sandboxed. No password, cookie or TLS exception is supplied
in its command line. A dedicated [Chromium data directory](https://www.chromium.org/developers/creating-and-using-profiles/)
isolates it from ordinary browsing, but it may still contain sensitive cookies
and saved browser data. It is not guaranteed to be memory-only storage.

At **Display-only sign in**, enter the display password manually. Never enter
the operator password into this profile. **Sign out** returns to display login;
closing the browser or pressing Ctrl+C stops the foreground launcher. Successful
close returns `0`, a browser crash or transient connectivity failure returns
`75`, and configuration/preflight contract errors return `78`.

## 4. Optional graphical-session service

Only after interactive testing, inspect the packaged template:

```bash
/opt/sdsctl-kiosk/bin/sdsctl browser-kiosk-service
```

It is a **user service**, not the TUI's system console service. It reads
`%h/.config/sdsctl-kiosk.env` with these non-secret settings:

```ini
SDSCTL_KIOSK_ORIGIN=https://scanner.example:8443
SDSCTL_KIOSK_BROWSER=/usr/bin/chromium
SDSCTL_KIOSK_CA_FILE=/home/display/server.crt
```

Keep passwords out of this file. Review the template's executable path and
profile directory, then install it under the dedicated account's user-service
directory. Confirm the desktop actually starts `graphical-session.target` and
exports its display environment to the user manager before enabling the unit.
[systemd's desktop integration guidance](https://github.com/systemd/systemd/blob/main/docs/DESKTOP_ENVIRONMENTS.md)
describes that boundary; a compositor may need its own integration. An SSH
session or `loginctl enable-linger` alone does not create a graphical session.

The template retries failures after fifteen seconds, allows three starts per
five-minute window, stops on exit `78`, and does not restart a clean browser
close. Session expiry keeps the browser open at login-required instead of
restarting it. After the exact host is qualified, use its user manager to
start/stop the installed unit and inspect its journal. Do not enable boot
startup merely because a unit passed syntax validation.

## Acceptance, upgrade and removal

Record each actual result: readable geometry; theme switching; live status;
visible/hidden Waterfall; denied controls and downloads; two concurrent clients;
temporary network outage; expiry; sign-out; web/App restart; certificate failure;
intentional close; and, separately, a physical cold start. Old data must be
labeled stale. Neither console TUI may be disrupted by the new display.

For upgrades, stop only this kiosk's service, preserve its private profile and
public trust material, update the isolated package, rerun preflight and the
interactive check, and restart only that service. Keep a verified rollback
package. Browser profile compatibility is separate from Python package rollback.

For removal, stop/disable only the installed kiosk unit and remove its exact
unit/environment file. Rotate the shared display password and restart the web
service if access must be invalidated; this affects all browsers using that
display password. Remove only the named kiosk profile and any dedicated trust
entry after reviewing them. Never clear a personal browser profile, unrelated
certificate store, production TUI credentials or a listener needed by others.
