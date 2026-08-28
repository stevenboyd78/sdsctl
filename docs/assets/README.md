# Project visual assets

This directory contains the sdsctl project branding and reproducible Textual TUI
screenshots.

## Branding

The sdsctl project branding set uses a generic neon green scanner display with
no agency, location, talkgroup, frequency, or channel references.

- `sdsctl-logo.svg` — primary horizontal vector logo
- <img src="sdsctl-logo.svg">
- `sdsctl-icon.svg` — square vector icon
- <img src="sdsctl-icon.svg">
- `sdsctl-logo-4k.png` — 4800×1200 transparent PNG
- <img src="sdsctl-logo-4k.png">
- `sdsctl-icon-2048.png` — 2048×2048 transparent PNG
- <img src="sdsctl-icon-2048.png">
- `sdsctl-wallpaper-1080p.png` — 1920×1080 wallpaper
- <img src="sdsctl-wallpaper-1080p.png">
- `sdsctl-wallpaper-4k.png` — 3840×2160 wallpaper
- <img src="sdsctl-wallpaper-4k.png">

The SVG files are the vector sources. Render the transparent PNG logo and icon
from those SVGs with Inkscape. Compose the wallpapers with ImageMagick by placing
the horizontal logo over a dark radial-green background.

## Textual TUI screenshots

The `screenshots/` directory contains native SVG exports from the real Textual
application:

- `screenshots/tui-overview.svg` — wide operational view with an active recording;
- `screenshots/tui-recordings.svg` — recording-library view;
- `screenshots/tui-compact.svg` — 24-row compact layout with concise audio,
  PSI health, and essential-control summaries.

All scanner names, departments, sites, channels, frequencies, endpoints, logs,
recordings, and timestamps shown in these images are fictional demonstration
data.

The generator creates temporary WAV files and does not require scanner hardware,
network access, PortAudio, or a display server.

Regenerate the screenshots from the repository root:

    python -m pip install -e ".[dev]"
    python scripts/generate_tui_screenshots.py

Do not edit the generated SVG files manually. Update the generator and regenerate
all screenshots together so the documented interface remains reproducible.

## Web dashboard screenshots

The `web-dashboard/` directory contains native Chrome captures of the real
packaged dashboard using deterministic fictional daemon, scanner, radio, audio,
recording, and reliability state. The gallery covers the deterministic built-in
theme order—System, LCARS-inspired, Matrix-inspired, First Responder, Amateur
Radio, and Pip-Boy-inspired—and the normal-zoom reference viewports 390x844,
800x480, 1366x768, and 1920x1080 used by the responsive six-pane workspace.
Two additional captures select the live deterministic Waterfall pane in System
at 1920x1080 and Pip-Boy-inspired at 800x480.
The original Pip-Boy-inspired presentation uses only project-owned declarative
CSS and contains no game assets.

Regenerate the complete checked-in gallery from the repository root with Chrome
or Chromium and the web dependencies installed:

    python scripts/generate_web_dashboard_screenshots.py

The helper requires Node.js 24 or newer and starts a temporary loopback-only demo
application. For each isolated Chrome profile, its dependency-free CDP bridge
sets and verifies the exact declared CSS width, height, and DPR; pins canonical
light color, normal contrast, forced-colors-off, and reduced-motion media; and
waits for dashboard state, fonts, stable Waterfall Canvas pixels, and consecutive
identical compositor frames. It captures only that viewport and returns the
authoritative outer HTML; Python then verifies the ready DOM plus complete PNG
structure, CRCs, compressed scanlines, and DPR-scaled physical dimensions before
reconstructing and deterministically re-encoding Chrome's pixels and atomically
publishing the image. Chrome outer-window dimensions are not used as a viewport
proxy. The helper shuts the server down when generation completes. Do not edit
generated PNG files manually; update the packaged dashboard or generator and
regenerate the gallery together. Run
`python scripts/generate_web_dashboard_screenshots.py --verify-gallery` to verify
the exact generator, local asset, canonical documentation, and raw-main wiki
reference set without opening Chrome. CI and release validation run
`--verify-repeatability` after the browser audit for the System scanner capture
and both Waterfall captures; the mode captures selected names twice into
temporary directories with one Chrome executable and does not require pixel
identity across Chrome versions.
The reviewed `wiki/Web-Dashboard.md` page embeds this same default-branch image
set rather than keeping a second wiki-only copy, so documentation and wiki
captures remain synchronized after publication.
