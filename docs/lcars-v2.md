# LCARS v2

LCARS v2 now replaces the original LCARS-inspired WebUI design. Select **LCARS**
in the normal Theme picker; its stable identifier remains `lcars`. System is
still the default and safe fallback. There is no duplicate LCARS v2 entry.

## Existing selections

- A saved `sdsctl.web.theme=lcars` automatically uses the new design.
- The preview identifier `lcars-v2` is normalized to `lcars` before first paint
  and repaired in browser storage when storage is writable.
- Existing v2 palette and typography keys are retained unchanged. No system
  palette, other theme selection, scanner setting, or server configuration is
  migrated. If browser storage is blocked, theme selection still works for the
  current page; persistence is optional.
- The retired `lcars-v2` identity remains reserved against managed-theme
  installation to prevent another package from capturing migrated selections.
- The old artwork is no longer shipped as a selectable theme. System remains
  the alternative for users who prefer the original functional layout.

## Appearance

- Six palettes: Classic, Nemesis Blue, Lower Decks, Lower Decks PADD, Voyager,
  and Picard. The accent values match the public original CSS palette values
  from [TheLCARS](https://www.thelcars.com/themes/), observed September 28, 2026.
- Typography: **LCARS / Antonio** (default), **Readable mix** (Antonio headings
  and system-font data), or **System**. The unmodified Antonio variable font is
  bundled locally; there are no runtime Google Fonts or other CDN requests.
  If the font cannot load, the browser uses a system fallback immediately.
- Black canvas, colored horizontal segments with black breaks, and rounded
  frame elbows. A decorative row above the wide-screen navigation buttons
  leaves room for the upper curve to finish without clipping labels.
- Site sits beneath System, Department beside it, and Channel spans the next
  row. The same live Site node is moved, not duplicated. Other themes restore
  it to its original technical-details position.
- At smaller sizes the navigation returns to horizontal / wrapping tabs.
  Existing tab keyboard handling, status meanings, hidden controls, and
  display-only authorization remain in place.

Palette and typography are browser-local under `sdsctl.web.lcars-v2-palette`
and `sdsctl.web.lcars-v2-typography`. Switching away and back preserves them.
Invalid preferences fall back to Classic and Antonio; blocked browser storage
does not prevent selection. Changing appearance never sends scanner commands.

The manifest and paint stylesheet live in `themes/web/lcars`. The built-in
shell explicitly supplies `web_assets/lcars-v2.css` and `lcars-v2.js` for layout,
pickers, and reversible Site placement. This does not expand the capabilities
of third-party CSS-only theme packages. Same-origin assets use the existing
content-security policy and explicit display-only GET allowlist.

## Attribution and bundled font

The design is inspired by TheLCARS; the sdsctl layout and styles are original,
not redistributed site templates. Palette URLs and observed checksums are in
[the palette provenance record](lcars-v2-palette-sources.json). Semantic
online, warning, and error colors remain stable across all palette variants.

Antonio is copyright 2013 The Antonio Project Authors, licensed under the SIL
Open Font License 1.1. Source: [Google Fonts Antonio](https://github.com/google/fonts/tree/main/ofl/antonio).
The original `Antonio[wght].ttf` is renamed `antonio-variable.ttf`, without
conversion, subsetting, or glyph changes. Its Git blob SHA-1 is
`e30920a139fbe8709cba29163b805b44f0a9876a` (74,104 bytes).
The complete license is shipped as `web_assets/fonts/antonio-OFL.txt`, also
included in distribution license metadata. The font is not relicensed under
the application's MIT license.

## Scope and release review

This is a WebUI integration, not a TUI or Home Assistant card redesign. Theme
replacement was approved following the design review. It does not install an
update on Home Assistant or either Pi, change scanner behavior, or change
runtime daemon configuration. The LCARS gallery shows the replacement design;
normal release-wide checks and real ingress/Pi browser acceptance remain part
of rollout. Source history retains the former theme for recovery.
