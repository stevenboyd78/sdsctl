# Supplemental WebUI controller candidate

Status: **offline integration only; no startup activation or deployment**.
Builds on the [delivery and expiry contract](scanner-display-supplemental-delivery.md).
The existing public schema-1 endpoint and ordinary acquisition policy are unchanged.

## Same-capture response

`project_supplemental_web_bundle(capture, now=...)` accepts one immutable owner
capture, validates it and produces a separate internal protocol:

```text
protocol: sdsctl.mimic-supplemental
version: 1
display: existing schema-1-shaped frame set, without supplemental clock text
supplemental: sdsctl.supplemental version 1 envelope
```

This is not a response from the current `/api/v1/display-frame` route. The
[authenticated transport candidate](scanner-display-supplemental-transport.md)
can serve it only through separate daemon and web-application dependency opt-ins,
which ordinary startup does not supply. Projection does not run commands or
renew demand. The profile source status comes from the same capture, never an
assumed match. Changed sources, pending profile refreshes and failed imports are
refused by this narrow candidate. It only covers the qualified conventional and
trunk families. It does not enable reads in additional scanner modes.

Keeping clock text out of the base frame is intentional: when clock freshness
expires, the client must not fall back to a server-inserted value carrying only
the longer PSI deadline. Favorites remain a separately expiring diagnostic bank;
they are not mapped to the scanner's F0, S0 or D0 rows.

## Explicit WebUI controller opt-in

The existing `sdsctlMimic.create` polling controller accepts an internal optional
`supplementalContext` argument. Its default is null. The normal dashboard caller
does not supply it, and does not load or serve `mimic-supplemental.js`. The
generated Home Assistant card does not include the new controller or guard.
Both the explicit context and the dependency are required; there is no fallback
from a missing guard into unguarded supplemental rendering.

The opt-in controller binds once to the context verified by its caller. It never
adopts an incoming response's endpoint, stream, session, profile or context revision.
A new verified context requires a new controller; ordinary status callbacks,
layout changes and hide/show do not replace or reset the existing guard.
Automated context negotiation/replacement remains future integration work.

The actual controller's existing response-size limit (256 KiB), UTF-8/JSON and
canonical frame validation apply before it can update the DOM. Bundle decoding
also checks frame/auxiliary identity, profile, PSI sequence and age atomically.
An invalid response clears the whole candidate view. A valid bundle may still
have one unavailable or expired auxiliary source; that source alone is omitted.

Each request begins a guard ticket before transport. Request duration consumes
the lease rather than restarting it on receipt. Local timers clear each source
without requiring another response. The existing scanner-frame timer is separate.
Clock expiration clears only the profile-selected small Day/Time cells; it does
not clear otherwise-current scanner fields. Dates use the scanner-local calendar
and times use 24-hour `HH:MM`; no host clock or timezone inference is introduced.

Global Favorites states appear only inside the existing details disclosure,
explicitly labeled as global 00–99 states rather than LCD quick-key rows. They
wrap on narrow displays and disappear when their independent deadline expires.
Stopping, hiding or losing capability clears both the UI and pending callbacks,
while retaining sample retirement history. Terminal stop cannot be undone by a
late status callback. Failed requests also retire the last displayed auxiliary
samples, requiring new successful sample IDs for recovery.

## Offline tests and remaining gates

The deterministic test harness loads the real WebUI source, actual dependency,
canonical generated geometry, and a bundle from the fake scanner/cache/owner.
It drives streamed bodies, hung requests, local timers, response mutations,
visibility, layout choice, sign-out and independent controller instances. This
is synthetic DOM/HTTP testing in Node, not visual browser or physical acceptance.
The regular WebUI and generated HA lifecycle suites remain regression gates.

The opt-in authenticated server transport now provides explicit negotiation,
no-store responses, bounded bundles and stale-context rejection. Before deployment,
connect the actual browser coordinator and define verified context replacement;
integrate TUI and HA consumers; and preserve the qualified bounded acquisition
policy. Then build a source-pinned candidate and request fresh operator readiness.
There is no new hardware trial, background task or CLI/config activation switch.
