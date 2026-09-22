# Authenticated supplemental demand candidate

Status: **internal, default-off, offline qualification**. This adds the explicit
lease transport and opt-in client behavior to the
[bounded acquisition owner](scanner-display-supplemental-acquisition.md). It is
not a production setting, continuous polling permission, or hardware acceptance.

## Separate cached delivery from acquisition

Context and frame GETs remain passive. The additional daemon operation is
`display.supplemental.demand`; its exact parameters are `context` and
`renewal_id`. The service must be constructed with the exact existing
`DaemonSupplementalAcquisition`, its own feed, and the same daemon runtime.
Attaching it to a different runtime or feed is rejected. Construction does not
start or arm the owner. A caller cannot arm, rearm, extend the test window,
increase the read quota, change scanner targets, or lift quarantine through it.

The operation is advertised only when the acquisition dependency was explicitly
injected. The remote observe grant permits this observational lease but does
**not** grant controls, recording actions or profile administration. Demand is
not listed in `read_only_operations` or `control_operations`. An explicitly
enabled observe connection therefore reports `read_only: false` while its
`control_operations` remains empty. Clients must check the particular operation,
not interpret `read_only: false` as permission to control the scanner. Default
daemon capabilities remain unchanged.

## Context-bound acknowledgment

The service validates a fresh six-field owner/profile/context capture before
renewal. The cache repeats the checks atomically. First or expired demand may
advance `context_revision` once; normal renewals coalesce without changing it.
The response has exactly:

```json
{
  "protocol": "sdsctl.supplemental-demand",
  "version": 1,
  "context": {"...": "the six post-renewal context fields"},
  "renewal_id": "a caller-generated canonical UUID v4",
  "lease_seconds": 5
}
```

The context placeholder above is explanatory, not a valid request. Decoders
require the actual endpoint, stream, session, profile revision, profile
invalidation and context revision fields. Every field except context revision
must match the negotiated request; that revision may stay equal or advance by
exactly one. A distinct UUID correlates each renewal acknowledgment, preventing
an older response from authorizing the next frame. Clients bind the acknowledged
context before requesting a frame, avoiding an initial-renewal negotiation loop.

The UUID is a correlation token, **not an idempotency key** or a credential.
Requests are not automatically replayed. If dispatch or post-renewal capture
fails, acquisition may already have occurred. A fixed
`supplemental_demand_unconfirmed` result expresses that uncertainty; it does not
promise rollback or absence of scanner commands. A context mismatch detected
before mutation can instead return `supplemental_context_changed` and permit
fresh negotiation. Changes during renewal may conservatively require stopping.

The acknowledgment does not refresh a displayed sample. Independent PSI,
clock and Favorites sequence/age guards still enforce their own five-second
freshness limits. Nor does it promise five more seconds beyond the owner's
fixed deadline/quota, or any successful native read.

## HTTP authorization and bounds

The internal `supplemental_demand=True` factory option additionally requires
`supplemental_delivery=True` and either native HTTPS authentication or trusted
Home Assistant Ingress. Normal startup supplies neither option.

`POST /api/v1/display-supplemental/demand` uses an empty body and exactly one
of each header:

- `X-SDSCTL-Supplemental-Version: 1`
- `X-SDSCTL-Supplemental-Context`: the bounded negotiated JSON context
- `X-SDSCTL-Supplemental-Renewal`: canonical UUID v4

Queries, duplicate context/renewal headers, duplicate JSON context keys,
malformed versions and nonempty bodies are rejected before dispatch. Empty-body
validation has a finite wait; clients bound acknowledgments to 2 KiB and the
whole context/renewal/frame cycle to five seconds. The HTTP bridge checks the
daemon's context, frame and demand capabilities before issuing a mutation.
Responses use no-store and fixed sanitized error messages.

Native manual display sessions and enrolled device cookies may POST only this
additional observational route when explicitly enabled. Exact Origin and
Fetch Metadata checks still apply, as do authentication expiry, revocation,
cookie conflict checks and request leases. Other display POSTs stay forbidden.
Ingress continues to require the actual trusted Supervisor peer; forwarded
headers cannot grant access. No new cross-origin/CORS allowance is introduced.

## Explicit consumer selection

- TUI: `daemon_supplemental_source(client, demand=True)` selects the additional
  capability on its dedicated finite-timeout authenticated client. The default
  remains cached-only. The ordinary CLI does not select either supplemental mode.
- WebUI: internal `supplementalDemand: true` additionally requires negotiated
  `supplementalRoot`; a fixed-context consumer cannot request demand. The separate
  exact-boolean `supplemental_consumer=True` application-factory opt-in loads the
  helper before Mimic and chooses the context route under the current web root,
  preserving HA Ingress prefixes. It requires explicit delivery and selects
  renewal only when demand was also enabled. Authenticated native display/device
  sessions may load that helper only when selected. Missing candidate assets or
  invalid bootstrap selection show a fixed notice without starting an ordinary
  Mimic fallback or preventing the rest of the dashboard from initializing.
  Ordinary startup does not select or serve the supplemental helper; no
  URL parameter, local-storage value, CLI flag or normal configuration enables it.
- HA card: internal constructor `supplementalDemand: true` additionally requires
  `supplemental: true`. Neither is an accepted YAML/editor option. It uses the
  existing trusted Ingress context URL and fixed sibling demand/frame routes.

The two browser consumers use `crypto.getRandomValues`, not a secure-context-only
UUID helper, so the candidate also supports trusted HA HTTP/IP installations.
There is no fallback to weak randomness or anonymous acquisition.

Hidden, stopped, signed-out or unmounted consumers stop renewal. Closing one
client never globally cancels a sibling's coalesced lease; absent further
renewals the lease expires. An unknown/late/malformed renewal acknowledgment
stops that consumer instead of retrying. Browser cancellation during an in-flight
mutation also stops that instance conservatively. A TUI worker may validate a
late response after hide, but cannot render it; an uncertain result remains
terminal even across a visibility generation change. New authenticated context
negotiation is allowed after an explicit pre-mutation context rejection.

## Qualification and remaining activation

Tests cover actual localhost UDP ownership, Unix IPC, native and enrolled-cookie
HTTP admission, trusted Ingress, concurrent renewals, passive reads, expired
leases, post-mutation acknowledgment loss, two TUI consumers, visibility and
terminal failures. The actual WebUI controller and generated HA resource run
against synthetic DOM/HTTP/timers, including IP origins and sibling isolation.
This is not visual browser, physical Pi, scanner continuity or audible acceptance.

The source-pinned finite launcher, explicit consumer selection and independent
deadline/automatic restoration have now passed the narrow 2026-09-22
[clock/global Favorites hardware trial](scanner-display-supplemental-acceptance.md).
That result includes separate user confirmation of normal scanning. A subsequent
[finite playback-only trial](scanner-display-supplemental-acceptance.md#finite-playback-only-result--2026-09-22)
also passed with one browser stream, 45 supplemental read attempts, user-confirmed
normal scanning and audible transmissions, and independently audited restoration.
It does not qualify [recording/finalization](scanner-display-supplemental-recording.md),
continuous operation, Pi/TUI or HA-card behavior through the finite path. Those
next trials require **new** reviewed cases and fresh readiness. Closed hardware
cases remain closed. Public configuration and normal App activation
remain disabled; continuous operation and schema-safe production upgrades are
separate work. The [saved-player follow-up](web-dashboard.md#saved-player-browser-qualification)
also remains distinct: both silent and audible local fixtures passed manual
native Pause and explicit saved controls, with stopped/reset playback and fixture
cleanup independently confirmed. The automated native Pause/browser crash cause
remains unresolved. Those saved-file results do not qualify recording/finalization
through the finite handoff.
