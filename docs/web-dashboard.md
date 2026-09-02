# Web dashboard

Milestone 20.1 established the optional daemon-backed HTTP service and
loopback-only command. Milestone 20.2 added the first accessible responsive,
read-only browser shell. Milestone 20.3 added live ordered browser updates over
Server-Sent Events. Milestone 20.4 added explicit browser playback of daemon-owned
PCMU audio. Milestone 20.5 added daemon-owned recording workflows, finalized
recording inventory, and safe saved-WAV playback and download. Milestone 20.6
added capability-negotiated scanner hold, previous/next navigation, and bounded
reconnect controls without changing daemon scanner ownership. Milestone 20.7
adds browser-local system-adaptive, LCARS-inspired, Matrix-inspired,
First Responder, and Amateur Radio themes over the same accessible dashboard
structure, including immersive full-screen desktop compositions and compact
responsive fallbacks for the four custom environments. Milestone 20.11 adds an
explicit Home Assistant Ingress mode, prefixed-path-safe assets and API requests,
Ingress framing policy, and browser-audio compatibility for non-secure Home
Assistant browser contexts without changing standalone loopback defaults.
Milestone 26.1 adds a separate password-authenticated native-TLS mode for one
explicit LAN interface while preserving the default, generic-container, and
Home Assistant security boundaries. Milestone 27.3 replaces the original card
grid with a viewport-owned Scanner, Controls, Waterfall, Audio, Recordings, and
Diagnostics workspace, redesigns the stable System theme around the existing adaptive
scanner-display model, and adds an original Pip-Boy-inspired built-in theme
without changing daemon ownership, authentication, or Ingress behavior.
Milestone 27.4 adds a sixth Waterfall pane over the private validated daemon
waterfall stream. Its bounded Canvas renderer remains explicitly relative and
uncalibrated and creates scanner demand only while the visible pane has an
authenticated browser consumer. Milestone 29.5 keeps the nominal text-GWF
schedule phase-stable and carries bounded timing plus refreshed typed GST status
to the same renderer without changing scanner ownership.

## Architecture

The web service is a daemon client. It does not open USB serial hardware, create
an SDS200 UDP control connection, or start a second RTSP/RTP audio session.

Run the foreground daemon as the single scanner owner:

```bash
sdsctl --log-level INFO --host 192.168.0.251 daemon
```

Then start the web service in a separate terminal:

```bash
sdsctl web
```

The web process resolves all five private daemon sockets used by the dashboard:

- `daemon.sock` supplies bounded request-response status, recording operations,
  and inventory reads;
- `events.sock` supplies the authoritative snapshot-first ordered event stream;
- `pcmu.sock` supplies accepted RTP PCMU packets for explicit browser playback;
- `recordings.sock` supplies finalized WAV bytes by daemon inventory-relative
  identifier; and
- `waterfall.sock` supplies ordered, validated session checkpoints,
  transitions, PWF records, and 240-value GWF frames on demand.

Their default locations are under `$XDG_RUNTIME_DIR/sdsctl`, with the existing
user-state fallback when `XDG_RUNTIME_DIR` is unavailable.

Select explicit sockets when needed:

```bash
sdsctl web \
  --daemon-socket-path /run/user/1000/sdsctl/daemon.sock \
  --daemon-event-socket-path /run/user/1000/sdsctl/events.sock \
  --daemon-pcmu-socket-path /run/user/1000/sdsctl/pcmu.sock \
  --daemon-recording-file-socket-path /run/user/1000/sdsctl/recordings.sock \
  --daemon-waterfall-socket-path /run/user/1000/sdsctl/waterfall.sock
```

Every browser event connection creates an independent local
`DaemonEventClient`. The client validates the daemon event protocol, requires
the first event to be `stream.snapshot`, and enforces strictly increasing,
gap-free sequence delivery. Closing the browser stream closes that client
without stopping the daemon event service or scanner ownership.

Every browser audio connection creates an independent local `DaemonPcmuClient`.
The web service forwards each validated daemon PCMU v1 frame exactly as encoded;
it does not decode, re-encode, or create another scanner RTSP/RTP session.
Stopping playback or leaving the page closes that browser PCMU client without
affecting daemon audio ownership or other subscribers.

Browser recording commands use short-lived `DaemonApiClient` connections. The
daemon recording manager attaches `PcmWavSink` to the existing decoded-PCM router,
so starting a browser recording never creates another scanner RTSP/RTP session.
The recording remains daemon-owned if the page reloads or the web process exits.

Completed WAV playback and download use `recordings.sock`, not `pcmu.sock`.
The web service passes only daemon inventory-relative identifiers to
`DaemonRecordingFileClient`; it never opens recording filesystem paths itself.
The daemon recording-file service revalidates the inventory entry and securely
reopens it before streaming bytes.

Every visible Waterfall pane creates one independent `DaemonWaterfallClient`.
The local client enforces the `sdsctl.waterfall` protocol and version, requires
an initial session checkpoint, rejects sequence gaps, and accepts no record
larger than the configured bound. Hiding the pane, hiding or leaving the page,
or losing the stream aborts the same-origin response and closes that private
client. Closing the daemon session's final consumer releases shared demand and
performs the existing GWF/PWF cleanup; the web process never sends scanner
waterfall commands and never exposes the Unix socket path to the browser.

## Installation

Install the optional web dependencies:

```bash
python -m pip install "sds200[web]"
```

The extra installs FastAPI and Uvicorn. Development installations also include
HTTPX2 for host-independent HTTP tests.

## Local binding and security boundary

The service binds to `127.0.0.1:8000` by default:

```bash
sdsctl web
```

Select another loopback address or local port:

```bash
sdsctl web \
  --listen-address ::1 \
  --listen-port 8123
```

The command accepts `localhost`, IPv4 loopback addresses, and IPv6 loopback
addresses. It rejects wildcard addresses, LAN addresses, public addresses, and
hostnames other than literal `localhost`.

Default loopback mode does not enable the application login or TLS. Do not
publish or proxy that listener to another host.

### Authenticated LAN mode

Authenticated LAN access is a separate all-or-nothing mode. It requires a
specific LAN interface address, one canonical HTTPS browser origin, a password
environment-variable reference, and native TLS certificate and private-key
files:

```bash
export SDSCTL_WEB_PASSWORD='replace-with-at-least-16-characters'
chmod 600 /etc/sdsctl/dashboard.key

sdsctl web \
  --authenticated-lan \
  --lan-listen-address 192.168.1.25 \
  --listen-port 8443 \
  --lan-origin https://scanner.example:8443 \
  --lan-password-env SDSCTL_WEB_PASSWORD \
  --lan-tls-certfile /etc/sdsctl/dashboard-fullchain.pem \
  --lan-tls-keyfile /etc/sdsctl/dashboard.key
```

Open `https://scanner.example:8443/`. The unauthenticated root redirects to the
password form at `/auth/login`. The DNS name must resolve to the selected
interface, the certificate must contain the origin hostname (or literal IP) in
its Subject Alternative Name, and the browser must trust its issuer. The
origin's effective port must equal `--listen-port`; port 443 may be omitted from
both the origin and browser URL. The origin cannot contain credentials, a path,
a query, or a fragment.

`--lan-listen-address` accepts only one literal RFC 1918 IPv4, IPv4 link-local,
IPv6 unique-local, or IPv6 link-local address. It rejects wildcard, loopback,
public/global, and hostname binds. Selecting a specific interface is
intentional: this mode does not listen on every current or future interface.
IPv6 link-local addresses may include a platform scope identifier, while the
browser origin should normally use a certificate-covered DNS name.

The password value is never accepted as a command-line argument. The CLI
resolves only the name supplied to `--lan-password-env`, requires at least 16
characters, and does not include the value in errors or authentication object
representations. Restart the web process after rotating the environment value.
A restart also invalidates every process-local browser session.

At startup, the authentication object derives a 32-byte scrypt verifier with a
fresh 16-byte process-local salt and the lower-memory OWASP profile
`N=2^14`, `r=8`, `p=5`. Candidate derivation runs outside the application event
loop in a dedicated worker, and only one derivation may be active at a time so
login work cannot consume the worker pool used by dashboard streams. The worker
stops accepting work during application shutdown; an already-running bounded
derivation is allowed to finish. A new worker is created only if the
authentication object is reused. The authentication object does not retain a
fast SHA-256 password verifier; SHA-256 remains appropriate only for indexing
the independently random 256-bit session tokens.

The certificate and key paths must be absolute readable regular files no larger
than 1 MiB. Only an unencrypted PEM service key is supported; an encrypted PEM
key is rejected instead of allowing an interactive startup prompt. On POSIX,
the key must grant no permissions to the POSIX `other` class (`chmod 600` is the
simplest setting; a deliberately service-group-readable `0640` key is also
accepted).
Malformed or mismatched certificate material fails during native Uvicorn TLS
startup before the listener is used.

The application permits one exact HTTPS `Host` and requires that exact
`Origin` on login and every state-changing request. Every application route
other than the login form, including health, static assets, APIs,
documentation, SSE, live audio, recordings, and downloads, is authenticated
before a private daemon client is created. WebSocket requests fail closed.
Sessions use an opaque 256-bit
server-side token in the `__Host-sdsctl-session` cookie with `Secure`,
`HttpOnly`, `SameSite=Strict`, and `Path=/`; only a token digest is retained.
Sessions have a 30-minute idle lifetime, an eight-hour absolute lifetime, a
64-session process limit, and a 16-request per-session concurrency limit.
Re-login rotates the current cookie, while logout, replacement, eviction, or
absolute expiry terminates associated long-lived responses without affecting
other sessions.

Failed-login accounting uses a one-minute window bounded per peer, across the
process, and to at most 256 tracked peers. Limits and concurrent-derivation
admission are checked before spending scrypt work, and malformed submissions
without one password do not consume the failure budget. A peer at its limit is
rejected until its failures expire. After the global limit, at most one recovery
derivation is admitted every five seconds; a matching password clears the
global failures. These login limits do not affect established sessions. Login
bodies are limited to 4 KiB and five seconds. Authentication responses and
protected content use `Cache-Control: no-store`, HSTS, and `nosniff`
protections.

Native TLS terminates directly in Uvicorn. Proxy-header trust remains disabled;
a reverse proxy is not part of this mode. Wildcard binding, public/global
binding, port forwarding from the public Internet, generic-container LAN
publication, and trusted-reverse-proxy deployment remain unsupported. The
authenticated LAN, Home Assistant Ingress, and generic container modes are
mutually exclusive.

Generic Compose deployment has one separate explicit container mode:
`sdsctl web --container-exposure`. It binds exactly `0.0.0.0:8000` inside the
container so Docker bridge publication can reach it, but Compose publishes it
only on Docker-host loopback. The default browser URL is
`http://127.0.0.1:8000/`; set `SDSCTL_WEB_PORT` to change only that host-loopback
port, not the fixed container port 8000. LAN and public clients cannot reach the
Compose publication by default. The internal wildcard is safe only because the
publication is constrained to `127.0.0.1`; never copy `--container-exposure`
into arbitrary public or LAN publication. The native authenticated LAN mode
above is a separate host-process contract and does not change generic Compose.
Do not use host networking for the web container. Home Assistant Ingress remains
a distinct mode with its Supervisor peer guard and is not enabled by generic
container exposure.

Uvicorn proxy-header trust and its identifying server header are disabled.
Graceful shutdown is bounded to two seconds so an intentionally long-lived SSE
or audio response cannot make one `Ctrl+C` wait indefinitely. Requests still
active at that deadline are cancelled by Uvicorn before application shutdown.

## Milestone 26.1 physical validation

Physical acceptance completed on August 22, 2026, on the native Ubuntu 26.04
LTS host `port-a-boss` with Python 3.14.4, OpenSSL 3.5.5, and a physical SDS200
at `192.168.0.251` running firmware Version 1.26.01. The run used a transient
one-day local CA, an IP-SAN certificate for `192.168.0.40`, a random password
environment value, and `https://192.168.0.40:8443`. The CA was supplied directly
to the validation clients; no browser or operating-system trust store was
changed. Saved decoded-audio destinations and MQTT configuration were explicitly
disabled before the daemon connected.

An unauthenticated status request returned `401`, an incorrect mutation origin
returned `403`, and the exact-origin login issued two distinct secure,
HTTP-only, same-site session cookies without exposing the password. The two
independent authenticated HTTPS sessions then simultaneously held two ordered
SSE responses and two browser-audio responses open. The first session observed
14 SSE events plus 52 nonempty audio HTTP chunks, and the second observed 19
events plus 65 nonempty audio chunks. Those HTTP chunk counts demonstrate live
delivery rather than decoded PCMU frame boundaries. Live socket inspection
confirmed exactly one daemon-owned UDP scanner-control connection to port 50536
and one TCP RTSP session to port 554; the web process remained only a client of
the four private Unix-domain services.

A temporary daemon-owned recording started while all four streams were active.
Logging out the first session terminated only its SSE and audio responses; the
recording remained active, the second session remained authorized, and its audio
byte count continued advancing. The second session stopped the recording,
listed exactly one finalized entry, downloaded a nonempty RIFF/WAVE file through
`recordings.sock`, and logged out independently. Web shutdown left the original
daemon healthy. Daemon `SIGTERM` returned status zero and removed the API,
event, PCMU, and recording-file sockets. The temporary password was absent from
daemon and web logs, and all generated credentials, recordings, sockets, and
other validation files were removed.

This establishes the native-host direct-TLS LAN boundary with concurrent
authenticated dashboard sessions and one daemon/scanner owner. It does not
establish trusted-reverse-proxy, wildcard, public/Internet, generic-container
LAN, or remote daemon-client support.

## Browser dashboard

Open the local dashboard after starting the web service:

```text
http://127.0.0.1:8000/
```

Theme selection is presentation-only and browser-local. The deterministic
built-in picker order is **System**, **LCARS-inspired**, **Matrix-inspired**,
**First Responder**, **Amateur Radio**, and **Pip-Boy-inspired**. System follows
the browser or operating-system light/dark preference and remains the stable
default and safe fallback. It now frames the shared workspace and prominent
scanner pane around the established scanner-display proportions, hierarchy,
status treatment, and adaptive screen profiles. The five custom choices are
more theatrical while preserving the same dashboard semantics:

When **System** is active, a second browser-local selector appears in the center
of the overview row. **Follow device** preserves the adaptive behavior above.
The remaining choices mirror Textual's full built-in TUI color-scheme inventory:
ANSI Dark/Light, Atom One Dark/Light, Catppuccin Frappé/Latte/Macchiato/Mocha,
Dracula, Flexoki, Gruvbox, Monokai, Nord, Rosé Pine/Dawn/Moon, Solarized
Dark/Light, Textual Dark/Light, and Tokyo Night. An explicit choice fixes the
System presentation to that light or dark scheme until changed, independently
of later operating-system preference changes. It recolors System surfaces,
scanner display, semantic status colors, controls, and Waterfall without
changing layout, data, or behavior. The choice is stored under
`sdsctl.web.system-palette`, remains available when switching away and back, and
is never shown for another web theme. Because browsers have no terminal ANSI
color table, the two ANSI choices use stable web equivalents; the other 19
schemes retain Textual 8.2's resolved built-in source values. Browser-facing
label and semantic-status tokens are minimally shifted toward black or white
only when the terminal value would fall below WCAG AA against its web surface;
accent-filled controls receive the higher-contrast black or white label. On
phone-sized viewports the selector moves below the overview title and connection
message rather than compressing either one.

- **LCARS-inspired** connects the six operational panels with asymmetric rails,
  segmented console bands, luminous command-deck surfaces, and layered display
  depth.
- **Matrix-inspired** turns the shared dashboard into a cinematic terminal
  workstation with varied terminal panes, technical grids, scan illumination,
  data-field staging, and perspective depth.
- **First Responder** presents the same controls as a dispatch/CAD workstation
  with application chrome, tactical/radar instrumentation, restrained emergency
  light washes, and operations-center monitor depth.
- **Amateur Radio** frames the dashboard as an SDS200-inspired rack/front panel
  with a scanner display window, tactile controls, rotary hardware, vents,
  chassis depth, and bench-equipment lighting.
- **Pip-Boy-inspired** uses an original phosphor-green and amber field-terminal
  treatment with restrained CRT depth, grids, meters, scanlines, and console
  framing. It contains no game logos, character or corporate artwork,
  screenshots, sounds, proprietary fonts, copied hardware geometry, or remote
  resources.

The six choices are built-in packages under the installed
`sds200/themes/web/<theme-name>/` resource hierarchy. Each directory contains
only a versioned `manifest.json` and its declared `theme.css`. The manifest
records schema version 1, the `web` interface, stable theme ID, human label,
picker order, local stylesheet filename, color scheme, and light/dark browser
theme colors. The registry requires the directory name and manifest ID to match,
rejects unknown fields and schema versions, and exposes only validated local CSS
files.

The base `/assets/dashboard.css` contains shared presentation, responsive pane
composition, and accessibility defaults in the `sdsctl-shared` cascade layer.
Theme-owned selectors, design tokens, and decorative effects remain inside their
package and are served at `/assets/themes/<theme-name>/theme.css`. Managed CSS is
confined to the `sdsctl-managed-theme` layer. A final
`/assets/dashboard-viewport.css` contract owns structural dimensions, pane
visibility, overflow, semantic-text reachability, compact tab geometry, and the
accessibility scrolling escape. Its protected declarations cannot be displaced
by either a built-in or schema-valid managed theme, including managed
`!important` declarations or attempts to reuse the protected layer name. The
HTML options, stylesheet links, and pre-paint browser metadata are generated
from the same immutable ordered registry, so they cannot drift into separate
hard-coded theme lists.

The viewport-owned shell exposes six panes: **Scanner**, **Controls**,
**Waterfall**, **Audio**, **Recordings**, and **Diagnostics**. At normal browser zoom, the
document and active pane fit without horizontal or vertical scrolling at the
390x844 portrait-phone, 800x480 compact-landscape, 1366x768 desktop, and
1920x1080 full-HD reference viewports. Information that cannot fit concurrently
uses the named panes, scanner inspection controls, or recording page controls
instead of an unannounced scrolling region. Large displays scale the workspace
cleanly. When user text enlargement or browser zoom crosses the compact
composition's safe boundary, accessibility and content reachability take
priority and conventional scrolling is deliberately restored.

The base and packaged theme stylesheets are declared before the same-origin
`/assets/theme-bootstrap.js` script in the document head. That parser-blocking
script still runs before the body is parsed and before first paint. It validates
the stored theme and System sub-palette against registry-generated metadata,
applies the corresponding `data-theme` and `data-system-palette` values to the
document root, and updates `color-scheme` and `theme-color` metadata. Managed
links remain inert until the bootstrap has
installed failure handling and selected their ID. A missing, removed, mutated,
or unloadable stylesheet removes the failed link target and repairs the
document theme, metadata, visible picker, and stored selection to **System**;
explicit reselection performs a fresh request. Unavailable local storage also
falls back safely while the existing `sdsctl.web.theme` key and public theme IDs
remain compatible.
The normal dashboard Content Security Policy remains unchanged: inline scripts
and styles are still forbidden, and no remote fonts, scripts, styles, or theme
assets are required.

The pane selector is a semantic tablist. Pointer selection and the Arrow keys
activate adjacent panes; Home and End activate the first and last panes. One
roving tab stop follows the selected pane, and browser-local storage under
`sdsctl.web.pane` restores that selection. The cinematic layer is a shared
`aria-hidden` decorative stage. It is pointer-inert and carries no scanner
meaning. All themes retain the same labels, DOM structure, ARIA state, keyboard
focus treatment, responsive behavior, and status text; scanner state is never
communicated by color alone. Decorative animation and transitions are suppressed
for `prefers-reduced-motion`, and forced-color users receive the shared semantic
workspace without decorative staging.

Milestone 26.13 provides the explicit local-directory lifecycle with staging
validation, collision policy, rollback, removal, and recovery. Milestone 26.14
automatically adds valid managed web packages to one immutable startup registry.
Only the selected managed stylesheet is enabled. The existing same-origin route
serves only its declared CSS after rechecking the exact nonsymlink directory
chain, original directory identity, and complete startup package digest. A
missing, malformed, removed, replaced, or changed package fails closed and is
absent after the next process start; a stale stored selection falls back to
System. No filesystem watcher, live reload, runtime upload, archive extraction,
remote asset, inline code, or theme JavaScript is accepted. Third-party CSS is
presentation-capable, so operators must inspect it before installation even
though CSP, path, schema, size, and digest controls remain enforced. Home
Assistant and TUI themes remain separate inactive renderer adapters under their
own interface directories; `gui` stays reserved until a desktop renderer exists.
Valid managed web themes use the same six-pane layout and lifecycle. Theme
switching preserves the selected pane and does not interrupt live state, form or
control state, browser audio, recording state, or the meaning of keyboard focus.

### Waterfall workspace

Opening **Waterfall** starts the authenticated same-origin
`GET /api/v1/waterfall` response. The endpoint retains NDJSON for API clients;
the dashboard requests the same JSON records as standards-compliant
Server-Sent Events. This negotiated browser representation prevents Home
Assistant Core Ingress from applying JSON-stream compression that can delay
incremental records, while preserving the existing NDJSON contract. The
browser requires the same strict protocol, version, checkpoint-first ordering,
contiguous sequence, and bounded record shape as the private client. Each GWF
frame must contain exactly 240 raw, non-empty hexadecimal strings. Malformed,
incomplete, oversized, non-hexadecimal, or out-of-order input closes the browser
stream, clears live state, and retries only while the pane remains visible.

The upper Canvas shows the newest 240-bin relative spectrum. The renderer parses
each validated source token as a base-16 code, then normalizes that frame's code
range for presentation only; this does not claim dB, RSSI, power, or another
calibrated magnitude. The lower Canvas retains at most 240 normalized frames and
draws the newest row at the bottom. **History** keeps the compatible 60-, 120-,
or 240-frame policy, or explicitly selects a 15-, 30-, or 60-second elapsed-time
window. Duration placement uses accepted monotonic browser receipt time,
preserves delivery gaps, and retains the same 240-frame cap as an independent
memory bound.
Normalization is per frame and is only a presentation transform: the latest 240
source strings remain preserved in the adjacent raw output, and the UI does not
label the values as power, dB, RSSI, or calibrated FFT magnitude. Lower, center,
upper, and marker values appear only when all four GST frequency strings and the
marker position form a structurally valid axis; otherwise those fields remain
unavailable and the plots retain bin position only. While demand remains live,
the daemon refreshes typed GST status at a separate bounded cadence and embeds
the current session snapshot in delivery records. Changing the scanner's span
therefore replaces the displayed lower, center, upper, and marker metadata with
the scanner-reported range. A missed refresh leaves the last valid range visible
and does not interrupt GWF frames.

Adjacent semantic HTML reports session state, frequency metadata, frame rate,
frame age, stream sequence, cumulative queue loss, overflow, poll failures,
successful GWF command round-trip time, scheduler lag and skipped deadlines,
GST refresh revision and failures, and session-transition count. **Pause
display** freezes Canvas history while the browser continues consuming data; it
does not pause the scanner protocol or accumulate a hidden frame backlog.
**Clear history** clears only browser memory. A stream-generation change or
teardown also clears retained history. **Full screen** uses the browser Full
Screen API when available. Canvas backing dimensions follow the rendered CSS
size and device pixel ratio, and resize or theme changes redraw the bounded
history from memory. Base visualization tokens keep every built-in and managed
theme legible; System and Pip-Boy-inspired define deliberate spectrum, grid,
marker, pointer, history, and unavailable-state palettes.

**Frequency pointer** enables one shared vertical guide over the spectrum and
history. Pointer or touch movement and the arrow, Home, and End keys move the
guide; Escape clears it. The adjacent output interpolates the structurally valid
scanner-reported lower and upper bounds and formats the result in MHz. Invalid
or incomplete bounds make it unavailable. The pointer is display-only: it does
not tune, hold, search, change span, or establish a calibrated frequency
measurement.

Milestone 26.10 extraction acceptance completed on August 24, 2026, with the
real packaged demo application and Google Chrome. All five themes rendered at
1920x1080, 800x480, and 390x844; Amateur Radio also rendered at the documented
1366x768 reference. The System 1920x1080 capture was byte-identical to the
pre-extraction commit. Side-by-side custom-theme captures preserved layout,
geometry, colors, controls, and responsive reflow; only the sampled frames of
their existing decorative animations varied. The wheel and sdist both contained
all five manifests and stylesheets, and no checked-in gallery image required a
content change.

Milestone 27.1 adaptive-profile acceptance completed on August 25, 2026,
through Home Assistant Ingress against a physical SDS200 running firmware
1.26.01. One continuous daemon ownership session followed normal scanning,
Quick Search Hold, Close Call Only, Weather Scan, and Tone-Out. The activity
heading changed to **Now scanning**, **Quick Search**, **Close Call**,
**Weather**, and **Tone-Out** respectively, while the complete hierarchy, RF,
identity, receiver, and special-mode groups remained available. Browser audio,
recording finalization and inventory, restart recovery, themes, controls, and
the single-owner boundary remained healthy throughout the bounded run.

Milestone 27.3 responsive-workspace acceptance completed on August 26–27,
2026, through Home Assistant Ingress against the same SDS200 firmware. Isolated
Apps built from exact merged commit `db2e6c0` and reconnect closure commit
`dca445e` rendered all six built-in themes over the five-pane shell, preserved
theme, pane, and Simple/Detail fallback choices across reload, and followed
Quick Search, Close Call, Weather, configured Tone-Out, and zero-tone detection
through the normalized Auto groups. Every semantic scanner control, explicit
field group, browser-audio lifecycle, daemon recording, three-entry pagination,
saved playback, and byte-identical download was exercised while one App remained
the sole scanner and audio owner.

The first restart exposed a terminal EventSource failure: two-second status
polling recovered authoritative state, but ordered events required a full page
reload. The closure repair added explicit duplicate-free stream recreation. A
deliberate stopped-App interval exceeding ten seconds then forced repeated
unavailable-backend attempts; the same untouched Ingress document recovered a
new ordered snapshot and continuing radio updates after the App returned. A
second normal restart preserved the document and recovered again. Bounded
shutdown cancellation of the one open long-lived response remained consistent
with the documented two-second graceful deadline.

## Theme gallery

These documentation captures render the real packaged dashboard and stylesheet
through `create_web_dashboard_app()`. The screenshot helper supplies deterministic
fictional daemon, scanner, radio, recording, and reliability state; the images do
not contain live scanner identifiers, locations, or recordings.

Each built-in theme has the same ordered four-viewport review set: 1920x1080
Full HD, 1366x768 desktop, 800x480 compact landscape/Raspberry Pi display, and
390x844 portrait phone at DPR2. The phone PNGs are validated at 780x1688
physical pixels. This complete matrix makes wrapping, alignment, clipping,
spacing, responsive reflow, and intentional theme differences directly
comparable between releases. At phone width, the header uses one compact brand
row above a shared connection-status and theme-selector row. The supporting
subtitle and visible `Theme` label are omitted from that narrow presentation
while the selector retains its accessible name, so long theme names remain
complete and more height stays available to the working pane.

### System

![System theme at 1920x1080](assets/web-dashboard/theme-system-1920x1080.png)

![System theme at 1366x768](assets/web-dashboard/theme-system-1366x768.png)

![System theme at 800x480](assets/web-dashboard/theme-system-800x480.png)

![System theme at a 390x844 CSS viewport and DPR2](assets/web-dashboard/theme-system-390x844-dpr2.png)

### LCARS-inspired

![LCARS-inspired theme at 1920x1080](assets/web-dashboard/theme-lcars-1920x1080.png)

![LCARS-inspired theme at 1366x768](assets/web-dashboard/theme-lcars-1366x768.png)

![LCARS-inspired theme at 800x480](assets/web-dashboard/theme-lcars-800x480.png)

![LCARS-inspired theme at a 390x844 CSS viewport and DPR2](assets/web-dashboard/theme-lcars-390x844-dpr2.png)

### Matrix-inspired

![Matrix-inspired theme at 1920x1080](assets/web-dashboard/theme-matrix-1920x1080.png)

![Matrix-inspired theme at 1366x768](assets/web-dashboard/theme-matrix-1366x768.png)

![Matrix-inspired theme at 800x480](assets/web-dashboard/theme-matrix-800x480.png)

![Matrix-inspired theme at a 390x844 CSS viewport and DPR2](assets/web-dashboard/theme-matrix-390x844-dpr2.png)

### First Responder

![First Responder theme at 1920x1080](assets/web-dashboard/theme-first-responder-1920x1080.png)

![First Responder theme at 1366x768](assets/web-dashboard/theme-first-responder-1366x768.png)

![First Responder theme at 800x480](assets/web-dashboard/theme-first-responder-800x480.png)

![First Responder theme at a 390x844 CSS viewport and DPR2](assets/web-dashboard/theme-first-responder-390x844-dpr2.png)

### Amateur Radio

![Amateur Radio theme at 1920x1080](assets/web-dashboard/theme-amateur-radio-1920x1080.png)

![Amateur Radio theme at 1366x768](assets/web-dashboard/theme-amateur-radio-1366x768.png)

![Amateur Radio theme at 800x480](assets/web-dashboard/theme-amateur-radio-800x480.png)

![Amateur Radio theme at a 390x844 CSS viewport and DPR2](assets/web-dashboard/theme-amateur-radio-390x844-dpr2.png)

### Pip-Boy-inspired

![Pip-Boy-inspired theme at 1920x1080](assets/web-dashboard/theme-pip-boy-inspired-1920x1080.png)

![Pip-Boy-inspired theme at 1366x768](assets/web-dashboard/theme-pip-boy-inspired-1366x768.png)

![Pip-Boy-inspired theme at 800x480](assets/web-dashboard/theme-pip-boy-inspired-800x480.png)

![Pip-Boy-inspired theme at a 390x844 CSS viewport and DPR2](assets/web-dashboard/theme-pip-boy-inspired-390x844-dpr2.png)

### System Waterfall — 1920x1080

![System theme Waterfall pane at 1920x1080](assets/web-dashboard/theme-system-waterfall-1920x1080.png)

### Pip-Boy-inspired Waterfall — 800x480

![Pip-Boy-inspired theme Waterfall pane at 800x480](assets/web-dashboard/theme-pip-boy-inspired-waterfall-800x480.png)

The Waterfall captures use the same bounded deterministic fictional stream as
the browser audit. They exercise the real authenticated web adapter, Canvas
spectrum and rolling history, relative-data labeling, lifecycle telemetry, and
theme tokens without including live scanner frequencies or programming.

Regenerate the checked-in gallery from a repository checkout with Chrome or
Chromium and the web dependencies available:

```bash
python scripts/generate_web_dashboard_screenshots.py
```

The helper requires Node.js 24 or newer, starts a loopback-only demo instance of
the real application, selects each theme through same-origin browser-local state,
and uses an isolated Chrome profile per capture. Its dependency-free Node bridge
reuses the browser audit's Chrome DevTools Protocol client to set the declared
width, height, and DPR as the exact CSS viewport; pin canonical light color,
normal contrast, forced-colors-off, and reduced-motion media before navigation;
and verify `innerWidth`, `innerHeight`, `devicePixelRatio`, and the visual
viewport. It waits for dashboard state, fonts, consecutive pixel-identical
Waterfall Canvas frames, and consecutive byte-identical compositor captures,
then returns the corresponding outer HTML. The Python helper accepts the staged
PNG only when that HTML contains the expected theme, authoritative fictional
scanner and recording values, pagination state, and fixed update clock. It
validates the complete PNG chunk structure, CRCs, compressed scanlines, and
DPR-scaled physical dimensions, then reconstructs and deterministically
re-encodes Chrome's RGB/RGBA pixels before atomically publishing the image and
shutting the demo server down. It never derives a CSS viewport from Chrome's
outer-window size. None of that demo behavior is part of the shipped `sdsctl
web` service.

Verify the exact 26-file generator, asset-directory, canonical-guide, and raw
default-branch wiki reference contract without opening Chrome:

```bash
python scripts/generate_web_dashboard_screenshots.py --verify-gallery
```

CI and release validation capture the System scanner reference and both
Waterfall references twice with the same Chrome executable and compare each
image's PNG bytes. Maintainers may select any other named image in the same mode.
Both runs use temporary output and profile directories, so this does not rewrite
the checked-in gallery or impose pixel equality across Chrome versions:

```bash
python scripts/generate_web_dashboard_screenshots.py \
  --verify-repeatability \
  --only theme-system-1920x1080.png \
  --only theme-system-waterfall-1920x1080.png \
  --only theme-pip-boy-inspired-waterfall-800x480.png
```

Run the screenshot-free real-Chrome acceptance matrix before closing responsive
workspace or theme changes:

```bash
node scripts/audit_web_dashboard_browser.mjs --timeout-ms 30000
```

The audit reuses the same fictional demo service and one isolated Chrome
session. It resizes that session through all four reference CSS viewports and
DPR transitions, exercises every built-in theme and all six panes, drives
recording pagination and focus, reveals all 35 radio fields, and probes adaptive
screen mappings, reduced motion, forced colors/high contrast, the browser-zoom
scrolling escape, accessibility-tree semantics, and an Ingress-style URL
prefix. At the constrained 800x480 reference viewport it also uses trusted
browser Tab and Shift+Tab input across every pane and theme, comparing traversal
with an independent inventory of rendered enabled semantic controls. Every
scanner matrix case selects both Simple and Detail and applies the same
geometry, clipping, and readability checks to scanning, search, Close Call,
weather, Tone-Out, and unknown automatic presentations.

Normal and forced-color checks use browser-computed foreground, opacity, and
ancestor-composited background colors. The audit applies the WCAG 2.x AA text
contrast floors: 4.5:1 for ordinary text and 3:1 only for text at least 24 CSS
pixels, or at least 18.66 CSS pixels with a bold weight. This standards-derived
floor is shared by authoritative visible radio values and enabled control text;
themes do not receive individual tolerances. The audit writes no gallery
images. Use `--help` for executable overrides or `--list` to inspect the
144-case matrix without opening Chrome.

The browser performs one initial `/api/v1/status` request and opens
`/api/v1/events` with the same origin. The event response uses
`text/event-stream`. Every daemon envelope is emitted as one SSE message with:

- an `id` equal to the validated daemon sequence;
- a `data` field containing the complete daemon event JSON object; and
- a blank line terminating the message.

The first message is always the authoritative `stream.snapshot` checkpoint.
Later messages retain the existing daemon event kinds and payloads:

- `daemon.transition`;
- `scanner.connection`;
- `scanner.psi`;
- `radio.state`;
- `audio.state`;
- `recording.state`; and
- `destination.health`.

### Shared scanner-state fields

The **Scanner** pane renders every field in the 35-field
`RadioStateSnapshot` contract. Its stable labeled groups cover:

- hierarchy names, indexes, and hold state for system, department, site, and
  channel;
- raw scanner screen, renderer-neutral screen kind, channel number and source
  kind;
- frequency, modulation, service type, and detected subaudio;
- talkgroup and unit identifiers, volume, squelch, optional raw SDS100 battery
  telemetry, P25 status, mute, and the scanner's native recording flag; and
- Weather mode/SAME and Tone-Out A/B values.

These values are read-only browser presentation. Browser audio and daemon WAV
recording remain separate application workflows, and the scanner-native
recording field does not control either one. The page renders scanner-provided
values as text without inferring unknown semantics. Integer zero and false-like
text such as `Off` are retained except for configured Tone-Out A and B values:
numeric zero, including equivalent scanner text, renders as **Detect** because
zero tells the scanner to identify a received tone. Null, missing, and empty
fields render as **Unavailable**.

Battery presentation follows the authoritative GSI/PSI field lifecycle: an
omitted value clears an earlier value, while literal zero remains visible. The
dashboard does not infer a unit, percentage, charging status, expected range, or
applicability to SDS150/SDS200. SDS150 `GCS` charge status remains an explicit
request/response command and is not automatically polled by the dashboard or
daemon.

Initial status, the ordered SSE checkpoint and radio/PSI events, fallback
polling, and periodic reconciliation all pass complete authoritative snapshots
through the same field renderer. Consequently, changing among conventional or
trunk scanning, Quick Search, Close Call, Weather, Tone-Out, and unknown future
screens clears values that are no longer present rather than retaining stale
mode-specific details. Raw `screen` and classified `screen_kind` remain separate
so an unknown future screen is still visible without being misclassified.

The Scanner pane keeps System, Department, and Channel in one prominent stable
hierarchy. Its **Auto**, **Hierarchy**, **RF**, **Identity**, and **Special**
controls make every remaining field group explicitly reachable. Auto uses the
normalized `screen_kind`: Search and Close Call select the RF group and search
presentation, while Weather and Tone-Out select the Special group and their
dedicated presentation. Scanning and unknown values use the configurable
**Simple** or **Detail** scan fallback; Detail initially selects Hierarchy and is
the safe default. The fallback persists under
`sdsctl.web.scan-fallback`. Selecting a field group is an inspection choice that
remains active across scanner updates until **Auto** is chosen again. This is
presentation only: all 35 shared fields remain rendered and reachable, and
unknown or future screen values do not discard data.

The browser directly applies complete runtime snapshots, scanner connection
changes, PSI and radio-state updates, audio snapshots, and recording snapshots.
`recording.state` updates the recording panel directly and is also committed into
the current browser snapshot so later unrelated events cannot repaint stale
recording state. Destination-health events trigger an authoritative
reconciliation because the displayed router summary is broader than one
subscriber transition.

When the event stream disconnects, the dashboard closes the failed
`EventSource`, clears its sequence checkpoint, and schedules exactly one new
same-origin stream after two seconds. This explicit recreation also survives a
terminal proxy HTTP response that native EventSource retry would not revisit.
Source-identity guards ignore stale callbacks, and visibility or page teardown
cancels pending work before a later start creates one stream. Two-second
`/api/v1/status` polling remains active while the event stream is unavailable.
A status request also runs every 30 seconds during healthy streaming to
reconcile incremental browser state with the authoritative daemon snapshot.

The interface presents:

- scanner connection, model, firmware, and endpoint;
- active system, channel, mode, screen kind, signal, and RSSI when available;
- daemon lifecycle and transition sequence;
- PSI activity and interval;
- audio and destination-router state;
- daemon-owned recording state, elapsed time, packet and sample totals, audio
  duration, RTP reliability, and current file;
- a newest-first list of recent finalized recordings with Play and Download
  actions for compatible WAVs, paginated three entries at a time; and
- the local time of the most recent applied update.

The interface uses semantic landmarks, definition lists, a skip link, visible
keyboard focus, status text that does not rely on color alone, the semantic
six-pane tablist, system light and dark modes, reduced-motion and forced-color
behavior, and the explicit accessibility scrolling escape. JavaScript updates
text through `textContent`; it does not render daemon-provided HTML.

The HTML, CSS, and JavaScript are package resources served with `no-store`, a
restrictive Content Security Policy, no-referrer behavior, MIME sniffing
disabled, and framing denied.

## Browser audio playback

Browser playback is explicit and never starts on page load. Press **Play audio**
from the dashboard to satisfy browser autoplay requirements and create one
same-origin `GET /api/v1/audio` stream. The route connects its independent
`DaemonPcmuClient` before returning HTTP `200`, then forwards complete
`encode_pcmu_delivery` frames as `application/octet-stream`.

The main browser thread validates arbitrary HTTP chunk boundaries, PCMU magic,
version, flags, frame and body sizes, monotonic stream sequence, cumulative
daemon queue-loss counters, and their relationship to skipped publications. It
then transfers the raw PCMU payload to the packaged AudioWorklet.

The AudioWorklet decodes G.711 mu-law at the scanner's 8 kHz rate, keeps a
bounded two-second sample buffer, waits for a 60 ms startup threshold, inserts
bounded silence for reported missing samples, and linearly resamples to the
browser output rate. Backwards RTP timestamps and large discontinuities reset
the playback buffer rather than replaying stale samples.

The panel reports the current source endpoint, browser-received packet count,
daemon subscriber queue drops and overflows, and cumulative RTP missing packets.
The source is mono; normal browser output may reproduce that mono signal through
both destination channels.

**Stop** aborts the HTTP request, cancels the reader, disconnects the
AudioWorklet, closes the `AudioContext`, and releases the daemon PCMU client.
Hiding the dashboard intentionally suspends SSE but does not stop audio. Returning
to the page reopens SSE while preserving the same audio stream. Closing or
navigating away from the page stops both event and audio streams.

## Browser recording workflows

Browser recording is explicit and daemon-owned. Press **Record** to send
`POST /api/v1/recording/start`. The daemon recording manager allocates a
collision-safe WAV path beneath its configured recording root and attaches a WAV
sink to the already-running decoded-PCM router. No browser request supplies a
filesystem path or filename, and no second scanner RTSP/RTP stream is opened.

While recording is active, the browser reconciles `GET /api/v1/recording` once
per second so elapsed time, packet and sample totals, audio duration, sink
statistics, and RTP reliability continue to advance even when no state
transition event is emitted. While inactive, the normal 30-second reconciliation
checks recording status. `recording.state` events and visibility restoration
provide faster state recovery.

Reloading the page or stopping the web process does not stop an active recording.
The daemon continues to own the sink until **Stop**, explicit daemon shutdown, or
a recording failure. Press **Stop** to send `POST /api/v1/recording/stop`;
successful finalization closes the WAV, writes the adjacent metadata sidecar, and
refreshes `GET /api/v1/recordings`.

The recent-recording list is bounded and newest-first. Only finalized inventory
entries marked playable receive actions. **Play** assigns the same-origin
recording-file route to a native `<audio>` element. **Download** uses that same
route with a browser download filename derived from the inventory identifier.
Neither action creates a browser PCMU client or changes live scanner-audio
ownership.

`GET /api/v1/recordings/file/{identifier}` never reads a caller-selected
filesystem path. The web service sends the identifier to the daemon's private
recording-file client. The daemon accepts only canonical inventory-relative POSIX
WAV identifiers, rejects traversal and non-inventory entries, excludes active or
pending recordings, securely reopens path components without following symlinks,
requires a regular file, revalidates compatible WAV parameters, and then streams
the already-open file with an exact content length.

Daemon shutdown stops recording-file readers before closing the recording
manager. The manager finalizes any active WAV and metadata while the shared audio
runtime is still alive, then the normal destination, runtime, PCMU, and event
shutdown continues.

## HTTP endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/` | Accessible responsive browser dashboard shell |
| `GET` | `/api/v1` | Service metadata and endpoint links |
| `GET` | `/healthz` | Web-process health without contacting the daemon |
| `GET` | `/api/v1/status` | Negotiated daemon capabilities and runtime snapshot |
| `GET` | `/api/v1/snapshot` | Authoritative daemon runtime snapshot |
| `GET` | `/api/v1/events` | Snapshot-first ordered daemon Server-Sent Events |
| `GET` | `/api/v1/audio` | Validated daemon-owned PCMU v1 binary frame stream |
| `GET` | `/api/v1/waterfall` | Validated ordered daemon waterfall NDJSON or negotiated SSE stream |
| `POST` | `/api/v1/scanner/hold/{scope}` | Set desired system, department, site, or channel hold state |
| `POST` | `/api/v1/scanner/next` | Compatibility alias for the next current channel selection |
| `POST` | `/api/v1/scanner/next/{scope}` | Move to the next current system, department, site, or channel selection |
| `POST` | `/api/v1/scanner/previous` | Compatibility alias for the previous current channel selection |
| `POST` | `/api/v1/scanner/previous/{scope}` | Move to the previous current system, department, site, or channel selection |
| `POST` | `/api/v1/scanner/reconnect` | Request one bounded daemon-owned scanner reconnect |
| `GET` | `/api/v1/recording` | Current daemon-owned recording snapshot |
| `POST` | `/api/v1/recording/start` | Start one daemon-owned WAV recording |
| `POST` | `/api/v1/recording/stop` | Stop and finalize the active recording |
| `GET` | `/api/v1/recordings` | Bounded newest-first finalized recording inventory |
| `GET` | `/api/v1/recordings/file/{identifier}` | Stream one finalized playable WAV by inventory-relative identifier |
| `GET` | `/api/v1/openapi.json` | Machine-readable API schema |
| `GET` | `/api/v1/docs` | Self-hosted interactive Swagger UI |
| `GET` | `/api/v1/redoc` | Self-hosted ReDoc API reference |

Swagger UI and ReDoc are served entirely from version-pinned assets packaged
with `sds200`. Loading either documentation page does not contact the scanner
daemon and does not require a CDN, Google Fonts, or another third-party browser
request. Both documentation UIs read the same local `/api/v1/openapi.json`
schema. Swagger UI is interactive: its **Try it out** feature can invoke
same-origin API operations, including mutating operations such as recording
start and stop, so use those controls deliberately.

The documentation pages keep `script-src 'self'` and `connect-src 'self'`.
Their docs-specific Content Security Policy allows inline styles because the
vendored Swagger UI and ReDoc runtimes generate style attributes or style
elements while rendering. Inline scripts remain prohibited, and the normal
dashboard keeps its stricter `style-src 'self'` policy without this exception.

The service envelope uses protocol `sdsctl.web`, version `1`. Each
request-response daemon route creates a bounded local API client, negotiates the
daemon protocol, performs the requested operation, and closes the client.

Scanner controls are deliberately narrow browser operations rather than a raw
scanner-command passthrough. Browser hold requests never provide raw `HLD` or
`KEY` values: `POST /api/v1/scanner/hold/{scope}` accepts exactly one JSON field,
`{"held": true}` or `{"held": false}`, and forwards the semantic scope plus
desired state to daemon `scanner.hold_state`. Previous and Next resolve the
current authoritative indexes to the documented `SYS`, `DEPT`, `SITE`, `TGID`,
or `CFREQ` target; the original unscoped routes remain channel aliases. The daemon performs an
authoritative `GSI` read before deciding whether a verified key gesture is
needed. Scoped navigation resolves the current documented selection from the
daemon snapshot and never accepts raw `NXT` or `PRV` targets from the
browser.

`GET /api/v1/status` already carries the negotiated daemon `hello` result. The
browser enables semantic hold controls only when the daemon advertises
`scanner.hold_state`; scoped navigation and reconnect continue to negotiate
`scanner.next`, `scanner.previous`, and `scanner.reconnect` independently.
Controls additionally require a running runtime and connected scanner. A new
hold requires a usable current selection, while release remains available when
the authoritative hold field is `On` even if scanning has already exposed the
SDS200 unsigned-32 no-current-selection sentinel (`4294967295`). Next/previous
continue to reject that sentinel.

Hold controls reflect authoritative PSI/GSI hold fields. An unheld scope renders
`Hold system`, `Hold department`, `Hold site`, or `Hold channel`; an active scope
changes the button to the corresponding `Release` action with
`aria-pressed="true"`. Each scope keeps the current System, Department, Site, or
Channel directly above its action and always presents a separate `Held`,
`Not held`, or `Unavailable` state. The action's accessible description includes
both that current target and state. The daemon no-ops an already
satisfied desired state, otherwise executes the complete verified SDS200 gesture
under the mutation lock and polls authoritative `GSI` until only the requested
hold field converges. This target-field convergence deliberately tolerates the
temporary unrelated field inconsistencies observed after the Site Hold gesture.
Previous and Next are available beside every scope through the same bounded
typed navigation operations. Department navigation includes the current System
index required by the scanner protocol.
Reconnect remains available while the scanner is disconnected when the running
daemon advertises bounded reconnect support.

The browser allows only one scanner-control mutation at a time. The daemon
runtime independently retains its own nonblocking control lock, so another local
client still receives the daemon's stable busy response rather than overlapping
scanner commands. Successful HTTP control responses contain the daemon control
sequence, operation timestamps, and authoritative completion `snapshot`. The
browser renders that snapshot immediately when no ordered SSE event arrived
during the request. If an event did arrive while the control was in flight, the
browser does not compare the independent control and event sequence spaces or
use wall-clock timestamps as an ordering surrogate. Instead it performs a fresh
authoritative status read. If the event stream remains continuously active
through the bounded reconciliation attempts, its ordered projection is preserved
and the normal periodic status refresh provides the next complete snapshot
boundary. A temporarily unavailable reconciliation status read likewise preserves
the event-derived projection; because the scanner control has already completed,
that read failure is not reported as a scanner-control failure, and normal status
refresh supplies a later full snapshot boundary.

Control failures are mapped to stable redacted HTTP errors. Busy, unavailable,
unsupported, rejected, timeout, invalid-parameter, connection, and generic
control failures do not expose private daemon paths or low-level exception
messages.

Physical Milestone 20.6 validation on SDS200 firmware 1.26.01 exercised
semantic release and re-hold for System, Department, Site, and Channel through
the loopback web HTTP routes. Channel release authoritatively returned `Off`
while the current channel index was the SDS200 `4294967295` no-selection
sentinel; the route later restored Channel Hold after a new TGID became usable.
A representative real-browser Channel cycle also confirmed the visible
`Release channel` plus `Held` state, successful Release completion, transition
to enabled `Hold channel` with the indicator hidden, successful Hold completion,
and restoration of the Release/`Held` presentation. The daemon and web process
IDs remained unchanged, PSI stayed active, and daemon-owned audio continued
advancing throughout the browser validation.

The event route receives its first validated daemon event before starting the
HTTP response. An absent, refused, inaccessible, incompatible, or malformed
initial stream therefore returns HTTP `503` with the same stable redacted
message as other daemon-backed endpoints. Private socket paths and low-level
exception details are not included.

After HTTP streaming begins, a later daemon disconnect or protocol failure ends
that SSE response and closes the local event client. The dashboard closes its
corresponding browser source and constructs one replacement after the tracked
two-second delay to obtain a new authoritative snapshot boundary. The web
service does not invent event replay, skip daemon sequence validation, or
translate a gap into partial browser state.

The audio route connects to `pcmu.sock` before starting its HTTP response. An
absent, refused, inaccessible, incompatible, or malformed initial PCMU
connection therefore returns the same redacted HTTP `503`. The route does not
pre-read a PCMU frame before returning `200`, because audio reception may be
idle. After streaming begins, daemon disconnect or protocol failure ends the
response and closes the PCMU client.

Recording status, mutation, and inventory routes use the daemon API and map the
stable `recording_busy`, `recording_unavailable`, and `recording_failed` codes to
redacted HTTP responses. The finalized-file route uses only `recordings.sock`;
invalid identifiers return `400`, missing entries `404`, unavailable or
non-playable entries `409`, and local service failures `503`. Successful WAV
responses use `audio/wav`, `Cache-Control: no-store`, and
`X-Content-Type-Options: nosniff`. Default loopback, generic-container, and Home
Assistant Ingress responses also advertise the exact daemon-reported
`Content-Length`. Authenticated LAN middleware deliberately omits
`Content-Length` from protected responses, including a finalized WAV, so
session revocation can terminate an in-flight response without promising an
undeliverable remainder.

## Command options

```text
--home-assistant-ingress
--container-exposure
--authenticated-lan
--lan-listen-address ADDRESS
--lan-origin HTTPS_ORIGIN
--lan-password-env NAME
--lan-tls-certfile PATH
--lan-tls-keyfile PATH
--daemon-socket-path PATH
--daemon-event-socket-path PATH
--daemon-pcmu-socket-path PATH
--daemon-recording-file-socket-path PATH
--daemon-waterfall-socket-path PATH
--daemon-timeout SECONDS
--daemon-max-response-bytes BYTES
--daemon-max-event-bytes BYTES
--daemon-pcmu-max-endpoint-bytes BYTES
--daemon-pcmu-max-frame-bytes BYTES
--daemon-recording-file-max-content-bytes BYTES
--daemon-waterfall-max-record-bytes BYTES
--listen-address ADDRESS
--listen-port PORT
--no-access-log
```

The daemon timeout defaults to five seconds and applies to API, event, PCMU,
recording-file, and waterfall connection establishment. The response, event,
PCMU endpoint, PCMU frame, recording-file content, and waterfall record limits
default to the existing daemon client contracts. Browser PCMU frame size must be
at least the fixed 82-byte header and cannot exceed 131,072 bytes. The waterfall
record limit defaults to 64 KiB and may be lowered for a stricter deployment.

Disable the HTTP access log when a supervising service supplies request logging:

```bash
sdsctl web --no-access-log
```

## Current scope

The dashboard now includes:

- the optional `web` package extra;
- a host-independent FastAPI application factory;
- versioned health, status, snapshot, metadata, event, audio, and OpenAPI routes;
- redacted daemon-unavailable responses;
- capability-negotiated browser scanner hold, previous/next
  system/department/site/channel navigation, and bounded reconnect controls with
  authoritative selection resolution,
  mutation serialization, completion reconciliation, and stable redacted errors;
- a default-loopback Uvicorn adapter with bounded graceful shutdown;
- explicit password-authenticated direct-TLS LAN activation on one private,
  unique-local, or link-local interface, with exact HTTPS origin enforcement,
  bounded server-side sessions, guarded login input and failures,
  secret-by-environment reference, and revocation of active long-lived
  responses;
- the `sdsctl web` command;
- a packaged accessible responsive six-pane browser workspace with persistent
  pointer and keyboard selection, normal-zoom no-scroll reference layouts, and
  an enlarged-text/browser-zoom scrolling escape;
- snapshot-first same-origin Server-Sent Events;
- validated daemon sequence identifiers and complete JSON event envelopes;
- direct incremental scanner, radio, PSI, audio, and daemon updates;
- tracked duplicate-free browser stream recreation, two-second polling fallback,
  and periodic authoritative reconciliation;
- a scanner-display presentation with adaptive Search/Close Call, Weather, and
  Tone-Out modes, persistent Simple or Detail scan fallback, and explicit
  Hierarchy, RF, Identity, and Special inspection of all 35 radio fields;
- scanner, radio-activity, daemon, PSI, audio, and router summaries;
- explicit Play and Stop browser audio over daemon-owned PCMU with AudioWorklet
  mu-law decoding, bounded buffering, resampling, and loss telemetry;
- daemon-owned Record and Stop workflows over the existing decoded-PCM router,
  with live recording and RTP reliability telemetry;
- ordered `recording.state` browser updates plus active-recording polling and
  reload/reconnect reconciliation;
- bounded newest-first finalized recording inventory, three-entry pagination,
  and safe same-origin Play and Download actions through the private
  recording-file service;
- deterministic browser PCMU and SSE cleanup, including hidden-tab event
  suspension without stopping active audio or daemon-owned recording;
- active recording survival across browser or web-process disconnects and
  daemon-shutdown finalization before audio runtime teardown;
- idle disconnected daemon event-client reaping;
- restrictive static-, event-, audio-, and recording-file response headers;
- browser-local System, LCARS-inspired, Matrix-inspired, First Responder,
  Amateur Radio, and original asset-free Pip-Boy-inspired themes in deterministic
  order over one shared accessible workspace, with managed-theme compatibility,
  reduced-motion and forced-color handling, CSP-safe pre-paint restoration, and
  no daemon or scanner state coupling;
- deterministic documentation screenshots generated from the real packaged
  dashboard with fictional demo state and bounded native-Chrome capture; and
- parser, application, event, audio, recording lifecycle, shell, server,
  packaging, and regression tests.

Later work remains responsible for browser logs, additional shared branding
assets, trusted-reverse-proxy deployment, and any public/Internet remote-access
design. Home Assistant App operation is documented separately in [Home
Assistant App](home-assistant-app.md); it uses Supervisor Ingress rather than
the standalone authenticated LAN flow.
