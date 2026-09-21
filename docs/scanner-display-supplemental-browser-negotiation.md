# Supplemental browser negotiation candidate

Status: **offline-tested internal opt-in; not activated or deployed**.
This connects the [WebUI controller](scanner-display-supplemental-web-controller.md)
to the [authenticated transport](scanner-display-supplemental-transport.md).
Ordinary startup, dashboard shell, schema-1 response, TUI and HA card activation,
and scanner acquisition policy are unchanged.

## Explicit integration seam

The existing `sdsctlMimic.create` factory now has two mutually exclusive internal
options, both null by default:

- `supplementalContext`: existing fixed-context mode, with no automatic rebinding.
- `supplementalRoot`: absolute same-origin web-root URL ending in `/`. It retains
  any HA Ingress path prefix. Only the fixed `api/v1/display-supplemental/context`
  and `api/v1/display-supplemental/frame` paths are resolved beneath this root.

The separate `mimic-supplemental.js` dependency remains required and is **not
served or loaded by the ordinary dashboard**. Neither option is supplied by the
normal shell. Supplying a script alone does not enable the feature. The generated
HA card contains the shared bounded reader, not this coordinator or its guard.

A future opted-in shell must pass its existing `webRootUrl.href` and
`request: dashboardFetch`, preserving the outer session lifecycle. Do not replace
that wrapper with a parallel raw fetch. Native 401 responses already trigger
the established sign-in path and stop all session activity. An Ingress 403 is
an admission failure, not an invitation to open a native password page.

Roots accept HTTP or HTTPS only when they exactly match the current origin
(including port); both DNS names and IP-address origins work. Relative roots,
cross-origin roots, credentials, query strings, fragments and missing trailing
slashes are refused. This does not relax the server's existing TLS/authentication
policy. There are no profile-controlled URLs, credential query parameters,
redirect following, storage writes, or fallback to the ordinary display route.

## Negotiation and request lifecycle

The selected, active, available and visible Mimic pane negotiates before its
first frame request. HTTP admission and the server's capability check remain
mandatory. The browser validates the exact protocol, version and six-field
binding before constructing its guard. Negotiation responses are limited to
2,048 bytes; frame bundles remain limited to 256 KiB. UTF-8, JSON and canonical
bundle validation remain required.

Both requests send protocol version 1. Only frame requests include the negotiated
context header. Requests use same-origin credentials, no-store and redirect-error.
One five-second request budget covers negotiation plus frame delivery; each
source's independent freshness lease starts immediately before the frame request,
not when its body arrives. The scanner RTC is never extrapolated between samples.

| Result or event | Coordinator behavior |
| --- | --- |
| Valid negotiation and frame | Publish a validated same-capture bundle through the existing expiry guard. |
| Frame 409 | Clear values, suspend the guard, then negotiate again after the retry delay. A frame body cannot authorize rebinding. |
| Same context after negotiation | Reuse the existing guard and its retired-sample history. Cleared old samples do not reappear. |
| Verified different context | Close the old guard, replace it inside the same controller, and retain layout/LED choices without adding DOM controls or listeners. |
| 503 or malformed response | Clear values and retry conservatively. No automatic schema-1 fallback or new read demand. |
| 401 or 403 | Permanently stop this controller; use the existing authorized dashboard entry to recover. No credential retry. |
| Hide, inactive pane or unavailable capability | Cancel the current generation and timers, clear values, retain retirement history. |
| Terminal stop | Permanently close the guard and cancel all owned timers. Late headers/bodies/status calls cannot restart it. |

Responses must still belong to the active generation after every asynchronous
boundary. This includes transports or body readers that ignore an abort. Cleanup
from an old request cannot clear the newer request's deadline timer. An aborted
promise that never settles cannot publish data; independent expiry still clears
the display, and stopping/hiding cancels its owned timers. The coordinator does
not create overlapping retries for such a permanently nonconforming transport.

Endpoint identity remains pinned for the page. Within one stream/session, context
and profile-invalidation revisions cannot go backward. Their high-water marks
retire earlier epochs without storing each ordinary cache invalidation, so those
routine revisions do not exhaust history. Retired stream/session pairs cannot
be revisited even with a higher revision. Within the same epoch, retired profile
hashes cannot be revisited either; an advancing epoch clears that profile set.
The page bounds retired connections and same-epoch profile hashes to 64 each;
an overflowing replacement stops with a fixed reopen message rather than
growing history without bound.
Reopening must go through the normal authenticated entry, never an automatic
credential submission or deletion of managed-device state.

## Offline evidence and remaining gates

The tests execute actual production JavaScript against synthetic DOM, monotonic
timers and HTTP responses. Cases include IP origins, HA Ingress prefixes, stale
contexts, profile repair, retired identities, independent expiry, response/body
delays, ignored aborts, out-of-order failures, sign-out and bounded history.
Another test supplies real authenticated HTTP responses from a real local Unix
daemon API using a fake scanner/cache owner; delivery produces no extra reads.
This is not visual browser or physical scanner acceptance.

The [TUI reader candidate](scanner-display-supplemental-tui.md) now covers the
equivalent offline TUI lifecycle without activating ordinary startup.
The [HA card candidate](scanner-display-supplemental-ha.md) likewise remains
default-off. Still required: explicit shell/dependency activation and the bounded
acquisition enable/demand policy.
Only after those gates pass should a source-pinned candidate be staged for fresh
operator-assisted visual and continuity testing. Closed hardware trials stay
closed. This work does not resolve the separate saved-recording Pause issue.
