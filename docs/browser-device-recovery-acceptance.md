# Experimental browser recovery acceptance record

This is a development evidence record for [PR #250](https://github.com/stevenboyd78/sdsctl/pull/250),
not an installation procedure or permission to reset a display profile. The
browser-device recovery workflow remains unreleased. Preserve uncertain profiles,
one-use operation records and prior failed attempts; do not repeat initialization
or recovery confirmation to manufacture a passing result.

## Scope and provenance

The September 9, 2026 checkpoint used the small Raspberry Pi's physical 800×480
display and Chromium 151.0.7922.173. A dedicated non-login test account, private
browser directory, private D-Bus session and encrypted fictional keyring separated
the test from the user's desktop. Its endpoint was a fictional loopback listener
that counted and closed connections, not a Home Assistant or authentication server.
Both Pis are bench-test devices; historical service names containing
`production` do not describe their deployment status.

The installed candidate was verified against all 289 packaged runtime files and
the same worker build used by branch head
`fe7b3e98d3d9f03f6b0ba71095f8250ae968a6ac`:

- Private wheel SHA-256:
  `da173eedafb56f75e30aa56350a0544d33d0d3146761188fea48673fc3cc6560`.
- Worker build:
  `ee851c8a66578ad3b1db194a16f1be7c2bdd71e444a8bff9366b6072c6f74351`.

The wheel carries development metadata `0.29.5`; it is **not** the published
PyPI 0.29.5 artifact. Subsequent CI/test/documentation commits did not change
these runtime bytes. Public release acceptance is a separate decision.

## Completed checkpoint

| Check | Observed result | Evidence boundary |
| --- | --- | --- |
| Physical recovery review after worker idle | User confirmation produced a matching durable paused acknowledgement; the bounded browser supervisor exited successfully. | Initial pending state was deliberately seeded in this fictional fixture. The final success sentence disappeared before the user could read it; acknowledgement success is established by the durable record, not a claim that the sentence was read. |
| Host restoration and paused-only guard release | Original canonical host restored; exact release committed; native state remained paused at revision 4. | The retained guard file is expected even after a committed release. Its existence alone does not mean release failed. |
| First ordinary managed browser start | User confirmed the settled paused page, without login prompt or manual reload. | Used the ordinary installed foreground CLI, not the recovery launcher or a storage-seeding extension. |
| Separate ordinary browser restart | A second user confirmation verified the same paused page without manual reload or login. | Recorded only after the first confirmation, graceful browser stop, unchanged proof and a new browser start. |
| Persisted browser readback after shutdown | Exact clean paused state, no device-session cookie and no recovery alarm. | Separate read-only browser API observer on an authenticated virtual display; not another physical startup test. |
| Final shutdown and preservation | Test services exited successfully, no test-account processes or Chromium Singleton markers remained, and the original TUI was visible with an established daemon connection. | Home Assistant, the HDMI Pi and their configuration were not changed by this checkpoint. All test profiles and evidence were retained. |

Both ordinary starts and the final observer recorded **zero endpoint connections**.
Original normal/native/archive/handoff/release evidence bytes and metadata stayed
unchanged, including the native paused revision. Recovery approval, host restoration,
guard release and native suspension were not repeated to obtain the two start passes.

The observation result is `PASS_ORDINARY_PAUSED_STARTS`, with `starts: 2`,
`authentication_connections: 0`, `recovery_approval_repeated: false` and
`native_pause_reapplied: false`. It explicitly records
`browser_storage_readback_tested: false`; that check belongs to the later,
separate `PASS_D_PERSISTED_READBACK` result. The latter confirms clean paused
storage, absent cookie/alarm, unchanged evidence and clean browser shutdown.

The readback extension used the same public extension identity and only storage,
cookie and alarm read APIs. It had no native-messaging permission, background
worker, authentication request or storage-write operation. Its first navigation
needed a fixture-only reload after extension replacement. This is recorded as
`observer_navigation_reloaded: true`; neither accepted ordinary start used a
reload. Browser extension-installation/cache metadata is not claimed byte-identical
after this observer; protected native and recovery evidence is.

## Retained failures and observation corrections

Earlier attempts and their original results remain preserved, not retrospectively
marked successful. Some timed physical attempts expired without an acknowledgement.
The successful recovery attempt then encountered a missing adjacent test-helper
import **after** acknowledgement, host restoration and paused-only release.
Independent read-only checks established those completed boundaries. A separately
packaged continuation added the missing test dependency without changing or
replaying the original recovery attempt.

An initial continuation was stopped after a five-second screenshot showed
`ERR_BLOCKED_BY_CLIENT`. Isolated ordinary Chromium startup subsequently reproduced
that early frame followed automatically by the correct page at 15 seconds. The
single early screenshot did not establish persistent product failure. The stopped
run remains incomplete; it was not relabeled a pass or restarted over its output.

The new continuation captured each start at 5, 15, 30 and 45 seconds. A recent
settled image was inspected before requesting each separate physical confirmation.
Elapsed time or an assistant screenshot alone never supplied a human-pass marker.
Each wait was bounded; expired or failed attempts retained their state without
automatic replay. No product security deadline, browser sandbox, TLS validation
or consent check was weakened. The continuation helpers passed 50 focused tests;
the final readback helper passed 25 tests, including wrong/missing/extra state,
cookie/alarm refusal and populated-output refusal. These are private fixture
tests, not additions to the repository-wide coverage measurement.

## Genuine ordinary-resume interruption and paused recovery

A later September 9 qualification used a **new isolated profile** on the small
Pi, real Chromium 151.0.7922.173 and the same verified 289-file candidate above.
This was an authenticated virtual X11 display, not another physical-display
test. A private home, D-Bus session, encrypted fictional keyring and network
namespace separated it from the desk displays and Home Assistant. The local
TLS authority and device enrollment existed only in that fictional namespace;
browser sandboxing and certificate verification remained enabled.

Unlike the earlier seeded fixture, the ordinary product UI performed setup,
initial sign-in, sign-out, paused startup, resume review and one confirmation.
The fault wrapper withheld the complete successful response to the second
`POST /auth/device/session`. It observed HTTP 200 and the native client's real
disconnect after approximately 3.00 seconds, forwarding **no response messages**.
No product timeout, extension source, browser storage or native approval was
altered to cause the interruption.

| Boundary | Verified result |
| --- | --- |
| Ordinary setup and initial sign-in | Setup saved without attempting login; the following ordinary start established a session and obtained a successful protected session read. |
| Explicit sign-out | One logout request paused server/native authority and removed the first server session. The waiting landing page was observed; the transient sign-out confirmation sentence was not claimed readable. |
| Interrupted explicit resume | The server completed session issuance, but its response was withheld until native disconnect. The browser displayed that resume could not be confirmed; native state was paused at revision 8 with one complete, consumed approval. |
| Two ordinary starts before maintenance | Both showed the paused page for 30 seconds without another session request, manual reload, repeated approval or injected storage. |
| Once-only native retirement | Archived the exact consumed history, retained its complete terminal anchor and advanced the paused revision from 8 to 9. It did not replay that approval or authenticate. |
| Canonical supervised recovery | After verified page/worker/native readiness and 35 seconds idle, one review and one confirmation produced a real paused acknowledgement and clean supervised shutdown. |
| Restoration and local release | Exact canonical host restoration was confirmed. Normal startup remained refused until a separate, fresh local paused-only guard release; the guard file itself remained retained. |
| Two ordinary starts after recovery | Both used the normal installed CLI and the same complete distro-expanded browser arguments as the accepted pre-maintenance start. Each remained paused for 30 seconds, with no authentication connection or manual reload. |
| Independent saved-state readback | A separate GET-only observer found exact clean paused state, no pending intent, no device-session cookie and no recovery alarm. Actual-host proofs before and after the observer confirmed stopped supervision, restoration/release and unchanged native, bundle, archive, handoff and recovery evidence. |

The separate records are `PASS_ORDINARY_RESPONSE_LOSS_AND_PAUSED_STARTS`,
`PASS_GENUINE_INTERRUPTION_RECOVERY`, `PASS_GENUINE_CLEAN_PAUSED_READBACK` and
`PASS_HOST_PROOF_BEFORE_AND_AFTER_READBACK`. Recovery and readback each recorded
zero endpoint connections. The earlier authentication phase intentionally made
two session requests, one logout request and three verification requests; do not
describe the whole scenario as having zero authentication traffic.

The undelivered second response corresponded to a server-issued session. Local
paused recovery and absent browser cookies **do not prove server revocation**.
The fictional server was shut down after the ordinary interruption test; this
checkpoint does not prove later server-authorized sign-in after maintenance or
credential replacement. Those need a separately reviewed continuation.

The final observer had only storage/cookie/alarm read APIs, no native messaging
or background worker. Its first navigation required one fixture-only reload
after extension replacement; none of the four ordinary starts required one.
Browser extension/cache metadata is not claimed unchanged by the observer.
Native and recovery evidence stayed read-only inside its sandbox. Full stopped
supervisor/host/release proofs ran in the actual host PID namespace before and
afterward, where their existing owner lock can be opened without weakening the
read-only sandbox. Content and identity/metadata snapshots matched throughout.

### Qualification corrections and preserved uncertainty

An earlier genuine interrupted case remains **incomplete**, not relabeled a
pass. Native retirement completed, but a test namespace first made trusted
root-owned executables appear unmapped, and a subsequent namespace setup could
not start D-Bus because its private temporary directory was not writable. A
later canonical launch reached verified readiness, but the harness waited for
the page's superseded initial placeholder. It timed out before any browser
review or confirmation; no acknowledgement was written. Its consumed history,
guard, handoff, archives and retained Chromium Singleton markers remain intact.
Normal startup correctly refuses that case. It was not reset or relaunched to
obtain the new result.

Before the new genuine case, a fresh synthetic Chromium rehearsal qualified the
corrected sequence: wait for settled verified-ready text, idle, review once,
check the exact paused native revision, then confirm once. The same shared
keyboard helper was used for the successful genuine case. The outer recovery
namespace preserved executable ownership and writable private temporary storage;
only namespace setup used privilege, with all candidate/browser work running as
the ordinary test user. No product security deadline or browser safety control
was relaxed. Private helper checks passed 10 input-sequence tests, 13 observer
host-boundary tests and 26 GET-only readback tests; these are not additional
repository-wide coverage claims.

All owned test processes stopped and all evidence was retained. The small Pi's
existing TUI remained active with its original process and zero restarts.
Home Assistant, the HDMI Pi, real credentials and port mappings were unchanged.

## Subsequent request-boundary review

The next review found a gap beyond the completed paused-start checkpoint:
the normal native wrapper did not revalidate the live owner/release before each
ordinary request. In a new local test fixture, a resume preparation following a
paused-only release was accepted, advanced the native ledger and invalidated the
release proof. The regression failed with all three observations: request not
refused, protected state changed, and release no longer valid. No retained Pi
profile was used for this reproduction.

The candidate now checks canonical live-owner authority on every normal worker
request. A paused-only release permits only read-only status and denies resume,
authentication, setup claim and suspension before dispatch. Read-only inspection
also avoids clock-correction writes. This preserves the completed paused record;
an earlier clock that prevents reconciliation confirmation still causes a
read-only refusal rather than a repair or a relaxed evidence check. This change
does **not** implement later online sign-in. At that checkpoint the normal resume
link received a refusal on these released installations. The later paused-only
presentation below replaces that unusable link. A separately designed
administrator continuation and credential-writer coordination are still required
before offering post-maintenance sign-in.

This change modifies native runtime bytes and the derived worker build identity.
All Pi/Chromium results above remain evidence for their exact earlier candidate,
not acceptance of the new request-boundary fix. A **new isolated small-Pi case**
then qualified this separately packaged candidate:

- Wheel SHA-256: `2479a6626b795de952f3c0dfa828348e0929ce39d56765b97dca1f72c4ba2cf8`.
- Worker build: `186a89399c06f433d535e9fcc727de3a019ba618725fd9102acd78bea3820c23`.
- All 289 installed package files matched the wheel before creating any state.
  The private artifact retains scalar version 0.29.5; it is not the public wheel.
- Real Chromium 151.0.7922.173 completed normal first-run setup after 55 seconds
  idle. A fictional extension then seeded pending state; this is a synthetic
  recovery rehearsal, not a new genuine response-loss qualification.
- Canonical supervised recovery reached verified-ready, idled 35 seconds,
  accepted one review and one confirmation, acknowledged the pause and exited
  cleanly. The canonical normal host was restored, then a fresh local paused-only
  release was committed while retaining the guard.
- Two ordinary starts remained paused for 30 seconds each with no manual reload.
  On the second start, one trusted keyboard activation of the normal resume
  review was visibly refused. No connection to the fictional server occurred.
- A separate GET-only extension read back clean paused state, no pending intent,
  no session cookie and no recovery alarm. Host restoration, release confirmation
  and normal-start eligibility still validated after that observer. The observer
  used one fixture-only navigation reload; the ordinary starts did not.
- Protected native, bundle and archive bytes remained unchanged. The virtual
  screenshots were reviewed. All owned test processes exited; the existing desk
  TUI retained its process with zero restarts. All old and new evidence remains
  preserved; Home Assistant, HDMI Pi, real credentials and ports were unchanged.

Local checks passed 170 guard/worker/bundle tests, 96 native/resume/transport tests,
599 JavaScript tests and 43 documentation/release-contract tests. Ten private
keyboard-sequence tests passed. Ruff, mypy across 217 source files, all 81 Markdown
checks and whitespace checks passed. This is targeted validation, not a new full
suite or coverage claim. The initial regression failures and an incorrect global
clock-rollback expectation were retained with the corrected passing reports.

This result does not qualify genuine response loss, ordinary server sign-in or
physical layout on the new build, nor any post-maintenance online continuation.
Do not rebuild or relaunch a populated preserved profile to manufacture that
result.

## Explicit paused-only role and read-only continuation preflight

The next candidate separates the native-selected paused-only worker from ordinary
recovery/resume composition. The startup page explains that a separate
administrator continuation is required and hides the ordinary resume link. A
directly opened resume page remains inert; an explicit review explains the same
restriction and never enables confirmation. The native request gate remains
authoritative; this is not just hiding a control.

An internal stopped-checkpoint inspection can now review and re-confirm the exact
existing release without writes or server access. It is an ephemeral advisory
selection, not consent, a journal writer, a CLI or an executable permission. The
[continuation design](browser-device-continuation.md) records the remaining
durable transition and why historical recovery cannot itself grant sign-in.

A **fresh small-Pi virtual-display fixture** passed on Chromium 151.0.7922.173:

- Wheel SHA-256: `1cff8bf95dc0b9f56476b2cd618e009d840c8ce00c65b323c67890c33bd5b6d6`.
- Worker build: `3df9e413095920b1473b439ea54d2b25061537a77802159c92689be5df0005e9`.
- All 291 installed package files matched the candidate wheel. Its scalar version
  remains 0.29.5; this is not the public Python artifact or a deployed App update.
- Normal first-run setup after 55 seconds idle, synthetic pending-state seeding,
  supervised recovery after 35 seconds idle, explicit review/confirmation, clean
  shutdown, canonical host restoration and local paused-only release passed.
- Both ordinary starts showed the administrator-required message for 30 seconds
  without manual reload. The resume link was absent from visible text and could
  not be activated with keyboard focus. No fictional endpoint connection occurred.
- Stopped continuation inspection and confirmation returned the same advisory
  selection without granting permission or invalidating the existing release.
- A separate GET-only browser observer found exact clean paused state with no
  pending intent, session cookie or recovery alarm. Full stopped host/release
  checks passed afterwards; protected native, bundle and archive bytes matched.
- The paused and readback screenshots were reviewed. All owned fixture processes
  exited; the desk TUI retained its original PID with zero restarts. Host NSS
  trust, Home Assistant, HDMI Pi, real credentials and port mappings were unchanged.

An initial new fixture reached local release but its harness lacked a test-helper
import before ordinary startup. That run remains preserved, not replayed or
counted as a pass. The successful run used a new directory and the complete
harness against the same immutable candidate. Its browser state was not borrowed
from the failed run or any older retained profile.

Local final checks passed 83 guard-release/continuation cases, 41 namespace launch
cases, 199 worker/bundle/native/resume cases, 43 documentation/release-contract
cases, 652 JavaScript tests and 10 private input-sequence tests. No case in these
final reports was skipped. Ruff, mypy over 218 source files, all 82 Markdown
checks and whitespace checks passed. Two earlier guard reports each retained a
fixture setup error; those runs overlapped edits to hashed runtime source. The
final unchanged-graph run passed all 83 cases without weakening validation or
reusing a failed fixture. This is targeted validation, not a new full-suite or
coverage claim; remote checks must qualify their own exact commit.

This validates paused presentation and preflight, not online continuation,
genuine response loss on these new bytes, physical layout, credential replacement,
cross-release migration or boot/power-loss behavior. No merge, release tag,
public wiki publication or real-device configuration change is implied.

## Internal administrator continuation intent

The following candidate adds only the stopped, local administrator intent step
described in the [continuation design](browser-device-continuation.md). Its
fixed purpose is enabling a future fresh browser resume review. It does not
activate a successor, grant browser consent, rotate a credential or contact a
server. Even a completed intent blocks normal startup and native requests.

Final combined local validation passed **173 cases** without failures, errors or
skips: 41 process-isolated launch cases, 83 guard-release/preflight cases and
49 new intent cases. The JUnit execution gate now requires all three modules
on every dedicated Python 3.11–3.14 namespace job. Coverage includes exact
consent/cancellation, clock expiry, busy launch/profile/native owners, changed
credentials/evidence, inode replacement, malformed/conflicting journals and
request refusal after a worker has already started.

Seven process-loss cases terminate the writer without Python cleanup at empty,
prepared, directory-sync, updated, before-commit, after-commit and final-read
boundaries. Only a complete unchanged record can subsequently confirm read-only;
none may replay, clear pause, start a browser or authenticate. These checks use
fresh fictional fixtures, not retained real-device profiles or physical power loss.

All 652 JavaScript cases and 70 documentation/release/gate cases passed. Ruff,
strict mypy (219 source files), all 82 Markdown checks, whitespace, isolated wheel
build and wheel metadata validation passed. All 292 packaged files matched the
frozen source. The private wheel SHA256 is
`0f558959796ec1d384c534aae9145293754309112f83acae740b533281bae1fa`;
worker build is `0b88cbe6862ce4f76eba9d0fb20d5ae88293f7fba22f2a4ea4f3c6b3fab0e247`.
The unchanged scalar 0.29.5 is not a claim that this is the published artifact.

At runtime head `477deae65da519997e96d923785a81aed239b484`, the final full local
suite passed **7,202 tests**, with no failures, errors or skips and 87.22%
statement coverage. All 27 executed hosted checks passed; three publication
jobs skipped as intended. Across both push and PR events, all eight Python
3.11–3.14 full-suite jobs passed 7,041 tests with 161 explicit environment-dependent
skips each and 86.40–86.41% coverage. All eight dedicated namespace jobs ran the
173 required cases without skips. The coverage floor remains 86%. The jobs
reported the upstream Starlette/AnyIO deprecation warning; the local full run
also emitted background capture-stream logging errors in the unchanged audio
failure-listener test without a test failure. These observations were retained,
not suppressed or treated as failures in the recovery cases.

Exact-head CodeQL analyses reported zero findings and no analysis errors or
warnings. See [the completed PR CI](https://github.com/stevenboyd78/sdsctl/actions/runs/34420692944)
and [CodeQL](https://github.com/stevenboyd78/sdsctl/actions/runs/34420690222).
These results describe that exact runtime checkpoint, not future activation
code. No new real-browser/physical acceptance is claimed. Home Assistant, both
desk Pi displays, real credentials, listeners and all retained profiles were untouched.
Successor activation and online post-recovery sign-in remain unimplemented;
PR #250 remains draft, with no merge, release or public wiki publication.

## Native archive-plan separation

The archive-plan parser now reconstructs selected native retirement and
reconciliation archives without reading the current ledger. It returns a
different, immutable result type that does not establish commit or permission.
Existing strict confirmations still require the exact current ledger state and
now recheck archive bytes after that comparison. No native schema, activation
writer, historical commit anchor, startup role or browser message was added.

The new parser suite passed **115 cases**, including no-I/O checks, typed and
canonical validation, all stopped modes, both reconciliation schema versions,
malformed/duplicate/oversized records and real synthetic native transactions.
Those transaction cases distinguish a valid archive preceding rollback, exact
post-commit confirmation and a newer pause; passing a plan as a confirmation
review is refused. Archive changes during the live-ledger check are retained
and refused, not repaired.

The existing maintenance, reconciliation and private ownership-boundary suites
also passed **225 cases**. The combined coverage run before the final two
readback tests passed 338 cases and covered all 96 statements of the new parser.
This is targeted statement coverage, not whole-project coverage or proof of
all interruption schedules. The project coverage floor remains 86%; the broader
100% goal is still deferred to v1.0. Wider process-isolation, runtime, package
and exact-commit CI results must be recorded separately for this changed runtime.

No retained browser profile, real credential, Home Assistant service or Pi
configuration was touched. This checkpoint did not implement the historical-chain
reader, current-permission selector or successor activation; PR #250 stays draft.

## Retained historical-chain reader

The next candidate adds `inspect_continuation_history`, a separately typed,
read-only result anchored by the complete paused-release and administrator-intent
SQLite journals. It reconstructs native archive/review/guard bindings, both
canonical runtime bundles, the supervised handoff and paused acknowledgement,
restored host and fixed file identities. It rechecks retained evidence while
holding stopped launch and shared private-profile/archive ownership.

The new suite passed **26 cases** using actual isolated Linux processes with the
fictional browser/native-pipe fixture. Both retirement and reconciliation chains
validate before and after a simulated later native pause. Existing strict current
confirmations refuse that later revision, and ordinary startup/worker requests
remain blocked even when historical inspection succeeds. Separate frozen result
types prevent an internal archive representation from being mistaken for the
existing live retirement or continuation evidence.

Negative cases cover altered archive, review, guard, handoff, acknowledgement,
restoration, readiness, supervisor, host and bundle files; replaced release,
intent and ledger inodes; changed credentials; missing or prepared journals;
malformed canonical bodies; wrong selections; dangling SQLite sidecars; competing
ownership and final-readback changes. Each case retains the observed fixture
state. The journals are opened read-only; inspection never repairs or completes
them. The two journal tests also exercise multiple malformed bodies in each case.

All four namespace modules are now required by the hosted execution gate;
the full-suite jobs remain on their existing runner and the 86% coverage floor
is unchanged. These focused results do not substitute for a fresh whole-suite,
package or exact-head hosted result, and they are not physical power-loss,
real-Chromium or online-server acceptance.

Current permission selection, successor schema/activation and online continuation
remain unimplemented. No real profile, credential, Home Assistant or Pi change
was made. This internal reader is not an administrator runbook; PR #250 stays draft.

## Historical file/current-state separation

The next local candidate separates canonical private-input/bundle reconstruction
from native-state inspection. The file reader validates fixed configuration and
credential syntax, actual CA certificates, extension key identity, private file
ownership/modes, exact paths, the canonical receipt and every installed-runtime
asset. It never opens SQLite. Normal bundle/profile/registration APIs retain
their current-ledger inspection and new-registration freshness requirement.

Portable regression cases exercise this separation, unsafe/malformed inputs,
changed paths and identities, and disagreement between live inspection and the
file snapshot. Existing arbitrary-asset, forged-receipt, extra-file and runtime
substitution cases also run against the extracted reader. Neither missing nor
corrupt/unsupported current state becomes usable merely because bundle bytes
still validate.

The supervised historical-chain fixture separately tests both legacy archive
kinds with an unsupported or corrupt same-inode native ledger. It asserts that
historical inspection never opens that ledger, while current confirmation and
startup still refuse it. Missing/replaced/unsafe ledger files and sidecars remain
errors, as does a syntactically valid replacement credential. No current-schema
support, activation or permission follows from a successful history inspection.

These are local/isolated-process checks, not a real-device acceptance or physical
power-loss test. Retained real profiles and both bench Pis remain untouched.

## Scoped history ownership candidate

The canonical historical reader now accepts an internal, short-lived ownership
scope. Separate entrypoints preserve stopped-administrator checks and validate a
selected live native child. Neither introduces a browser-selectable ownership
flag, a normal-worker role or current permission.

Portable tests use real private directories and advisory locks with explicitly
simulated Chromium ancestry. They cover competing launchers/private writers,
Singleton markers, changed directory/lock inodes and permissions, wrong native
selection/configuration, missing ancestry, lost locks, process/parent changes,
scope expiry, failure latching and sanitized errors. A separate case exercises
actual process discovery and rejects the unselected test process.

Retained-chain tests reconstruct both legacy archive kinds inside an already-held
stopped scope and under the simulated later live owner. The fingerprint must agree
with the public stopped reader, including after a fixture-only later native pause.
Expired scopes and final-readback changes must fail. The public reader and normal
worker still refuse the already-recorded continuation intent; no reader contacts
the server or writes the retained state.

These are automated qualification cases, not physical-Pi, real-browser or
power-loss acceptance. Current-permission selection, successor schema/activation
and online continuation remain unimplemented. No real profile is eligible for
reuse from the success of these historical checks alone.

## Isolated native activation transaction candidate

The native-only core now derives a canonical candidate manifest, stages its
epoch/digest/inode anchor and one paused revision advance in a single native
transaction, and provides an exact read-only after-state view. It does not open
or create manifests, obtain consent, prove ownership from supplied bytes, expose
a CLI/browser action, or grant current permission. Ordinary recovery/resume
helpers still reject schema 3; retained real profiles remain blocked and untouched.

Portable tests build fresh schema-1/2 ledgers with real retirement/reconciliation
archives. They cover atomic commit, retained terminal approvals, uncommitted and
rolled-back state, lost-reply confirmation without replay, malformed/substituted
manifests and anchors, later native changes, noncanonical SQLite schemas, WAL and
transaction-policy refusal, consent expiry/clock rollback, integer timestamp
roundtrips, and failure or interruption after each write/readback boundary.
Injected rollback failure must close the writer; a caller that catches the error
must not be able to commit it. No test contacts a device/session server.

Both archive forms also run through the full retained-chain namespace fixture.
The same stopped owner reconstructs history before native DML under the write
transaction. A competing writer is refused, and the historical reader still
refuses the active rollback journal. After commit or rollback it can reconstruct
the unchanged retained history; only the committed exact native after-state can
pass the new read-only activation view. Existing handoff/release/intent confirms
remain strict and refuse the changed native ledger. Normal startup remains blocked.

These checks qualify this internal transaction component, not an installed
activation workflow, current-epoch selector, physical power-loss behavior or
headed-browser/Pi acceptance. The future owned filesystem/consent adapter must
sync and recheck the actual immutable manifest and all ownership/private-input
bindings around a dedicated transaction. Epoch-bound approvals and their
cancellation on pause/sign-out/clock correction must be complete before ordinary
helpers can accept the new schema. No user testing is requested for this core.

## Owned current-state selector candidate

The internal current reader now selects the fixed release/intent/activation
records from installed paths, reconstructs their complete historical chain under
ownership, and reads the anchored current epoch in one query-only native
transaction. Stopped and live-worker ownership are distinct; neither accepts a
caller-supplied history object or a cached result as permission. The live boundary
derives the profile directory from the selected browser ancestor and rechecks its
fixed native wrapper and busy launcher lock.

Portable fixtures exercise actual private files, SQL and locks with simulated
history/ancestry. They cover exact read-only state, later valid epochs, prepared
or noncanonical journals, unsupported SQL shape, unsafe files and sidecars,
replaced inodes, changed private inputs/runtime, competing writers, lost ownership,
process/parent changes, interruption, final-readback checks and latched failures.
The returned current snapshot is a distinct frozen/redacted type, not a grant,
browser session, consent object or historical activation confirmation.

The full-chain namespace fixtures additionally select both supervised archive
forms after paused activation and later native prepare/claim/complete/pause
stages. Current state changes while retained historical evidence stays valid;
ordinary startup and requests remain blocked even for a valid active epoch.
The live-owner cases simulate only the later browser ancestor, not the complete
historical chain. Late archive/journal/credential/runtime changes invalidate an
open read scope without repair, adoption or network access.

Exact-candidate test counts, package hashes and hosted CI results are recorded
with the draft PR checkpoint, not inferred from earlier commits. No real profile,
credential, Home Assistant service or bench Pi was changed. Owned mutation,
online authority/session exchange, browser-role integration and physical
acceptance remain unqualified; no user test is requested for this internal reader.

## Owned native cancellation candidate

The internal adapter now owns explicit native pause and backward-clock
correction, without exposing a browser role, dispatcher or CLI. It reconstructs
fixed files, complete history and current SQL inside an actual-owner native write
transaction, compares the entire expected current snapshot and rechecks inputs,
ledger policy/identity, clocks and exact staged state before one commit.

Portable fixtures cover both owner paths and legacy archive schemas, paused and
pending/active native states, atomic clock-correction-plus-pause, unchanged legacy
history, exact readback, stale same-revision claims, wrong selections, unsafe
files/sidecars, competing writers, ownership/process changes, invalid/backward/
expired clocks, post-DML input/SQL changes, interrupted writes and rollback failure.
They distinguish committed-but-unacknowledged state from uncommitted rollback;
neither a lost reply nor a later paused state permits automatic replay.

The complete-chain namespace cases additionally exercise both archive forms,
cancelled old claims, preserved historical evidence, post-DML archive/runtime
changes, copied release/intent journals before selection and uncertain commits.
The minimal portable journals simulate historical inode anchors; their copied
journal test therefore checks replacement after bootstrap, not original journal
provenance. The namespace cases supply that missing full-chain evidence.

Readback pins the original selected files and directory/launcher identities.
It describes only the exact expected current state retained by the same-process
attempt object, not durable operation provenance or a cached permission lease.
Lost process state is not reconstructed or repaired. No test here establishes
server-session revocation, browser sign-out, physical power-loss durability or
headed-browser acceptance. Real retained profiles and both bench Pis remain
untouched. Exact-commit qualification belongs to the draft PR checkpoint.

## Owned prepare/claim candidate

The new live-worker-only fixture adapter prepares and consumes one native approval
without completing an active grant, contacting a server or enabling ordinary
schema-3 requests. A fresh in-process review callback acknowledges its exact
context; this is fictional trusted consent/generation input for qualification,
not headed-browser gesture verification or verified online authority.

Portable cases use real private files, ownership locks and native SQL with
simulated retained history and later Chromium ancestry. They cover actual private
digest reconstruction, redaction, unchanged stopped modes and legacy records,
single-use phases, exact same-revision claim comparison, retry delays, wall and
monotonic lifetime, stale/changed inputs and owners, competing writers, rollback
failure, interruptions and uncertain commit/readback outcomes. Confirmation never
restores a failed phase gate or adopts a pending row into a new object/process.

The complete-chain namespace cases additionally exercise both retained archive
forms, prepare/claim followed by newer pause, copied release/intent records,
post-DML historical-input/runtime changes and uncertain commits. Only the later
browser ancestor and trusted consent/generation are simulated there; the retained
chain and current filesystem/database boundaries are reconstructed in full.

Neither test layer establishes cross-native-message lifetime, fresh server proof,
session issuance/revocation, cookie installation, physical power-loss durability
or live browser continuation. The same-process prototype is not wired into the
old prepare/commit message pair. Home Assistant, both bench Pis, retained profiles
and credentials remain unchanged. Exact-commit qualification is recorded in the
draft PR, separately from these scope descriptions.

## Owned verification/completion candidate

The isolated live-owner adapter now creates its own prepare/claim attempt and
uses the fixed verified HTTPS transport before native completion. It does not
accept supplied proof or approval objects, create a browser session, expose a
native request role or permit ordinary schema-3 startup. Consent and later
Chromium ancestry remain simulated fixture inputs.

Portable cases cover completion and exact-claim failure handling, private-input
coordination without an open native transaction during proof, newer pause and
same-revision changes, altered files/owners, whole-operation wall/monotonic timing,
interruptions, rollback failure and uncertain commit/readback. A failed approval
acknowledgement cannot reach network proof. A lost completion reply cannot be
rewritten as failure or replayed by readback. A truly uncommitted prepare can be
followed by a separately initiated fresh review, not reuse of the old attempt.

Selected fixtures additionally use actual verified TLS and fictional responses
at DNS and IPv4 origins, testing exact generation and refusal of wrong authority,
unconfirmed drainage, malformed responses, redirects, cookies/compression,
rate limits, and untrusted or hostname-mismatched certificates. Certificate
failures must send no credential-bearing HTTP request. These are not physical
LAN/Pi tests or a complete browser/session acceptance run.

The full-chain namespace cases preserve actual retained retirement/reconciliation
evidence through completion, transport refusal, a newer owned pause during proof,
and a lost completion reply. The later ancestor, trusted consent/generation and
server response are simulated in that layer; the actual chain/files/SQL/locks are
not. The original history and all non-native inputs remain unchanged.

The adapter checks elapsed time across one whole operation; fixtures do not prove
independent supervisor termination of a blocked proof request. That deadline must
remain enforced when native dispatch is wired. A native ACTIVE after-state is
not an installed session, ongoing server permission or proof of old-session
revocation. Real profiles, Home Assistant and bench Pis remain unchanged.
Exact-head test counts and qualification results belong to the draft PR record.

## Successor activation design checkpoint

The [proposed activation contract](browser-device-continuation.md#proposed-activation-contract-history-is-not-current-permission)
separates immutable historical recovery evidence from current permission. It
specifies an immutable prepared manifest and a single authoritative native
transaction, interruption decisions, unchanged strict confirmation methods and
the requirement to implement the current-epoch selector before exposing a writer.
The contract is design guidance; the separately qualified internal components
above do not implement the complete selected activation path or resume-review
role. Existing complete intents still block startup and
native requests; no receipt becomes a runnable permission through documentation.

## CI evidence and remaining review gates

At the exact head `fe7b3e98d3d9f03f6b0ba71095f8250ae968a6ac`, all 27 executed
remote checks passed; three publication jobs skipped as intended. Python
3.11–3.14 full suites ran for both push and pull-request events, with 7,024 passes,
91 explicit namespace-prerequisite skips and one upstream warning per job.
The separate namespace matrix ran all 99 cases without skips on all eight jobs;
its JUnit gate confirmed 41 launch and 58 guard-release cases. Statement coverage
was 86.57–86.59%, above the unchanged 86% floor. Exact-head CodeQL analyses for
Python, JavaScript/TypeScript and Actions reported zero findings and no analysis
errors; the repository had no open code-scanning alerts when checked.

Those checks belong to that exact head, not to a later documentation or runtime commit.
See [the CI run](https://github.com/stevenboyd78/sdsctl/actions/runs/34324509915)
and [CodeQL run](https://github.com/stevenboyd78/sdsctl/actions/runs/34324507532).
That earlier documentation checkpoint added no runtime changes. The subsequent
request-boundary review above does change native runtime behavior; its validation
must be recorded separately. Neither checkpoint adds a release tag, production
enrollment, credential operation, port exposure or Home Assistant catalog change.

The physical recovery/paused-restart evidence gap is closed **for the earlier
same-build seeded checkpoint**. The new virtual-display qualification additionally
proves ordinary interrupted resume and its recovery back to paused for its
recorded candidate. Neither checkpoint qualifies:

- Later server-authorized session establishment after paused maintenance, or
  revocation of a session whose response was lost.
- A coordinated credential-file replacement workflow or cross-release worker,
  browser cache and profile migration.
- Automatic boot, actual power loss or a combined server/display outage.
- Physical HDMI recovery handoff, Firefox/WPE unattended enrollment, or secure
  unattended keyring handling in other deployments.

The next bounded qualification should separately review fresh permission and
session establishment after paused recovery, including any credential-file
replacement it requires. Do not infer a working online continuation from the
offline paused result, restore an old credential snapshot, replay consumed
consent or repurpose a retained uncertain profile. Passing this record alone
does not mark PR #250 ready for merge or make the broader unattended-browser
feature production accepted.
