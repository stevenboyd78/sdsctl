# Experimental device replacement and resume boundaries

Status: **experimental trusted-page/native-bridge candidate, not production accepted**.
Generated experimental bundles now include a two-step resume page and an
identity-bound native bridge. Isolated real-browser acceptance has passed on
both Raspberry Pis; this is not a supported production resume or credential-replacement
installer. Do not call internal Python methods, edit browser storage, delete a
ledger, or rerun first-time setup to work around a refused or uncertain operation.
The published manual-login kiosk and remote TUI are unchanged.

This guide builds on [first-run setup](browser-device-first-run.md),
[service lifecycle](browser-device-service.md) and the
[enrollment/recovery design](managed-display-enrollment-design.md). It describes
what the candidate components guarantee and what a supported workflow must add.
It does not enable production enrollment, accept rotation downloads, change
credentials, start services or deploy to either display.

## Three separate decisions

A display being allowed on the server is not the same as a user consenting to
automatic sign-in on that display. Three independently persisted states matter:

| State | What it controls | What changing another layer does not do |
| --- | --- | --- |
| Server device record and generation | Whether this device credential can receive a new display-only session | Server resume does not erase local pause or native terminal errors; old-generation sessions stay invalid |
| Native helper mode and revision | Whether this installation may attempt a verified authentication exchange | Replacing a credential or fixing trust does not reset the ledger; a native-only reset does not override a paused server |
| Browser recovery record | Whether this browser has intentionally stopped automatic sign-in | Native-only reset does not clear browser sign-out; the browser reasserts native pause on its next recovery attempt |

The native ledger has a trusted internal reset primitive with a revision check.
It is deliberately absent from the native-message protocol. It is not an
end-to-end resume operation or evidence that the server and browser agree.
Likewise, a native response marked `active` permits an attempt; it does not prove
that an authenticated browser session is installed or a screen is updating.

## Keep four operations distinct

**Code-only update:** use the existing stopped-browser maintenance path. Preserve
the device identity, credential, trust, browser state and native ledger. An update
must never become an implicit resume.

**Credential rotation for the same device:** the administrator issues a new
credential and advances the server generation. Old credentials and old sessions
are invalid. A paused device stays paused. The first-time profile importer still
refuses rotation handoffs: a checked, private replacement installer is separate
work. Merely writing a new secret must not reset either local recovery layer.

**Resume after intentional sign-out or a repaired terminal error:** the candidate
requires explicit consent and agreement across all three states. A server
resume by itself is insufficient. If an attempt was made while the server was
still paused, the resulting native credential-rejection state must remain stopped
even after the administrator subsequently resumes the server.

**Replacement of a revoked device:** revocation is terminal for that identifier.
The current authority refuses re-enrollment, rotation and resume under the revoked
ID. A replacement uses a separately enrolled new identity and new setup. It must
not inherit the old browser's saved state, reuse an already consumed first-run
claim, or erase retained failure evidence. Revoking or replacing one display must
not disturb another display's credential or valid session.

## Interrupted administration is not permission to retry a mutation

An administrator mutation can commit before its acknowledgement is received.
An unconfirmed rotation still has a one-time private credential handoff; it is not
safe to repeat the original rotation as a generic retry. Confirm the exact
committed record instead. A stale generation cannot authorize a later resume,
and a stale native revision cannot clear a newer local pause.

Owner acknowledgement is point-in-time evidence of the exact server record and
old-request drainage. It does not establish browser consent, protect against a
subsequent administrator change, or authorize clearing local state. Retain
uncertain outputs for review; do not mark the whole display resumed when only
one step succeeded.

## Requirements before accepting a coordinated workflow

The following remain development requirements, not runnable instructions:

1. **Review the exact installation.** Bind the operation to the canonical server
   origin, device, extension/native installation, current server generation,
   native revision, and browser recovery state. Validate private credential/trust
   inputs without displaying their contents. Retain DNS, private IPv4 and IPv6
   support; a proxy or internal DNS must not become mandatory.
2. **Separate replacement from consent.** Stage and validate a new same-device
   credential without clearing pause or terminal state. Reject wrong-device,
   revoked, stale and unconfirmed handoffs as authorization to resume. A reviewed
   confirmation-only retry must not rotate again or select a newer record.
3. **Make resumption explicit and race-safe.** Use an authenticated administrator
   boundary and a trusted browser/setup boundary; ordinary dashboard messages,
   helper starts and manual password login must not grant resume permission.
   Coordinate durable state changes so that a new sign-out wins over an older
   approval. A crash, lost response, concurrent operation or partial write must
   not silently resume the remaining layers or replay consumed approval.
4. **Prove a fresh session separately.** Recheck current server authorization and
   native revision, obtain a newly authorized session using verified TLS, and
   verify the browser's cookie and protected display access. Keep `allowed`,
   `pending`, `paused`, `rejected` and `session ready` distinct. Do not weaken
   ordinary rate limits, cookie protections or display-only route restrictions.
5. **Qualify recovery and retirement.** Test each interruption point and repeated
   request, preserve old-generation rejection and unaffected displays, and retain
   exact evidence for failed operations. Physical screens, service ordering,
   secure unattended keyring handling, boot and combined power loss remain
   separate acceptance gates.

The native transaction, verified transport, browser coordination, trusted consent
page and supervised native protocol bridge are implemented as candidates below.
The real-browser proof adapter passed the isolated two-Pi matrix described below.
Credential replacement, retained-operation maintenance and physical deployment
remain separate requirements.

## Native approval engine: internal candidate only

`browser_device_resume.py` provides a trusted internal `prepare`/`commit` engine.
There is no resume CLI or automatic caller. The generated experimental trusted
page reaches it through the identity-bound native bridge described below.
Existing first-run, ordinary recovery, service and credential import paths never
prepare or commit an approval. Do not invoke internal methods on a real
installation to bypass the pending coordinated acceptance.

Preparation requires an exact stopped native revision, a browser-intent
fingerprint and context-bound evidence of an already-active server record with
confirmed old-request drainage. The installation fingerprint binds origin, device
and extension identity. Preparation also fingerprints the validated private
credential and certificate bundle. It retains the native stopped mode, advances
its revision and returns one private, two-minute approval ticket. Only the ticket
digest is stored; the returned ticket must not be logged or generically serialized.
The browser-intent fingerprint is supplied by a trusted caller: this module does
not read browser storage or verify an actual user gesture.

Private-file checks are snapshots, not a transaction across external file writers,
server authority and browser storage. A future installer must serialize credential
and configuration changes with approval handling. Losing the prepare response
leaves an outstanding approval; neither its ticket nor a replacement approval is
silently recreated.

| Durable approval phase | Meaning |
| --- | --- |
| `prepared` | One exact review is outstanding; native recovery remains stopped |
| `claimed` | Approval was consumed before requesting fresh server evidence; another commit or automatic replay is refused |
| `complete` | Fresh evidence and unchanged inputs allowed an atomic native reset; this is not browser/session readiness |
| `cancelled` | A newer native pause, trusted reset or clock correction invalidated the approval |
| `failed` | Claimed verification failed, or a correctly identified approval was refused for expiry/clock rollback; a later retry cannot revive it |

Commit consumes the ticket before calling the proof adapter, without holding the
native SQLite transaction across external work. The adapter must authenticate its
own transport and confirm the exact reviewed active server generation and drain
result. A display credential must never grant authority to resume a server-side
administrator pause. The Python evidence object is not a signed receipt or a
network authentication mechanism. Returning a fabricated object is not verified
server authorization; same-account/root code and supplied adapters are trusted.

After proof, commit rechecks input fingerprints, native revision/mode and ticket
validity, then saves native permission and `complete` atomically. A new pause can
cancel an approval while proof is blocked, including when the native mode was
already paused. Failures retain their evidence rather than replaying or deleting
it. The engine rejects proof callbacks that return after ten seconds, but cannot
interrupt a callback that never returns. Each bridge action therefore runs under
the existing independent native process supervisor: it kills and reaps a stuck
child at ten seconds, without serializing private exception details.

If the process dies after claiming, `claimed` remains consumed. If commit succeeds
but its acknowledgement is lost, the native ledger may already be active and the
approval `complete`; the caller receives no assurance of success and cannot replay
the reset. **The browser controller retains its own durable pending pause on that
uncertainty.** Browser pause recovery reasserts native pause; it does not finish
an interrupted resume automatically.

Explicit preparation atomically upgrades only the selected native ledger to
schema version 2, including its approval history. Ordinary operations do not
migrate version-1 ledgers. Version-2 state is validated on each open, including
read-only inspection. Older helpers that accept only version 1 refuse an upgraded
ledger; do not downgrade or strip the new table as a rollback method. History is
bounded at 128 approvals and retained, not silently pruned. A reviewed history
maintenance/retirement path is still required before production use; the native
candidate below supplies only its stopped-ledger step.

These native guarantees are necessary but insufficient for a working display
resume. The connected UI/native components below have isolated real-Chromium
evidence; physical deployment and retained-operation maintenance remain gates.

## Retained-history retirement: internal candidate only

An interrupted approval is evidence, not permission to replay it. The internal
`browser_device_resume_maintenance.py` candidate can now review and retire native
approval history without granting permission to sign in. **It has no CLI, native
message, browser page or automatic caller.** Do not call internal methods on a
real display to bypass a pending operation. This is not yet the coordinated
browser recovery workflow.

The candidate keeps review, mutation and confirmation separate:

1. **Review without changing state.** Read one validated version-2 ledger
   snapshot, bound to the exact profile path and installation identity. Return
   only a private fingerprint, native revision/mode, approval counts and a
   two-minute review window. Reject active, old-schema, corrupt, unsafe or
   clock-rolled-back state; never migrate, repair or reset it during inspection.
2. **Archive before retirement.** Under the native ledger's write lock, compare
   the complete reviewed snapshot again. Exclusively create a new mode-0600
   archive in a separate existing mode-0700 directory. Write, synchronize and
   read back the archive before changing the ledger. An existing file, unsafe
   path, changed review or expired window is not overwritten or silently retried.
3. **Fence old approvals while staying stopped.** Advance the native revision,
   preserve its exact stopped/error mode and failure state, and retain the newest
   approval as a terminal anchor. Pending approvals become unusable; older rows
   move out of the live bounded history only after their evidence is archived.
   Schema 2 remains in place and the history is never empty. A returning older
   commit cannot authorize a session with its cancelled/missing claim or stale
   revision. Any future resume requires separate fresh server proof and consent.
4. **Confirm without replay.** If the mutation reply is lost, read-only
   confirmation compares the exact archive plan and live after-state. Archive
   existence alone is not success: a crash after writing it may leave the ledger
   unchanged. A superseded ledger is refused, not restored to match the archive.

The private archive includes the complete prior native approval rows and intended
after-state, including identity, intent and private-input fingerprints. It has no
raw credential, approval ticket, browser cookie or browser-storage contents, but
is still private evidence, not a public diagnostic or a signed authorization.
Keep all files after an uncertain result; there is no archive-restore or automatic
cleanup operation. Local history retirement does not revoke server credentials,
drain server requests or establish authenticated browser access.

Native retirement also **does not clear `resume_pending` or intentional pause in
the browser**. The separate internal browser coordinator below can explicitly
acknowledge the exact retired intent while keeping sign-in paused. A supported
workflow must still coordinate the launcher, serialize credential/configuration
writers and supply trusted UI and native transport bindings. The native candidate
cannot certify that a browser is stopped, so it must not be wired directly to a
generic page button. No production recovery procedure or physical/outage
acceptance is implied.

## Browser acknowledgement of retired intent: internal candidate only

Resolving an interrupted attempt and consenting to automatic sign-in are separate
operations. The coordinator now has optional internal `reviewPendingRetirement`
and `retirePending` methods. **No installed Chrome adapter, page or CLI exposes
them.** The confirmation-only controls described below are internal candidates,
not generated extension wiring. Without an explicitly supplied trusted retirement
adapter these methods are absent, and ordinary startup still retains pending
state. Do not manually edit Chromium storage or use a second controller to invoke
them beside the running worker.

The native `confirm_browser_intent` handoff is read-only. A trusted adapter must
select the already-reviewed native archive out of band and revalidate its exact
after-state. The newest retained approval must match the browser's exact pending
intent and installation. An older matching row elsewhere in the archive, an
uncommitted archive, a changed native revision or an unrelated pending intent is
not enough. The worker handoff contains identity, intent, retirement fingerprint,
native revision and stopped mode; it is not a signed receipt or server permission.
Private paths and fingerprints must not be supplied by or returned to an ordinary
dashboard page.

The browser operation has these boundaries:

- One read-only review per worker, valid for one minute, returns only native
  revision/mode. The pending record, cookie and alarms are unchanged by review.
- A separate explicit confirmation consumes that review on the same operation
  queue as normal recovery. Clear the reserved device cookie, cancel recovery
  alarms, obtain fresh matching native evidence, and recheck the persisted
  pending browser record before the final write. No session exchange occurs.
- Successful acknowledgement replaces only the exact version-2 `resume_pending`
  record with a version-1 clean record that **still has `paused: true`**. It reports
  `retired_paused`, never session readiness. The native archive is retained.
- Newer suspend/sign-out intent invalidates the old operation. Changed evidence,
  wrong identity/intent, unsafe storage, cleanup failure and expiry never report
  successful retirement. A queued wake cannot intervene halfway through the
  explicit operation. A lost reply does not authorize another attempt.
- Final storage acknowledgement is not atomic with native confirmation. If a
  write fails before persistence, pending state survives restart. If it persists
  but its reply is lost, clean-but-paused state may survive. **Neither outcome
  permits automatic sign-in.** The failed worker does not report completion.
- Successful retirement does not reuse the earlier resume approval or enable
  same-worker resume. A later worker remains paused and needs a separate fresh
  server review and explicit resume consent.

The history-retirement handoff deliberately refuses a pending intent with no
matching latest native history record. The separate internal reconciliation
candidate below handles absence from retained history, without borrowing an
older approval. Fixed installation/transport selection, trusted user-facing
consent, private-file writer ownership and real-browser/physical acceptance
remain gates.

Deterministic browser tests cover state, cookie and message boundaries. A joined
Node/Python fixture also calls the actual native archive/ledger implementation
over test-only subprocess I/O for DNS, IPv4 and IPv6 identities. It performs no
network authentication and uses controlled browser storage/cookies; this is not
real Chromium, a supported native-message bridge or deployed-Pi acceptance.

## No-matching-record reconciliation: internal candidate only

The browser saves `resume_pending` before native preparation. If preparation
fails before its transaction commits, a browser can remain paused with no native
approval for that intent. **A missing retained row does not prove the attempt
never happened:** older rows might already have been archived. The internal
`browser_device_resume_reconciliation.py` candidate proves only the present
absence and a new stopped revision fence. It does not invent an approval or
claim historical absence.

An explicit, private two-minute review binds the exact browser intent, profile
path, installation identity, ledger schema, native state and retained history.
It accepts existing stopped schema-1 or schema-2 ledgers, without migrating
either. Any matching row, even terminal, or any other prepared/claimed approval
refuses this path. Corrupt/empty schema-2 history, active state, unsafe files,
unsupported journal modes and clock rollback also refuse; nothing is repaired.

Explicit confirmation takes the SQLite write lock, compares the whole snapshot,
then creates and synchronizes a new private archive before advancing the native
revision. It preserves every history row, the schema, stopped/error mode and
failure state. It neither frees history capacity nor removes earlier archives.
If an in-flight prepare commits first, the review is stale and reconciliation
refuses. If reconciliation commits first, the delayed prepare's old expected
revision refuses. Neither outcome is permission to authenticate.

Read-only completion inspection reconstructs the exact archived plan and checks
the live after-state plus the requested browser intent. An archive without a
committed revision fence is not success; a later native change refuses the old
confirmation. A lost mutation reply is inspected, never automatically replayed.
As with retirement, archives and same-account/root adapters are trusted local
inputs, not signed audit receipts or an atomic transaction with browser storage.

The same internal browser acknowledgement coordinator can consume this exact
evidence through a test-only adapter. A separate browser review/confirmation
still resolves only to **clean-but-paused**, with no same-worker resume and no
authentication call. Joined tests cover both ledger schemas, DNS/IPv4/IPv6
identities, wrong intent, an uncommitted archive, newer native state and a lost
browser storage reply. Native tests also cover process death on each side of
commit, competing preparations/reconciliations, preserved terminal errors and a
full 128-row history. These are local implementation tests, not browser/TLS or
physical-display acceptance.

There is still **no installed transport, CLI, native mutation action, page binding
or automatic caller** for either maintenance path. Do not call these internal
methods or edit browser storage on a real display to bypass a pending operation.
The internal selection/ownership boundary below is the next layer, not an
installed workflow. Trusted consent, replacement-writer integration and actual
browser acceptance still need qualification before exposing it.

## Trusted maintenance selection and profile ownership: internal candidate

`browser_device_resume_boundary.py` fixes the native profile and a separate,
existing private archive directory when a trusted local caller constructs the
boundary. Neither directory can be selected through a browser message. The
boundary supports explicit history retirement and no-matching-record
reconciliation; it does not choose or authorize a resume on the user's behalf.

A read-only review binds the exact native review and browser intent to both
directory identities and the current private inputs. Configuration, credential
and CA files are fingerprinted privately, including their presence and filesystem
metadata. Missing credentials/trust remain missing; unsafe files are refused,
not repaired. A replacement or rewrite invalidates the old selection even if
the same bytes are subsequently restored. No credential bytes enter the review
document, return value, exception message or logs.

Execution consumes the exact in-memory review once. It exclusively creates a
directory named by its opaque operation fingerprint under the configured archive
root, then synchronizes `review.json` before the native core creates
`native-history.json` and commits its stopped revision fence. These filenames
are fixed. Existing or partial outputs are retained, never overwritten, deleted
or automatically retried. Read-only confirmation after restart takes only the
opaque operation ID and exact browser intent, reconstructs the recorded review,
and checks unchanged inputs and the core's exact committed after-state.

`browser_device_profile_access.py` supplies a nonblocking Linux advisory lock on
the existing private profile directory; it creates no lock file. The updated
supervised native runner takes shared ownership across request parsing, private
reads and dispatch, including network work. Boundary mutations take exclusive
ownership before opening SQLite. Shared reviews/confirmations may coexist with
native requests; any conflicting owner causes refusal instead of waiting or
silently retrying. A killed process releases its OS lock, but not its retained
operation evidence or intentional pause.

This is a **cooperating-writer contract, not a credential-replacement installer**.
Future configuration/credential writers must acquire exclusive profile ownership,
fence affected native approvals and implement durable partial-write handling.
They must not rename or replace the profile directory while holding its lock.
Direct low-level core calls, older helper versions and arbitrary same-account/root
file edits do not automatically participate. An advisory lock does not stop such
edits; input fingerprint checks are not a substitute for a coordinated writer.
Do not deploy a mixed-runtime maintenance workflow or claim that holding a lock
alone makes credential rotation safe.

Native-process lock tests and joined Node/Python fixtures exercise this boundary
with private synthetic inputs. Browser storage/cookies remain controlled in those
fixtures. No native-message maintenance mutation action, CLI, installed Chrome
adapter or trusted page is enabled yet. The confirmation-only candidate below does not execute
maintenance. Profile ownership does not itself prove browser shutdown,
server permission, user consent, session readiness or physical outage recovery.

## Confirmation-only recovery controls: internal candidate

An optional `BrowserRetirementSelection` passed by trusted local wrapper code
fixes the existing archive root and a single opaque operation ID. It switches the
supervised native runner into **confirmation-only** mode: the only accepted
request is `confirm-retirement` with the exact installation identity and browser
intent. The default generated wrapper has no such selection and refuses this
request. The confirmation-only endpoint refuses normal status, suspend,
authentication, claim and resume requests; it cannot execute maintenance or
reconstruct a review to replay a mutation after process loss.

The selected operation must already have committed through the maintenance
boundary. Each call reads and verifies its fixed evidence files, current native
after-state and private input binding. Missing, incomplete, edited, superseded
or unrelated evidence fails closed. No request can supply an archive/profile
path, operation ID, credential, URL or serialized native review. The existing
ten-second process supervisor also bounds confirmation and releases process
ownership on timeout. Replies contain bounded proof or a fixed redacted error,
never credential bytes, archive contents or a session cookie.

`browser_device_retirement_ui.mjs` provides candidate Chromium page, worker and
native-port adapters. They are **not included in normal startup bundles**.
The separate recovery-only preparation described below can stage `recovery.html`
and a fixed confirmation wrapper, but does not register a host or change any
launcher. Do not register a wrapper with browser-supplied selection values.

The page controller is inert on opening. A trusted review gesture checks the
already-completed maintenance; a second trusted submission requires an unchecked-
by-default confirmation. The worker binds its one-use, one-minute ticket to the
exact active, top-level, non-incognito extension page document. Page messages
carry only the action and, on confirmation, that volatile UI ticket. Native
operation IDs, browser intent and native evidence stay out of the page.

Confirmation reuses the existing paused acknowledgement coordinator: clear the
cookie, cancel alarms, recheck native proof and unchanged persisted browser
intent, then save clean-but-paused state. Success is acknowledged only with
`localPauseSaved: true` and `sessionReady: false`. Native errors are not repaired;
server permission is not changed; no authentication is attempted. A later resume
requires a new worker and a separate fresh consent review. Lost replies and
stale or newer pause/sign-out state must not trigger automatic mutation replay.

Node tests cover trusted gestures, document binding, strict messages, expiry,
restart, redaction and paused state. Joined Node/Python tests exercise the page
and worker controllers through the actual native frame parser, process
supervisor, fixed maintenance boundary and SQLite evidence, including absent
commits and lost browser-storage acknowledgements. Browser DOM, cookies and
storage remain controlled fixtures; this is not real-browser, TLS, physical Pi,
Firefox/WPE or production acceptance. Installer provisioning, native mutation
consent and credential replacement remain separate work.

## Stopped-browser local maintenance session: internal candidate

`browser_device_resume_workflow.py` adds a trusted local review/apply session
around the native maintenance boundary. It is **not a CLI, a browser button or
an installed recovery procedure**. The local adapter fixes the dedicated browser
directory, native profile, current canonical bundle, public extension key and
separate private archive root. These directories must not overlap. The candidate
requires intact, valid private profile inputs and an exact current-runtime
registration; missing credentials/trust need a separate recovery workflow.

Before presenting a review, the session validates the registration and acquires
the managed launcher's nonblocking lock. Existing Chromium Singleton markers,
another launcher/maintenance owner, or a prior maintenance guard cause refusal;
the session does not stop processes, disable services or delete stale locks. It
holds launcher ownership across the local review and execution. Opening a first
review can create the launcher's empty private coordination file; cancellation
leaves that file in place but does not change native or opaque browser state.

The immutable local review identifies the display origin/device, selected
directories, exact native operation, stopped mode/revision, retained/pending
approval counts and expiry. A trusted local UI must display that context and the
effects before obtaining confirmation. The callback must return the exact
session-specific confirmation phrase; returning `None` cancels. The phrase
includes a fresh random challenge, so a previous confirmation is not accepted
even if two native reviews otherwise match. The original private execution
review stays in memory in the same process. A page/native message, saved phrase
or reconstructed review is not an execution adapter.

Both wall-clock and monotonic limits must remain within the native two-minute
review window. After consent the session revalidates the registration and
browser directory/lock identity. It exclusively writes and synchronizes
`.sdsctl-browser-maintenance.json` in the dedicated browser directory **before**
calling native execution. The marker records the fixed targets, operation,
reviewed state and approval time; it contains no credential, cookie or reusable
resume ticket. It is private local evidence, not signed proof of human consent
or server authorization. Consent is rechecked after the marker write so slow
I/O cannot extend the execution window.

The marker remains on **every post-write outcome, including success**. Native
execution uses the exact one-use in-memory boundary selection, retaining its
existing archive-before-commit rules. Browser registration, native-host files,
credentials and opaque Chromium storage are not replaced or reset. Normal
inspection/startup continues to reject the retained marker without the separate
[paused guard release](#paused-only-guard-release) completion. This workflow does
not remove it, publish a completion receipt that enables launch, acknowledge
browser pending state or resume automatic sign-in.

After an interrupted or lost reply, a new process can perform exact read-only
confirmation using the operator-selected operation ID and browser intent. It
requires the existing launch-lock file (it will not recreate a missing one),
the matching private marker, the current canonical registration and independently
confirmed native archive/after-state. It also checks that displayed review
fields match the recorded native review. A marker written without a native
commit is not completion; missing, partial or superseded evidence is refused.
No confirmation path replays execution, overwrites evidence or releases the
startup guard.

This coordinates **cooperating managed launchers for one dedicated browser and
native profile**, not arbitrary direct Chromium launches, old runtimes or manual
same-account/root edits. The native boundary still serializes participating
private-input writers and fences stale reviews. Neither advisory lock proves
all possible processes are stopped or implements credential replacement. The
local callback has not been turned into a user-facing, supervised prompt.

Tests use actual canonical generated registrations, Linux launcher locks,
private archives and SQLite. They cover all stopped modes, cancellation, stale
or reused consent, competing owners, changed files, slow/failed guard writes and
real process loss during review, after the guard and after native commit. No real
Chromium, Home Assistant, Pi display or production profile is used. A separately
reviewed handoff that can safely launch the confirmation-only browser controls
and explicitly release this guard is still required before deployment; never
delete it manually to advance the workflow.

## Recovery-only bundle preparation: internal candidate

`browser_device_retirement_bundle.py` can now prepare a **new, inert** recovery
bundle for one already-completed stopped-browser maintenance operation. This is
not an installation command or a runnable recovery procedure. Normal bundle
generation, registration, startup and service behavior remain unchanged.

The trusted local caller supplies the exact original browser directory, profile,
normal bundle, public extension key, private archive root, operation ID and
browser intent. Preparation requires the existing launcher lock and validates
the retained maintenance guard, canonical original registration, committed native
evidence and current private-input binding. The new output must be separate from
all those directories. Missing, partial, edited, superseded or unrelated evidence
is refused. No browser files, credentials or existing bundles are replaced.

The staged bundle uses the **same public extension key and extension identity**,
so a future handoff can address the existing extension storage without Python
reading or changing Chromium's databases. Its artifact contract is distinct
from a normal startup bundle: canonical normal registration rejects it. An
inspection reconstructs every expected file from the installed runtime and
current maintenance context; merely editing the receipt and recomputing file
hashes cannot authorize arbitrary code. Output is created exclusively, with
private permissions, synchronized files and a receipt written last. Partial or
uncertain output is retained, never overwritten or automatically retried.

The recovery worker creates one instance of the existing recovery coordinator
and exposes only the review/confirmation controls. It does **not** call the
normal startup adapter or initial recovery tick. There is no startup, install or
alarm listener, dashboard content script, normal control page, first-run setup
or resume page. Ordinary native dispatch, alarm scheduling and cookie
installation are unavailable in this composition. Opening or restarting the
worker only restricts storage access to trusted extension contexts and reads
the saved recovery record; it does not authenticate, suspend native state,
clear an error or initialize missing browser state.

The only staged native endpoint is a confirmation-only wrapper with the fixed
profile, identity, archive root and operation ID. It deliberately uses the same
native-host name as normal recovery. A future guarded handoff must **replace**
the normal endpoint while the browser is stopped, not add a second endpoint
alongside it: a cached old worker must not retain a reachable authentication
host. The wrapper rejects ordinary status, suspend, claim, authenticate and
resume requests. Browser messages cannot select a different operation or path.
Each accepted request independently reconfirms the native evidence/after-state.

After explicit page review and confirmation, the canonical coordinator verifies
cookie absence, cancels the old recovery alarm, reconfirms native evidence and
checks that the browser's pending intent has not changed. The recovery-only
storage adapter then writes **clean-but-paused** state and reads it back exactly
before reporting success. Lost writes, mismatched read-back, newer browser
intent or native proof changes cannot produce a successful acknowledgement or
automatic retry. A later worker remains paused and cannot use the previous
volatile page consent.

**Preparation and a successful page reply do not release the maintenance guard.**
Preparation changes no dedicated host registration, launches no browser or
service, and produces no durable browser-acknowledgement receipt itself.
Likewise, Chromium exit status alone is not proof of saved browser state. Exact
temporary registration, supervised launch, process-loss handling, durable
acknowledgement verification and safely restoring the original registration
are handled by the separate handoff boundary below or remain required before
an end-to-end recovery procedure can be deployed.

Tests exercise generated page and worker modules joined to the generated native
executable, native framing/supervision, private archives and SQLite. Additional
Node contracts check inert worker restarts, unavailable ordinary actions,
cookie/storage failures, changed intent and lost acknowledgements. DOM, browser
storage and cookie APIs in these tests are controlled fixtures. They do not
prove actual Chromium loading/replacing the extension, physical Pi display
behavior, server TLS, Firefox/WPE compatibility or production acceptance.

## Guarded host handoff and paused acknowledgement: internal candidate

`browser_device_handoff.py` provides an internal local handoff boundary, not a CLI
or browser launcher. Preparing a recovery bundle with a separately selected
handoff directory enables its additional paused-acknowledgement capability.
Normal startup bundles and confirmation bundles without that selection cannot
write handoff acknowledgements. All paths and the native maintenance operation
stay fixed by trusted local code; a page cannot select them.

Activation requires exact current normal/recovery bundles, the retained
maintenance guard, confirmed native evidence, a stopped dedicated browser and a
new private handoff directory outside the other selected directories. The local
owner holds the managed-launcher lock and shared native-profile ownership through
the callback. A separate empty owner lock and the original owner's Linux PID and
process start ticks identify the live handoff. An on-disk active record or a
fork-inherited lock alone does not make a dead supervisor live. This is advisory
coordination for participating code, not protection from malicious same-account
or root processes.

Before changing the host, activation writes and synchronizes the exact original
registration, native-host manifest, guard and handoff operation. It records the
switch before unlinking the original host and exclusively writing the canonical
recovery host. A ready marker follows the synchronized replacement. Only the
single native-host manifest changes: the normal registration receipt, original
bundle, credentials, native archive and opaque browser storage are preserved.
The native-host name is unchanged, so the authentication host is replaced rather
than left reachable alongside a second recovery host. A partial/missing host
blocks progress; no stale lock or guard is removed to work around it.

The trusted callback is the future browser-supervisor boundary. Activation does
not itself launch a browser, run an arbitrary page-supplied command, install or
stop a service, or change server permissions. Returning zero, `True`, a success
string or a clean browser exit is **not** acknowledgement. The browser must be
stopped again before activation can confirm the final result. If it is still
running, preserve its Singleton markers and the guarded handoff for review.

During a live handoff, the selected native wrapper can confirm the exact native
maintenance evidence and accept `acknowledge-retirement`. That acknowledgement
contains only the exact installation identity, browser intent, retirement
fingerprint, stopped mode and native revision. The runner rejects extra fields,
changed proof, ordinary authentication/resume actions, stale owner identity,
missing ownership or changed registration/evidence. Native confirmation and
acknowledgement retain the existing ten-second process supervisor. Receipt
writes also serialize on the handoff directory; no existing or partial receipt
is overwritten.

The generated worker first completes the existing explicit page confirmation,
cookie cleanup and clean-but-paused browser write/read-back. In a handoff bundle
it performs a further exact paused-state read, then sends the proof already
confirmed by its native port. A fresh worker cannot manufacture an acknowledgement
from a generic paused state or reuse the previous volatile consent. Only a
verified native acknowledgement produces page success. A lost storage write
leaves no acknowledgement; a lost native reply may leave a valid receipt and must
not trigger automatic replay.

The private acknowledgement binds the exact handoff record—including the
original owner, bundle/guard hashes and operation—to the native after-state. It
states that the trusted browser worker saved pause and has no ready session.
**This is a trusted-extension attestation, not independent native inspection of
Chromium's internal databases, a signature, or server authorization.** Same-account
and root code remain trusted. The native boundary never reads or repairs opaque
browser storage.

After process/reply loss, exact read-only confirmation requires the browser to
be stopped, reacquires launcher/profile coordination, and checks the guarded
registration, current native evidence and complete acknowledgement. No switch,
browser write or acknowledgement is replayed. An optional explicit restoration
then journals the exact acknowledgement before replacing only the temporary host
with its canonical original manifest. A final restoration marker allows
read-only confirmation of a completed but lost reply. Interrupted restoration
is retained for review, not automatically rolled back or repeated.

**Even successful restoration leaves the maintenance guard in place.** There is
no automatic login, native error reset, guard release or resume grant. A real
Chromium supervisor still needs reviewed executable/arguments, bounded lifetime
and shutdown, canonical extension activation checks and isolated browser
qualification before physical deployment. This callback alone does not supervise
an arbitrary process tree; callers must not substitute an unmanaged launcher.

Tests exercise real generated native executables, Linux locks, PID/start identity,
private files and SQLite; kill local test owners before/after switching and ACK,
including a child deliberately retaining inherited locks; and join generated
page/worker modules through controlled browser APIs to the real native endpoint.
Failures preserve evidence and guards. These are not real Chromium, Firefox/WPE,
Home Assistant or physical Pi acceptance, and production services are unchanged.

## Bounded Chromium supervisor: internal candidate

`browser_device_launch.py` now supplies the handoff callback as an internal,
foreground operation. It is **not a production CLI or service installer**.
Prepare a new recovery bundle and handoff with `supervised=True`; the ordinary
bundle/launcher and the earlier confirmation-only composition do not acquire
this capability. Existing or partial handoffs are not replayed or upgraded.

The launcher requires non-root Linux, the main thread, an existing graphical
session, Linux process handles (`pidfd`) and an explicitly selected existing
`bwrap` executable. Browser and isolation executable paths must be canonical,
executable and not writable by other users. Their file identities are checked
again before starting the browser. Chromium's bounded version response must
identify Chromium 120 or newer; Chrome-branded output is refused. No arbitrary
extra flags, shell command, debugging endpoint, certificate exception, password
store override or sandbox-disabling option is accepted.

The existing bubblewrap creates a **private PID namespace**, with a small sdsctl
supervisor as PID 1 and Chromium as its child. The supervisor independently
checks its PID-1 role before qualification or launch. Exiting that namespace's
PID 1 ends its descendants, including a process that detached into a different
session. The outer launcher signals only its owned process handles, never a
discovered process group or another browser. Bubblewrap's parent-death behavior
also covers abrupt loss of the outer launcher.

This is process containment, **not filesystem or network isolation**. The normal
filesystem and device mounts remain available. Chromium receives a namespace-local
`/proc`, as its own sandbox requires. The supervised bundle includes an empty
private `host-proc` mount point; bubblewrap mounts the host's procfs there read-only
inside the supervised namespace. Only the fixed native handoff construction uses
that separate view for owner PID/start checks. It verifies actual read-only procfs,
not merely a directory with process-looking files. No page message or environment
variable selects that path. Outside the namespace the mount point stays empty.
The outer launcher records its own namespace identity before launch. The native
endpoint does not weaken proc/ptrace restrictions to inspect that outer namespace.
Chromium's own sandbox, trust settings and graphical environment
remain unchanged. No packages, host policies, certificates, firewall rules or
production service settings are installed or changed. If isolation is unavailable,
qualification fails before the host switch; there is no unisolated fallback.

The lifetime is bounded at several independent stages:

- Browser version output is limited to 1 KiB and five seconds.
- Outer namespace/qualification replies have a twelve-second deadline. The inner
  supervisor waits at most fifteen seconds for the guarded launch instruction.
- The exact recovery page/worker must report readiness within sixty seconds.
- The guarded launch/confirmation phase lasts at most 180 seconds.
- After a saved-pause acknowledgement, graceful Chromium shutdown uses SIGINT
  and a ten-second limit. Failure triggers owned-namespace termination and is
  not a successful recovery result.
- The inner supervisor has an independent 240-second alarm, covering an outer
  launcher that remains alive but stops making progress. Cleanup waits on kernel
  process handles rather than treating a sent signal as proof of exit.

The generated supervised bundle adds a fixed **launch binding**. It hashes the
entire canonical pre-injection bundle, local targets and native evidence, then
embeds the result in the generated page and worker. The page starts with review
disabled and asks its matching worker for readiness. Only the exact active,
top-frame recovery document, matching extension and binding can reach the fixed
`recovery-launch-ready` native action. Dashboard pages, frames, caller-selected
paths/PIDs and other bundle generations cannot use it.

The fixed browser command starts at `about:blank`. After the recovery worker's
assets load, it opens its own fixed confirmation page. This avoids racing
Chromium's initial extension registration by navigating directly from the command
line. A generation-bound marker in trusted-context `chrome.storage.session`
coalesces installation/startup events and prevents a restarted worker from
reopening an already claimed page. The marker is written and read back before
opening; an uncertain page-opening reply is not retried. It contains no consent
or credential, is separate from persistent recovery state, and disappears with
the browser session. Opening the page never approves maintenance or signs in.

The native endpoint verifies the current handoff, live outer owner, inner
supervisor, namespace and deadline before writing a separate readiness receipt.
This receipt explicitly states `browser_consent: false`. It is **not** a saved
pause, recovery approval, server grant or session. Only a verified response
enables the page's existing explicit review and confirmation controls. An old
worker without the matching handshake leaves those controls disabled. A recreated
matching worker may read-confirm an exact completed readiness receipt, but cannot
rewrite a partial receipt or inherit prior user consent.

The later saved-pause acknowledgement still requires the original explicit
recovery workflow. The launcher waits for its native publisher to release the
handoff-directory lock after synchronization and post-write checks before
requesting shutdown. It does not contend for that lock while waiting for user
consent. Success requires exact readiness and saved-pause evidence, clean browser
exit, kernel-confirmed supervisor exit, and stopped-browser revalidation.
Absent Chromium Singleton files alone are insufficient while that supervisor
is alive. Read-only confirmation after a lost reply enforces the same stopped
process requirement.

Timeouts, crashes, interrupted publication or forced shutdown retain the temporary
host, native state, browser profile and maintenance guard. A complete saved ACK
can still be inspected after confirmed shutdown; a failed launch does not imply
that the browser failed to save pause. Explicit exact host restoration remains
separate, and **even successful restoration does not release the guard**.

Local tests use actual bubblewrap/PID namespaces, PID-1 processes, detached child
processes and generated native executables with fictional profiles. They cover
SIGTERM/SIGKILL owner loss, an independent inner timeout, refused versions, absent
readiness/ACK, changed evidence and failure to shut down. JavaScript tests exercise
the separate page/worker handshake through controlled browser APIs. The fictional
browser executable in the process tests does not render an extension or create
real Chromium storage. These results therefore do not establish real Chromium
activation/replacement, Pi display, Firefox/WPE or power-outage acceptance.

The process containment uses the documented [bubblewrap PID namespace and
parent-death options](https://github.com/containers/bubblewrap/blob/main/bwrap.xml).
Real-browser qualification is additionally exercised by
`scripts/experimental/qualify_browser_recovery.py` and its adjacent X11 helper.
Use only a new private fixture directory, an installed candidate wheel, an
authenticated private Xvfb display and the required local runtime dependencies.
The harness uses a separate D-Bus session and encrypted disposable keyring; it
does not change browser password-store settings, sandbox policy or TLS trust.
It drives the unmodified generated page with real keyboard events, without a
debugging endpoint. Its initial pending browser state and fictional cookie/alarm
are deliberately seeded through a separate fixture extension's browser APIs.
That seed is **not evidence for the normal setup/resume path**. Success requires
real page/worker/native readiness, explicit confirmation, clean shutdown, exact
host restoration and retained guard. Physical display, live scanner, normal
authentication/TLS, cold boot/outage and Firefox/WPE acceptance remain separate.
A separate paused-only guard-release candidate follows. Deployment/service wiring
and a production maintenance policy remain absent.

### Paused-only guard release

The separate [headed first-start qualification](browser-device-startup.md#exact-headed-first-start-qualification)
shows that normal startup respects missing setup, explicit consent and a paused
native profile on both tested Pi Chromium versions. It does **not** authorize
removing a maintenance guard or prove a resumed server session.

The internal `BrowserPausedGuardRelease` candidate implements the reviewed narrow
transition. It has **no CLI, browser/native-message action or service wiring**.
Its trusted local caller selects an exact supervised handoff out of band. There
is no automatic invocation after restoration and no public “ignore guard” option.
The original `confirm(restored=True)` still requires the unchanged retained guard.

- Fresh explicit local consent names the exact installation and completed
  maintenance/handoff. An old approval, page readiness, browser exit code or
  restoration receipt alone must not grant release.
- Acquire the existing launch lock, then shared native-profile ownership and an
  unchanged native-ledger `BEGIN IMMEDIATE` transaction. The directory lock blocks
  private-input maintenance; the transaction fences native revision writers.
  Require a stopped browser, no Singleton markers, canonical normal registration and
  exact completed maintenance, acknowledgement and restoration evidence. Recheck
  these immediately before the transition, rather than trusting an earlier view.
- Only the **paused-only** path is eligible. Releasing launch inhibition does
  not clear browser/native pause, reinitialize state, authenticate, enroll,
  rotate credentials, repair errors, start a service or start a browser. Other
  native terminal modes need their own review before becoming eligible.
- Preserve the original guard and all maintenance/handoff evidence. Lost-result
  handling must be exact read-only confirmation, not an automatic replay of the
  mutation. The original historical confirmation must remain interpretable after
  a successful release, without introducing a general guard bypass.

The callback receives a fresh, volatile confirmation phrase for one release ID.
It must respond in the same process within 120 seconds, checked using wall and
monotonic time. Cancellation creates nothing. The phrase is not saved; the local
journal contains only its fingerprint, selected public identity/paths, exact
evidence fingerprints, inode bindings and consent times. It contains no password,
credential, cookie or session token. Old consent cannot be replayed.

#### Durable completion and normal startup

The original guard is **never unlinked or renamed**. A new private, exclusively
created `.sdsctl-browser-guard-release.sqlite` first records `prepared`. The
directory sync must succeed before completion is attempted. After rechecking the
same evidence, inode and consent deadline, SQLite commits `complete` atomically
using `journal_mode=DELETE` and `synchronous=EXTRA`. SQLite documents the extra
rollback-journal directory sync in its [synchronous settings](https://www.sqlite.org/pragma.html#pragma_synchronous)
and the commit/recovery assumptions in [atomic commit](https://www.sqlite.org/atomiccommit.html).

Normal registration/startup checks the fixed journal whenever either the guard,
release database or one of its SQLite sidecars exists. It requires a completed,
strict-schema, private rollback-format database, no leftover journal/WAL sidecars,
the original guard, exact supervised ACK/restoration evidence, canonical normal
assets and the same paused native revision. Checks are repeated under the normal
launcher's lock. Removing just the guard or just the release database still
blocks startup. Replacing the completion database or guard with an identical
copy also fails its inode binding. No hot journal is recovered by inspection.

An empty, prepared, partial or inconsistent journal remains blocked and cannot
be overwritten by another attempt. An interruption before commit does not become
permission to launch. A commit error or lost reply can be **uncertain**: it may
have happened before or after durable completion. Only exact read-only
`confirm(release_id=...)` can establish a surviving completed result; no mutation
is automatically retried. Hardware power-loss durability still depends on
SQLite's filesystem/storage assumptions, not merely passing process-exit tests.

This is not an archive-cleanup or general resume mechanism. The fixed guard,
completion database and original evidence must stay at their original paths.
Offline inspection conservatively requires the released browser to be stopped.
Changed native revision, another terminal mode, a second maintenance cycle,
moving/updating bundles and later explicit resume need separate lifecycle review.
Same-account/root edits are trusted and advisory locks cannot prevent manual
removal of every record.

The regression matrix includes cancellation/expiry, competing locks and native
writers, changed inputs/receipts/inodes/revisions, preparation sync failure,
late rechecks, partial writes, and actual process exit before and after the SQLite
commit. Completed evidence is re-confirmed without altering historical records.
The separate headed `release` qualification scenario in
`scripts/experimental/qualify_browser_recovery.py` attempts the full supervised
handoff, local release, ordinary paused start and second paused start, followed
by a read-only browser-storage/cookie/alarm observer. The recovery-produced pause
is not rewritten between handoff and ordinary startup. A fixture-owned loopback
listener rejects any authentication-endpoint connection.

**Combined Chromium acceptance is currently blocked.** On the small Pi's
Chromium 151.0.7922.173 and HDMI Pi's 152.0.7977.75, the original seed-first
experiment completed supervised ACK, exact host restoration and local guard
release, but ordinary startup did not display the normal paused page. The visible
tabs instead showed unavailable `recovery.html` and `startup.html` extension URLs.
A stronger HDMI fixture first completed real canonical normal setup; its later
recovery launch failed readiness. These failures are not passes, and the earlier
separate startup/recovery results do not establish this combined transition.

The script's `release` scenario now includes the prior real normal setup;
`release-seed-first` preserves the narrower diagnostic sequence. Both still seed
their interrupted state synthetically. Failed runs retain their files and never
count tab navigation, manual reload, a copied browser profile or a new wrapper as
acceptance. A tested recovery-manifest version change did not resolve the problem
and was removed. At that checkpoint the specific cause was unresolved; the
follow-up investigation below narrows it. No permissions were broadened, policy bypass added or Chromium
database edited. The next gate is a reviewed, reliable normal/recovery bundle
transition followed by both full Pi sequences. Do not deploy this local API yet.

This fixture still deliberately seeds its initial pending browser state through
a separate extension. It does not qualify how that pending state originally
arose, real-server authentication, browser TLS trust, Firefox/WPE, a physical
display, cold boot, actual power loss or production service deployment.

#### Chromium worker-switch investigation

An isolated follow-up reproduced the underlying stale-worker behavior without
SDSCTL native messaging, recovery state, credentials or a server. Two fictional
extensions share an identity and worker URL but have different worker code. On
both tested Pis, Chromium loads the new manifest while executing the previous
worker code. The visible readback reports `role: A` and `manifestRole: B` in the
same fresh worker. This is not merely an old tab title or a delayed scanner update.

The preserved diagnostic matrix also found that changing the worker filename
produced worker-start status 18, changing just the manifest version did not solve
the transition, and varying the two extension-loading switches did not solve it.
Status 18 is Chromium's [worker-disallowed result](https://raw.githubusercontent.com/chromium/chromium/main/third_party/blink/public/common/service_worker/service_worker_status_code.h).
Its [worker activation code](https://raw.githubusercontent.com/chromium/chromium/main/extensions/browser/service_worker/service_worker_task_queue.cc)
tracks existing registrations using extension-version information, while its
[extension worker checks](https://raw.githubusercontent.com/chromium/chromium/main/chrome/browser/extensions/chrome_content_browser_client_extensions_part.cc)
restrict root-scope worker scripts to the manifest-selected script. Those sources
support the stale-registration explanation; they do not establish the exact
internal race/order on every Chromium build. Manual refresh and runtime reload
were diagnostic interventions only, never accepted as unattended startup fixes.

`scripts/experimental/qualify_browser_worker_switch.py` preserves a smaller
repeatable comparison. Its `changed` control uses different worker bytes at the
same URL. Its `stable` prototype uses byte-identical worker code and reads a
**fictional role** from the current manifest. Both use the fixed foreground
Chromium flags, private Xvfb/D-Bus/keyring and the existing PID-namespace owner.
There is no logging wrapper, debugging port, browser-storage reset, manual
refresh, runtime reload, native messaging or host permission in this comparison.
Generated inputs are hashed and checked unchanged after each clean shutdown.

The sequence is A → A → B → B → A → A, with a fresh worker nonce, worker start
time, matching page nonce and matching manifest/executed role required for each
pass. A restored tab, repeated nonce or a worker from an earlier launch cannot
pass. On Chromium 151.0.7922.173 and 152.0.7977.75, the stable prototype passed all
six starts per Pi. Both changed-code controls reproduced the stale A worker on
the two B starts. The script records `production_acceptance: false`; reproducing
the expected negative control is not a successful production transition.

#### Stable worker dispatch: next design boundary

The supported primitives above provide a promising implementation direction,
not a qualified replacement for the real handoff. Do **not** copy the prototype's
manifest-name selector into production. A display label cannot grant normal
mode, recovery authority, resume consent or guard release.

The next candidate should keep the worker entry and its complete imported code
graph identical across normal/recovery roles within one build. Before composing
either controller, obtain an exact, bounded, read-only context from the selected
native host. The native side must validate canonical registration, the current
owned profile, code/build identity and the applicable maintenance/handoff evidence.
Browser/page messages must not select paths, operation IDs or the authority mode.
Missing, stale, ambiguous, unknown or inconsistent context must fail closed.

Recovery mode must construct only the existing confirmation-only composition:
no ordinary startup tick, authentication, cookie installation, resume, alarm
scheduling or normal control listeners. Normal mode must still respect setup
consent and browser/native pause. A stale worker/build must be refused explicitly,
not allowed to fall back to ordinary operation. Existing launch ownership,
supervisor readiness, acknowledgement, restoration and guard-release checks must
remain intact. Shared source availability is not permission to execute both modes.

Required follow-up includes strict context/schema and stale-build tests, proof
that recovery cannot initialize normal capabilities, lost/invalid context with
zero authentication, and both complete Pi handoff/release sequences with two
ordinary paused starts and read-only final browser-state inspection. The current
runtime still uses the earlier separate worker compositions and combined
acceptance remains blocked. Worker changes across application releases, actual
power loss and deployment remain separate lifecycle gates.

## Verified server evidence and exact-generation sessions

The experimental server adapter adds native-only `POST /auth/device/verify`.
It accepts the fixed device identifier and an optional exact `generation`, using
the same device Bearer credential, origin validation, bounded body/workers and
shared rate budget as `/auth/device/session`. Cookies, browser Origin and Fetch
Metadata are refused. A device credential cannot select administrator operations.

Verification authenticates an active device, waits for older-generation requests
to finish, then reauthenticates the same binding. Only a confirmed result returns
the device, generation, active state and `drained: true`. It does not issue a
session, reveal other records, rotate credentials or resume paused authority.
Unknown/wrong credentials and stale generations are denied; unavailable or
unconfirmed drainage never becomes a successful proof.

The existing session endpoint additionally accepts an exact `generation`. If
supplied, issuance must match it and the response echoes it. Requests containing
only `device_id` retain the original response contract. A pause/resume cycle
between verification and exchange changes generation: the approved exchange is
refused, not silently redirected to the latest record. Every subsequent protected
request still rechecks server authority; verification is not a durable lease.

`browser_device_verification.py` uses only the fixed private native configuration,
credential and CA bundle. It shares verified TLS/hostname checks, bounded response
handling, no redirects, no proxy-environment handling and no cookie jar with the
ordinary transport. DNS names and IP addresses remain supported. Responses must
match the exact schema, device and requested generation. No credential or token
is inserted into the verification result or error text.

The native engine's internal `prepare_verified` checks the reviewed revision and
generation, obtains this verified proof and prepares the one-use approval.
`commit_session` consumes that approval using fresh proof, then separately obtains
a session for its bound generation under an exact native-revision check. It never
modifies browser storage/cookies. Both calls **require an independent process
deadline**; socket timeouts alone do not bound DNS or a slowly dripping peer.
The bridge supplies that deadline; no direct resume CLI is added.

## Durable browser consent: trusted controller

Only a controller explicitly constructed with the trusted resume adapter offers
internal review/resume methods. The generated experimental worker supplies that
adapter; the two-argument Chrome adapter remains unchanged. Dashboard messages,
the old control page, startup and alarms cannot submit resume approval. Do not
construct another controller or write browser storage to bypass this boundary.

One explicit reviewed attempt snapshots the native revision and server generation.
It saves a version-2, paused `resume_pending` record with a fresh intent fingerprint
**before** native approval or network work. Approval tickets and session tokens
remain process-local; browser storage contains neither. The same controller queue
owns approval and normal recovery, so a queued alarm cannot cancel approval
halfway through its intended operation. A newer sign-out synchronously invalidates
the old attempt and saves pause intent without waiting for outstanding I/O.

Only after a fresh generation-bound session, matching native revision, cookie
installation and a trusted adapter's protected-session verification may the
controller persist final browser consent and schedule ordinary recovery. A native
`active` response is insufficient. The Chrome adapter below implements
cookie/document/session verification; controlled-port tests alone do not
establish a working end-to-end browser flow.

Worker loss before the final browser consent commit leaves pending consent paused.
The next worker clears the cookie and reasserts native suspend, cancelling any
outstanding native approval. It retains the pending record rather than replaying
or repairing it. The optional internal retirement coordinator above is a separate
explicit operation, not an automatic recovery path. Its production binding is
still required. Older
browser code rejects this version-2 pending record instead of interpreting it as
ordinary recovery. Corrupt/missing state remains refused.

Final storage acknowledgement is a distinct boundary: if verified installation
and the final consent write actually succeed but its response is lost, saved
consent may already allow a later worker's ordinary recovery. The failed caller
does not report readiness or replay the approval. This is not an atomic transaction
across Chromium storage, native SQLite, the cookie service and server authority.
The browser adapters and maintenance tooling must preserve that distinction.

## Trusted review page and supervised native bridge

The generated bundle's startup page links to `resume.html`. Opening that page
does nothing to server permission, local consent or sessions. It displays the
fixed canonical server, device and extension identity; it never requests a
password or accepts a replacement credential.

1. **Review:** a trusted click requests a non-mutating server verification using
   the installation's saved credential and CA. Native mode must already be
   stopped and the server must already allow the device. The native revision
   must remain unchanged across verification. Only the revision and confirmed
   server generation return to the page.
2. **Confirm:** the user checks the consent box and submits within one minute.
   A volatile, one-use page review is bound to that exact top-frame extension
   document and tab. Caller-supplied revisions, URLs and native tickets are
   refused. A newer pause or changed/expired review cannot prepare approval.
3. **Verify completion:** durable pending consent precedes native prepare and
   commit. The worker installs the fresh Secure, HttpOnly, host-only, Strict
   cookie, checks actual protected access, rechecks native revision and only then
   saves final consent and reports a verified fresh display-only session.

The exact native actions are `review-resume`, `prepare-resume` and `commit-resume`.
Only a caller from the configured extension with the launcher-bound installation
identity can use them. Unknown fields, malformed fingerprints, unsafe numbers
and caller destinations are refused. Private approval tickets remain in the
worker/native exchange, not the page, persistent browser storage or diagnostics.
All three actions use the ten-second process supervisor. The raw `resume`
action remains invalid.

For protected-access verification, the worker opens one inactive tab at the fixed
`/device-display` route. An isolated, top-frame extension script makes a same-origin
`GET /auth/session`, allowing the server to check the actual HttpOnly cookie.
It requires an exact, bounded display-only/enrolled response with sufficient
remaining lifetime and returns only a request-bound Boolean verdict. The worker
rechecks the cookie and tab before accepting it, and closes only the temporary
tab it created. It uses a twenty-second overall deadline and bounded navigation
retries. No dashboard script receives the credential, cookie or response body.

There is one review attempt per worker lifetime, including a refused or lost
review. Reloading the page does not mint another approval. A lost confirmation
does not authorize retry or deletion: retain the pending browser/native records
for administrator review. A reviewed recovery/retirement path for these records
is still a prerequisite for production use.

A successful verified resume also retires the previous volatile logout ticket.
This permits a fresh sign-out in the same browser worker, while old completion
tickets cannot finish or upgrade the new logout operation. Only the trusted
resume path can perform that retirement, and it rechecks verified active
readiness first. A refused, uncertain or superseded resume does not reset the
logout bridge.

The end-to-end harness has `resume` and `resume-stale` cases, using generated wheel
artifacts, actual form clicks, loopback HTTPS and the real native process. Initial
workstation attempts stopped at sandbox initialization before the extension
loaded; those attempts remain blocked, not passes. The revised candidate then
passed eight isolated cases across both ARM64 Raspberry Pis: successful resume
over IPv4, DNS (`localhost`) and IPv6 on each host, plus refusal when server
permission changes between review and confirmation. Chromium 152.0.7977.75 and
151.0.7922.173 ran with sandboxing and TLS verification enabled.

Each successful case verified a fresh protected display-only session, closure of
only the temporary probe tab, a second sign-out in the same worker, and retained
pause after browser restart. The stale-review cases installed no session and
remained paused after restart. Setup performed no authentication exchanges.
The initial runs exposed a harness tab-counting mistake and then the real
same-worker logout-ticket lifecycle bug; neither failed run counts as acceptance.
All eight final passes used the corrected runtime and page-identity assertions.

These are headless, fictional loopback-authority tests through the installed
foreground launcher, not physical-screen, live-scanner, LAN/proxy, cold-boot or
power-outage acceptance. Production TUI services, credentials and Home Assistant
were unchanged. No sandbox, certificate or password-store protections were
disabled to obtain these results.

## What the isolated tests establish

- `tests/test_browser_device_verification.py` covers authenticated non-mutating
  proof, real old-request drain waiting, generation comparison, shared admission,
  malformed responses, certificate/hostname failure and DNS/IPv4 loopback TLS.
- `tests/test_browser_device_resume_transport.py` joins verified loopback HTTPS
  to the real ASGI/owner, authority, native approval and generation-bound session
  code. It verifies display-only access and refusal after server/native changes.
  Its HTTPS fixture forwards to ASGI TestClient; it is not a deployed server.
- `scripts/experimental/test_browser_device_resume.mjs` tests durable pending
  consent, single-use attempts, exact review snapshots, sign-out races, worker
  restart, malformed approvals, cookie/proof failure and lost final storage ACK.
  Storage, cookie and native ports are controlled doubles, not real Chromium.
- `scripts/experimental/test_browser_device_resume_ui.mjs` covers trusted-page
  gestures, document-bound one-use reviews, expiry, strict native wrappers,
  isolated protected-session verdicts, cookie changes and exact probe-tab cleanup.
  Paired logout tests require verified active readiness before retiring the old
  one-use ticket; refused or uncertain resume cannot acknowledge that retirement.
  Browser APIs are controlled doubles; this does not qualify actual UI rendering.
- `scripts/experimental/audit_browser_generated_recovery.mjs` exercises the real
  installed launcher, setup/resume forms, native helper, ASGI server, cookie and
  protected display access in sandboxed Chromium. Its eight two-Pi passes cover
  loopback DNS/IPv4/IPv6, a second sign-out, stale-review refusal and browser
  restart. It does not seed state or inject cookies, and is not a physical test.
- `tests/test_browser_device_resume_bridge.py` sends real native frames through
  identity-bound subprocesses, joins the verified loopback TLS/ASGI fixture,
  checks single-use prepare/commit and wrong-caller refusal, and demonstrates
  each action's independent ten-second kill-and-reap deadline.
- `tests/test_browser_device_resume.py` covers one-use approvals, exact input and
  intent matching, schema integrity, cancellation, expiry/clock rollback,
  concurrent commits, retained uncertain outcomes and atomic write failure.
  Tests join the actual local server-owner acknowledgement for DNS/IPv4/IPv6
  contexts and run a native subprocess that exits immediately after claiming.
  The server-proof callbacks are local trusted test adapters, not verified HTTPS
  evidence delivery or browser consent. These low-level approval tests do not
  create a display session.
- `tests/test_browser_device_resume_maintenance.py` covers exact read-only
  reviews, private archive ordering, the full 128-approval history, preservation
  of every stopped mode, stale/unsafe inputs, competing retirements and in-flight
  commits. Fault cases include partial archive writes, failed synchronization,
  SQL rollback, process death and lost post-commit acknowledgement. These are
  synthetic native-ledger tests, not coordinated browser or physical acceptance.
- `scripts/experimental/test_browser_device_retirement.mjs` tests exact retired
  intent, one-use review/confirmation, stale evidence/storage, queued wakes,
  newer sign-out, clock changes and both sides of lost storage acknowledgement.
  `tests/test_browser_device_retirement.py` joins that coordinator to actual
  native retirement evidence, including refusal for changed native state and an
  archive that was written without committing retirement. Browser APIs and the
  subprocess adapter are controlled test boundaries, not deployed Chromium.
- `tests/test_browser_device_resume_boundaries.py` joins the real SQLite authority,
  owner acknowledgement, session middleware and native recovery ledger. It covers
  server-only and native-only resume, all five stopped native modes, paused
  rotation, stale reviews, lost rotation acknowledgement, terminal revocation,
  old-session rejection and an unaffected second device. The in-process ASGI
  requests use exact DNS, IPv4 and bracketed-IPv6 Host identities. They do not
  establish IPv6 socket routing, TLS, browser cookies or stream-drain races.
- `tests/test_browser_device_native.py` replaces a fictional private credential
  file under each stopped native mode and invokes the real native process twice.
  No exchange occurs and the ledger remains byte-identical. A separately invoked
  trusted test-only reset then allows verified loopback HTTPS with the new secret.
  This synthetic HTTP server records the credential; it is not the real device
  authority and does not qualify a credential-file installer.
- The Node recovery tests preserve browser sign-out after native-only reset and
  refuse a different installation identity without rewriting the saved record.
  `tests/test_browser_device_extension.py` additionally joins the coordinator to
  real native framing and a persistent ledger in subprocesses. It demonstrates
  that browser pause reasserts native pause after a trusted test-only reset.
  Browser storage and cookie APIs in these tests are controlled doubles, not
  headful Chromium or physical-display acceptance.

These tests intentionally do **not** claim that coordinated resume or credential
replacement is shipped. They establish the protections that implementation must
preserve. No production password, device credential, Home Assistant connection,
scanner, trust store or display service is used.
