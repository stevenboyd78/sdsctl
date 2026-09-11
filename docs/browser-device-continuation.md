# Post-recovery continuation: design boundary

Status: **development design, read-only preflight/history, internal intent journal,
fixture-only owned paused activation, current-epoch transactions, owned current-state reads,
controlled native cancellation, owned prepare/claim, verification and initial-session fixtures;
one-shot browser installation I/O with modeled authority qualification;
fixed read-only continuation context and explicit server-review routing;
not an online resume implementation or an administrator runbook**. PR #250 remains experimental.
Do not invoke internal methods on a real profile, delete guards, edit Chromium
storage, replay setup or replace credentials to make a blocked display sign in.
The published manual-login kiosk and remote TUI are unchanged.

This follows the [paused-only guard release](browser-device-resume.md#paused-only-guard-release).
The release allows an ordinary browser start with sign-in still paused. It is
deliberately bound to the exact paused native revision and completed recovery
evidence. A subsequent ordinary resume would change that revision and invalidate
the release; merely exposing a resume button or allowing a native action cannot
safely implement the next transition.

## Implemented now

The [operation ownership and stop boundary](browser-device-continuation-operation.md)
defines the next integration handoff and the unselected separate-key browser
stop marker. It is not a new live continuation or sign-out route.

### Fixed read-only continuation route

After a valid fixture-owned activation, a fixed installed worker/native wrapper
can select a separate **read-only continuation** role from the actual owned
manifest/epoch. A mere intent, missing anchor, invalid history or different build
still fails; no browser-supplied role or path can select the route. The ordinary
startup guard remains in place: this is not a runnable activation command or
permission to launch a retained real installation.

The actual synchronous worker gate constructs a read-only state handler. It
restricts storage access to trusted extension contexts but never edits stored
values. Startup status checks fixed native state twice, exact clean saved pause,
cookie absence and alarm absence. An explicit trusted resume-page review may
add one fixed verified server-status request, followed by fresh native/browser
readbacks. Its UI reports the current revision/generation **without a ticket**,
and leaves confirmation disabled. Initial sign-in, native pause, initialization,
renewal, state migration, cookie cleanup and accepted-state adoption are refused.

After native context validation, a separate bounded entry helper can reload only
the already-selected, exact local `startup.html` tab when Chromium attempted to
open it before the unpacked extension loaded. It never creates tabs, selects
setup/resume/dashboard URLs, reloads an existing valid extension document, or
changes saved authentication state. It rechecks the current tab and document
before each retry, permits at most three retries per tab in that worker, and
uses only the existing bounded startup rescans. Invalid native context permits
no entry retry. This local loading recovery is not authentication or permission
to start the profile through the ordinary product launcher.

Both a clean legacy paused record and an exact same-build schema-3 paused record
can be inspected, but neither is rewritten. Missing, pending, accepted, malformed,
different-build or unexpected-cookie/alarm state requires administrator review.
An in-flight operation owns the lane. Timeouts do not cancel browser/native API
work: a timed-out worker remains failed, and late completion cannot trigger
another request or change state. The browser's whole-operation wall/monotonic
budget and native process's independent ten-second deadline remain distinct.

This route is under isolated qualification. It does not enable the complete
post-recovery sign-in or unattended-renewal flow, remove guards, change published
installations or constitute physical-display acceptance.

### Unselected document-confirmation handshake

The experimental confirmation component reuses the same fixed native paused
reader without selecting a new native action. Its separate page adapter requires
a trusted review click followed by a trusted, explicitly checked submit. A
one-use random ticket stays in that page/worker only and expires within one
minute on both wall and monotonic clocks, measured from the start of review.
The worker binds it to Chrome's actual top-level extension document and tab,
then performs a second complete server/native/browser review on confirmation.
Saved pause, native fingerprint/revision, selected epoch/build/identity and
server generation must remain equal; a supplied ticket is not server authority.

The worker checks the exact active extension context, reads the tab's status,
then checks the context again. A matching tab ID and URL alone do not prove the
document survived a same-URL reload. An opt-in navigation observer behind the
existing synchronous MV3 gate cancels the selected document on navigation,
retaining only a tab ID and navigation signal, never the destination URL.
Closed/replaced documents fail fresh context reads. Explicit cancellation and
fixed-origin sign-out fence the in-memory handshake. A timeout or late response
cannot revive its lane. An undetectably lost response leaves only the unused,
bounded ticket: it does not permit another review or automatic replay.

The completion sink is deliberately **synchronous, in-process and fixture-only**.
It is invoked at most once after fresh checks, receives no ticket or credential,
and cannot return an asynchronous job or claim an accepted session. The result
is always confirmation-only with `sessionReady: false`. This callback is not a
native grant or a durable approval record. Browser/native snapshots are not a
cross-process transaction or a lease; the future mutation owner must independently
revalidate authority, own issuance and serialize durable sign-out/cancellation.
In-memory sign-out fencing here does not acknowledge native pause, revoke a
session, change browser storage or complete that future durability requirement.

Neither the active worker nor the ordinary resume page selects this component.
Its distinct review/result modes cannot enable the ordinary confirmation UI.
The entire component is included in the canonical build identity and graph tests
enforce its unselected status. Deterministic adapter/DOM fixtures are not real
browser gesture, full continuation or physical-display acceptance. No profile
activation, initialization, cookie mutation, native issuance or renewal is added.

Chrome's [runtime context API](https://developer.chrome.com/docs/extensions/reference/api/runtime#method-getContexts)
provides the active document IDs used for these comparisons; the identity is
never accepted from page-supplied message fields.

### Existing paused-only guard-release route

The native context selects a distinct paused-only worker for this installation.
It does not construct the normal authentication or resume controllers. A trusted
startup request reads the exact clean paused browser record, a read-only native
status, the fixed-origin device-session cookie and the fixed recovery alarm.
Only matching paused state with neither cookie nor alarm produces the explicit
administrator-required message. The worker does not clear unexpected state,
create a session, schedule authentication or initialize a missing record.

Startup hides the ordinary resume link in this role. A directly opened resume
page is inert; an explicit review explains the restriction and leaves its
confirmation disabled. Native checks remain authoritative on every request, so
UI hiding is not a permission boundary. Unguarded installations keep their
existing two-step resume flow.

The internal `BrowserContinuationInspection` provides a **read-only advisory
checkpoint** for a future stopped-installation review. It has no CLI, browser
message, durable journal writer, grant, apply, execute or resume operation.

- Review acquires the managed launcher's stopped ownership and shared private
  profile ownership, then validates the existing canonical release, guard,
  handoff, archive, restored host, runtime and paused native state twice.
- The checkpoint identifies the exact installation and native revision without
  exposing credentials. Its representation omits identity and evidence fields.
- Confirmation accepts only the same in-memory selection in the same inspector,
  within two minutes on both wall and monotonic clocks, and revalidates stopped
  ownership and the same evidence. Copied, expired or changed selections fail.
- Review and confirmation do not change the ledger or recovery evidence, contact
  a server, read or repair Chromium's opaque storage, or permit sign-in.

This checkpoint is not durable consent and must not be serialized or accepted as
authorization by a future mutation. Historical browser acknowledgement is not a
new browser-state observation. Cookie absence is not proof that an undelivered
server session was revoked. Advisory locks coordinate trusted cooperating
processes; they are not protection against root or arbitrary same-account edits.

## Administrator intent journal (internal, not activation)

`BrowserContinuationIntent` adds the first durable step after preflight. It
accepts a fresh trusted **local** confirmation callback, not an advisory
checkpoint, browser message or saved approval. There is no CLI or deployed UI.
The callback displays the exact installation, release, native revision and a
one-use confirmation phrase. Its recorded purpose is narrowly fixed to enabling
a future **fresh browser resume review**, not automatic sign-in or credential
replacement; a broader or different purpose cannot confirm. Cancellation creates nothing; wrong, expired or
failed consent does not create an intent. Both wall and monotonic clocks bound
consent to two minutes, including a final recheck before committing.

The writer holds stopped managed-launcher ownership, shared private-profile
ownership and an **unchanged** native SQLite write transaction. Together these
exclude cooperating launchers, private-input writers and native revision changes
through the consent and commit window. They do not make browser storage or a
remote server part of that transaction and do not protect against arbitrary
root/same-account changes.

The fixed, private `.sdsctl-browser-continuation-intent.sqlite` records the exact
prior release fingerprint, targets, identity, native revision, consent hash and
its own inode. The original guard, release, handoff, archive, native ledger,
credentials, host registration and Chromium-owned storage remain unchanged.
The plaintext confirmation phrase and credentials are not journaled.

| Durable point | Exact read-only confirmation | Normal launch and native requests |
| --- | --- | --- |
| No intent; consent cancelled or refused | No completed intent | Existing paused-only release rules |
| Empty name, prepared row, partial write or any SQLite sidecar | Refused; retain all evidence | Blocked |
| Complete intent commit with unchanged evidence | Confirms only that intent, including after a lost reply | Still blocked |
| Changed evidence, replaced inode, conflicting schema or later native revision | Refused; retain all evidence | Blocked |

The name is created exclusively. Preparation is committed and its parent directory
synced before completion is possible. SQLite rollback-journal mode with
`synchronous=EXTRA` supplies the completion commit/directory synchronization.
An interrupted commit is never retried, rolled forward by the application or
treated as consent merely because a file exists. Confirmation opens read-only,
refuses sidecars before SQLite access, checks exact schema/canonical bytes and
revalidates the original recovery chain. It does not repair an uncertain database.
These durability claims depend on the operating system/filesystem honoring sync;
process-exit tests are not a physical power-loss qualification.

**A complete intent is deliberately not a runnable grant.** Any intent name or
sidecar, including a dangling link, blocks ordinary startup and every normal
worker request. An already-running worker cannot bypass that check. The earlier
advisory inspector also refuses further reviews. Internal exact historical
release confirmation remains available, but cannot authorize launching. No native
revision changes, server request, browser start, service change or sign-in occurs.
Do not invoke this writer on real retained profiles until the successor path has
been implemented and qualified: it intentionally leaves the installation stopped.

## Native archive-plan inspection (not committed recovery evidence)

`inspect_resume_archive` separates structural validation of a native retirement
or reconciliation archive from comparison with the current ledger. It reads
only caller-supplied bytes: no file access, SQLite connection, credential read,
browser action or network request. It reconstructs the exact selected transition
and returns a frozen, redacted `BrowserResumeArchivePlan`, not a retirement
acknowledgement or current continuation permission.

The parser requires bounded canonical JSON with exact fields, the selected
identity/profile/review, valid typed state and approval rows, canonical ordering,
the original two-minute review window and precisely the permitted after-state.
Retirement retains its terminal anchor; reconciliation cannot reinterpret a
matching or pending row as absent. Malformed values, duplicate keys, unexpected
fields, unsupported schemas and substituted selections are refused.

**A valid archive plan can exist even when the native transaction rolled back.**
It can also still describe the old transition after a newer pause. Both cases
remain insufficient for the existing `confirm` methods: those independently
compare the reconstructed after-state with the current validated ledger and
recheck the archive bytes before returning. A plan cannot be supplied as the
review input to those methods. No `ignore_revision` switch or relaxed revision
comparison has been added.

This is the structural building block for the separate historical-chain reader
below, not that complete reader or its commit anchor. Existing release/intent checks,
normal startup and per-request refusal remain unchanged. Nothing in this parser
activates a successor or grants browser/server authority.

## Retained historical chain (internal, not current permission)

`inspect_continuation_history` reconstructs the selected archive, canonical review,
launch guard, original and recovery bundles, supervised handoff, browser paused
acknowledgement and restored native host. It then requires the exact **complete**
paused-release and administrator-intent SQLite records. Their checked bindings
anchor the archive transition: the trusted writers confirmed its committed native
after-state before committing those records. A valid archive, a prepared journal,
a copied journal inode or an unrelated completed record cannot substitute.

The public reader holds stopped managed-launcher ownership and shared private-profile
and archive ownership. It uses fixed filenames, checks current canonical runtime
assets and private-input bindings, verifies the old supervisor has exited, and
rechecks retained bytes and inodes. It does not read Chromium-owned storage,
write or repair files, start a browser or contact the server. Filesystem and
same-account/root trust remain the same as the existing recovery workflow.

Its frozen, redacted `BrowserContinuationHistory` is a distinct result, not a
live retirement, release or intent confirmation. An internal, separately typed
archive binding is used only to reconstruct old wire bytes. **A later native
pause can leave this history valid while all existing current-state confirmations
refuse.** The historical revision and paused mode describe the old transition;
they do not describe current state or imply current authority.

Canonical bundle reconstruction validates the fixed private configuration,
credential syntax, CA certificates, extension public key and exact installed
runtime bytes **without opening the current native ledger**. This breaks the
dependency on current-schema inspection; it does not provide a way around it.
The historical reader separately preserves the bound private ledger inode and
refuses missing/unsafe files or SQLite sidecars. It still validates the complete
release and intent journals read-only.

An unsupported or damaged same-inode native ledger can coexist with valid retained
history. Historical success is deliberately **not a health check or permission to
use that ledger**. Normal profile/bundle/registration checks still validate its
current supported schema and state, and normal startup/requests still stop on the
existing intent marker. Changed private inputs or runtime assets still invalidate
the bound history. This historical reader does not add successor schema support,
migration, a current-epoch selector, an activation writer, a browser action or an
administrator CLI. The isolated native transaction core below is separate.

### Scoped ownership for historical reads

The canonical reconstruction now has two separate internal ownership boundaries.
The public `inspect_continuation_history` contract is unchanged: it must acquire
its own stopped launcher lock and refuses any Chromium Singleton marker. It never
adopts an already-busy lock. An internal caller that already holds a checked scope
can reconstruct within that scope without reacquiring the exclusive launch lock.

The other internal boundary requires a fixed native-worker selection, matching
private configuration, the actual selected Chromium ancestor, and the same busy
launcher-lock inode. Only this boundary permits a running browser's Singleton
markers. Both boundaries pin the browser directory and lock inodes, share the
private-profile/archive directory locks, and repeatedly recheck their bindings.
They never inspect or repair Chromium-owned storage.

An ownership scope is tied to one handoff and one process/parent, expires on exit,
and stays invalid after an observed ownership failure. Returning the original
files or process selection cannot revive it. These are cooperative local ownership
checks, not protection against arbitrary same-account/root Python code.

The live-owner reader has no standalone browser message or CLI. The fixed
read-only continuation route uses it internally; normal startup and authentication
remain blocked. Its result is still historical evidence only. It cannot clear
the intent blocker, prove the current native ledger is healthy, grant a role,
authenticate, or turn a retained result into permission on a subsequent request.
The old recovery supervisor must still have exited; that historical check is
separate from verification of a later live Chromium owner.

## Isolated paused activation core (internal, not a runnable migration)

`browser_device_continuation_native` implements the native transaction portion
only. It is not called by startup, worker dispatch, a browser message, a CLI or
an installed command. The fixture-only stopped-owner adapter and current-epoch
transaction core below share its internal validators.
**Do not call these on a real profile.** Existing
recovery/resume helpers continue rejecting schema 3, and the continuation-intent
marker continues blocking ordinary startup and authentication/mutation requests.
The narrowly scoped continuation read/review route does not remove that blocker.

The core derives canonical candidate manifest bytes from a selected paused
historical after-state. The manifest binds the exact old state and terminal
approval rows, identity/profile, release and intent IDs, retained-history digest,
ledger inode, fresh epoch, two-minute approval window and a confirmation digest.
Its after-state preserves pause and approval history, advances the revision by
exactly one, and records the approved time. No plaintext confirmation, reusable
approval, credential or session token belongs in the manifest.

The retained-history digest binds the previously checked targets, runtime assets
and private inputs. **A supplied history object or manifest byte string cannot
prove those files are still unchanged.** The stopped-owner adapter must
reconstruct that history, collect fresh local consent, exclusively create and
sync the private manifest and its parent directory, and check the actual bytes
and inode under coordinated ownership. This module performs no filesystem I/O
and is not that adapter. A caller-supplied inode is not an ownership proof.

Within a caller-owned, dedicated `BEGIN IMMEDIATE` transaction, the staging
function requires the exact legacy schema-1/2 ledger and canonical table/index
definitions, rollback journaling and `synchronous=EXTRA`. It refuses unknown
schemas, extra tables/views/triggers/indexes, pending approvals and any change
to the selected prior state. The manifest digest and inode/epoch anchor, the
schema-3 marker and the paused revision change are staged together in that same
ledger, together with an empty epoch-approval table and an exact current-state
consistency fence. The internal manifest is now version 2: earlier version-1
fixture manifests/layouts are rejected, not migrated or repaired. No real
schema-3 profile has been qualified for deployment. Success is **uncommitted**
until the owning adapter commits. No second
completion database decides whether activation happened.

Any failure or interruption after DML starts rolls back the **whole** native
transaction. This avoids leaving a partial transition—or SQLite savepoint
rollback bookkeeping—for a caller that catches the error and tries to commit.
If rollback itself fails, the connection is closed rather than left reusable.
The manifest remains retained evidence, not permission to retry the operation.
The adapter must never mix unrelated work into this dedicated transaction.

A separate read-only transaction can inspect the exact anchor and after-state,
including after a lost commit reply. It neither renews expired consent nor
accepts a later revision by ordering. Any subsequent native state/history change
invalidates this exact activation view. That result is deliberately **not current
continuation permission**, does not select a role, and cannot authorize a browser
resume or server exchange. The current-epoch native transactions below are still
missing their owned current-permission and online adapters. Schema 3 cannot be
accepted by the ordinary recovery path until the complete contract is qualified.

Fictional native-ledger tests cover schemas 1 and 2, both archive forms, exact
readback, rollback, injected write/readback interruptions, altered anchors and
manifests, stale state, unsupported SQL shape and old-helper refusal. The namespace
fixtures additionally reconstruct the real retained chain under stopped ownership:
history is checked before native DML, its reader refuses the writer's rollback
journal, and after commit or rollback the same retained history remains readable.
These tests are not physical power-loss durability or headed-browser acceptance.

## Owned paused activation workflow (fixture-only)

`browser_device_continuation_activation` now owns the filesystem and consent part
of that transaction. No CLI, browser message, native role or ordinary startup path
exposes this workflow. Schema 3 still cannot be used by ordinary recovery helpers.
**This is not yet a real-profile migration or permission to sign in.**

The workflow holds stopped launcher ownership, coordinated private-profile and
archive locks, and a dedicated fixed-ledger `BEGIN IMMEDIATE`. Before asking for
confirmation, it reconstructs the complete retained chain and verifies the exact
old native state. It refuses any existing activation manifest or sidecar—even an
empty file, dangling symlink or directory—without adoption, overwrite or cleanup.

Fresh local confirmation is bound to a random epoch and checked against both wall
and monotonic clocks within two minutes. Cancellation creates no manifest and
makes no native change. Accepted confirmation exclusively creates the fixed
private `.sdsctl-browser-continuation-activation.json`, pins its created file
descriptor's inode, syncs the file and parent directory, and rechecks actual bytes,
mode, identity and inputs. Only a confirmation digest is retained, not its text.

After native DML, a scoped **input-only** verifier rechecks retained files, runtime
assets, private inputs, ownership and other journals. It is not a historical or
current native-state reader; the writer validates its own transaction and pinned
ledger separately. The full historical reader remains sidecar-refusing, with no
"ignore my journal" flag. The input verifier expires with ownership and latches
failure: restoring changed inputs does not make that verifier reusable.

All final checks precede the single native commit. Exact read-only confirmation
then reconstructs history and verifies the actual manifest and native anchor.
If the commit reply or final acknowledgement is lost, confirmation may establish
that exact paused transition; it cannot retry the write, renew consent, repair
partial state, accept a later revision or grant ongoing runtime permission.
Partial manifests and uncertain artifacts are retained. Manifest presence alone
also blocks ordinary startup and requests if the intent journal is absent.

Portable tests use real ownership, private files and SQLite, with explicitly
simulated history. Required namespace tests additionally exercise complete
retirement/reconciliation chains, interrupted writes, post-DML input changes,
read-only confirmation and unchanged refusal by old helpers. Fault injection is
not a claim of physical power-loss durability or online browser acceptance.

## Current-epoch transaction core (fixture-only)

`browser_device_continuation_epoch` validates current native SQL separately from
the exact initial paused activation. It has no filesystem opener, server exchange,
CLI, browser action or ordinary runtime caller. A supplied selection or snapshot
does **not** establish current permission, actual ownership or fresh consent.

The selected immutable manifest/anchor, canonical SQL layout and unchanged legacy
terminal rows must match. New approvals live in a separate `browser_epoch_approval`
table and bind the selected epoch, identity, exact native revision, intent, device,
generation and credential/trust digests. A legacy ticket digest cannot be reused
as new consent. Terminal rows are preserved, with a hard 128-row limit and no
automatic pruning. No raw credential, reusable ticket or session is stored.

The singleton `browser_epoch_state` binds the actual state, epoch-approval history
and active-grant digest with a same-ledger consistency hash. A higher revision by
itself is insufficient. This hash is **not a signature, anti-rollback mechanism,
or defense against a malicious same-account/root writer**; private ownership and
fresh file/ledger selection remain independent requirements.

Within a dedicated existing write transaction, the internal stages are:

| Stage | Native effect | Still required outside this core |
| --- | --- | --- |
| Prepare | New epoch-bound row; stopped revision advances; two-minute expiry | Fresh trusted-page consent and reviewed current server inputs |
| Claim | Prepared becomes claimed; claim time and exact snapshot change | Commit before bounded external proof; no automatic replay |
| Complete | Exact claimed row becomes complete; native state becomes active with that grant | Fresh ownership/input/epoch rechecks, verified server proof and separate session exchange |
| Fail | Pending or uncertain row becomes terminal, including after expiry | Adapter must record uncertainty without overwriting cancellation |
| Pause/sign-out cancellation | Cancel pending rows, clear active grant, advance paused revision | Actual browser/server session invalidation is separate |
| Clock correction | Cancel pending rows, advance revision, apply ten-second delay while preserving mode | Trusted clocks and monotonic timing checks in the owned adapter |

Every stage re-reads the exact current SQL view and compares the supplied snapshot,
including phase/history changes that do not change the native revision. Claim time
must not move backward; completion must precede approval expiry and occur within
ten wall-clock seconds of claim. The future adapter must also enforce monotonic
elapsed time. A newer pause, sign-out or clock correction defeats old completion,
even with a newly fetched snapshot. An already completed active grant survives
clock correction, but old in-flight snapshots do not. Each explicit pause advances
the revision even if already paused, invalidating outstanding review snapshots.

Success remains **uncommitted**. Any exception or interruption aborts the whole
transaction; uncertain rollback closes the connection. No second database is
written, no browser storage is altered, and confirmation never replays a stage.
The original paused confirmation remains exact and rejects all later states.

Portable tests use fictional selections with real native archives and SQLite.
The separate namespace fixtures run the stages against complete retained
retirement/reconciliation chains under stopped ownership, with fresh input and
manifest checks. Those are not a current-permission adapter, browser consent,
online server-proof test, sign-out/session-invalidation test or hardware acceptance.

## Owned current-state selector (fixture-only, read-only)

`browser_device_continuation_current` connects actual fixed files, scoped
ownership, the complete retained chain and current native SQL. It does not take
a caller's historical result, saved epoch, approval or manifest bytes as proof.
The trusted installed paths select the fixed complete release and intent journals
and the private activation manifest. Their canonical contents select the retained
handoff/archive targets; full historical reconstruction must validate those
selections before any current-state result is returned.

Stopped inspection acquires its own managed-launcher lock. The separate internal
worker entrypoint derives the directory from the actual selected Chromium
ancestor and revalidates its fixed wrapper, private configuration and busy lock.
It has no caller-selected directory, intent or normal-bundle override. Only that
worker boundary permits the current browser's Singleton markers.

Within one existing-ledger, query-only transaction, the scope pins actual file
identities, checks canonical private bytes and runtime assets, reconstructs the
retained chain, and validates the manifest/anchor and exact current epoch state.
Every inspection checks ownership, inputs, selected records, ledger identity and
all SQLite sidecars both before and after the native read. A reader never adopts
an active native rollback journal. The same scope cannot observe a different
snapshot; later state requires a new owned read. A failure latches, and process,
parent or scope exit invalidates the reader. Normal exit also performs a final
check, so a caught failure cannot make the scope succeed.

The frozen, redacted `BrowserCurrentContinuation` describes **one verified read
point**, including current mode/revision, epoch and state fingerprint. It is
distinct from historical evidence and the exact initial paused-activation result.
It is **not a cached permission lease**, trusted-page consent, server proof,
session or browser role. It performs no clock correction, writes, repair,
credential replacement or network request. The old strict confirmation methods
still reject later native revisions, and ordinary schema-3 startup and
authentication/mutation requests remain blocked even if this reader sees a valid
active epoch. Read-only continuation dispatch does not grant those capabilities.

Portable tests use real files, locks and SQLite with explicitly simulated full
history/browser ancestry. Required namespace fixtures also select complete
supervised retirement/reconciliation chains, read later epoch states, exercise
the live-owner boundary with simulated later ancestry, and inject retained-input
changes after selection. These are not headed-browser, physical-Pi, online
authorization or power-loss acceptance. The separate owned prepare/claim boundary
below does not add permission to this reader. Online checks and runtime-role
integration remain separate work.

### Owned mode and generation observations

The internal reader's `observe()` method adds a redacted
`BrowserContinuationObservation` containing that exact current read point and
the locally approved generation. A stopped state returns `generation=None`:
offline status does not invent a generation or obtain fresh server review.
For an active state, the method selects the exact complete active approval from
the fully validated epoch view and compares its device, credential digest and
trust digest with the actual fixed configuration and private files. The retained
history still requires the original file bytes and identities. A syntactically
valid active row with different approval inputs is refused, not repaired.

The complete scope, current fingerprint and private files are rechecked before
return. Scope/process/owner loss, a native writer journal, same-revision changes
or a changed private file invalidate the reader. The result exposes no secret,
grant digest, cookie, completion token or session-ready field. An internal live
worker helper reacquires the same owned read; no message role or startup path
invokes it. This is **local observation, not online authority**. An active local
approval may still be revoked on the server, or may have no installed browser
session after an interrupted initial exchange.

Complete retained-history tests now pass this actual observation and native
session output into the JavaScript model. When issuance or its acknowledgement
is uncertain, the native active observation cannot promote the browser's pending
record: it still requires administrator review. Browser storage, cookie and page
I/O remain modeled; these checks do not enable or qualify a real continuation.

An in-process HTTP regression also confirms that the existing generation-bound
server verification leaves a current same-generation live request and session
usable, without allocating a token or setting a cookie. Its drainage check waits
for older generations only. This supports later accepted-state verification; it
does not implement unattended renewal or permit replaying initial approval.

### Fixed continuation context (read-only routing)

`browser_device_continuation_context` produces a narrow metadata envelope for
the separate read-only continuation worker. Fixed installed configuration and wrapper
selection enter the actual owned current-state scope; no browser-supplied path,
role, saved epoch or serialized observation selects the read. It accepts only
the current paused or active mode, verifies the exact identity/origin/device,
and returns the selected epoch, state fingerprint, native revision and locally
approved generation. Paused mode carries no generation. It returns only after
the repeated observation, successful ownership-scope exit and final runtime
graph check. A changed input or failed exit yields no successful context.

The paired inert JavaScript parser requires the exact envelope and executing
build/extension identity, canonical HTTPS DNS/IPv4/IPv6 origin, typed bindings
and fixed native host. Its copied, frozen settings and observation contain no
credential, approval, cookie, session-ready result or authority lease. Native
active state alone still cannot promote saved browser pause or pending state
to acceptance. Later requests must reacquire current ownership and state.

This adapter performs no mutation, network call or clock correction. Only the
fixed read-only native route selects it; the normal-role parser still rejects
its separate envelope. Portable tests use real private files and SQL with
simulated history/ancestry; complete retained-chain tests exercise the same
producer with both paused and active epochs. Later browser ancestry in those
chain tests remains simulated. This is not real-browser continuation, a runnable
migration, online authority, fresh user consent or physical-display acceptance.

### Owned active-generation server recheck

The internal `browser_device_continuation_recheck` adapter performs one fixed
verified-HTTPS proof between two owned native observations. It validates actual
live ownership, the original private files, complete retained history, immutable
activation manifest and current full fingerprint before contacting the server.
The supplied expected observation is an exact comparison, not authority. Its
generation must match the actual complete active approval; a latest-generation
lookup is not substituted for that approval.

Native SQLite scopes are closed before the network request. Private-input and
live-launch coordination remain held. The fixed existing verification transport
authenticates the exact approved generation and confirms older request drainage;
no session or cookie is allocated. Before return, a new owned read must match the
entire original observation. A newer pause, same-revision change, private-file
replacement, owner/process loss or invalid/expired clock refuses the result.
Failure, interruption and lost proof never rewrite the grant, advance revision,
repair state or retry initial approval. One object executes at most once and
offers no cached proof or `confirm()` API.

The same conservative wall/monotonic ten-second elapsed budget spans selection,
all native reads and network I/O. It does not interrupt blocked I/O: eventual
native dispatch must retain the separate supervising-process deadline. A success
returns only its actual last local observation, not a browser-ready flag or a
reusable server permission. The server can change immediately after proof.

This is **not unattended renewal or browser acceptance**. Native ACTIVE can
remain after an uncertain initial exchange, so the real browser controller must
still refuse pending state even if this recheck would succeed. No browser role,
message, CLI, cookie handler or ordinary schema-3 startup invokes the adapter.
Portable cases simulate historical ancestry while selected cases use real TLS;
complete namespace chains cover successful proof, refusal and concurrent pause.
They do not qualify real-browser or bench-Pi activation.

## Owned native cancellation (fixture-only)

The separate active-state server recheck described below does not invoke these
mutations. Routine verification must preserve the complete native fingerprint;
it must not replay initial prepare/claim or rewrite a scheduling/backoff field.

`browser_device_continuation_cancel` adds narrowly scoped **pause** and
**backward-clock correction** operations. Separate stopped and live-worker
entrypoints retain the same fixed-file and actual-owner requirements as the
current reader. There is no browser action, native dispatcher, CLI or normal
schema-3 startup support. Do not invoke this on a retained real profile.

Each attempt reconstructs the complete historical chain, actual immutable
manifest, private inputs and exact current SQL under coordinated ownership and a
dedicated `BEGIN IMMEDIATE`. A supplied current snapshot is only an optimistic
comparison value: its complete binding must match the reconstructed current view.
A changed revision, same-revision claim, epoch, input, file identity or owner
refuses the write. The read-only selector has not gained a write switch.

Pause cancels pending native approvals, clears the active native grant and
advances the paused revision. If the wall clock is behind the stored observation,
the explicit pause first stages clock correction and then pause in the **same
transaction**: two revision advances, one commit, no intermediate visible grant.
Explicit clock correction alone requires a real backstep; it cancels pending
approvals, advances revision, applies the existing ten-second delay and preserves
mode (including an already completed active grant). It is not automatic status
maintenance. Any further wall/monotonic backstep, invalid clock or ten-second
attempt-window expiry before commit refuses the whole write. These elapsed checks
are not an independent process-kill deadline.

After staging, the adapter rechecks scoped inputs, retained records, runtime,
owner, actual ledger identity, the exact same-connection after-state, transaction
policy and clocks before a single commit. The full historical reader and
read-only selector still refuse active SQLite sidecars; only the dedicated
writer validates its own rollback-mode transaction. Exceptions and interruptions
abort the entire transaction. Failed rollback closes the connection rather than
leaving reusable staged changes.

An attempt object is one-use even after failure. Before committing, it retains
the exact expected after-state and selected file/owner bindings privately in
memory. Final acknowledgement uses a new query-only transaction and must match
that exact state. A lost commit reply may be followed by **read-only confirmation
on the same attempt object**, never another cancellation write. A later state
that merely happens to be paused is not a match. A new process/object cannot
reconstruct this expected state from a caller-supplied receipt.

`BrowserCancellationState` describes an exact current after-state; it is not a
durable operation-provenance record, permission lease or proof of session
invalidation. If the object/expected state is lost, preserve the uncertainty for
review instead of replaying the write. There is no second completion journal,
automatic repair, pruning, browser-storage change, server exchange or credential
replacement. Native cancellation does **not** revoke browser/server sessions or
prove that sign-out completed. The owned approval and verification fixtures below,
and unimplemented session handling, are separate from native cancellation.

## Owned prepare and claim (fixture-only, one process)

`browser_device_continuation_approval` supplies a separate **live-worker-only**
adapter for preparing and consuming one native approval. It has no stopped
administrator entrypoint, dispatcher action, CLI, external transport or active
grant completion. Normal startup and authentication/mutation requests still refuse
schema 3; the fixed read/review route cannot invoke this approval adapter.
Do not run this adapter against a retained real profile.

Preparation reconstructs fixed installed paths, actual live ownership, complete
history and current SQL. Its expected snapshot is only an exact comparison value.
The adapter reads and validates the actual fixed configuration, credential and
CA files, pins their bytes/inodes and derives their digests itself. Browser-supplied
hashes or a constructed proof record cannot stand in for those private inputs.

The trusted in-process callback receives a fresh immutable context containing
the exact current state, intent and reviewed generation. Only that same context
object may be acknowledged; a boolean, copied context or earlier receipt is
refused. **This callback is a fixture boundary, not an implementation of browser
gesture verification or fresh TLS server proof.** The eventual trusted bridge must
establish those facts. Generation is bound as reviewed input, not silently replaced
by the newest generation. No SQLite transaction is held while the callback runs;
the actual owner/private-input coordination remains held.

After acknowledgement, the adapter reconstructs history/current SQL again inside
its own `BEGIN IMMEDIATE`, rechecks the complete expected state and private files,
enforces `next_at` and backward-clock refusal, and stages preparation. It generates
fresh private random material and persists only its digest in the existing epoch
approval row. Neither the random material nor a reusable ticket is returned.
Legacy rows stay unchanged; the existing 128-row bound is not pruned or bypassed.

A successful prepare return permits **one claim on that same process/object**.
Claim reacquires actual ownership and validates the original file/directory/lock
bindings and exact prepared fingerprint in a new dedicated transaction. It changes
the phase without advancing revision; revision equality alone is therefore not
enough. A newer pause, changed input, copied inode, competing claim or different
owner/process refuses the claim. Both prepared and claimed states remain stopped,
without an active grant, server permission or browser session.

The same-process lifetime uses finite, nondecreasing wall and monotonic clocks,
starting conservatively before the review and expiring at 120 seconds. Each phase
also enforces a ten-second elapsed window before commit and after exact readback.
These checks do not implement an independent process-kill deadline. In particular,
an object-local timer cannot span the old separate `sendNativeMessage` prepare
and commit calls. This adapter **does not serialize/adopt an attempt across those
processes**. Eventual request-role integration must preserve one native attempt
and the independent worker deadline, with fresh trusted-page consent verified
before it runs; it must not expose this prototype through the old message pair.

Each phase stages and commits once, validates exact SQL and scoped inputs, and
reads the result in a separate query-only transaction. Interruptions roll back the
whole native write; failed rollback closes the connection. An uncertain reply,
failed readback or expired phase disables further progression. Same-attempt
`confirm` may describe the exact staged after-state, but never restores that gate,
replays the write or starts external proof. A new process/object cannot adopt a
pending row. Readback is not durable operation provenance or a permission lease.

The separate verification fixture below supplies exact-generation proof and
post-network claim comparison. Fresh-page consent and session handling remain
required before a real continuation path can be enabled. This prepare/claim adapter
does not call `_stage_complete`, relax legacy schema acceptance, edit browser
storage, exchange sessions, replace credentials or repair uncertain profiles.

## Owned server verification (fixture-only, no browser session)

`browser_device_continuation_verification` creates its own approval internally,
requires successful prepare and claim returns, and performs one fixed-context
HTTPS verification. It accepts no caller-provided approval object, ticket, server
proof, transport callback, credential/trust digest or requested permission role.
The supplied current snapshot is comparison input, not authority. The fresh
in-process review callback still simulates browser consent; no trusted browser
gesture or native-message integration is established by these fixtures.

One finite, nondecreasing wall/monotonic timer bounds the **whole** operation to
less than ten seconds, including review, prepare, claim, proof and completion.
This does not interrupt blocked I/O. Eventual native dispatch must also preserve
the existing independent supervising-process deadline; it cannot renew the
deadline per phase or spread the private attempt across the old message pair.

Before contacting the server, the adapter reacquires the actual live owner and
rechecks the exact acknowledged claim, fixed inputs and original bindings. It
closes every native SQLite transaction before the network call while retaining
shared private-input coordination. It uses the existing verified TLS transport
to request the exact reviewed active device generation and requires confirmed
old-request drainage. It cannot resume a server-side pause or issue a session.

After proof, a dedicated native write transaction reconstructs complete retained
history, rechecks the actual files/owner and compares the **original exact claim**
with current SQL. It must not substitute the latest snapshot as approval. A newer
pause, same-revision state change, changed inputs or expired/backward clock blocks
completion. The adapter stages completion, validates staged state and inputs,
commits once, and confirms the exact after-state in a separate read transaction.

A transport refusal or malformed proof permits only best-effort terminalization
of that exact claim. Changed state/ownership/inputs or an expired operation may
prevent even that cleanup, leaving the claim for review. Interrupts and completion
commit/readback failures never trigger an automatic second write to mark it failed.
A lost completion reply may describe an already committed native ACTIVE state;
exact readback cannot replay proof, create a session or renew server authority.
An unchanged ledger after an uncommitted prepare does not permanently forbid a
separately initiated fresh review; the failed attempt itself remains consumed.

Server verification is a point-in-time observation, not a transaction shared
with the remote server or permission cached indefinitely. Later session exchange
must independently enforce exact-generation authority and handle session-response
loss. This adapter returns no session/token, installs no cookie, changes no
Chromium storage and does not expose activation or normal schema-3 dispatch.
Real retained profiles, Home Assistant and both bench Pis remain out of scope.

## Owned initial session issuance (fixture-only, no browser installation)

`browser_device_continuation_session` composes a new owned verification attempt
internally and consumes its successful return once. No supplied verifier, proof,
completed grant, transport or result can serve as a session-issuance ticket. A
lost verification acknowledgement prevents issuance even when exact readback
later reports native completion. A new object cannot adopt an old ACTIVE grant.

Before the one fixed exact-generation session request, it reacquires the
original owner and rechecks the exact completed state, retained history and
pinned private configuration/credential/trust. Native SQLite scopes are closed
during the request, while shared private-input coordination remains held. The
server independently reauthenticates the exact generation; earlier verification
is not a transaction with the server or authority to substitute its latest state.
A newer pause or changed files/owner/state prevents returning the session.

One wall/monotonic budget spans the entire operation, including consent,
verification and issuance. All elapsed time is conservatively subtracted from
the returned session lifetime. The existing independent native supervisor's
ten-second deadline is still required before wiring dispatch; these elapsed
checks do not interrupt a blocked socket.

The private result contains an in-memory bearer only once, with redacted
representations. This does not make the bearer single-use for HTTP requests.
No token is saved in the ledger, history, manifest, diagnostics or command line.
Exact confirmation reports native state only and never retrieves a token,
repeats issuance or establishes browser readiness.

A refused, interrupted, late or lost session response does not trigger an
automatic retry, a failure-state rewrite or a second approval. Native ACTIVE
may remain after uncertainty, and a server-issued token may still be valid.
Neither readback nor the absence of a browser cookie proves remote revocation.
Ordinary schema-3 dispatch remains blocked; no retained pending browser record
may treat this native state as permission to retry initialization or sign in.

The adapter adds no session-claim database or schema relaxation and does not
call the legacy schema-1/2 recovery authenticator. Browser clean/pending-state
selection, trusted gesture integration, cookie installation/protected-page
confirmation, renewal and restart roles remain separately unimplemented
boundaries. No real retained profile, Home Assistant or bench Pi was used.

## Browser acceptance record and restart contract (inert core only)

`browser_device_continuation_state.mjs` defines a strict browser-side acceptance
state machine and read-only restart classification. It is included in the
canonical build graph but is **not imported by the active worker, registered as
a listener, or called by native dispatch**. It performs no storage, cookie,
network, timer or service operation. Its supplied observations are fixture
inputs, not proof of actual browser consent, file ownership, a cookie, or a page.
The one-shot installation I/O adapter below now composes this core. Fixed-role
consent/native/probe dispatch and complete browser/native qualification remain
unfinished; the active worker does not import either installation component.

### Decision: separate browser acceptance from native approval

The browser record describes whether this trusted worker completed installation;
the existing native epoch ledger describes the approved device and exact current
state. This design does **not** add a second native completion database or pretend
that one transaction covers SQLite, Chromium storage and the remote server.

For this browser format, the selected identity, activation epoch and canonical
build are fixed. An accepted record binds the exact completed native fingerprint,
revision, reviewed server generation and installed cookie fingerprint. These comparison values are not bearer
tokens, signed permissions or substitutes for freshly reconstructed native state.
The proposed ordinary request adapter must recheck original approval-bound
credential/trust files, fixed live ownership and exact current server generation
on every exchange. It must not accept browser-provided fields as those proofs.

Only the fixed trusted MV3 worker may load the actual trusted-context storage and
interpret it. The core cannot establish that caller boundary: a supplied object
or fabricated call sequence is not evidence. Same-account/root malicious code
is outside the existing ownership threat model. Before exposure, the real worker
must prove fixed build/context selection, active document/gesture binding,
single-queue ownership and refusal of foreign/content-script/cached callers.

| Browser record | Native observation | Restart classification |
| --- | --- | --- |
| Selected clean pause | Matching selected paused native state; generation may be unknown offline | Paused; no sign-in |
| Initial installation pending | Any state, including ACTIVE | Administrator review; never replay initial issuance |
| Accepted | Exact selected active fingerprint/revision/generation | Fresh verification required; **not session-ready** |
| Accepted | Paused, changed, missing or conflicting native state | Administrator review; no automatic adoption |
| Missing, legacy, malformed or different-build record | Any state | Administrator review; no initialization or migration |

This deliberately separates uncertain first installation from ordinary outage
recovery of an already accepted display. A later bounded renewal/restart adapter
may use the accepted classification only as an additional precondition for fresh
native/server/browser verification. It must never convert classification into
an issuance ticket or dashboard readiness. The same-build unattended path remains
the target; cross-build migration and hardware boot acceptance are separate gates.

Only read-only **paused** classification permits an explicitly unknown (`null`)
server generation, so offline paused status does not force a network request.
Fresh initial consent/issuance, pending/accepted records and active observations
still require a positive exact reviewed generation. Unknown is never zero,
latest-generation permission or a sign-in fallback.

### Fresh paused server review (internal, not consent or session issuance)

Offline paused context deliberately has no current server generation. The internal
`_BrowserWorkerPausedReview` obtains a fresh generation through the fixed verified
HTTPS device-review endpoint without changing native state, approving an attempt
or issuing a browser token. It accepts installed configuration and worker selection,
not browser-provided state, proof, role or filesystem selectors.

One process owns the full read/network/read operation: private input coordination
and browser ownership remain pinned, while SQLite transactions are closed during
network I/O so a newer native pause can commit. The adapter checks the complete
paused-state fingerprint before and after review, exact device identity, active
server generation, drained response, both elapsed clocks, scope exit, selected
journals and executing build. Any observed change or lost response refuses the
result without repairing state. The same object cannot replay a review.

The result is one online review point, **not consent, a lease or an issuance
ticket**. A fresh trusted browser gesture and the initial-session adapter's own
exact-generation verification are still required later. The ten-second elapsed
budget does not interrupt blocked I/O; the fixed native dispatch retains the
independent supervising-process deadline. The fixed read-only native route
now invokes this review without enabling any continuation mutation or sign-in.

Tests exercise private SQL/files, changed inputs and same-revision fingerprints,
newer pauses, malformed/refused or lost proof, deadlines, scope-exit changes and
actual verified DNS/IP TLS. Separate retained-history fixtures cover both recovery
paths. Later browser ancestry is still simulated in those fixtures; these passes
are not complete browser continuation or physical-display acceptance.

### Accepted cookie identity (inert comparison, not permission)

An accepted native binding alone cannot identify the cookie installed by that
browser attempt. Another same-origin display session could satisfy the generic
protected-page check, so the unreleased browser record also requires a
`cookieFingerprint`. It is null while paused or initial-pending, and a SHA-256
digest only when accepted. Older unbound experimental records are refused;
there is no automatic adoption, migration or repair.

The inert `fingerprintContinuationCookie` helper uses Web Crypto to hash a
domain-separated, fixed-order snapshot containing the selected canonical HTTPS
origin and exact token, scope, flags, cookie store and expiration. The bearer
itself is never saved in the browser record. Changing the token, expiration,
scope or origin changes the comparison or is refused. The trusted controller
must hash actual cookie readback after installation, supply the digest only
after the same-cookie/protected-page/native checks, and freshly hash/recheck it
again before accepting final persistence. The pure state core's supplied digest
is still a modeled adapter input, not browser provenance.

`cookieMatchesContinuationRecord` is read-only and returns false for an unbound
record or changed/malformed cookie snapshot. Even true means only matching
comparison bytes: it proves neither the cookie is still in Chrome nor current
expiry, native/server authority or protected-page access. Those remain separate
checks before readiness or bounded renewal. It never authorizes cookie removal;
Chrome has no compare-and-swap cookie deletion, and failure cleanup must not
delete or replace a possibly foreign cookie. Neither helper reads or changes
actual browser storage/cookies, registers a listener or enables a native action.

Tests compare actual Web Crypto results with an independent SHA-256 oracle and
feed them into the inert acceptance core for DNS, IPv4 and IPv6. They cover
foreign-token and expiration replacement, wrong scope/store, old unbound state,
final-digest mismatch, malformed fields and snapshot changes during hashing.
These are data-contract tests, not actual-browser continuation qualification.

### Initial installation ordering and acknowledgement cuts

After fresh document-bound consent, one process-local attempt produces the
pending browser record. Only its exact successful persistence/readback permits
progression to the owned initial native request. A failed or out-of-order step
permanently invalidates that in-memory attempt; supplying a later matching
snapshot cannot restore the gate. No core method repeats issuance or native writes.

The native result must match the reviewed generation and expected two revision
advances (prepare and complete). Claim changes phase and fingerprint at the same
revision; three phases do not mean three revision increments. Cross-language
fixture tests feed the actual owned native result from both retained-history
paths into the browser core, rather than inventing its final revision. The result
must have a changed fingerprint and an exact fresh active native observation.
The token is handed privately to the owning
cookie adapter, never included in the saved browser record or comparison request.
It must produce the fixed secure, HttpOnly, host-only, SameSite=Strict cookie in
the normal cookie store, with the exact token and conservative expiration.
Browser expiration rounding down is allowed; extension or later replacement is
not. The full browser-attempt elapsed time is subtracted conservatively in
addition to the native operation's own lifetime deduction.

The owning isolated top-frame probe must select and bind its tab, document and
fresh ticket, verify the fixed protected display route, and recheck the same
cookie and unchanged native state. Only then can the core produce an accepted
record. Its final successful persistence/readback and native/time checks allow
the current in-memory attempt to report readiness. No later token getter exists.

| Interrupted boundary | Durable browser outcome | Required treatment |
| --- | --- | --- |
| Before pending commit | Existing paused record, or uncertain pending write | Never issue without successful exact pending acknowledgement |
| After pending, before accepted commit | Pending, even if a server session was issued or a cookie installed | Retain evidence; no initial replay or automatic authentication |
| During accepted write | Pending or accepted; acknowledgement may be lost | Current attempt remains unconfirmed; no repair/rewrite to infer success |
| Accepted write committed, acknowledgement lost | Accepted was written only after verified installation | Restart still requires fresh native/server/browser verification; never repeat the initial request |
| Newer pause/sign-out or changed epoch/build | New intent/state must win | Invalidate the attempt and refuse stale completion; no cookie cleanup claim implies remote revocation |

The accepted browser write is the browser acceptance commit point, not its later
UI acknowledgement. A matching accepted record after restart is not proof that a
specific lost caller received its response, nor does it recover that caller's
token. The future owning adapter must serialize actual storage writes with
pause/sign-out and deal explicitly with late writes/cookie calls; this inert
core does not supply a Chromium compare-and-swap or an I/O cancellation mechanism.

Dual finite wall/monotonic checks bound the in-memory attempt to strictly less
than 45 seconds, starting **after** human consent. Every subsequent sample must
be nondecreasing, and cookie/session life must retain more than 30 seconds.
These elapsed checks do not interrupt a blocked browser API or native child.
The actual adapters still require bounded browser calls and the independent
ten-second native supervisor. The existing legacy resume protocol is unchanged.

Deterministic Node tests cover exact ordering, interruption/restart cuts,
invalidation at every stage, both clocks, stale native bindings, cookie scope and
expiration, selected probe/document mismatches, immutable snapshots and redacted
failures. DNS/private IPv4/IPv6 origin cases here test **pure data contracts**, not
real browser cookie representations, TLS interoperability or Pi acceptance.

### Document-bound browser probe (experimental adapter)

The separate `browser_device_continuation_probe.mjs` adapter exercises real
browser I/O but is **not imported by the active worker**. Its fixed selected
HTTPS origin cannot come from a page request. It creates one temporary display
tab, obtains its document identity from Chrome's actual `MessageSender`, and
targets subsequent messages to that exact document rather than adopting any
later top-frame document at the same URL. The isolated content script performs
one bounded same-origin request to `/auth/session`, validates the exact
display-only response, and sends only the redacted result back to its worker.

The probe has one open/verify lifecycle and a whole-attempt wall/monotonic budget
of less than 15 seconds. Cancellation invalidates pending work synchronously;
a tab created after cancellation is still owned and receives a close request.
Navigation after document selection, missing replies, malformed content, changed
sender metadata and expired lifetime refuse completion. A Promise race alone is
not treated as API cancellation. Cleanup targets only the tab created by this
attempt; cleanup failure is not reported as successful deletion.

This component neither installs/removes cookies nor reads/writes acceptance
storage, grants native permission or issues a session. Its result is a
point-in-time page observation, not a live-document lease. The future owning
controller must still compare the actual cookie and exact native state before
and after verification and serialize final acceptance with pause/sign-out.
Missing or initial-pending browser state cannot be promoted by this probe.

The experimental `document-probe` fixture in `audit_browser_recovery.mjs` uses
the existing actual-native authentication fixture to obtain a fictional session
without driver cookie injection. It checks successful document-bound access,
replacement-document refusal, cancellation during a pending browser request,
owned-tab cleanup and unchanged native state, browser storage and cookie.
It also selects a probe before the ordinary fixture sign-out and requires its
specific refusal afterwards, a 401 protected route, no cookie/session issuance,
and unchanged paused native/browser state. An unrelated JavaScript error does
not satisfy the negative checks; subsequent browser restart stays paused.
The sign-out comparison waits for the completed content-script acknowledgement
and exact clean paused browser storage; native pause and cookie absence alone
can precede that final save. The probe must not change the completed state.
It is not the new continuation controller, installed-wheel or physical-Pi
acceptance. Its loopback IP/DNS/IPv6 cases require normal sandboxing and verified
TLS; unavailable sandbox support is a failed prerequisite, not a skip or a
reason to disable the sandbox.

## One-shot browser installation I/O (fixture-only)

`browser_device_continuation_install.mjs` composes the acceptance core with
actual Chrome storage and cookie calls. It is in the canonical graph but **not
imported by the active worker or exposed through native dispatch**. It adds no
UI action, administrator command, launch permission or background renewal.
Construction is inert. Only a future fixed-role worker holding fresh
document-bound consent may construct and run it; injected native/probe functions
and a supplied review object are internal adapter inputs, not authority.

One object owns at most one run. It checks exact clean paused storage, cookie
and alarm absence, and matching paused native observations before writing. A
clean legacy pause may become schema-3 `initial_pending` directly; it is never
rewritten to an intermediate clean schema-3 pause. There is no initialization
of missing records, adoption of pending/accepted records, or automatic migration.

The pending write must resolve **and** match actual whole-storage readback before
the one initial-session request. Changed native state, cookie/alarm presence or
changed saved bytes prevents issuance. After issuance, the adapter checks actual
cookie installation/readback, hashes its exact scope and value, invokes the
owned document probe, and rechecks saved/native/cookie state around the protected
page observation. Acceptance requires the accepted write, actual readback and
final native/cookie/hash/alarm checks. Only the fingerprint, never the bearer,
is saved in the browser record. Object-key ordering is not treated as a state
change: Chrome may reorder keys when saving and returning objects.

One 45-second wall/monotonic budget bounds the whole browser run. Every awaited
API return is checked before its successor may start. Failure or synchronous
invalidation permanently consumes that object, aborts the owned probe and
refuses new runs; it does not enqueue another attempt. The native supervisor's
independent ten-second deadline is still required by the future native binding.

**Timeout is not cancellation of a Chrome API call.** A late pending write may
still leave a pending record; a late cookie installation may leave an installed
cookie beside pending state; and a lost accepted-write acknowledgement may leave
accepted state. None triggers a retry, cookie removal, native pause, state repair
or retrospective success. On restart, pending requires administrator review;
accepted still requires fresh verification and is not itself session-ready.

Chrome storage/cookies have no compare-and-swap transaction covering native SQL,
browser storage and server state. Read/check/write/read catches observed conflicts
but is not an atomic transaction or a lease. The future active worker must own a
single serialized mutation lane and independently implement fresh consent,
durable sign-out/cancellation fencing, accepted restart and renewal. This adapter
does not yet provide those boundaries or permit real-profile activation.

Deterministic tests cover every modeled browser/native/probe API boundary before
and after acknowledgement, lost/late writes, changed state/cookies/alarms,
clock reversal/expiry, concurrent invocation refusal and restart cuts. A separate
`audit_browser_continuation_install.mjs` fixture uses actual sandbox-enabled
Chromium storage and cookies in fresh disposable profiles, but **models native
issuance and protected-page observations** and seeds a fictional clean pause.
It makes no server request and uses no native host or real credential. Healthy,
late-pending, late-cookie and late-accepted cases check persistence across actual
browser restart, refusal of repeat initial installation, no saved bearer and no
renewal alarm. These are browser-I/O checks, not real sign-in, verified TLS,
generated-package, physical-display or power-loss acceptance.

## Selected probe ingress (unconnected worker-gate capability)

The experimental worker gate can now prepare a separate, fixed-origin probe
channel after trusted native-context validation and before opening its event
gate. This method is not called by the active worker or any native role. It
does not select authority from page messages, issue a session or enable resume.

The real Chrome listeners are still registered synchronously before the first
native await. The selected channel delegates to one owned message listener and
one navigation listener; a probe can attach/remove these delegates after the
gate opens without late registration of actual Manifest V3 listeners. Ordinary
role handlers do not receive probe results, and the default worker admits no
probe messages. Selection is one-time and cannot change the fixed origin.

The channel accepts only the exact selected/result schemas, a bounded ticket,
the configured HTTPS origin and display route, and Chrome's active top-frame
document/tab identity. The probe still performs its own ticket and selected
document checks; ingress acceptance is not a successful protected-page proof.
Navigation-away signals reach the owned probe without retaining unrelated tab
URLs. Pending events share the existing 64-event bound and 12-second worker
startup deadline. Failure disables dispatch; no late delegate can revive it.

Node tests join the actual probe and gate, including late delegate attachment,
wrong origin/document/schema, queue overflow, navigation, ownership removal and
failure. The `gated-document-probe` scenario in the isolated browser recovery
harness joins actual Chrome ingress with verified loopback TLS, the existing
native/ASGI fixture and protected-page fetches. Its initial session still comes
from the older fictional recovery fixture, including driver-seeded recovery
state, not a new continuation grant. It is not installed continuation, consent,
accepted-restart, physical-display or power-outage acceptance.

## Remaining selected successor path, not implemented end to end

The next candidate must preserve the existing identity for same-device
continuation and use a separately reviewed durable authorization epoch. A new
profile or identity is not an interchangeable workaround: enrollment, credential
generation, extension identity and old-session invalidation have different
semantics. Revoked-device replacement remains its own enrollment operation.

| Checkpoint | Required evidence | Does not yet establish |
| --- | --- | --- |
| Recovered and paused | Existing complete release and unchanged historical chain | Permission to change the bound ledger |
| Administrator intent recorded | Fresh exact consent and a durable intent bound to that chain | Native activation, browser consent or server authorization |
| Successor activated (isolated native core only) | Exact completed intent plus a separately validated durable successor epoch | A runnable migration, browser consent or an installed session |
| Browser resume reviewed | Current clean pause, trusted-page gesture and exact native revision/generation | An installed or usable session |
| Fresh session confirmed | Verified current server authority, completed native/browser exchange and protected-page access | Earlier lost sessions revoked or a power-outage qualification |

Before a real-profile activation writer is enabled, the design must answer all of these:

1. **Preserve history while describing the successor.** Never rewrite the old
   receipt to match current state. Historical recovery validation and current
   runtime permission must be distinct, without weakening the default guard.
   Every normal launch and native request must select the same exact completed
   successor. Missing, partial, stale or conflicting successor evidence blocks
   mutation, including through cached workers.
2. **Specify activation crash states before adding its writer.** Bind selected old evidence,
   expected revision, runtime identity, consent and the intended next state.
   Define synchronization order, commit points and exact confirmation after every
   interruption. A lost reply must never automatically repeat a mutation, issue
   replacement consent or reinterpret a prepared operation as complete. The
   intent journal implements preparation/confirmation only; it does not implement
   the successor's native activation or a consumable runtime permission.
3. **Serialize private-input changes.** Coordinate credential/configuration
   replacement with this transition and native approval handling. Shared read
   locks alone are not a transaction across credential files, SQLite, Chromium
   and server administration. Rotation stays separate, uses a checked private
   handoff and never clears pause just because a new file exists.
4. **Require fresh authority and consent.** Restored local host registration does
   not resume a server-side pause. A display credential can verify current
   authority but cannot perform administrator resume. Browser permission still
   requires a fresh trusted gesture; a later sign-out or native pause invalidates
   an older approval. No ordinary startup, manual login or recovery ACK implies
   this consent.
5. **Keep uncertain exchanges stopped.** Verify the exact active generation and
   old-request drainage, then separately establish a new session over verified
   TLS. An issued session with a lost response remains a server-side concern;
   cleaning browser storage does not revoke it. No credential, approval ticket
   or session token belongs in the continuation journal or public diagnostics.

Canonical HTTPS DNS names, private IPv4 and IPv6 origins remain supported. Neither
internal DNS nor a reverse proxy should become a requirement, and certificate
verification must not be bypassed for an IP-address installation.

## Proposed activation contract: history is not current permission

This section specifies the complete implementation boundary. The isolated native
core and fixture-only owned adapter above implement the manifest and single-ledger
paused anchor/view. The fixture-only epoch core adds current SQL and approval
transactions; the owned prepare/claim fixtures above add actual file/ownership
checks but not verified browser consent. The owned verification fixture adds
exact-generation HTTPS proof and native completion. Owned initial issuance and
the inert browser acceptance core are separately qualified fixtures. **Real
browser consent, session installation and request-role integration are not
implemented end to end**.
The read-only selector above supplies the file/ownership/current-SQL read
boundary, not a mutation lease or browser-facing permission.
It does not change the stop condition imposed by a complete intent. Implement
and qualify the whole selected path before enabling any part on a real profile.

### Separate the evidence types

The current release confirmation reconstructs the handoff, guard and native
retirement/reconciliation chain and compares it with the **current** paused
ledger. That comparison is correct for the existing paused-only release. It
must not be relaxed to accept `current_revision >= released_revision`, or replaced
with a caller-selected `ignore_revision` flag.

The successor needs two distinct internal results:

- **Historical recovery evidence** reconstructs the exact prior transition from
  the retained canonical archive, guard, handoff acknowledgement, restoration,
  release and administrator intent. It verifies their bindings and hashes,
  including the recorded prior native state, without claiming that this is the
  current ledger state. An archive that merely describes a possible transition
  is not by itself proof that the transition committed. It must be anchored by
  the complete release/intent chain validated before successor activation.
- **Current continuation permission** additionally verifies the exact committed
  successor epoch and current native state in one read transaction, plus the
  unchanged runtime and private-input bindings. Only this result may select the
  future resume-review role. Historical evidence must not be accepted where
  current permission is required, including by a cached worker.

Existing release, intent, maintenance and handoff `confirm` methods remain strict.
The separately named historical reader implements the first result only; it does
not change what their successful results mean. Neither result proves current browser
storage, server authorization, drained requests or an installed session.

### One authoritative activation commit

The proposed design uses an exclusively created, immutable activation manifest
and an activation anchor committed **inside the existing native ledger**. It
must not require successful final writes to two independent SQLite databases to
decide whether activation happened.

1. With the browser stopped, hold the launch lock, coordinated private-input
   ownership and the native write transaction. Revalidate the exact complete
   intent, unchanged paused revision and complete historical chain. Refuse an
   existing activation artifact or anchor; never adopt or overwrite one.
2. Derive the only permitted plan from that validated state: retain the device
   identity, retain pause, advance the native revision exactly once and enable
   only a later fresh resume review. Bind the plan to the intent and release
   digests, exact targets, runtime/private inputs, native ledger inode, old and
   proposed revisions and a fresh authorization epoch. No credential, cookie,
   session token, reusable approval or plaintext confirmation belongs in it.
3. Create the private manifest exclusively, write its canonical bytes and sync
   the file and parent directory. Check its exact bytes and inode and revalidate
   the selected state. A file that exists but was not fully synced cannot be used
   as an activation result.
4. In **one native SQLite transaction**, commit the manifest digest/binding and
   successor epoch together with the planned revision change. The native state
   remains paused; no old approval becomes usable. This transaction is the sole
   activation commit point. There is no later "complete" file write that grants
   permission or repairs the outcome.
5. Return success only after exact read-only confirmation of both the immutable
   manifest and committed native anchor. A lost reply requires that confirmation,
   not another apply call. Confirmation never inserts a missing anchor, completes
   a prepared artifact or changes pause.

The native schema version, migration and synchronous-commit behavior require
implementation review before step 4 exists. Reject unsupported schema/journal
modes without automatic conversion; qualify any explicit migration in the same
transaction as the anchor. In particular, an older worker must fail closed on
the new schema. Do not silently change ordinary unguarded installations. Sync
claims remain conditional on the filesystem honoring them, not merely on a
successful process-exit test.

### Interruption decisions

| Observed state after an interruption | Exact confirmation | Launch or mutation |
| --- | --- | --- |
| Complete intent; no activation artifact or anchor | No activation | Blocked by intent |
| Empty, partial, unsafe or unsynced manifest; no anchor | Refused | Blocked; retain all evidence |
| Complete manifest; native transaction never committed | Not activated | Blocked; do not replay or fill in the anchor |
| Native transaction committed but reply was lost | Confirm only the exact matching epoch, plan and paused post-state | At most eligible for the separately implemented resume-review role |
| Anchor without its exact manifest, conflicting epoch, changed identity/input/runtime or replaced inode | Refused | Blocked; no fallback to the old release |
| Later native revision after confirmed activation | Do not report the old paused post-state as current | Revalidate current epoch and state; revision ordering alone grants nothing |

An exact historical activation result may later be useful to an administrator,
but it must be a different result from permission to proceed now. A newer
sign-out, pause, rejection or pending/uncertain operation still controls current
behavior. A rollback to an old release, an earlier intent or an earlier native
ledger is not a supported way to recover a conflict.

### Wire current permission before exposing the writer

Ordinary launch and every native request must select the same exact completed
epoch from fixed installation paths. A missing selector, unsupported worker or
partial combination must keep the present stop behavior. Do not expose a writer
that leaves users dependent on an unimplemented selector to recover their display.

The first permitted successor role is **resume review while paused**, not ordinary
authentication. It must freshly observe the clean browser pause and absence of
unexpected session/alarm state, require the existing trusted-page gesture and
bind new native approval to the selected epoch and current revision. The
administrator's local intent is not browser consent. Existing approvals,
restoration acknowledgements and automatic startup cannot substitute for it.

Any later online flow must separately verify current server authority and
generation over verified TLS, enforce server-side administrator resume when
required, handle old-request drainage and account for a session whose response
was lost. Credential rotation remains a separately authorized operation. These
conditions apply equally to canonical DNS, private IPv4 and IPv6 installations.

## Acceptance gates

Use only fresh isolated fixtures with fictional device identities until the
transition above is implemented and reviewed. Retain previous completed and
uncertain profiles; do not reuse them to manufacture a passing result.

Tests must cover every durable interruption boundary, exact confirmation without
replay, competing launchers/private-input writers, changed revisions/generations,
newer sign-out, stale workers, server rejection and session-response loss. A new
real-browser run must independently prove protected display access after explicit
consent, plus the unchanged behavior of another display. Stopped preflight and
paused startup are necessary but are not that online acceptance.

Before enabling the complete selected path, add explicit negative tests for a
historical result passed where current permission is required, complete manifest
without a native commit, native anchor without the selected manifest, a lost
post-commit reply, schema migration rollback and an old worker against the new
schema. After activation, test a newer pause/sign-out and an old approval against
the same epoch as well as against a different one. Re-run the existing strict
confirmations to prove their meaning has not changed. Those successor tests are
requirements, not claims about the fixture-only paused activation candidate.

Physical layout, service ordering, boot and actual combined power loss remain
separate checks. See the [versioned acceptance record](browser-device-recovery-acceptance.md)
for the precise scope of completed qualifications.
