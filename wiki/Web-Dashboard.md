# Web Dashboard

> [!IMPORTANT]
> The repository's
> [web dashboard guide](https://github.com/stevenboyd78/sdsctl/blob/main/docs/web-dashboard.md)
> is the authoritative reference for configuration, security boundaries, and
> exact behavior. This page is a visual task-oriented introduction.

The daemon-backed dashboard provides Scanner, Controls, Waterfall, Audio,
Recordings, and Diagnostics panes in one responsive browser workspace. It remains a client of
the existing daemon and does not open a second scanner-control or RTSP/RTP audio
connection.

## Open the dashboard

For local standalone use, install the web extra, start the daemon, and start the
loopback web service in another terminal:

```bash
python -m pip install "sds200[web]"
sdsctl --log-level INFO --host SCANNER_IP daemon
sdsctl web
```

Open `http://127.0.0.1:8000/` on the same host. Home Assistant OS users normally
open the same workspace through authenticated Home Assistant Ingress. An
explicit private-LAN mode is also available; review the canonical guide before
enabling it.

## Use the workspace

Select a pane with a pointer or move through the tablist with Left Arrow, Right
Arrow, Home, and End. The browser remembers the selected pane. In the Scanner
pane, **Auto** follows Search/Close Call, Weather, and Tone-Out screens;
**Hierarchy**, **RF**, **Identity**, and **Special** provide explicit inspection,
and Simple or Detail controls the normal-scanning fallback.

The Controls pane places the current System, Department, Site, or Channel
directly above its Hold or Release action. Each action row also reports
**Held**, **Not held**, or **Unavailable**, with Previous and Next navigation
available for that same System, Department, Site, or Channel.

## Choose a theme

Theme choice is browser-local presentation state. It does not change daemon or
scanner state. All 28 built-in choices share the same panes, controls, fields,
keyboard behavior, and security boundary.

Every theme is audited at 1920x1080 Full HD, 1366x768 desktop, 800x480 compact
landscape/Raspberry Pi display, and 390x844 portrait phone at DPR2. The visual
gallery retains all four references for the established themes and one
authoritative Full-HD Scanner reference for each Cinematic theme. At normal
zoom the workspace fits every audited reference size. Compact LCARS panes can
scroll internally to keep fields and controls reachable without shrinking
them. When text enlargement or browser zoom needs more room, content
reachability takes priority and conventional scrolling returns.

The captures show theme-default typography, using locally bundled fonts where
available; no internet font service is required. Appearance controls are centered
above the workspace and wrap onto a centered row at narrower sizes. See the
[typography guide](https://github.com/stevenboyd78/sdsctl/blob/main/docs/theme-typography.md)
for the available pairings and readable/system-font alternatives.

At phone width, one compact brand row sits above a shared connection-status and
theme-selector row. The supporting subtitle and visible `Theme` label are
omitted from that narrow presentation while the selector retains its accessible
name, leaving more height for the active workspace without abbreviating theme
names.

### System

![System theme at 1920x1080](https://raw.githubusercontent.com/stevenboyd78/sdsctl/main/docs/assets/web-dashboard/theme-system-1920x1080.png)

![System theme at 1366x768](https://raw.githubusercontent.com/stevenboyd78/sdsctl/main/docs/assets/web-dashboard/theme-system-1366x768.png)

![System theme at 800x480](https://raw.githubusercontent.com/stevenboyd78/sdsctl/main/docs/assets/web-dashboard/theme-system-800x480.png)

![System theme at a 390x844 CSS viewport and DPR2](https://raw.githubusercontent.com/stevenboyd78/sdsctl/main/docs/assets/web-dashboard/theme-system-390x844-dpr2.png)

### LCARS

LCARS now uses the replacement v2 design. These captures use the Classic palette
and Antonio font, with Site directly beneath System and segmented frame rails.
The stable theme choice remains **LCARS**; see
[LCARS design and migration](https://github.com/stevenboyd78/sdsctl/blob/main/docs/lcars-v2.md).

![LCARS theme at 1920x1080](https://raw.githubusercontent.com/stevenboyd78/sdsctl/main/docs/assets/web-dashboard/theme-lcars-1920x1080.png)

![LCARS theme at 1366x768](https://raw.githubusercontent.com/stevenboyd78/sdsctl/main/docs/assets/web-dashboard/theme-lcars-1366x768.png)

![LCARS theme at 800x480](https://raw.githubusercontent.com/stevenboyd78/sdsctl/main/docs/assets/web-dashboard/theme-lcars-800x480.png)

![LCARS theme at a 390x844 CSS viewport and DPR2](https://raw.githubusercontent.com/stevenboyd78/sdsctl/main/docs/assets/web-dashboard/theme-lcars-390x844-dpr2.png)

### Matrix-inspired

![Matrix-inspired theme at 1920x1080](https://raw.githubusercontent.com/stevenboyd78/sdsctl/main/docs/assets/web-dashboard/theme-matrix-1920x1080.png)

![Matrix-inspired theme at 1366x768](https://raw.githubusercontent.com/stevenboyd78/sdsctl/main/docs/assets/web-dashboard/theme-matrix-1366x768.png)

![Matrix-inspired theme at 800x480](https://raw.githubusercontent.com/stevenboyd78/sdsctl/main/docs/assets/web-dashboard/theme-matrix-800x480.png)

![Matrix-inspired theme at a 390x844 CSS viewport and DPR2](https://raw.githubusercontent.com/stevenboyd78/sdsctl/main/docs/assets/web-dashboard/theme-matrix-390x844-dpr2.png)

### First Responder

![First Responder theme at 1920x1080](https://raw.githubusercontent.com/stevenboyd78/sdsctl/main/docs/assets/web-dashboard/theme-first-responder-1920x1080.png)

![First Responder theme at 1366x768](https://raw.githubusercontent.com/stevenboyd78/sdsctl/main/docs/assets/web-dashboard/theme-first-responder-1366x768.png)

![First Responder theme at 800x480](https://raw.githubusercontent.com/stevenboyd78/sdsctl/main/docs/assets/web-dashboard/theme-first-responder-800x480.png)

![First Responder theme at a 390x844 CSS viewport and DPR2](https://raw.githubusercontent.com/stevenboyd78/sdsctl/main/docs/assets/web-dashboard/theme-first-responder-390x844-dpr2.png)

### Amateur Radio

![Amateur Radio theme at 1920x1080](https://raw.githubusercontent.com/stevenboyd78/sdsctl/main/docs/assets/web-dashboard/theme-amateur-radio-1920x1080.png)

![Amateur Radio theme at 1366x768](https://raw.githubusercontent.com/stevenboyd78/sdsctl/main/docs/assets/web-dashboard/theme-amateur-radio-1366x768.png)

![Amateur Radio theme at 800x480](https://raw.githubusercontent.com/stevenboyd78/sdsctl/main/docs/assets/web-dashboard/theme-amateur-radio-800x480.png)

![Amateur Radio theme at a 390x844 CSS viewport and DPR2](https://raw.githubusercontent.com/stevenboyd78/sdsctl/main/docs/assets/web-dashboard/theme-amateur-radio-390x844-dpr2.png)

### Pip-Boy-inspired

![Pip-Boy-inspired theme at 1920x1080](https://raw.githubusercontent.com/stevenboyd78/sdsctl/main/docs/assets/web-dashboard/theme-pip-boy-inspired-1920x1080.png)

![Pip-Boy-inspired theme at 1366x768](https://raw.githubusercontent.com/stevenboyd78/sdsctl/main/docs/assets/web-dashboard/theme-pip-boy-inspired-1366x768.png)

![Pip-Boy-inspired theme at 800x480](https://raw.githubusercontent.com/stevenboyd78/sdsctl/main/docs/assets/web-dashboard/theme-pip-boy-inspired-800x480.png)

![Pip-Boy-inspired theme at a 390x844 CSS viewport and DPR2](https://raw.githubusercontent.com/stevenboyd78/sdsctl/main/docs/assets/web-dashboard/theme-pip-boy-inspired-390x844-dpr2.png)

Pip-Boy-inspired is an original project-owned field-terminal treatment. It uses
local declarative CSS and contains no game artwork, logos, screenshots, sounds,
proprietary fonts, copied hardware geometry, or remote resources.

## Cinematic collection

These 22 original project-owned themes evoke broad genres and operational
settings without using franchise names, logos, characters, screenshots,
proprietary typefaces, copied interface geometry, or remote resources. Each
Full HD reference uses the same fictional held-channel scanner fixture. Six are
independent panoramic environments; the other 16 are distinct colorway
treatments intended for compact and general-purpose displays.

Carrier Air Wing, Tactical Field, Wizarding Archive, Galactic Operations,
Mission Control, and Cyberpunk Night are the headline panoramic environments,
not re-themed instances of one template. They use distinct flight-deck,
map-console, illuminated archive, bridge-command, annunciator-switchbank, and
asymmetric neon architectures, with their own materials, instruments, motion,
and held-state reactions. A panorama activates only at a CSS viewport of at
least 1280x720 and an aspect ratio of at least 3:2. If any condition is false,
the theme returns to its compact colorway and the shared horizontal navigation.
Reduced-motion mode keeps the full static scene and all state indications.

Across every theme, the Scanner hierarchy remains one stable two-by-two live
matrix: System and Department on the first row, then Channel and Site on the
second. Decorative panoramic reactions supplement those semantic fields; they
never replace the visible held-field state.

### Mission Control

![Mission Control theme at 1920x1080](https://raw.githubusercontent.com/stevenboyd78/sdsctl/main/docs/assets/web-dashboard/theme-cinematic-mission-control-1920x1080.png)

### Cyberpunk Night

![Cyberpunk Night theme at 1920x1080](https://raw.githubusercontent.com/stevenboyd78/sdsctl/main/docs/assets/web-dashboard/theme-cinematic-cyberpunk-night-1920x1080.png)

### Synthwave Outrun

![Synthwave Outrun theme at 1920x1080](https://raw.githubusercontent.com/stevenboyd78/sdsctl/main/docs/assets/web-dashboard/theme-cinematic-synthwave-1920x1080.png)

### Cold War Radar

![Cold War Radar theme at 1920x1080](https://raw.githubusercontent.com/stevenboyd78/sdsctl/main/docs/assets/web-dashboard/theme-cinematic-cold-war-radar-1920x1080.png)

### Dieselpunk Command

![Dieselpunk Command theme at 1920x1080](https://raw.githubusercontent.com/stevenboyd78/sdsctl/main/docs/assets/web-dashboard/theme-cinematic-dieselpunk-command-1920x1080.png)

### Steampunk Telegraph

![Steampunk Telegraph theme at 1920x1080](https://raw.githubusercontent.com/stevenboyd78/sdsctl/main/docs/assets/web-dashboard/theme-cinematic-steampunk-telegraph-1920x1080.png)

### Retro Arcade

![Retro Arcade theme at 1920x1080](https://raw.githubusercontent.com/stevenboyd78/sdsctl/main/docs/assets/web-dashboard/theme-cinematic-retro-arcade-1920x1080.png)

### Y2K Media Center

![Y2K Media Center theme at 1920x1080](https://raw.githubusercontent.com/stevenboyd78/sdsctl/main/docs/assets/web-dashboard/theme-cinematic-y2k-media-1920x1080.png)

### Mecha Operations

![Mecha Operations theme at 1920x1080](https://raw.githubusercontent.com/stevenboyd78/sdsctl/main/docs/assets/web-dashboard/theme-cinematic-mecha-operations-1920x1080.png)

### Comic Book

![Comic Book theme at 1920x1080](https://raw.githubusercontent.com/stevenboyd78/sdsctl/main/docs/assets/web-dashboard/theme-cinematic-comic-book-1920x1080.png)

### Wizarding Archive

![Wizarding Archive theme at 1920x1080](https://raw.githubusercontent.com/stevenboyd78/sdsctl/main/docs/assets/web-dashboard/theme-cinematic-wizarding-archive-1920x1080.png)

### Gothic Cathedral

![Gothic Cathedral theme at 1920x1080](https://raw.githubusercontent.com/stevenboyd78/sdsctl/main/docs/assets/web-dashboard/theme-cinematic-gothic-cathedral-1920x1080.png)

### Fantasy Cartographer

![Fantasy Cartographer theme at 1920x1080](https://raw.githubusercontent.com/stevenboyd78/sdsctl/main/docs/assets/web-dashboard/theme-cinematic-fantasy-cartographer-1920x1080.png)

### Desert Empire

![Desert Empire theme at 1920x1080](https://raw.githubusercontent.com/stevenboyd78/sdsctl/main/docs/assets/web-dashboard/theme-cinematic-desert-empire-1920x1080.png)

### Carrier Air Wing

![Carrier Air Wing theme at 1920x1080](https://raw.githubusercontent.com/stevenboyd78/sdsctl/main/docs/assets/web-dashboard/theme-cinematic-carrier-air-wing-1920x1080.png)

### Motorsport Pit Wall

![Motorsport Pit Wall theme at 1920x1080](https://raw.githubusercontent.com/stevenboyd78/sdsctl/main/docs/assets/web-dashboard/theme-cinematic-motorsport-pit-wall-1920x1080.png)

### Noir Detective

![Noir Detective theme at 1920x1080](https://raw.githubusercontent.com/stevenboyd78/sdsctl/main/docs/assets/web-dashboard/theme-cinematic-noir-detective-1920x1080.png)

### Art Deco Radio

![Art Deco Radio theme at 1920x1080](https://raw.githubusercontent.com/stevenboyd78/sdsctl/main/docs/assets/web-dashboard/theme-cinematic-art-deco-radio-1920x1080.png)

### Solarpunk Communications

![Solarpunk Communications theme at 1920x1080](https://raw.githubusercontent.com/stevenboyd78/sdsctl/main/docs/assets/web-dashboard/theme-cinematic-solarpunk-1920x1080.png)

### National Parks Ranger

![National Parks Ranger theme at 1920x1080](https://raw.githubusercontent.com/stevenboyd78/sdsctl/main/docs/assets/web-dashboard/theme-cinematic-national-parks-1920x1080.png)

### Galactic Operations

![Galactic Operations theme at 1920x1080](https://raw.githubusercontent.com/stevenboyd78/sdsctl/main/docs/assets/web-dashboard/theme-cinematic-galactic-operations-1920x1080.png)

### Tactical Field

![Tactical Field theme at 1920x1080](https://raw.githubusercontent.com/stevenboyd78/sdsctl/main/docs/assets/web-dashboard/theme-cinematic-tactical-field-1920x1080.png)

## Waterfall references

The Waterfall pane uses the daemon-owned private waterfall service only while a
visible authenticated browser consumer exists. Its spectrum and rolling history
are relative and uncalibrated; they do not claim dB, RF-power, or documented FFT
magnitude semantics.

### System Waterfall at 1920x1080

![System theme Waterfall pane at 1920x1080](https://raw.githubusercontent.com/stevenboyd78/sdsctl/main/docs/assets/web-dashboard/theme-system-waterfall-1920x1080.png)

### Pip-Boy-inspired Waterfall at 800x480

![Pip-Boy-inspired theme Waterfall pane at 800x480](https://raw.githubusercontent.com/stevenboyd78/sdsctl/main/docs/assets/web-dashboard/theme-pip-boy-inspired-waterfall-800x480.png)

## Security boundary

- Standalone operation remains loopback-only unless private-LAN mode is
  explicitly configured.
- Home Assistant App access normally uses authenticated Ingress.
- Browser clients consume daemon fanout and do not become independent scanner or
  RTSP/RTP owners.
- Theme packages are presentation-only and cannot add scanner controls or remote
  resources.

See the canonical
[web dashboard guide](https://github.com/stevenboyd78/sdsctl/blob/main/docs/web-dashboard.md)
for authentication, TLS, Ingress, audio, recording, managed-theme, and recovery
details.
