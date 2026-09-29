# Built-in WebUI typography

The Theme picker still defaults to System. The other built-in themes now offer
locally bundled typography; no internet connection or operating-system font
installation is needed. The browser falls back to system fonts if loading fails.

| Theme | Default | Other font pairings |
| --- | --- | --- |
| LCARS | Antonio | Its existing LCARS / Readable mix / System selector |
| First Responder | Barlow Condensed headings, controls and scanner names; IBM Plex Mono readouts; system explanatory text | None |
| Amateur Radio | Orbitron headings and controls, Atkinson Hyperlegible Next names/body, Atkinson Hyperlegible Mono readouts | Audiowide headings/controls, or Atkinson readability-first |
| Pip-Boy-inspired | Share Tech Mono | None |
| Matrix | IBM Plex Mono | JetBrains Mono or Source Code Pro |

For the four non-LCARS themed layouts, **Typography** offers:

- **Theme fonts:** the pairing above.
- **Readable mix:** themed headings with system-font controls, names and body
  text, and native monospace readouts.
- **System fonts:** native sans-serif with native monospace readouts throughout.

**Font pairing** appears only where there are alternatives. On wide screens,
appearance controls are centered between the overview title and connection
status, matching LCARS. In the full dashboard, widths up to 65rem wrap them onto
a centered row below the title and status. In display-only mode, those narrow
screens (including tall phones) keep typography in the dashboard menu's
Appearance section instead, preserving plot space and full-sized touch controls.
Compact display-only layouts keep their other appearance controls there too.
Resizing back to a wide screen restores the same controls and preferences to
their original positions. A theme with no font alternatives hides Font pairing.
Preferences are independent for each theme and stored only in this browser under
`sdsctl.web.theme-typography.v1`. Switching themes does not overwrite the previous
theme's choices. Invalid values fall back to defaults; blocked browser storage
does not prevent changes for the current page.

Rapid Response and OCR-A are not bundled or offered. Matrix readouts disable
ligatures and decorative text shadows. Mimic-SDS retains its scanner-oriented
field fonts; this does not change the TUI terminal font, Home Assistant cards,
scanner configuration or daemon behavior. LCARS retains its existing separate
preferences and stable `lcars` identity; see [LCARS v2](lcars-v2.md).

## Offline assets and attribution

These are unmodified original TrueType files distributed under the SIL Open
Font License 1.1, not under the application's MIT license. Each family includes
its original copyright and complete OFL text alongside the font, and those
licenses also appear in wheel/distribution license metadata. Files are renamed
for stable asset URLs only; no glyphs, font metadata or internal family names
have been changed.

[The provenance manifest](theme-font-sources.json) records the pinned Google
Fonts source revision, exact original URLs, byte lengths and SHA-256 hashes for
every added font and license. Antonio's attribution is in the LCARS document.
Assets are same-origin with explicit display-only GET authorization; third-party
font hosting and broader filesystem access are not enabled. `font-display: swap`
allows readable fallback immediately and only fonts in use are requested.
