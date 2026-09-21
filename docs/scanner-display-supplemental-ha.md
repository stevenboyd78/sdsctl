# Supplemental Home Assistant card candidate

Status: **offline-tested internal opt-in; not activated or deployed**.
This connects the existing Mimic-SDS card to
[authenticated supplemental delivery](scanner-display-supplemental-transport.md).
It does not enable scanner reads, change the installed HA resource or add a
supported YAML option yet.

## Explicit integration and shared authentication

The card's constructor has an internal `supplemental` boolean, false by default.
Normal HA custom-element construction, `getStubConfig` and the configuration
editor do not set it. The ordinary YAML validator still rejects a `supplemental`
option. Capability advertisement alone cannot activate this candidate.

The generated resource includes a private copy of the canonical, transport-free
supplemental guard. It does not publish or overwrite `window.sdsctlSupplemental`.
Multiple resource versions cannot replace another controller's guard dependency.
The normal WebUI's separate supplemental asset remains unserved/unloaded.

Both first-party cards still use the **same page-local Ingress cookie/lease
owner**. There is no extra cookie writer, credential store, direct scanner client
or native sign-in page. Hiding, removing or terminating a Mimic instance releases
only its lease; another Mimic or Waterfall instance retains its own lease and
the shared refresh timer while needed.

Ingress discovery now additionally allowlists exactly
`api/v1/display-supplemental/context`. The frame URL is its fixed `frame` sibling
beneath the same verified Ingress prefix. Arbitrary routes, cross-origin paths,
query credentials, redirects, uninspectable candidates and ambiguous running Apps
remain refused. DNS and IP origins both work; this does not weaken authentication
or TLS requirements on the server.

An older page-local Ingress owner may not recognize the new route. The candidate
fails closed and does not replace that owner or fall back to ordinary frames.
Future activation will require a full HA page reload after resource updates so
all participating cards use the matching generated source.

## Lifecycle and freshness

Visible, mounted cards with valid HA contexts acquire a shared lease, discover
one App, negotiate a strict context and then request a bounded same-capture frame.
Negotiation has a 2 KiB body limit; frames have a 256 KiB limit. A five-second
request acceptance budget spans negotiation and frame delivery. Individual
source freshness starts immediately before the frame request, not at receipt.

Context conflicts and transient failures clear values and negotiate again after
the retry delay. A frame body cannot authorize rebinding. Same-context recovery
retains source retirement; a verified replacement closes the old guard while
preserving the card's layout, LED, density and details configuration.

Endpoint pinning survives Ingress-key changes. Within a stream/session, context
and profile-invalidation revisions cannot decrease. Retired connections and
same-epoch profile hashes cannot return; each history is capped at 64. Advancing
epochs compact profile history rather than consuming one slot per routine cache
invalidation. Retired-context adoption or exhausted history terminates the card's
supplemental reader until a newly authorized card instance is created.

Clock, Favorites and PSI use independent expiry. A pending network request does
not keep old values visible. Repeated sample IDs do not renew source leases.
Only configured small Day/Time slots receive scanner-local RTC text; base PSI
is not mutated, extrapolated or used as a fallback clock. Global Favorites
00–99 appear in details, explicitly distinguished from the LCD F0/S0/D0 banks.

Every asynchronous boundary checks the current mount/generation and demand.
Hidden documents/intersections, changed HA contexts/panels and unmount clear
values and cancel owned polling, request and source-expiry timers. An old
request's cleanup cannot cancel a newer request's deadline. If a transport ignores
abort and never settles, expiry still clears the display; the card does not
create overlapping automatic retries of that unsettled request.

Supplemental HTTP 401/403 permanently ends that instance's reader and releases
its lease. It cannot be restarted by a late callback, visibility change or
remount of the same instance. Recovery means reloading through an authorized
Home Assistant session, not resetting saved-device state. The ordinary card's
existing authentication-retry behavior is unchanged.

## Offline evidence and remaining gates

Tests execute the actual generated module with synthetic HA contexts, DOM,
monotonic timers and HTTP responses. Cases cover DNS/IP origins, source expiry,
blocked headers/body, sign-out/admission loss, late generations, request budgets,
context/profile replay, 150 ordinary epochs, bounded history, independent instances,
shared Mimic/Waterfall leases, legacy owners and resource-private guards.
An additional case renders a bundle delivered through authenticated HTTP and a
real local Unix daemon API with a fake scanner owner; delivery adds no reads.
These are not physical-device or visual browser acceptance tests.

Use `scripts/build_mimic_lovelace.py` to regenerate the shared resource, Waterfall
owner copy, manifests and aggregate imports together. Do not edit generated
assets manually or install this private build as a production release.

Remaining work: explicit client/shell/resource activation and bounded acquisition/
demand policy, followed by a fresh source-pinned supervised display and scanner
continuity test. Closed hardware trials remain closed. This change does not
resolve the separate saved-recording Pause/browser timeout issue.
