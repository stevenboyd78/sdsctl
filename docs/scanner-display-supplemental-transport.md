# Authenticated supplemental delivery candidate

Status: **offline-tested opt-in transport, not activated or deployed**.
This connects the [same-capture bundle](scanner-display-supplemental-web-controller.md)
to the daemon API and authenticated HTTP admission. Ordinary schema-1 display
responses, scanner acquisition, installed Apps and Pi displays are unchanged.

## Two explicit opt-ins

The daemon must receive an explicitly constructed `SupplementalDeliveryService`
through `DaemonReadOnlyApi(..., supplemental_display=service)`. The service wraps
an existing `DaemonDisplayFrames` owner with an already configured supplemental
cache. Without it, neither new operation is advertised and requests receive
`unsupported_operation`:

- `display.supplemental.context`: no parameters; return the current binding.
- `display.supplemental.frame`: exactly one `context` parameter; return a bundle
  from one current owner capture only if that binding still matches.

The WebUI application must independently set
`create_web_dashboard_app(..., supplemental_delivery=True)`. This exact-boolean
option defaults to false and requires native authentication or trusted Home
Assistant Ingress. Neither ordinary startup nor CLI/configuration exposes or
sets these options. The normal dashboard does not load the auxiliary JavaScript
dependency or opt its controller into this transport.

Delivery never starts a worker, renews demand, requests a refresh, switches
scanner modes, imports a profile, or performs scanner I/O. A dormant/expired
cache remains dormant/expired. A transport connection is not acquisition consent.
The service and its owner must share a monotonic clock domain; defaults use
`time.monotonic`, while offline fixtures inject their common fake clock.

## Authenticated negotiation

Both opt-in GET routes require exactly one
`X-SDSCTL-Supplemental-Version: 1` header and reject query parameters:

| Route | Additional request requirements | Successful response |
| --- | --- | --- |
| `/api/v1/display-supplemental/context` | No context header | `sdsctl.supplemental-context`, integer version 1, validated `context` |
| `/api/v1/display-supplemental/frame` | Exactly one `X-SDSCTL-Supplemental-Context` header containing the negotiated JSON object | `sdsctl.mimic-supplemental` version 1 same-capture bundle |

The context contains canonical endpoint/stream/session UUIDs, the accepted
profile hash, and profile-invalidation/cache-context revisions. It is a binding,
**not a credential**. Every request still needs a valid native operator/display
session, managed-device display session, or trusted Supervisor Ingress admission.
Forwarded headers do not replace the trusted Ingress peer check. Expiration,
sign-out and device revocation retain their existing authorization semantics.

Display-only sessions can perform these GETs, but not POSTs, profile
administration, scanner control, audio or recording operations. Authenticated
daemon observe peers may use the new operations only when explicitly provided
by the daemon. The scanner-control-only interface does not admit them.

The context header is limited to 1,024 characters. Duplicate headers, duplicate
JSON keys (including escaped spellings), extra fields and invalid types are
rejected before a daemon request. HTTP negotiation checks daemon capabilities;
it does not probe an unadvertised operation or downgrade to unguarded schema 1.

## Context changes and failures

| Response | Meaning / consumer obligation |
| --- | --- |
| 200 | Validate the complete response, then apply the consumer's existing freshness guard. |
| 409 | A current owner capture has a different binding. Clear the old view; explicitly negotiate again before replacing the guard. |
| 422 | Invalid protocol/header/context request. Do not treat it as a valid frame. |
| 503 | Missing capability, unavailable owner/profile/PSI, or invalid daemon response. Clear unavailable data; never replay the old bundle. |
| Authentication rejection | Follow the existing session/sign-out lifecycle; the context cannot authenticate the request. |

A reconnect cannot reuse a previous session. A failed profile reload retains
an invalidation barrier even if the same profile bytes are restored; pre-barrier
PSI is cleared and fresh PSI is required before a new context can be negotiated.
If there is no current capture, the response is 503, not a fabricated 409 context.
A frame response never instructs the consumer to adopt its context automatically.

Successful responses and route errors are `Cache-Control: no-store` and
`X-Content-Type-Options: nosniff`. Error messages are fixed and do not echo
credentials, raw input, source paths or daemon exception details. Bundle JSON is
limited to 256 KiB (measured with ASCII escaping, conservatively bounding UTF-8),
then validated against canonical geometry and coherent profile/PSI identity.
Existing IPC byte limits remain in force. The browser's bounded-body decoder and
request-start-relative expiry checks remain required; server validation does not
replace the consumer guard or its independent local timers.

## Evidence and remaining work

Offline tests exercise a real local Unix daemon API socket through authenticated
HTTP requests, as well as native-session expiry, managed-device revocation,
Ingress admission, context changes, malformed input, canonical bundle mutation,
sanitized errors and absence of scanner-read/demand side effects. All scanner
data and credentials in these tests are fixtures, not live hardware.

Still required: an actual browser negotiation/replacement coordinator, equivalent
TUI/HA lifecycle integration, explicit bounded acquisition enable policy, and
source-pinned visual/continuity acceptance. The transport alone does not enable
the feature or resolve the separate saved-recording playback/Pause investigation.
Closed hardware trials remain closed; no new trial is armed by this work.
