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

The broader full-suite/coverage run and exact new-commit hosted checks are
separate from these completed targeted results. Record their status on the PR;
do not transfer earlier green checks to these changed runtime bytes. No new
real-browser/physical acceptance is claimed. Home Assistant, both desk Pi
displays, real credentials, listeners and all retained profiles were untouched.
Successor activation and online post-recovery sign-in remain unimplemented;
PR #250 remains draft, with no merge, release or public wiki publication.

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
