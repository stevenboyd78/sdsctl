# Native HTTPS browser-kiosk design

Status: **approved display-only scope; implementation candidate, not released**.
Milestone 34.1 follows the accepted v0.29.0 managed TUI and the v0.29.1/v0.29.2
HDMI presentation patches. The operator selected the display-only starting point
and approved implementation followed by testing. See the
[candidate setup guide](browser-kiosk.md) for the implemented commands and the
remaining physical-acceptance boundary. The latest published v0.29.2 does not
include these commands.

## What a browser kiosk would add

A Pi would open the existing native HTTPS dashboard in a dedicated graphical
session, while the daemon remains the only scanner owner. Several displays
could follow that same daemon. The browser would not open a serial device,
connect to the scanner directly, or run a second scanner audio receiver.

Keep the existing [managed TUI](managed-pi-display.md) available independently.
A browser uses pixel dimensions, browser zoom and a graphical session; the TUI
uses terminal rows and columns. Do not replace a working `/dev/tty1` service or
change its accepted console font as a side effect of kiosk installation.

## Published v0.29.2 baseline

The current [native dashboard](web-dashboard.md#authenticated-lan-mode) already
provides exact-origin HTTPS and password authentication. The following limits
matter before adding unattended startup:

| Surface | Existing behavior | Kiosk consequence |
| --- | --- | --- |
| Authorization | One dashboard password authenticates native browser sessions; there is no per-browser observe-only role. | Full-screen presentation must not be described as read-only access. |
| Session lifetime | Thirty-minute idle and eight-hour absolute limits; active stream leases affect idle pruning, but do not remove absolute expiry. | Traffic cannot keep a session authenticated indefinitely. |
| Server restart | Session records are process-local. | A browser cookie cannot restore a server session lost on restart. |
| Browser storage | The secure, HttpOnly, SameSite=Strict cookie carries Max-Age. | Do not promise memory-only browser storage; treat its profile as sensitive. |
| Streaming | Status, events, audio and Waterfall use the web service's existing daemon clients. | Reconnect and visibility handling must retain bounded per-consumer cleanup. |
| Diagnostics | Connected-client inventory covers remote-daemon clients in Ingress, not native browser sessions. | Do not use that inventory as evidence of browser-kiosk health. |

These statements follow the existing [authentication implementation](../src/sds200/web_auth.py)
and [dashboard routes](../src/sds200/web_dashboard.py). Authenticated native
requests can reach the capability-negotiated scanner and recording routes;
hiding their buttons would not prevent direct requests. Neither the ordinary
dashboard login nor the TUI's client secret is a new browser-display identity.

## Decision 1: display-only or operator access

Selected starting point: a **display-only screen with server-side permissions**.
The candidate uses a separate manually entered display password. It permits
status, events, themes and Waterfall; audio, recording inventory/downloads and
all scanner or management mutations are denied. The password is shared by the
display sessions using that server, not an independently revocable per-device
identity. It must differ from the operator password.

For that mode, define an explicit allowlist for scanner status, live events,
selected diagnostics and visible Waterfall demand. Classify optional local
audio playback and access to saved recordings separately; viewing the scanner
does not automatically require permission to download recordings. Deny scanner
holds, navigation, reconnect, daemon recording start/stop, configuration and
credential management before any private daemon operation is created.

Server authorization must enforce these decisions on direct HTTP requests as
well as the rendered interface. Unknown or newly added operations must fail
closed. Classify operations by effect, not only HTTP method: the existing
Waterfall read stream legitimately acquires and releases shared scanner demand.

An operator dashboard is a different, deliberately privileged option. If chosen,
document the controls exposed to anyone with access to the unlocked browser.
Do not label that installation observe-only or silently enable it as the
display-only fallback. Preserve ordinary operator and Ingress behavior while
adding any new permission model.

## Decision 2: login and unattended recovery

The first implementation must distinguish these states visibly:

- **Live:** authenticated, with fresh data from the existing daemon stream.
- **Reconnecting:** a temporary transport or service outage; last-known data is
  labeled stale and retry work is bounded.
- **Login required:** no valid session, expiry, explicit logout, or a web-process
  restart; close streams and stop automatic authentication attempts.
- **Configuration requires attention:** origin mismatch, untrusted or expired
  certificate, or an invalid deployment; never bypass the failure.
- **Stopped:** the operator intentionally closed the display; do not relaunch it
  as though it had crashed.

Certificate failures can prevent the page from loading at all. Qualify the
browser's warning and the launcher's preflight behavior rather than expecting
dashboard JavaScript to diagnose a failed TLS connection. Never retry past a
certificate warning automatically.

Manual login is the conservative initial recovery contract. It is not a claim
of hands-off operation across reboot or session expiry. Browser relaunch and
page reload are not authentication renewal. Recovery tests must distinguish a
scanner-daemon-only restart from a web-process or whole-App restart, which can
invalidate the browser session.

If fully unattended re-login is required, design independently revocable,
least-privilege browser-device enrollment, protected local secret storage,
bounded renewal, revocation and explicit recovery first. Do not reuse the
operator dashboard password, TUI client secret, Home Assistant token or a copied
cookie as an automatic-login shortcut. Do not disable expiry or increase the
eight-hour limit merely to avoid the login screen.

## Deployment boundary

- Use a separately selected test host or an explicitly agreed temporary display
  transition. Existing production TUI clients and their independent identities
  must remain intact.
- Inventory the target OS, browser, graphical session, display resolution,
  input devices, service ownership and recovery access before choosing a
  launcher. Do not assume X11, Wayland, a desktop environment or a particular
  compositor from the words "Raspberry Pi" or "1080p".
- Use one dedicated non-root browser account and private profile. Keep browser
  sandboxing enabled, isolate its profile from ordinary personal browsing, and
  establish whether credentials/cookies can be written to disk. Do not copy or
  publish profile contents, cookies, screenshots of secrets or raw diagnostics.
- Configure trust for the exact reviewed server identity before acceptance.
  A matching certificate name alone is not issuer trust. Do not pass
  `--ignore-certificate-errors`, disable TLS verification, or copy a server
  private key to a display. Define trust removal and certificate-rotation steps.
- Use only the canonical HTTPS origin. The remote-daemon port is not a browser
  service; neither the loopback listener nor the Ingress listener may be exposed
  as a kiosk shortcut. Public exposure and reverse-proxy support remain outside
  this milestone.
- For Home Assistant, follow [advanced access](home-assistant-advanced-access.md).
  Its native service is disabled by default. Supervisor publishes an enabled
  mapping on all host interfaces; do not claim an interface restriction it does
  not provide. A new mapping or App restart needs a reviewed deployment scope
  and coordination with existing production clients.
- Startup ordering and recovery must follow the chosen graphical stack. Keep
  browser-crash recovery separate from authentication failure and intentional
  shutdown; do not transplant the TUI's exit-code contract without validation.
- Audio stays off by default. Validate explicit playback and stop behavior on
  the selected browser and physical output; do not bypass browser autoplay
  policy or start speakers during an unattended test.

## Implementation order and acceptance

1. Settle authorization and login/recovery requirements. Inventory every route
   and long-lived response; add synthetic tests for permitted and denied direct
   requests, ordinary operator compatibility, expiry and session invalidation.
2. Implement the selected server and browser-state behavior before adding kiosk
   startup. Prove denied operations never create daemon work, stale displays
   cannot appear live, and authentication failures do not cause reload loops.
3. Qualify one graphical/browser stack interactively, then package its exact
   launcher, preflight, start/stop, upgrade and removal workflow. Keep secrets
   out of command arguments, launcher files and service logs.
4. Run automated checks with fictional data. Verify browser geometry, session
   expiry, TLS/origin failures, denied controls, visible and hidden Waterfall,
   stream cleanup, several simultaneous clients and bounded reconnect work.
5. Physically accept the approved target: readable full-screen presentation,
   cold start, login-required recovery, temporary outages, web/App restart,
   certificate and device-access recovery where implemented, optional explicit
   audio, and clean intentional shutdown. Record actual results separately from
   headless tests; do not inherit the TUI's physical acceptance.
6. Restore the exact prior host/App state after temporary acceptance. Remove
   only the named test identity, profile, launcher and trust material; preserve
   production listeners, identities, recordings and unrelated browser data.
   Publish only after the tested feature and installation documentation agree.

This contract does not authorize unspecified production-host, browser-trust,
credential, port or startup changes. Physical acceptance still needs a selected
test host and an exact deployment scope; no release date or version is promised.
