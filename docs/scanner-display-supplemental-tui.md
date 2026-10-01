# Supplemental TUI reader candidate

Status: **offline-tested internal opt-in; not activated or deployed**.
This extends the [authenticated supplemental transport](scanner-display-supplemental-transport.md)
to the existing Textual Mimic-SDS screen. Ordinary CLI startup, daemon acquisition,
Home Assistant resources and the browser shell remain unchanged.

## Integration boundary

`ScannerTuiApp` and `run_tui` accept an optional `supplemental_display_source`.
It is mutually exclusive with the ordinary `display_source`. Neither CLI startup
nor service configuration selects it yet. Advertising a server capability does
not enable it automatically.

The `daemon_supplemental_source` adapter requires a **dedicated, finite-timeout
daemon API client**, using its existing authenticated transport and credentials.
Do not share that client with the event stream, controls or audio. The adapter
requires both `display.supplemental.context` and `display.supplemental.frame`.
There is no fallback to `display.frame`, no direct scanner connection, and no
automatic reauthentication or credential storage change.

Opening Mimic starts one lazy worker. Covering it with the runtime drawer or
command palette suspends it; returning resumes after explicit context negotiation.
The worker owns network reads and client close. UI callbacks only take a short
condition lock to invalidate a generation or project a guarded immutable view.
They never wait for network I/O, client close, or a worker join. Final shutdown
can join for at most five seconds; a custom source must still honor finite I/O.

## Freshness and recovery

The single-consumer freshness guard is serialized under that condition lock.
Negotiation and bounded bundle validation occur outside it, with generation
checks before adopting either response. A late response after hide or shutdown
cannot restore values. One five-second acceptance budget covers negotiation plus
frame delivery; late completions are rejected. This is not a mechanism for
interrupting an arbitrary blocking Python callable.

Frame freshness starts **before** its request, not when the response arrives.
Clock, Favorites and PSI expire independently. Repeated successful sample IDs
never renew a lease, and a suspended/expired sample cannot be revived by replay.
The existing 100 ms TUI refresh clears expired clock text even if no newer PSI
packet arrives or the worker is blocked: expiry changes the immutable projected
packet identity used by the renderer's memoization. Fractional age changes alone
do not repaint an unchanged packet.

Transient transport failures and invalid bundles clear values and retry after
two seconds, negotiating before another frame request. A changed frame binding
cannot itself authorize a new context. Same-context negotiation retains the
existing guard; a verified replacement closes it and creates a new guard without
replacing the screen or resetting layout/LED choices.

Endpoint identity stays pinned. Context and profile-invalidation revisions cannot
decrease within a stream/session. Retired connections cannot return, nor can a
retired profile hash return within the same epoch. Advancing epochs compact the
profile history; routine invalidations do not consume a connection-history slot.
Retired connections and same-epoch profile hashes are each capped at 64.

Authentication expiry, authorization denial, failed server trust, incompatible
capabilities, retired-context adoption and exhausted history stop this reader.
Hiding/showing it cannot restart it. Errors shown to users are fixed text, never
remote exception contents, paths or credentials. Recovery requires a newly
authorized application instance, not deletion of saved managed-device state.

## Presentation

Only profile-configured small `Day` / `Time` slots receive the scanner-local RTC.
Empty/blank slots and all other profile choices, colors, indicators and geometry
are preserved. Base PSI packets are never mutated or used as fallback clock text.
No timezone conversion, host-clock substitution, seconds extrapolation or inferred
LCD position is performed. The separate ordinary TUI header clock is unchanged.

Global Favorites keys 00–99 appear in the runtime drawer as Absent / Off / On,
with an explicit **snapshot at drawer open** label. They are not represented as
the physical F0/S0/D0 banks. The drawer pauses frame polling, so captured values
are not advertised as live. Stale or unavailable states contain no key values.

## Validation and remaining gates

Offline tests cover independent expiry, blocked/delayed reads, immutable views,
retired samples, context history, capability/authentication failures, hide/resume,
shutdown, a real local Unix daemon API with a fake scanner owner, and the actual
Textual application at 80×30 and 160×45. The Unix fixture verifies that delivery
adds no scanner reads. This is not physical Pi or visual hardware acceptance.

The [HA consumer candidate](scanner-display-supplemental-ha.md) now provides the
corresponding offline card lifecycle integration, also default-off.
The [bounded authenticated demand candidate](scanner-display-supplemental-demand.md)
now adds optional lease renewal, leaving this cached-only mode unchanged.
Still required: explicit client/shell/resource activation. Only then should a fresh,
source-pinned candidate be staged for supervised scanner and display testing.
Closed hardware trials stay closed. The separate saved-recording Pause/browser
timeout issue remains unresolved by this work.
