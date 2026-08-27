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

### System

![System theme at 1920x1080](https://raw.githubusercontent.com/stevenboyd78/sdsctl/main/docs/assets/web-dashboard/theme-system-1920x1080.png)

### LCARS-inspired

![LCARS-inspired theme at 1920x1080](https://raw.githubusercontent.com/stevenboyd78/sdsctl/main/docs/assets/web-dashboard/theme-lcars-1920x1080.png)

### Matrix-inspired

![Matrix-inspired theme at 1920x1080](https://raw.githubusercontent.com/stevenboyd78/sdsctl/main/docs/assets/web-dashboard/theme-matrix-1920x1080.png)

### First Responder

![First Responder theme at 1920x1080](https://raw.githubusercontent.com/stevenboyd78/sdsctl/main/docs/assets/web-dashboard/theme-first-responder-1920x1080.png)

### Amateur Radio

![Amateur Radio theme at 1920x1080](https://raw.githubusercontent.com/stevenboyd78/sdsctl/main/docs/assets/web-dashboard/theme-amateur-radio-1920x1080.png)

### Pip-Boy-inspired

![Pip-Boy-inspired theme at 1920x1080](https://raw.githubusercontent.com/stevenboyd78/sdsctl/main/docs/assets/web-dashboard/theme-pip-boy-inspired-1920x1080.png)

Pip-Boy-inspired is an original project-owned field-terminal treatment. It uses
local declarative CSS and contains no game artwork, logos, screenshots, sounds,
proprietary fonts, copied hardware geometry, or remote resources.

## Responsive references

At normal zoom the document and active pane fit without scrolling at the
project's phone, compact-landscape, desktop, and Full-HD reference sizes. When
text enlargement or browser zoom needs more room, content reachability takes
priority and conventional scrolling returns.

### 390x844 phone viewport at DPR2

![System theme at a 390x844 CSS viewport and DPR2](https://raw.githubusercontent.com/stevenboyd78/sdsctl/main/docs/assets/web-dashboard/theme-system-390x844-dpr2.png)

### 800x480 compact landscape

![Pip-Boy-inspired theme at 800x480](https://raw.githubusercontent.com/stevenboyd78/sdsctl/main/docs/assets/web-dashboard/theme-pip-boy-inspired-800x480.png)

### 1366x768 desktop

![Amateur Radio theme at 1366x768](https://raw.githubusercontent.com/stevenboyd78/sdsctl/main/docs/assets/web-dashboard/theme-amateur-radio-1366x768.png)

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
