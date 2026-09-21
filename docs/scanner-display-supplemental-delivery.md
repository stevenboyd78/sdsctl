# Supplemental delivery and expiry candidate

Status: **offline-tested contract; not deployed or enabled by ordinary startup**.
This extends the [point-in-time presentation groundwork](scanner-display-supplemental-presentation.md).
It does not change the existing public schema-1 display response or start reads.
The [opt-in WebUI controller candidate](scanner-display-supplemental-web-controller.md)
now exercises these guards in the real polling/rendering controller offline.
The [authenticated transport candidate](scanner-display-supplemental-transport.md)
adds separately opted-in daemon/HTTP negotiation; default routes remain absent.

## Source identity and coherent capture

Successful cache commits now allocate independent positive `sample_sequence`
numbers for DTM and each quick-key bank. Identical values from two actual
successful reads still have different sequence numbers. Repeated snapshots,
new PSI, busy lanes, rejected replies and discarded in-flight results do not
allocate numbers. Counters do not reset on invalidation/reconnect within the
cache object's lifetime and never wrap beyond the JavaScript safe-integer bound.
Counter exhaustion stops acquisition conservatively.

The cache's invalidation epoch is retained as `context_revision` in its
immutable snapshot and the coherent owner frame set. It changes across
demand/context invalidation, not on every current PSI. Clock/Favorites projection
retains successful sample IDs without exposing packet data or dispatch times.

`project_supplemental_delivery(capture, now=...)` serializes a newly acquired
coherent owner capture. It uses the presentation layer's canonical geometry,
profile and freshness validation before creating this separate envelope:

| Member | Meaning |
| --- | --- |
| `protocol`, `version` | `sdsctl.supplemental`, integer `1` |
| `context` | Endpoint/stream/session UUIDs, accepted profile hash, profile invalidation and cache context revisions |
| `psi` | This capture's PSI sequence and relative age in seconds |
| `clock` | Status, independent sample sequence, relative age, naive scanner-local calendar text |
| `favorites` | Status, independent sample sequence, relative age, 100 global states |

Current clock text is exactly `YYYY-MM-DDTHH:MM:SS`, with a valid calendar and
no timezone suffix or fractional seconds. It is a scanner-local calendar
reading, not a UTC instant. The diagnostic Favorites string has exactly 100
ASCII characters: `0` absent, `1` disabled, `2` enabled, indexed 00–99.
This is not an LCD decade, scoped SQK/DQK, or a compact substitute for validation.

Noncurrent sources have null value, sample sequence and age. A stale PSI capture
cannot carry current auxiliary data. Unknown keys, wrong types, malformed
calendar dates, non-finite ages and out-of-bounds sequences are rejected with a
fixed error, without echoing private input. Source paths, XML, raw packets,
opaque DST tokens and server monotonic timestamps are absent.

## Consumer lifecycle

The transport-free Python `SupplementalConsumer` and JavaScript
`sdsctlSupplemental.create()` implement the same state machine. The JavaScript
dependency is not served, loaded by the normal dashboard, or bundled into the HA card.
The WebUI controller has an internal explicit-context opt-in seam, unused by the
normal dashboard shell. Supplying the dependency alone does not enable it.
Neither guard reads a clock itself, schedules timers, makes requests or owns a
scanner. The caller supplies monotonic seconds from one event-loop clock.
For example, browser callers use `performance.now() / 1000`, not milliseconds;
Python callers use `time.monotonic()`. Do not mix clock domains or units.

- Bind explicitly to a verified context from the owner. Do not automatically
  trust an incoming response as authority to replace endpoint, session, profile
  or context revision.
- `begin` returns a request ticket. Only the latest ticket from this exact
  guard can deliver, once. Replaced, suspended, closed or foreign callbacks are
  ignored without clearing a newer accepted value.
- A deadline is `request start + 5 seconds - reported source age`, not five
  seconds after receipt. Including the whole request interval is intentionally
  conservative; the client does not compare server/client monotonic clocks.
- Repeating a sample may shorten its deadline but never extend it. A different
  value for the same sample number invalidates that source. A lower sample
  number is refused. A strictly newer successful sample can restore it.
- PSI has its own sequence and deadline. New auxiliary reads do not extend an
  old PSI lease; new PSI does not extend an old auxiliary lease.
- When a source expires or is cleared, retain its last observed sequence number
  so the same sample cannot reappear. A valid but expired-PSI response retires
  its included sample IDs even if they were not previously displayed.
- `suspend` clears values and in-flight callbacks while retaining sequence
  history. A subsequent accepted PSI must advance, and cleared auxiliary
  samples must advance independently. Keep this same guard across
  hide/show and layout changes; recreating it would discard those protections.
- `close` is permanent. Invalid/backward consumer time closes the guard.
  An explicit new owner context requires a new guard; old request tickets are
  not transferable to it.

The guard is single-event-loop state, not a synchronization primitive for
concurrent threads. A successful envelope acceptance does not mean both sources
are current: render only the current values returned by `snapshot`.
The Python snapshot returns immutable renderer-neutral values; JavaScript returns
detached plain values. Neither advances the actual scanner RTC between samples.

## Offline evidence

Tests cover real fake-transport/cache/owner capture through delivery and the
Python guard, plus the actual JavaScript candidate in Node's synthetic context.
Named lifecycle traces and 1,200 deterministic stress exchanges compare both
implementations, including duplicate/later replies, suspension, independent
expiry, malformed records and context mismatch. Calendar tests reject JavaScript
date-rollover behavior and timezone interpretation. No browser UI or hardware
acceptance is implied.

## Still required before deployment

1. Connect clients to the explicitly negotiated, authenticated opt-in transport
   without changing schema 1. The server candidate now bounds bundle bytes,
   sends no-store responses, and validates frame and auxiliary context/PSI
   sequence atomically from the **same** owner capture. Clients must retain
   their own body bounds and freshness checks. Never stitch to a later frame read.
2. Maintain consumer guards across layout/visibility changes, call snapshot
   from each client's local expiry timer even when no response arrives, and
   clear UI state on unavailable capability/context. Context replacement must
   follow verified owner state, not opportunistic rebinding.
3. Complete WebUI, TUI and generated HA integration together, with delayed-body,
   disconnect, profile replacement, multi-client and cancellation tests through
   their real controllers. The offline WebUI candidate covers these rejection,
   expiry and cancellation paths but does not yet negotiate a new verified
   context or call the opt-in authenticated server endpoints. TUI and HA remain pending.
4. Define the explicit acquisition enable policy, preserving one owner, bounded
   shared GETs, demand expiry, contention/media behavior and all context gates.
   These additions do not widen the qualified hardware-read window.
5. Stage a source-pinned candidate only after those gates pass, then request
   fresh operator readiness for visual and continuity acceptance. Closed
   hardware trials remain closed and must not be rearmed.
