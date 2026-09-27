# Finite recording App controller — development status

These are **uninstalled development libraries**, not an available Home
Assistant App option or a supported manual launch recipe. No existing command,
service, or passive `--prepare-idle-service` path selects them. A separate
read-only source inventory names their bundle but cannot select or launch it.
Do not run them against a scanner as a substitute for the still-pending
independent supervision and recovery integration.

## Implemented boundaries

| Phase | Explicit owner | Evidence retained |
| --- | --- | --- |
| Read-only controller source inventory | Explicit App `Layout` | Distinct 83-module private bundle, complete product inventory and unchanged double-read filesystem bounds; no source execution or installed dependency qualification |
| Read-only joint source inventory | Explicit service `Layout` | Distinct 90-module controller/observer bundle; includes original deadline/App/native/CLI custody and private channel, without changing any command or granting active authority |
| Read-only candidate assembly | `AppIdleCandidate` and explicit App `prepare_candidate` | Original running idle service, accepted Startup, borrowed clock and init witness; native App publication pins retained without dispatch or full qualification |
| Explicit service/native handoff | `AppService` and `AppNativePhase` | One reserved original IdleService, one top-level loop and original inbox lock; original prepared ledger checked before publication; independent Operator retained before cancellation |
| Explicit recording-phase handoff | `AppRecordingPhase` | Native cancellation retired before AppStart; exact returned start, original active inputs, progress checkpoints, separate finalized/preserved recovery routes and unchanged session clock |
| Native launch | `AppLaunch` | Original accepted Startup, candidate generation, published launch input, host journal, clock, init witness and full BootstrapHost |
| Native ready | `NativeReadyQualification` | Actual returned Ready and the one-time inventory of the four declared private sockets |
| Recording begin | `AppStart` | Original AppLaunch, fresh source/runtime/host/probe checks, returned durable host authorization and ledger intent, and the existing one-use Relay |
| Active inputs | `NativeActiveQualification` | Exact AppStart/Relay/PostBegin, original sockets and immutable input identities, and append-only validated receipt prefixes |
| Host and file observation | `AppRetainedHost` | Original authorization history, fixed metadata worker, owner-thread file read, and continuing original actor/lease evidence |
| Native status observation | `AppActiveSample` | One fresh cached native probe bracketed by complete App source/runtime/input inventories and host/file checks |
| Finalized recording and exit evidence | `AppAuthorizedFinalized` | Original successful AppStart, closed Relay and independently captured Operator; separate actual exit collection and returned journal publication |
| Finalized host observation | `AppFinalizedHost` | Original finalized files plus current complete ordinary metadata/static-file reads; native health and recording flags remain unknown |
| Successful-recording recovery | App-specific `recover_finalized` and `AppRestoredHost` | Original RecoverySession, journal, process/CLI owners and deadline; fresh normal-App status only after durable restoration intent and separate exit receipts |
| Interrupted recording recovery | `AppPreservedHost`, `AppPreservedRestoredHost` and App-specific `recover_preserved` | Exact original Preserved reader, closed ledger, retained partial files and independent exits; outcome remains unconfirmed even after normal service returns |
| Never-authorized recovery | `AppNeverAuthorizedHost`, `AppNeverAuthorizedRestoredHost` and App-specific `recover_never_authorized` | Exact original NeverAuthorized reader, prepared-only ledger, pristine files and independent exits; no recording authorization, abandonment or success is manufactured |

The direct-policy `Launch`, `Start`, `RetainedHost`, `ActiveSample`, and
`IdleService` action admission gates remain exact. They do not accept the App
classes just because they share implementation. An explicitly reserved idle
service also refuses a second driver through its direct `run()` path. App classes are
closed leaf policies; unreviewed subclasses are not admitted.

### Separate read-only source inventory

`supplemental_recording_app_host_source.Layout` names the closed App controller
library bundle with its own digest kind. Its 83 private modules include imports
deferred until native construction; the whole product tree is also inventoried.
It retains the original complete double-read metadata/content checks, file and
byte limits, time bound, private-file rules and exact inventory requirements.
Observed source is hashed, never imported. Legacy selectors, subclasses and a
digest made under another profile kind are refused. Existing source profiles
and the passive preparation command remain unchanged.

An isolated import smoke test runs only reviewed repository code, with network
connection operations and subprocess launches blocked. The inventory remains standard-library
only. Controller imports additionally require PySerial through scanner transport
code; the test explicitly distinguishes this requirement from the deferred native
imports. This is a local dependency observation, not a pinned installed image,
a dependency integrity check, or independent supervision. Those gates remain.

`supplemental_recording_service_host_source.Layout` now names the joint
controller/observer graph under a different digest kind. The closed 90-module
bundle adds the deadline watch, original App/native/CLI custody, private evidence
channel and its control dependency. Its inventory is also standard-library-only
and hashes the complete product tree without executing observed source. The
older 83-module controller profile and all four legacy profiles remain unchanged
and refuse this larger bundle. It does not include the passive permission or
preparation commands and cannot turn their consent into active action scope.

Isolated local import tests cover all joint roots and the complete graph, both
with PySerial available and with that dependency deliberately refused. The
inventory needs no PySerial; the controller requires it. Missing Linux timerfd
APIs are tested separately: imports remain passive, while deadline-watch
availability refuses. This API-absence simulation is not a test run on an older
Python interpreter. The planned installed runtime still requires an independently
authenticated complete interpreter/loader/package inventory, a supported kernel,
original process binding and separately measured bounds; source hashes and local
imports do not establish any of those facts.

### Read-only service preparation

The explicit App `prepare_candidate` consumes the original running idle
service's one candidate-preparation slot. It retains that service's original
session, journal, process witness and accepted Startup clock. `AppIdleCandidate`
creates only the duplicate Idle descriptors, full BootstrapHost and exact
NativeIdleQualification readers. The supplied native publication and source
profile are pinned; actual complete input/runtime qualification is a separate
bounded read, not inferred from construction.

No command selects this helper. Preparation does not publish launch inputs,
retire the idle coordinator, change its session reader, append journal events,
start native code or authorize recording. Existing direct launch gates refuse
the App candidate. The original explicit idle-cancellation route still works
while no native inputs have been published. Once they have, this idle reader
refuses recheck; the separate App native phase owns that transition.

Partial construction releases only acquired duplicate/read resources. Successful
construction registers original cleanup immediately with the existing service;
substitution cannot redirect cleanup to a replacement reader. Startup and the
service's borrowed original objects remain caller-owned.

### Explicit service-to-native handoff

`AppService` reserves one unused original `IdleService` inside the accepted
Startup lifetime. Construction is passive. Its one top-level loop initially
drives the existing idle coordinator; it never runs a nested App loop inside
an idle wait callback. Direct native and recording slots remain empty.

The explicit `start_native` verifies the original prepared ledger and distinct
launch/exit-observation endpoints, then holds the existing inbox publication
lock across input publication, launch and independent Operator capture. Pending
idle cancellation or publication contention refuses before launch input writes.
The idle coordinator and its reader are retired before the first publication
attempt; uncertain publication or launch cannot restore idle ownership.

Only a successfully returned original AppLaunch can supply Ready for independent
Operator capture. The Operator's original cleanup is registered immediately,
before subsequent checks can fail. Earlier launch/capture failures consume the
attempt and leave the original session's clock-only expiry available; pristine
files alone do not allow a never-authorized recovery claim.

Explicit cancellation closes only the original transport, once. A lost close
return is not exit evidence or permission to repeat. The independently retained
Operator must establish worker/init exits and publish evidence before the App
never-authorized route can continue the same RecoverySession. No recording
authorization, automatic cancellation, renewed budget or App stop is inferred.
All original cleanup callbacks remain owned by the borrowed idle service.

### Explicit recording-phase handoff

The App driver's separate `start_recording` retires native cancellation before
constructing AppStart. Direct IdleService recording fields remain empty. Only
the original Relay's returned start acknowledgment permits the started state;
lost authorization, intent or start acknowledgments remain uncertain.

The retired native phase retains static object custody, not obsolete idle or
pristine-ledger reads. Failed App begin/observation/completion does not disable
the original session's independent clock-only expiry. No fallback to idle,
never-authorized recovery, automatic abandonment or fresh begin is inferred.

Explicit active reads construct PostBegin from the original idle/Relay guard,
then NativeActiveQualification from the ORIGINAL ready qualification and
AppStart, plus AppRetainedHost and a fresh AppActiveSample per observation.
Original cleanup is registered before later operations can fail. Results are
not cached as session evidence and do not themselves publish progress.

Either closing route first durably retains intermediate progress if active
observation was prepared. Successful completion captures AppAuthorizedFinalized,
collects actual exit evidence, closes the original Ready and publishes the
independent Operator receipt without an intervening session tick. Explicit
confirmed-start abandonment closes the original ledger before the transport.
Lost ledger acknowledgment cannot be adopted or retried; a lost transport-close
return still requires independent worker/init exit evidence.

Only the distinct App finalized or preserved route may then continue the same
session. The service phase does not provide a lost-start preservation route:
uncertain begin remains review-only. Neither a source profile nor an entrypoint
selects this driver.

### One-way launch and begin

`AppLaunch` consumes one controller slot on the original native-idle candidate,
not on a replaceable passive wrapper. Its command hashes and profile pin come
from the already published original launch input. Construction does not dispatch
anything. The only supported action is the combined launch/confirmation path.

Original durable launch permission precedes Engine create/attach. Once the
actual Ready returns, the controller transitions to the socket-aware reader
before confirming Ready under a fresh complete source/runtime/host/probe
bracket. It never reuses the pre-launch assumption that the sockets directory
is empty. The original readiness timestamp and two-second confirmation bound
are not extended.

`AppStart` then consumes a distinct one-use slot on that original AppLaunch.
Fresh checks precede the durable recording authorization and ledger intent.
Only the returned intent permits the existing Relay to send begin. A sent
begin is not a received recording-start acknowledgment, completed recording,
verified file, process exit, or recovered App.

### Continuing observations

After begin, the original retained actor and journal checks apply. Expired
pre-begin Ready and idle reads are not polled again or renewed. Only the
original recording, stop, and recovery limits remain applicable to their
respective phases.

`NativeActiveQualification` now requires the explicit AppStart. The former
read-only synthetic direct-Start contract is not an alternate admission.
Receipt files are validated append-only output, never acknowledgments or a
replacement baseline. Previously seen bytes and inodes must remain unchanged.

`AppRetainedHost` leaves native health and recording flags unknown.
`AppActiveSample` obtains those flags from its fresh probe, not by inference
from recording files. Both preserve the original full collection bound. A
failed input, host, file, or probe observation consumes that path; another
sampler cannot repair it or reuse its result.

### Failure and ownership

A launch, begin, or active-observation failure closes its original recording transport, not a
substituted public handle, while retaining the original process descriptors for
separate recovery. Nested launch error wrappers do not retry a lost transport
close acknowledgment. Explicit final descriptor cleanup remains the owner's
responsibility and does not certify exit.

Startup, original clock, original journal, idle process witness, and input
readers remain borrowed. Nothing here signals the scanner, changes its mode,
stops Core, deletes recordings, reacquires a process identity, or silently
restarts a consumed case.

### Finalized files and independent exits

`AppAuthorizedFinalized` admits only the original successful AppStart and the
exact independent Operator captured before begin. Its constructor checks the
original recording authorization and closed ledger/Relay history. It delegates
the actual fourth-return, EOF, original-pidfd and Engine-exit checks to the
existing collector. The returned exit evidence must then be separately
published to the original host journal. A file or return code alone is not a
successful recording or permission to restore an App.

After those exits, live Ready and input qualification are not refreshed. The
reader instead checks original object identities, sealed authorization and
journal prefixes, immutable finalized files, and independent exit custody.
Lost returns cannot be adopted from a later disk entry or retried. A finalized
reader failure leaves the independent Operator and caller-owned journal intact.

`AppFinalizedHost` retains the full bounded ordinary host/static-file bracket.
It does not infer native health from a completed WAV, and does not dispatch
recovery. The existing direct finalized-host and recovery gates still refuse
these App-specific objects. Original-session restoration and failure branches
use a separate explicit App policy.

The App-specific `recover_finalized` routes the original session from candidate
shutdown observations to `AppRestoredHost` after normal-start intent. It retains
the original process and CLI trackers, executor, journal, and recovery deadline.
The normal status reader must observe a new generation with fresh healthy,
nonrecording state; old Ready health or a successful command reply is not enough.
An unavailable observation stays unavailable while the same session can still
expire by its independent clock ticks. There is no reader replacement, renewed
budget, repeated stop/start command, or fallback to pristine recording state.

### Interrupted and never-authorized recordings

The two failure branches have distinct explicit entrances. `recover_preserved`
requires the original lower-level Preserved reader: original independent
Operator exit publication, actual worker/init exits, closed unpoisoned ledger,
and retained recording files. A lost start return additionally needs the
original preservation scope; absence of an acknowledgment is not permission to
pretend no recording was attempted. Recovery leaves that recording unconfirmed.

`recover_never_authorized` requires its separate NeverAuthorized reader and
prepared-only ledger. It cannot accept any recording authorization, start
intent, retained output or recording generation. It may continue from an
unpublished host-ready state only when the actual original App Ready and its
qualification had already been acquired. Earlier launch failures do not enter
this path merely because files are pristine; they remain a review boundary.

Both readers check the original App publication, command/profile hashes,
plan and clock, acquired Ready/transport identities, one-use owners, and the
independent Operator's Ready hash. Failed/closed transport or retired input
qualifiers are allowed; live readiness and source/idle qualification are never
repolled after exit. Complete current ordinary host/static-file checks,
protected recording-file checks and the original two-second read budget still
apply. The original candidate must be independently exited and observed stopped.

Only the original RecoverySession may restore the normal App. The failure
branches retain the same session, process/CLI owners, journal, clock ticks and
deadline as the successful branch. Normal service recovery requires fresh
healthy, nonrecording status in a new generation. A failed read stays failed;
uncertain command returns are inspected without reissuing commands. Closing a
host reader does not close the caller's original ledger, Operator or journal.

None of these paths is an installed service or independent supervisor. The
App-aware recording-phase assembly is now explicit but remains unselected.

## Validation scope

The App controller composition tests use actual private source/runtime files,
socket inodes, journal and ledger operations, an owned init pidfd, and bounded
worker threads. App/Engine metadata, Startup provenance, native Ready/Relay
transport, selected host routes, and recording-file results are explicitly
synthetic in those composition fixtures. Separate lower-level tests exercise
actual private transport, native receipt formats and process behavior.

Read-only App candidate assembly tests retain the real original idle service,
session, journal and descriptors, with explicitly synthetic Startup publication,
App input identities and idle process facts. They prove preparation/cleanup and
unchanged action gates, not full App qualification or installed provenance.

App service/native-phase tests exercise the real original service, journal,
prepared ledger, policy deadlines and descriptor cleanup. Startup/App publication,
native launch/Ready, independent Operator and recovery-entry facts are explicitly
synthetic boundaries in that suite. They prove ownership/order and fault handling,
not an actual installed App/native lifetime. The actual lower-level App policy
tests remain separate and cannot be replaced by these synthetic boundary results.

App recording-phase routing tests cover real service/journal/ledger ownership
and the explicit start/observe/finish/abandon branches with synthetic native
return, active reader and exit boundaries. Complementary phase composition
tests use the actual AppStart, NativeActiveQualification and AppAuthorizedFinalized
against real private files, sockets, journals and ledgers; their outer service
shell, file results and native return/continuity/exit facts are explicitly
synthetic. Neither fixture is a complete installed App lifetime, and these
scopes must not be conflated.

A further recording-phase composition keeps a real RecoverySession, process
tracker, dispatcher and executor from before AppStart through the actual App
finalized/restored reader route. Deadline tests advance the underlying fixture
clock without replacing the original owner callbacks. Delayed or absent init
exit, lost stop replies, failed inspection, changed files, unhealthy normal
status and attempted session replacement cannot repeat recovery or renew its
deadline. The outer AppService shell and native/file/platform facts are still
explicitly synthetic; complete startup-to-service composition and installed
supervision are not proved by this narrower seam.

The original-driver composition adds an actual retained CasePlan before any
other owner, and constructs IdleService/AppService while the journal is still
preparation-only. One real top-level App loop then joins actual candidate
preparation, input publication/qualification, AppLaunch, AppStart and the
finalized or preserved recovery readers. A separate run covers native
cancellation and NeverAuthorized recovery without any recording authorization.
All routes keep the same tracker, dispatcher, executor, journal, original clock
callbacks and deadlines; cleanup closes the original owned handles while leaving
the borrowed journal, plan and Startup clock available to their caller.

These driver tests inject lost start/completion acknowledgments, transport-close
and recovery replies, absent worker/init exits, changed file results, unhealthy
normal status and owner replacement. Explicit abandonment preserves unconfirmed
output; pre-recording cancellation stays not-attempted. An unacknowledged progress
file is preserved for review, never adopted. Phase deadlines expire through the
original policy; an already expired hard recovery deadline refuses further
authority and closes the original service rather than renewing its budget.

Startup acceptance and the initial App transfer into candidate-idle are still
synthetic in this driver fixture, as are Engine/HA observations, idle facts and
native actor/return/exit/file I/O. The owned init pidfd, private file trees,
socket inodes, actual journal/ledger checks and App classes are real. This closes
the outer driver-to-recording/recovery composition gap, not accepted-startup,
full initial-dispatch, installed supervision or actual-native lifetime proof.

A complementary original-driver fixture now uses the actual private declaration,
clock-free template, original startup clock, plan publication, one-time acceptance
and `Startup.idle_service` assembly. It drives all three recovery outcomes through
that same accepted owner's AppService. It forbids repolling or renewing the startup
offer after service assembly. Preflight host facts, initial App transfer, idle
publication provenance and native/platform I/O are still explicitly synthetic;
this does not prove a complete installed or actual-native lifetime.

This composition exposed redundant declaration decoding at every intervening
file guard. Declaration construction still performs full canonical schema and
case/root validation. Rechecks compare the exact retained Template and immutable
bytes against the independently supplied digest while freshly reading the
original file and all descriptor, directory and metadata identities. No file
observation is cached, no changed template can be adopted, and all original
two-second collection limits and full source/runtime inventories remain intact.

The initial-dispatch fixture extends that accepted-startup composition with the
actual request Publisher, Inbox, IdleCoordinator, process tracker and dispatcher.
The original session produces the normal-stop and candidate-start journal entries
instead of accepting prewritten transfer history. The normal process exit is
observed through an actual owned subprocess pidfd; candidate binding also uses a
real pidfd. The candidate child is precreated, however, not started by Docker.
Container/cgroup/generation metadata, full host observations, App publication
provenance and native recording/exit facts remain synthetic. Tests cover all
three recovery outcomes, lost initial stop replies without reissuing them, and
missing exit evidence or lost create/inspection acknowledgments expiring to
review without launching native code.

Failure tests use two complementary fixtures: actual AppLaunch/input bindings
with explicitly synthetic exit evidence, and actual original worker/init
pidfds, exit receipts, ledgers, journals and private recording files with
explicitly synthetic App provenance and platform actions. The latter verifies
preserved, lost-start, no-progress and never-authorized continuations, but is
not evidence that an installed App has completed that entire lifetime.

Neither kind of test alone proves an installed end-to-end App lifetime or
real-hardware performance. No test permits extending deadlines or reducing
full source/runtime checks to make a run pass.

## Independent observer deadline prerequisite (uninstalled)

`supplemental_recording_service_deadline.DeadlineWatch` is a separately selected,
read-only observer library. It captures one original live `ObserverClock` before
mutation and retains its own duplicates of the helper pidfd and proven time
namespace descriptors. It arms a one-shot absolute `CLOCK_BOOTTIME` timer at the
**original** plan's recovery deadline. Closing or losing the startup comparison
does not close these duplicates or reset the timer. The old startup comparator
still refuses after its readiness deadline or helper exit; its policy has not
been widened to accept a late helper.

The continuing watch polls only retained kernel handles and its own namespace
metadata. It does not reopen a dead helper's numeric PID, call the helper or
Docker, or depend on service clock callbacks. The actual timer includes suspend
time, retains readable expiry until close, and cannot be extended through this
API. Clock-id metadata is checked because timerfds share an anonymous inode:
an otherwise similar monotonic timer must not silently replace a BOOTTIME timer.
The live pidfd's kernel PID is also checked during capture rather than relying
only on the separate `/proc/PID` identity read. Failed captures consume the one
attempt; changed timer settings, lost handles, and inconsistent metadata refuse.

Tests use actual owned child processes, pidfds, namespace descriptors and
timerfds. They freeze a harmless child, observe independent kernel expiry, and
close/reap a child plus all startup owners before reading the retained exit.
The short expiry fixture changes only its local test plan's timing constants;
the runtime's fixed recovery budget is unchanged. No system suspend, distinct
container namespace, installed source/runtime, App or scanner test is claimed.

**This is not an independently supervised controller yet.** It sends no signal,
publishes no recovery receipt, owns no normal/candidate/native exit witnesses,
and cannot authorize a restart or establish recording success. In particular,
kernel timer readiness does not by itself terminate a blocked observer or
recover a service. The source-qualified outer owner still needs original
App/native custody from before each relevant mutation, separate action consent,
bounded execution, and journal-safe failure recovery. No existing source
profile or command imports this watch; passive preparation stays passive.

This optional observer requires Linux's Python 3.13+ `os.timerfd_*` APIs;
their presence alone is not installed-runtime qualification. Missing support
consumes the capture attempt and refuses before descriptor acquisition. It
never substitutes a sleep, MONOTONIC clock, or weaker timeout. Actual timerfd
tests are capability-gated; missing-API refusal tests still run on supported
Linux Python 3.11/3.12. The product's `>=3.11` requirement and CI matrix are
unchanged. See the [Python timer file descriptor documentation](https://docs.python.org/3/library/os.html#timer-file-descriptors).

The active integration must preserve this ordering:

1. Independently qualify the exact active command, full source/runtime and
   original helper process. Do not reuse the passive command's permission.
2. Capture the observer deadline while the original plan/clock link is live,
   before granting any action permission. Capture failure must leave App
   mutation unauthorized; opening another clock link is not a retry mechanism.
3. Retain the original normal-App exit witness independently **before stop**,
   and the actual candidate init witness **before native launch**. Before
   recording begin, independently retain the original guardian/native/watchdog
   witnesses and separate Engine endpoint as well. A helper's report or a
   persisted PID alone is not this custody.
4. Keep a single journal/dispatch owner. An independent observer cannot start a
   competing RecoverySession or infer successful finalization from helper exit.
   Any failure handoff needs separately verified original receipts, bounded
   ownership transfer and fresh platform evidence; otherwise it stays review-only.
5. Bound the observer's own execution independently. This read-only kernel watch
   neither signals the helper nor makes a shell timeout a recovery protocol.

## Independent original App process custody prerequisite (uninstalled)

`supplemental_recording_service_app_custody.AppCustody` adds a separate,
observer-owned normal/candidate init-process watch alongside the original
`DeadlineWatch`. It is not selected by any command or source inventory.
The observer must have its own separately authenticated Engine endpoint and
independently qualified host PID/cgroup view; transport credentials are not
source, configuration, or action authorization.
Original PID, child-PID, cgroup, and mount namespace descriptors are retained
and rechecked; namespace changes cannot silently adopt a different process view.

Construction consumes one capture slot on the original deadline watch and
captures the normal App's plan-pinned generation **before stop**. Two fixed,
bounded, read-only Engine inspections bracket the actual live process identity
and pidfd acquisition. Kernel pidfd metadata verifies that the handle really
names that process. Changed generation/image/name, a wrong-process handle,
helper exit during capture, and partial acquisition refuse without adoption.

The subsequent candidate capture is one-use and requires the original normal
pidfd to be readable, the helper still alive, and the original readiness cutoff
still in the future. The supplied candidate-generation expectation is checked
against both independent Engine reads; a numeric PID or caller claim alone is
not evidence. Original normal, helper, and candidate containers must remain
distinct. A failed candidate capture cannot be retried and does not discard
the retained normal evidence. An uncaptured candidate remains **unknown**, not
exited. Readiness and recovery deadlines are never renewed.

Once captured, polling uses only retained kernel handles and the original
deadline watch. It does not contact Engine, repoll startup, reopen an App PID,
or replace an expired owner. Helper/Engine loss and even hard-deadline expiry
do not prevent read-only observation of previously retained exits. Explicit
cleanup releases only the custody object's own pidfds, not the borrowed
deadline watch or Engine endpoint, and sends no signal.

Local tests use real owned subprocesses, pidfds, namespace handles, timerfds,
and a real credential-checked Unix socket. Docker responses and cgroup
membership are synthetic. They cover changed first/final inspections, capture
races, wrong pidfds, missing processes, interrupts/partial acquisition,
descriptor substitution, foreign ownership, and continued observation after
original startup/Engine cleanup. These are not installed App/native tests.

This prerequisite does **not** gate the existing dispatcher, authenticate an
action grant, retain native worker or CLI execution custody, transfer a journal
lock, or start recovery. Init exit is not worker exit, absence of a replacement
App, recording completion, or restoration. A future active integration must
join these captures to its mutation gates and preserve a single journal/dispatch
owner. It must not reconstruct a new recovery session merely because these
read-only handles report an exit. Independent bounds on the observer itself
also remain required.

## Independent native-worker custody prerequisite (uninstalled)

`supplemental_recording_service_native_custody.NativeCustody` adds a one-use,
read-only native-process capture to the **same original** `AppCustody`. It is
not selected by an installed command or source inventory. The helper, candidate
init, guardian, native recording worker and watchdog must still be live, the
retained normal init must have exited, and the original readiness cutoff must
not have passed. A failed or interrupted capture consumes the slot but leaves
the borrowed App/deadline evidence intact; it cannot be retried.

The capture joins the original plan/projection, candidate generation and init,
fixed command path/source/deadline, and all three private dispatch intents.
The dispatch directory stays read-locked across both fixed exec inspections and
kernel capture. No intent is recreated or appended. The observer's separately
authenticated Engine endpoint supplies the exact running exec's guardian PID;
the second inspection must match the first. Untrusted reported local identities
must independently match kernel start times, root credentials, namespace
membership, and the guardian's exact two-child process tree. A reported identity
is **not** an authenticated Ready message or permission to begin recording.
The separate `namespace.Observation` type shares kernel mapping checks but is
not a `namespace.Witness`; the ordinary authenticated-Ready protocol and exact
witness-type gates are unchanged. This avoids treating reported hints as if
they had arrived through the recording transport's authenticated Ready path.

The original candidate pidfd is duplicated through the explicit live-only
`ProcessWitness(..., retained_fd=...)` path. This does not reopen a numeric PID:
kernel pidfd metadata and two live incarnation reads must agree before a new
witness is returned. An exited descriptor, another process's pidfd, a regular
file, or a changed process identity cannot manufacture custody. The borrowed
descriptor stays caller-owned, including on interruption or partial failure.
The ordinary process-witness constructor remains available with its existing
live-capture requirements.

After acquisition, the observer owns four distinct duplicated handles for init,
guardian, native and watchdog. Polling checks the retained immutable binding and
descriptor identities, returning their individual exits alongside the original
App/deadline observations. It does not replay dispatch files, contact Engine,
receive a helper report, reopen worker PIDs or consult expired readiness.
It can therefore continue after helper/Engine/startup cleanup and while other
workers remain alive. A frozen worker is not exited. Neither helper nor init
exit substitutes for any worker, and even all four exits supply no exec return
code, recording-completion acknowledgment or restoration proof.

Tests use actual disposable process trees, retained kernel handles, private
dispatch records and credential-checked local Unix transport. Docker metadata,
container credentials/membership and namespace mapping are explicit fixtures;
the fixed native command is not executed. Both ordinary peer and actual sender
credential paths are covered, along with wrong identities/descriptors, changed
commands/dispatch history, partial interrupts, and independent worker exits.
These tests do not qualify distinct installed containers or actual scanner I/O.

This captures process facts only. Authenticated Ready, reviewed active-command
and dependency provenance, explicit action permission, pre-begin admission,
CLI execution custody, bounded observer execution, and single-writer recovery
handoff remain separate requirements. The existing same-process `Operator`
reconciliation and its journal rules are unchanged. Do not replace that owner
with this object, treat its frozen `Status` as action authority, or create a new
recovery session from its exit set. A different worker time namespace is not
silently accepted; this capture currently requires the ordinary host user/time
namespace mapping, without inferring any alternate-domain proof.

## Independent fixed App-command custody (uninstalled)

`supplemental_recording_service_cli_custody.CliCustody` retains the original
fixed Home Assistant CLI command evidence in the observer. It is constructed
from the original `AppCustody` and manifest projection while the normal App and
helper remain live, before the first handoff request. Construction consumes one
capture slot. It does not install a command or upgrade the passive preflight
permission into action consent.

The original `TrackedDispatch` now has an explicitly optional observation
boundary at two points:

1. After the original journaled policy intent, **before exec/create**.
2. After the exact execution ID has been journaled and independently inspected
   as created, **before exec/start**.

The observer opens the original private journal directory read-only, without
acquiring or sharing its writer lock. It retains the directory identity and
checks bounded canonical hash-chain bytes, immutable prefix file identities,
original preparation/plan/projection and pure policy replay. Partial writes,
replacement, extra files, wrong permissions or a history mismatch refuse; no
repair, second `Journal`, recovery session or replayed dispatch is constructed.
The original writer must pause publication during each boundary exchange.

Using its separately authenticated Engine endpoint, the observer verifies the
pinned CLI container generation and reads the exact exec metadata before start.
The original dispatcher rechecks its unchanged owner/context, CLI generation,
created state and original two-second interval after each acknowledgment.
Missing, late, changed or lost acknowledgment consumes the attempt, never
permits start or retry. A lost create return remains a pending intent with an
unknown execution, not an idle state. Earlier commands cannot be adopted later;
their terminal facts must also have been independently observed before the next
App command boundary.

Subsequent `poll()` reads only already captured execution IDs through the same
Engine endpoint. It may do so after helper exit, within the original recovery
deadline. Actual terminal metadata is retained even when Docker later expires
it; no numeric CLI PID is reopened. A missing or malformed read retains the
last factual state, marks inspection failed and disables further Engine reads.
Created/queued is never exit, and neither exit code zero nor all CLI exits prove
App success, worker exit, recording completion or restored service. The original
writer remains solely responsible for qualified journal publication.

Local composition tests exercise both boundaries and all four fixed App actions
through the same real journal and dispatcher. They use actual Unix HTTP,
kernel peer credentials, App/helper pidfds and private files, with synthetic
CLI/container metadata and App operations. Faults cover lost acknowledgments,
changed generations, stale intervals, missing independent history, malformed
terminal state, helper loss, journal damage, foreign-thread admission and
cleanup that preserves the original writer. These are **not** an installed
cross-process supervisor or scanner acceptance test.

The callback receipt is an evidence acknowledgment, not peer authentication or
permission. A separately selected active command must authenticate the complete
cross-process exchange, qualify both runtimes and sources, bind explicit action
consent, bound observer execution and implement exclusive failure handoff. No
existing command/source inventory selects this observer, and no existing
`IdleService` gains an observer or new authority by default. The optional legacy
dispatch path remains unchanged when no observer is explicitly supplied.

## Private CLI evidence exchange (uninstalled)

`supplemental_recording_service_cli_channel.Link` connects the original writer's
two dispatch observation boundaries to `CliCustody` in another process. It
does not install, launch or authorize an active command. The separately reviewed
launcher supplies each peer's original live process witness, original plan and
clock, and two private directional Unix SEQPACKET endpoints. There is no public
socket, pathname listener, reconnect or permission-file fallback.

Every received datagram carries kernel sender credentials, checked against the
retained live peer pidfd and original incarnation. A descriptor forwarded to a
different process cannot stand in for the peer. Each side retains namespace
handles and currently requires identical user and time namespaces; a different
domain is refused, not inferred from similar clock readings. Borrowed channels,
clock and process witness remain caller-owned. Closing the link releases only
its duplicate pidfd and namespace descriptors.

Requests contain a bounded canonical notice description and receipt, the exact
plan digest, an incrementing sequence, a fresh challenge and a short absolute
BOOTTIME interval. They do **not** carry journal paths, journal content, arbitrary
commands or filesystem data. The observer rereads its original held journal,
reconstructs the notice and checks its digest, then performs `CliCustody`'s
independent capture before acknowledging. Replies bind the complete request,
challenge and exact captured receipt. Each case permits at most eight exchanges
(two per fixed App action); policy and original dispatcher one-use rules still
decide which actions, if any, are allowed.

Both sides retain their original two-second exchange budget and the original
plan cutoff. The observer passes the remaining interval into `CliCustody`'s
file/Engine checks; those checks cannot start a new two-second budget. Late,
missing, malformed, duplicated, out-of-sequence or forwarded messages permanently
fail that link. Unexpected descriptor messages are rejected
and any descriptors installed by the kernel are closed. A lost reply cannot be
retried or become success; already captured observer facts remain available
independently. A receipt is custody evidence, **not** action permission, an exec
exit, a finalized recording or restoration. No earlier passive preflight consent
is used by this protocol.

Tests exercise actual distinct local peer processes, anonymous datagrams, kernel
credentials, pidfds and clocks. Observer composition additionally uses the real
private journal and `CliCustody` with synthetic Engine/App metadata. The original
channel tests publish journal events in the parent fixture. A separate process
composition test now has the **child exclusively own the real Journal and
TrackedDispatch**, using the actual fixed Docker Unix-HTTP adapter against a
private synthetic server. The parent neither publishes journal entries nor
shares the writer lock. It independently retains custody and acknowledges over
the original private link. All four fixed App-command transitions complete
through the same writer/observer pair and eight evidence exchanges. Command
bodies and ordering are asserted at the test server; no App commands execute.

Cross-process faults cover lost replies at both dispatch boundaries and changed
CLI generation/created-exec state after custody capture but before acknowledgment.
The original writer refuses start and retry despite a valid evidence receipt.
After an uncertain exchange and writer exit, the observer retains the original
pending/created facts without opening a replacement journal or acquiring its
vacated lock. This is not active-command admission, native recording, App
restoration, independent termination, or installed lifetime qualification.
Source/runtime qualification, explicit action scope, independent
execution bounds, authenticated native Ready, and exclusive failure handoff
remain separate requirements before selecting any active command.

## Remaining gates before a human scanner/audio test

1. Complete real platform/publication and native-I/O lifetime qualification.
   Accepted startup, request/initial dispatch, outer service, native/recording
   handoffs and all three recovery routes are joined in local tests. App
   publication provenance, platform responses and native transport/file results
   remain explicit synthetic boundaries. Active checkpoint/file/native-I/O tests
   also remain complementary rather than proof of one complete installed lifetime.
2. Bind the separate App source inventory to a reviewed, independently supervised
   entrypoint with explicit interpreter/dependency provenance; passive preparation
   must remain passive. The read-only inventory alone does not enable this.
3. A fresh isolated fixed-command end-to-end lifetime with actual native
   returns, full source/runtime pins, and independently bounded termination.
4. Installed provenance and measured collection timing, plus a reviewed
   fresh-case staging procedure that preserves historical containers/evidence.
5. Fresh human readiness for a finite scanner/audio trial. Historical readiness
   or an earlier successful test is not permission to replay a consumed case.
