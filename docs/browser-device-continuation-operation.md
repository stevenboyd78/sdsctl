# Continuation operation ownership and stop boundary

Status: **private development candidate with ordinary continuation routing;
not released, physically accepted, or an administrator deployment runbook**.
Read the [continuation boundary](browser-device-continuation.md) first. Existing
profiles must not be edited, replayed or cleared to exercise this candidate.

## Foreground launcher validation

The private launcher candidate extends the existing experimental
`sdsctl browser-device-start` command without an ignore-guard flag. It does not
create a continuation, activate an intent, clear STOP, initialize setup again,
or decide that a server session is authenticated.

With no continuation artifacts, the original strict registration path remains.
Any intent/activation artifact or sidecar instead selects full continuation
validation, with no fallback to ordinary registration after a failure. Completed
intent alone is insufficient. The launcher reconstructs the original retained
chain and canonical installed files, verifies the immutable activation and
current native epoch, and permits only PAUSED or ACTIVE state without prepared
or claimed approvals. An ACTIVE grant must match the original private inputs.

`--check` performs this offline inspection without probing the browser, opening
network connections or changing state. Foreground launch independently validates
again while acquiring its own existing launch lock; it does not reuse the check
result as authority. Launch ownership and shared private-input locks remain held
until its browser child exits. The read-only SQLite transaction closes before
launch so native workers can commit approval and pause operations normally.
Missing locks, existing Chromium singletons and uncertain state are retained,
never deleted or adopted. `--setup` is refused for continuation profiles.

The fixed `startup.html` remains the only continuation launch destination.
Browser STOP, document consent, online verification and session acceptance are
still enforced by the installed worker. Opening the startup page after sign-out
can display its administrator-review notice; it does not resume automatic sign-in.
Legacy registration and native routes retain their strict continuation refusal.

Local tests cover actual installed CLI invocation with complete retained history
and a synthetic recovery browser. Real Chromium and physical acceptance of this
launcher change remain separate gates; earlier private-v9 physical results must
not be represented as acceptance of this changed native build.

## Ordinary startup selection

The MV3 entrypoint selects continuation only from the fixed installed native
`worker-context`. No page, query parameter, stored field or event payload selects
the role, server, profile or native host. Actual Chrome receivers are registered
synchronously before the first native await. Subsequent native requests carry
the executing graph's build identity in exactly one `worker-request` envelope.

`createContinuationStartupInspection` runs before any lifecycle is constructed.
It reads the whole storage area and compares two observations with two fresh
fixed-native context reads. Exactly one own enumerable recovery data property is
allowed. A STOP key with **any** value, extra key, accessor, unknown container,
pending record, changed identity/epoch/build or mismatched native binding is
terminal. A rejected first storage read does not call native again, restrict
storage access, construct authentication lanes, pause, issue, probe, remove
cookies or submit HTTP. Saved state is retained.

The exact version-1 clean-pause record produced by completed recovery is also a
non-ready review state, but only alongside matching fixed native paused context.
Startup does not convert that record, invent an epoch or persist a version-3
pause. After explicit document-bound consent, the existing installer transitions
it directly to `initial_pending`. Incomplete legacy records, extra keys, STOP,
active-native/legacy mismatches and accessor-backed fields remain terminal.

Clean pause additionally requires absent cookies and empty alarms on both sides
of inspection. Accepted state requires the separate fresh verifier described
below. The classifier never returns readiness. Inspection is read-only, not an
atomic permission, a readiness lease or acknowledgement of an existing STOP.

Both clocks must remain finite, safe and nondecreasing. Inspection has a
12-second budget but cannot renew the original 12-second MV3 gate, which starts
before the initial native request. A failed gate fences late inspection replies.
There is no await between lifecycle construction and replay of queued events;
a queued sign-out fences acceptance before its next asynchronous step.

The paused route returns freshly inspected non-ready status until the owned
resume document completes review and confirmation. The document lane separately
rechecks state and authority before issuance. A later STOP race cannot use the
earlier inspection to bypass those checks.

The active route binds one fresh verification to its requesting startup
tab/document, using actual context and tab readbacks. Navigation before delivery
fences the operation. Only the immediate verifier result can return readiness
to that request. Repeated polls return a fresh-verification-required message;
they do not reuse an earlier result, issue a session or select another verifier.
This candidate does not add renewal or profile repair.

## Document-bound initial consent

One canonical lifecycle owns review, confirmation, pending persistence, one
native issuance request, cookie installation, protected-document proof and
acceptance. Its fixed identity, epoch, build and origin are copied and frozen.
A page ticket is local to the exact current document and is never forwarded
as native authority.

The generated resume page receives its fixed origin from the installed bundle.
It requires trusted review and submit events plus an explicitly checked consent
box. Legacy UUID-shaped tickets and `reviewed`/`resumed` responses remain distinct
from continuation's 64-hex document ticket,
`continuation_confirmation_reviewed`, and exact `accepted` readiness response.
A malformed or crossed contract cannot enable confirmation or navigation.

The page consumes one opportunity. Its original one-minute budget starts at
review, not the reply or confirmation. Page hiding, changed location, rollback,
timeout and lost replies keep controls disabled and show fixed uncertainty.
No reply body, exception, token or arbitrary server-selected URL is rendered.
A successfully returned acceptance permits handoff to the fixed display URL
without treating that navigation as a new cancellation. Pending acceptance
remains navigation/cancellation-fenced; a lost reply is never replay permission.

The worker obtains identity from Chrome MessageSender, not a message field.
It checks the current extension document, tab and context before and after
review. Confirmation consumes its ticket before asynchronous work. After exact
pending persistence it rechecks the same document immediately before issuance,
inside the installer's checked I/O boundary, and checks it again before returning
acceptance. Native does not treat the request or a boolean as physical-click
proof: the fixed native owner independently reconstructs authority and state.

`connectContinuationOperationLanes` owns memory-only cancellation and its
installation. It writes no STOP marker. The older
`connectContinuationOperationWorker` is retained as an isolated qualification
wrapper with its own StopFence; the ordinary route never composes that wrapper
with another terminal owner. The confirmation-only page/worker fixture likewise
does not covertly enable installation.

## Fixed native requests and transient session ownership

The initial inner action is `continuation-initial-session`, with exact epoch,
fresh random intent and reviewed `{fingerprint, revision, generation}` binding.
The installed wrapper accepts it only for an independently selected continuation
installation inside the build-bound envelope. Non-continuation, unwrapped and
retirement routes reject it. No browser message selects a credential, path,
origin, native role, reusable approval or replacement token.

`createContinuationNativePorts` consumes one issuance attempt before validating
or awaiting its comparison request. It validates the entire success envelope
before projecting the installer's binding/session: version, ok, build, identity,
epoch, ACTIVE mode, revision +2, unchanged generation, new fingerprint and bounded
session shape/lifetime. Extra fields cannot be stripped away to hide a mismatch.

Fresh current reads must match the original paused selection before issuance,
or the exact fully acknowledged ACTIVE receipt afterward. The read-only
`pauseContext()` starts with the original selection and advances only after the
complete issue reply, deadline/lifetime checks and timer cleanup pass. It has no
token or intent. Unknown issuance and later observations cannot replace it.

A late native result may have committed ACTIVE state or issued an unreturned
token. Invalidation fences it immediately: it cannot revive the installer,
write a cookie, reconstruct a comparison or authorize another issuance.
All diagnostics are fixed and sanitized.

## Fresh accepted-startup verification

`createContinuationAcceptedStartup` is a one-use owner selected by the canonical
lifecycle. Construction validates and copies its fixed ACTIVE context without
I/O. Running it tracks the actual trusted-storage access-level write and reads
the whole local storage area before online verification. Exactly one accepted
recovery record is required; pending, paused, STOP and unknown state is retained
and refused, never migrated.

The stored binding and cookie fingerprint are only prerequisites. The owner
reads and validates the actual host-only, secure, HttpOnly, strict-same-site
device cookie, its store/path and remaining lifetime. It compares native context
and performs one `continuation-verify-active` request. That native action accepts
no saved record, generation, credential, URL, cookie or proof: its owner reconstructs
the ACTIVE generation, verifies the server, and rechecks native state/build.
SQLite scopes stay closed across network I/O.

The native result contains exact binding fields, not a token, session, browser
readiness or reusable proof. The browser then owns an isolated top-frame
`/device-display` probe and requires its actual tab/document/ticket identity.
Storage, cookie, native binding, alarm absence and remaining lifetimes are checked
again through final cleanup and return. A cookie hash or native success alone
is insufficient. Invalidation aborts the owned probe and fences late replies.

The accepted owner never issues a session, rewrites recovery state, repairs a
cookie, creates a renewal alarm, or signs out by itself. It exposes a retained
in-memory cookie comparison only after all verification and cleanup succeeds.
A stored comparison is never adopted as trusted logout ownership.

## Independent Stop and write draining

One `createContinuationLifecycle.stop()` promise is selected **before** callbacks
can reenter it. Concurrent stops, manual sign-out and failure paths join that same
attempt. The lifecycle invalidates memory-only consent/installation/native work
and its probe synchronously before browser/native Stop I/O. A broken or asynchronous
invalidation callback remains unconfirmed but does not suppress the independent
marker/pause attempts.

The Stop owner uses the exact lane's retained cookie comparison and write-drain
observations plus the original native selection or acknowledged issue receipt.
It never calls the legacy wrapper's durable invalidation or adopts a replacement
comparison after unknown issuance.

`createContinuationStopFence` writes only `sdsctlContinuationStop`: version 1,
selected identity/epoch/build and `stopped: true`. There is no token, ticket,
clock, URL or page payload. It refuses every pre-existing marker, including an
identical one, and requires exact readback of its own single write. It never
retries, clears or adopts another marker.

The separate key matters because a late `storage.local.set` of the recovery
record must not overwrite Stop. This is key-update behavior, not a multi-key
transaction, compare-and-swap, hostile-same-account defense or sudden-power-loss
persistence guarantee. The original pending/accepted recovery evidence remains.

The lane's `writeDrain()` follows underlying access-level, storage-set and
cookie-set promises, not only timeout races. Once fenced, it reports drained
only after every started write settles successfully. A queued access-level write
does not start after invalidation. Missing/rejected acknowledgements stay
unconfirmed. Local pending writes block logout/cookie removal without blocking
independent STOP persistence or native pause.

`createContinuationNativePause` accepts no page payload. It checks one fixed
context, sends one epoch/revision/fingerprint comparison, validates the entire
pause acknowledgement and rechecks the resulting paused context. Native pause
cancels approvals and removes the active native grant; it does **not** revoke
HTTP sessions. A later paused status does not acknowledge an earlier lost reply.

## Owned same-origin logout and presentation

Browser logout requires the trusted lane's retained cookie comparison, matching
actual cookie readbacks/lifetime, and successful local write draining. No owned
cookie after unknown issuance means no fabricated logout, unrelated-device pause,
or repeated issuance to obtain a logout token.

`createContinuationLogout` owns one inactive temporary `/device-display` tab.
Its isolated `connectContinuationLogoutContent` receiver is inert at registration.
Selection and results bind Chrome MessageSender to the exact extension, origin,
active top frame, non-incognito owned tab and document. Messages never substitute
body-supplied identity or page-provided proof for those checks.

One document ticket selects one same-origin POST through `submitDeviceLogout`.
The browser supplies the real Origin/Fetch Metadata and HttpOnly cookie. The
parser checks exact status, URL, redirect policy, content type, bounded body and
strict duplicate-free acknowledgement. HTTP 200 confirms drained; HTTP 202
confirms pause with draining still pending. A lost response is unconfirmed, not
permission to submit again. The final confirm checks the same current document.

After an acknowledged server result the Stop owner rechecks write drain. It
observes cookie absence or removes only the still-matching device cookie in store
`0`, requiring removal acknowledgement and absence afterward. A replaced or
unexpected cookie is retained. Tab cleanup is best-effort and is not proof of
server revocation or cancellation of an already-sent POST.

For the exact managed dashboard sign-out form, a trusted window-capture handler
stops dashboard streams, polling and expiry navigation before the extension's
document-capture handler starts Stop. This is presentation-only intent: it neither
submits another POST nor confirms browser/native/server cleanup. The menu and
document remain available for the actual complete, pending or unconfirmed result.
Late session/status reads cannot re-arm timers, reload the result away or label
stale data as newly live. Synthetic submits, unrelated forms and manual dashboard
sessions do not select this presentation path. Normal unexpected session expiry
still returns a managed display to its fixed guarded entry.

The worker-owned hidden logout document does not have a physical form submit.
After its isolated receiver's selection is acknowledged by the worker, it emits
the fixed, payload-free `sdsctl-device-signout-intent` event on its own window,
before selecting POST. The managed dashboard treats this as a UI-only request
to stop background reads and expiry navigation, preserving the document while
the real server drain response is pending. The event is deliberately untrusted:
it cannot select native/HTTP work, prove consent, clear a cookie, change saved
state, supply a result or authorize another request. Its return value is ignored.
The existing exact document, sender, one-use and deadline checks still govern
selection/submission/confirmation; loss or replacement remains unconfirmed.
Manual non-device dashboards do not register this presentation listener.

The ordinary isolated manual form joins the same terminal promise. Its response
channel binds to the first requesting tab/document/URL. Another document cannot
adopt that response. The form receives no legacy `submit: true` grant and sends
no second POST or `logout-finish`. Its pagehide/location and reply-timeout fences
prevent late results from reviving the UI. Messages project only fixed facts:

| Result | Required acknowledgements | User-facing meaning |
| --- | --- | --- |
| Complete | Browser STOP, native pause, local writes drained, server drained, cookie cleared | Sign-out complete; automatic sign-in remains paused |
| Pending | All local acknowledgements plus server pause, but server drain pending | Keep state; do not repeat sign-out |
| Unconfirmed | Any missing, failed or late acknowledgement | Show each separate fact; retain profile for administrator review |

Browser stop, native pause, server pause/drain and cookie cleanup must never be
collapsed into one inferred success. Unknown outcomes retain evidence. No method
in this route clears a guard, repairs a profile, resets setup or silently retries
a consumed approval.

## Deadline ownership

| Owner | Unrenewed budget |
| --- | --- |
| Initial MV3 native selection plus startup arbitration | 12 seconds |
| Read-only inspection / individual native browser call | 12 seconds each, within enclosing budgets |
| Native process supervisor | 10 seconds per native operation |
| Review and confirmation, including installation | 60 seconds |
| Initial installation / accepted verification / complete Stop | 45 seconds each |
| STOP marker / native pause adapter | 10 / 30 seconds |
| Owned logout document/channel / HTTP parser | 20 / 10 seconds |

Wall and monotonic deadlines are checked across asynchronous boundaries; the
larger elapsed interval is deducted conservatively from session lifetimes.
Timeout is not cancellation of an already-started Chrome/native/HTTP operation.
Late replies cannot select another action, renew a budget or upgrade a terminal
result. Stopping a worker cannot prove whether an uncertain server action committed.

## Qualification and remaining work

The predecessor's canonical lifecycle passed a private installed real-Chromium
matrix covering DNS, IPv4 and IPv6, initial acceptance and accepted restart,
same-origin sign-out, and retained stopped restart. Its consent was synthetic:
that matrix is **not** ordinary-route or physical-click acceptance of this
successor. Preserve those frozen artifacts and all uncertain/failed profiles.

Current deterministic tests compose the actual ordinary worker gate, native
response parsers, consent/installation and accepted lanes, probe/logout channels
and HTTP parsers with modeled Chrome/native/HTTP I/O. Separate page tests cover
trusted-event gating, ticket/completion separation, original deadlines, fixed
navigation, lost replies and persistent sign-out facts. These are model tests,
not evidence of real MessageSender, hardware appearance or 100% project coverage.

Before publication: complete source/installed regression and exact package
parity, then fresh ordinary Chromium trusted-input, idle/wake, accepted/stopped
restart and origin/cookie tests, followed by focused small-Pi and HDMI-Pi visual
acceptance. No existing Home Assistant installation, credential, Pi display,
global trust store or published release is changed by this source qualification.
