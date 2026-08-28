# Web Dashboard

> [!IMPORTANT]
> The repository's
> [web dashboard guide](https://github.com/stevenboyd78/sdsctl/blob/main/docs/web-dashboard.md)
> is the authoritative reference for configuration, security boundaries, and
> exact behavior. This page is a visual task-oriented introduction.

The daemon-backed dashboard provides Scanner, Controls, Audio, Recordings, and
Diagnostics panes in one responsive browser workspace. It remains a client of
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

## Choose a theme

Theme choice is browser-local presentation state. It does not change daemon or
scanner state. The six built-in choices share the same panes, controls, fields,
keyboard behavior, and security boundary.

Every theme is captured in the same review order: 1920x1080 Full HD, 1366x768
desktop, 800x480 compact landscape/Raspberry Pi display, and 390x844 portrait
phone at DPR2. At normal zoom the document and active pane fit without
scrolling at these reference sizes. When text enlargement or browser zoom needs
more room, content reachability takes priority and conventional scrolling
returns.

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

### LCARS-inspired

![LCARS-inspired theme at 1920x1080](https://raw.githubusercontent.com/stevenboyd78/sdsctl/main/docs/assets/web-dashboard/theme-lcars-1920x1080.png)

![LCARS-inspired theme at 1366x768](https://raw.githubusercontent.com/stevenboyd78/sdsctl/main/docs/assets/web-dashboard/theme-lcars-1366x768.png)

![LCARS-inspired theme at 800x480](https://raw.githubusercontent.com/stevenboyd78/sdsctl/main/docs/assets/web-dashboard/theme-lcars-800x480.png)

![LCARS-inspired theme at a 390x844 CSS viewport and DPR2](https://raw.githubusercontent.com/stevenboyd78/sdsctl/main/docs/assets/web-dashboard/theme-lcars-390x844-dpr2.png)

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
