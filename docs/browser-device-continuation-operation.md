# Continuation operation ownership and stop boundary

Status: **development design and unselected asynchronous operation owner, not an enabled
sign-in/sign-out route or administrator runbook**. Read the
[continuation boundary](browser-device-continuation.md) first. Existing deployed
profiles must not be edited, replayed or cleared to exercise this work.

## Selected operation and request ownership

One trusted worker must own review, confirmation, pending persistence, one native
issuance request, installation, probe and acceptance. Its selected identity,
epoch, build and origin come from the fixed installed native context, never a
page-supplied path or role. A page ticket remains local to that exact document;
it must not be forwarded as native authority. The existing synchronous fixture
callback remains confirmation-only and must not start installation covertly.

The separately exported `connectContinuationOperationWorker` now composes the
document-confirmation lane, initial installation and independent stop adapter.
It is **not selected by the active worker or page**. Native issuance and protected
page proof remain fictional boundary callbacks in its isolated tests, not newly
accepted native wire actions or browser-derived authority.

Only the confirmed document may begin installation, at most once. After exact
pending persistence the owner rechecks that document immediately before selecting
the issuance callback. It checks the document again after installation acceptance
before returning readiness. The original one-minute review budget spans the
asynchronous operation; installation has its separate 45-second budget. Neither
clock budget is renewed by confirmation, a late reply or a duplicate message.
The final pre-issuance document check runs inside the installer's checked I/O
boundary, separately from issuance. If that document read crosses the 45-second
installation deadline, the native issuance callback is never selected, even
while the one-minute confirmation budget has not yet expired.

The fixed native initial request has only comparison inputs: the selected
epoch, fresh random intent, reviewed native fingerprint/revision, and reviewed
server generation, inside the existing build-bound worker envelope. It carries
no credential, browser cookie, arbitrary URL/path, role, supplied proof, reusable
approval or browser readiness. The inner action is
`continuation-initial-session`, with exact `epoch`, `intent` and
`binding: {fingerprint, revision, generation}` fields. The fixed wrapper accepts
it only in the existing build-bound worker envelope for an independently selected
continuation installation; ordinary, unwrapped and retirement routes reject it.
Native reconstructs the owned current state and private inputs,
performs its own verification, and consumes one same-process issuance attempt.

The installed worker owns document-bound consent. Native does **not** treat the
message, a boolean or the private acknowledgement callback as evidence of a
physical click. That callback only compares the reconstructed review with this
one fixed request. The unchanged same-process owner independently verifies
private inputs and current server generation before issuance. Response fields
bind the transient session to the current build, identity, epoch and resulting
native state, never browser readiness. No ordinary browser page/worker selects
this request yet; real installed end-to-end acceptance remains outstanding.

Before dispatch the owner must observe the exact pending browser record and a
still-current confirmed document. Native keeps its independent ten-second
process deadline; the installation keeps its bounded wall/monotonic budget.
Neither restarts a consumed page confirmation or supplies a fresh budget after
uncertainty. A received token remains transient; no status/readback recovers it.

## Why a separate stop key

An outstanding Chrome storage write can complete after its caller times out.
Writing a pause into the same key as a pending/accepted installation therefore
does not make cancellation win: an older late accepted write could replace it.

The unselected `createContinuationStopFence` writes **only** the fixed local key
`sdsctlContinuationStop`, containing version 1, selected identity/epoch/build and
`stopped: true`. There is no token, ticket, clock, URL or caller payload. The
original `sdsctlDeviceRecovery` record is retained untouched. Chrome's
[StorageArea.set contract](https://developer.chrome.com/docs/extensions/reference/api/storage/StorageArea#method-set)
updates the supplied keys without replacing unrelated keys. Thus a late write
to the recovery key cannot by itself erase this separate marker. This is not a
multi-key transaction, compare-and-swap, defense against a hostile same-account
writer, or guarantee about sudden power-loss persistence.

The unselected asynchronous owner first invalidates its consent and installation lanes
**synchronously**, then attempts one marker save. The adapter restricts storage
access, refuses any pre-existing marker (including an identical one), writes
once, and requires exact readback within ten seconds on both clocks. Lost,
late, malformed, quota-failed or unavailable replies remain unconfirmed. There
is no retry, adoption, token retrieval, marker-clear or recovery-repair method.

Any present stop key is terminal for ordinary continuation, including an unknown
or malformed value. Existing paused readers and initial installers already
require the entire storage area to contain exactly their recovery key, so they
refuse a stop marker. A future accepted-startup owner must preserve this complete
storage check **before** passing a single record to the pure classifier; that
classifier cannot see other keys. Reopening the browser or resuming a device at
the server must not clear the marker. A reviewed later recovery transition is a
separate design, not provided here.

## Cancellation and acknowledgement table

| Cut | Immediate worker action | Possible retained state | Permitted claim |
| --- | --- | --- | --- |
| Before initial pending write | Fence the attempt; save stop marker | Original pause plus stop | Browser stop saved only after exact readback |
| Pending write outstanding | Fence now, do not queue cancellation behind a hung promise | A late pending record plus stop | No initial request after invalidation |
| Native issuance outstanding | Fence now; save marker independently | Native ACTIVE and possibly an unreturned server token | No native pause or server-revocation claim |
| Cookie write outstanding | Fence now; keep marker independent of cookie completion | Late cookie plus pending and stop | No claim that the cookie was removed or the session revoked |
| Accepted write outstanding | Fence now; stop key must survive the late recovery-key write | Accepted record plus stop | No readiness or accepted-restart adoption |
| Stop write/reply uncertain | Keep in-memory fence; never repeat the save | Marker may be absent or committed later | Browser stop remains unconfirmed |
| Worker/browser disappears | Next owner reads all persisted keys before work | Pending/stop/uncertain or accepted record | No initial replay; accepted state still requires fresh verification |

These outcomes are deliberately separate:

1. In-memory invalidation prevents later work in this worker.
2. A checked stop marker prevents ordinary successor work in this profile.
3. Owned native pause cancels native approvals; it does **not** revoke HTTP sessions.
4. Server compare-and-pause advances this device's generation; the separate drain
   acknowledgement covers older requests in the selected web owner.
5. Local cookie cleanup is neither server pause nor drain acknowledgement.

The existing HTTP sign-out requires the actual same-origin authenticated browser
request. If issuance has an unknown outcome and no usable cookie, the client
cannot fabricate that request or report server shutdown. It must retain the
uncertainty and request administrator review, not pause unrelated devices or
retry issuance to obtain a logout cookie. A future owner may use the existing
device-scoped path only with its independently verified authority and response.

Explicit owner stop, selected-document cancellation/navigation, exact-origin
sign-out and operation failure all invalidate the operation before attempting
the separate marker. A content-message cancellation receives no saved-stop
acknowledgement. Only the owner's awaited `stop()` can report checked browser
storage, and it always reports native pause and server revocation as unconfirmed.
An uncertain marker save remains terminal; a second stop never retries or adopts
an existing marker. Stopping after acceptance retains the original accepted
record and cookie alongside the marker. This is not session revocation.

## Qualification and remaining integration

The owner and its adapters are in the canonical graph but no ordinary page,
worker or native dispatch selects them. Tests compose the actual synchronous
event gate, document checks, installation and stop adapter using modeled Chrome,
native and probe calls. Every asynchronous boundary is tested on both sides of
its potential side effect for cancellation and acknowledgement failure, including
late pending/issuance/cookie/accepted completions and the final document check.
Additional cases cover sender isolation, exact document replacement, duplicate
confirmation, stop uncertainty and independent review/installation deadlines.
The separate adapter tests retain clock-fault, pre-existing-marker and wrong-readback coverage.
The model now uses Chrome's key-update semantics rather than whole-area
replacement. Simulated browser/native callbacks are not installed acceptance.

Before enabling the route, connect the unselected owner to the fixed native request,
generation-aware server sign-out, accepted-startup verification and marker
checks, then qualify actual installed native execution and verified TLS for DNS,
IPv4 and IPv6. Real Chromium persistence/restart and physical display tests remain
separate from deterministic callback tests. Do not infer sudden-power-loss or
complete unattended-recovery guarantees from a resolved storage promise.
