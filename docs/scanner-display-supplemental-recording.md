# Finite supplemental recording qualification (internal, not activated)

The [finite browser-playback trial](scanner-display-supplemental-acceptance.md#finite-playback-only-result--2026-09-22)
passed on 2026-09-22. It deliberately did **not** start a recording. This document
defines the next recording/finalization qualification boundary; it is not a
runnable live procedure or a claim that recording has passed through that path.

The existing schema-1 and schema-2 host plans still require recording to be idle
and both recording inventories to remain unchanged. They must not be used to
start a recording. Do not report an active recorder as idle, ignore its root,
remove files from a protected inventory, or modify a sealed/consumed case.
The normal App, old recordings, profile inputs, credentials and Pi configurations
are not changed by this work.

## Offline final-artifact evidence

`scripts/supplemental_recording_evidence.py` is a read-only, standalone component.
It is not part of the sealed host-service bundle, package startup, a daemon API,
or a recording/playback client. Importing or invoking it does not connect to a
scanner, start an App, create a recording, or dispatch recovery.

Before a future recording start, `capture_baseline()` captures every existing
file under one explicit recording root. It rejects any artifact using the new
case's reserved prefix, including an older timestamp, sidecar, collision suffix
or dot-prefixed metadata temporary file in any subdirectory. The baseline is an
immutable in-memory result for a trusted caller; its
durable storage, authenticity and binding to the installed candidate are separate
requirements, not properties supplied by this component.

The proposed filename template is
`sdsctl-acceptance-<32-hex-case>-{timestamp}.wav`. This preserves the existing
recorder's required timestamp template. The final name is derived from the
accepted start receipt's timezone-aware timestamp, in that receipt's original
zone. The verifier does not use the current clock or rename the writer's output.
A different filename or automatic collision suffix fails verification.

After a separately confirmed stop/finalization, `verify_finalized()` requires:

- The same case and independently established daemon generation as the start
  receipt. The supplied endpoint hash binds metadata to the selected audio
  source without returning that private endpoint in the result.
- Exactly the baseline's files plus one expected WAV and its adjacent `.wav.json`
  sidecar. Every older file must retain its bytes, size, permissions and owners.
  No extra recording, leftover metadata temporary file or unrelated file is
  permitted. Nothing is deleted or repaired on failure.
- A complete stopped/inactive snapshot, exactly one completed recording, no error,
  positive bounded packets/samples/durations, and fully drained sink counters.
  Submitted and written byte counts must both equal twice the PCM sample count;
  drops, queued bytes, underflows, overflows and callback statuses must be zero.
- The recorder's canonical 44-byte RIFF/WAVE header, mono 8 kHz 16-bit PCM, exact
  data length, and no truncated or trailing bytes. This is intentionally not a
  permissive general-purpose WAV validator.
- A complete version-1 recording sidecar with matching filename/format, source,
  start/stop instants, statistics and reliability counters. Duplicate JSON keys,
  non-finite constants, unknown schema fields and coercions such as booleans in
  place of integer counters are rejected.
- Two matching complete file inventories around the payload validation, held
  no-follow ancestor descriptors, stable file reads, and unchanged selected-root
  identity during the observation. Links, hardlinks and special files are refused.

The overall observation has a five-second cooperative budget. The existing
inventory helper retains its two-second per-capture budget, 4,096-file,
8,192-entry, 16-level and 64 MiB aggregate limits. This component reserves room
for the two new files and allows older files up to 16 MiB each. The new WAV has
a maximum of 180 seconds of 8 kHz mono 16-bit samples; its sidecar is bounded to
64 KiB. Kernel I/O stalls still require an independent outer process deadline.
These are qualification limits, not new product limits or retention rules.

The successful immutable result contains only the case/generation, WAV and
sidecar hashes, sample/packet counts, audio duration and old-file count. Failure
returns a fixed sanitized exception and no partial success. It never rewrites a
header, synthesizes metadata, retries recording, removes a partial file or
promotes an unconfirmed result to a pass.

### What that evidence does not prove

This is stable observed **file-content evidence**, not an atomic filesystem
snapshot or protection against a privileged actor replacing data between reads.
It does not attest to directory topology or file-inode continuity between the
pre-start and final inventories. Existing empty directories are not inventory
entries. Directory identity protection during each read and the selected root's
identity across captures are separate checks.

A valid WAV cannot establish that its writer has exited. A supplied generation
or stopped snapshot is not self-authenticating. Transport reliability counters
may include earlier faults; exact agreement proves consistency, not a clean
trial. The result does not claim scanner continuity, browser playback, audible
recording quality, successful optional scanner reads, restart persistence, or
normal-App restoration. Each still requires its own evidence.

## Offline native start/stop owner

`scripts/supplemental_recording_owner.py` now provides an independently tested
native start/stop controller. It is **not connected** to the acceptance launcher,
host service, public daemon API, or normal App startup. Importing or invoking the
module starts nothing. Its explicit Python operations are exercised only by
local fixtures at this stage.

The controller binds one actual `DaemonRecordingManager` to the reserved
template, endpoint hash, recording root identity and pre-start file baseline.
It requires a fresh idle manager, disabled recording organization/overwrite,
and a separate empty private journal. The trusted caller must establish the
native process/generation and exclude all other recording mutators; a supplied
generation string is not independent process attestation.

One frozen monotonic plan fixes preparation, latest start, scheduled stop and
latest completion. Total planned time is at most 180 seconds. Start and stop
have cooperative two- and five-second acknowledgment budgets within those fixed
deadlines. There is no timer thread, I/O cancellation, boot-resume authority or
automatic extension. A qualified caller still has to schedule stop, and an
independent outer supervisor must terminate a blocked or abandoned process.

Before each native dispatch, the controller exclusively publishes a private
intent, flushes it, and fsyncs both file and journal directory. It then rechecks
identity, state and deadline. Successful acknowledgments are bound to the exact
case filename and start time, checked against current native state and retained
as private receipts. Paths and endpoints are hashed in preparation evidence;
arbitrary failure text is never surfaced by the controller.

Lost, late, malformed or failed acknowledgments consume the operation. So do
publication failures and changed preconditions. There is no retry, reopened-case
dispatch or implicit cleanup stop. A nonempty/partial journal is preserved and
refused, even if the previous attempt did not reach dispatch. Advisory locking
and a nonblocking local lock reject cooperating concurrent/reentrant owners.
Links, unexpected files, altered receipt contents and replaced directories fail
closed. These checks do not protect against a privileged actor discarding the
entire case and its evidence.

Receipts record observations, not final acceptance. A slow receipt publication
can finish after the deadline and still leave a receipt on disk while the
controller returns an unconfirmed result. A future host reconciler must not infer
timely success, writer exit or restoration from receipt presence alone. Likewise,
a stopped native receipt with zero samples is possible but fails the separate
artifact verifier. Closing the controller only releases journal descriptors;
native daemon shutdown and independent recovery remain responsible for a writer
left active by an uncertain start or stop.

Local tests cover native PCM finalization, durable-intent ordering, partial
publication/fsync failures, unknown/late acknowledgments, deadline boundaries,
schema faults, journal/root replacement, concurrent calls and descriptor cleanup.
Real localhost RTP/PCMU cases also exercise the controller beside successful or
timed-out FQK/DTM reads; the surviving audio consumer continues after recording
stop, and the resulting pair passes the separate file verifier. These are not
hardware or audible acceptance tests.

## Offline active-file monitor

`scripts/supplemental_recording_monitor.py` adds a read-only observation for the
interval between an acknowledged start and final artifact verification. It does
not replace the existing live host inventory guard. Each observation verifies
every old file, with only the following exact new root-level entries permitted:

| Caller-qualified stage | Permitted new entries |
| --- | --- |
| Recording | One case/start-bound WAV, which may still be zero bytes because native writes are buffered. |
| Finalizing, before publication | That WAV and optionally one adjacent native metadata temporary file. |
| Finalizing, link publication | That WAV and both metadata names, proven to be the same inode with exactly two links. |
| Finalizing, publication complete | That WAV and one adjacent metadata sidecar with exactly one link. |

The temporary name must be `.<expected-wav>.json.<8-character-native-token>.tmp`;
this is not a root or wildcard exclusion. Other-case files, nested new files,
multiple temporary files, wrong types/owners/modes, unexpected links, renamed old
files and changed old contents are refused. A new baseline also refuses a
leftover temporary file from the same case instead of accepting it as old data.

The caller supplies separately qualified writer UID/GID and the expected WAV
mode. Supported modes are 0600, 0640, 0644, 0660 and 0664; the native temporary
metadata remains 0600. This recognizes the real writer's umask-derived mode
without changing it or accepting executable/world-writable output. These checks
do not themselves prove which process opened a file.

One observation has a two-second cooperative budget and retains the inventory
entry/depth/byte limits. It reserves room for three new entries, including both
publication links, so a recording-capable preparation must limit the baseline to
4,093 files. Dynamic growth is included in the aggregate byte bound. WAV and
metadata retain their individual 180-second PCM and 64 KiB bounds.

A trusted previous in-process observation binds the same case, complete start
timestamp, endpoint, generation, root and writer. It rejects shrinking/replaced
new files, publication regression, changed published metadata and a return from
finalizing to recording. Intermediate states may be missed by sampling; they are
not invented. Directory changes during sampling can produce an unconfirmed
observation. A later read must stay within the **original** deadline and cannot
reset it. The monitor never retries, finalizes, cleans up or signals anything.

Successful observations are not atomic snapshots, durable restart receipts,
authenticated writer/process evidence, audio-quality checks or a finalization
verdict. Buffered WAV bytes and headers deliberately are not treated as stable
audio contents. Linked/published metadata is hashed but only the separate final
verifier validates its schema and agreement with the complete WAV and stopped
receipt. Native writer integration tests cover the actual temporary-file,
flush/fsync, link and unlink sequence; fault tests cover races and descriptor
cleanup. No live recording acceptance is implied.

## Offline candidate API restriction

`scripts/supplemental_recording_api.py` provides a permanently restricted native
API subclass for a future dedicated candidate. It keeps the same real recording
manager, so status and library reads remain truthful, including active and failed
states. It does not report an active recording as idle to satisfy a guard.

An explicit observation-only allowlist is applied at the native shared dispatch
boundary used by payload, JSON, control and authorized-JSON entry points. Peer
permissions are intersected with that list and redaction is preserved. Native
capabilities reflect the same restriction. Recording start/stop, scanner controls
(including hold-state wire queries) and profile reload are unavailable.
Supplemental demand is also denied by default. One explicit internal binding may
enable only that operation for the **same exact native finite acquisition owner**,
runtime, frame feed and delivery service. Binding neither arms acquisition nor
creates demand. Failed binding cannot be retried; changed owner/feed/service
bindings fail closed. Peer permissions are still intersected, and the native
arm/window/quota/context checks remain authoritative for every demand.
New product operations are not implicitly granted. Manager/runtime
replacement fails closed. Restriction stays in place after the finite owner is
closed or failed; a client cannot retry an uncertain recording operation.

The finite owner and normal daemon shutdown retain the original manager, not a
facade or second recorder. Local tests exercise all request entry points, real
Unix clients, successful native PCM finalization and failed start/metadata paths.
This component is not installed in the current launcher or normal product. It
does not authenticate clients or prevent trusted in-process code from calling a
manager directly. The narrow offline assembly below qualifies its supported
transport bindings; the installed launcher and independent host supervision
remain separate gates.

## Isolated process-failure qualification

An additional Linux-only fixture runs the actual recording manager and finite
owner in a parent-owned child process with synthetic PCM and test clocks. The
parent opens a pidfd before releasing the stop barrier. It checks orderly exit,
abrupt exit while active, blocked stop, stalled metadata publication, a lost stop
acknowledgment, and a lost return after a stopped receipt was already fsynced.
The blocked cases are terminated and reaped under the fixture's separate deadline.

Only orderly acknowledged finalization is submitted as a successful content
verification. Complete-looking files, receipt presence and independently proven
process exit are kept distinct from a confirmed operation result. Each failed
case preserves its files and consumed intents and refuses another owner attempt;
older recording bytes stay unchanged. These tests prove component behavior in
owned local subprocesses, **not** a recording-capable host supervisor, native
daemon shutdown integration, container/pidfd recovery or normal-App restoration.

## Offline fixed-deadline schedule

`scripts/supplemental_recording_schedule.py` composes the prepared native owner,
restricted API and file monitor into one explicit synchronous run. The caller
must already have independently qualified process supervision and readiness;
constructing the scheduler does not start anything. It checks that the same
manager, runtime, baseline and immutable plan remain bound throughout the run.

After one start, it polls cancellation and native state at most every quarter
second between calls and samples the full file monitor about once a second. At
the original scheduled stop time it invokes the owner's single stop, observes
publication completion and separately verifies the finalized pair. No wait,
observation or slow call extends the original stop/finish deadline. Expired or
cancelled observations do not begin another verification step. A successful
result means only that this schedule acknowledged start/stop and verified file
contents; the shared runtime intentionally remains running.

Cancellation, interruption, changed bindings, unexpected native state, a changed
old file, an unconfirmed observation or a late result consumes the run. The
controller is closed even if cancellation happened before start, preventing a
new scheduler from reusing that prepared controller. This only releases receipt
descriptors: it does not stop or repair the writer. Native shutdown and an
independent outer termination deadline remain mandatory, including when a call
blocks and cannot check cancellation. No automatic retry or recovery operation
is dispatched here.

Tests combine deterministic clock/fault cases with an actual monotonic/event-wait
run using the real recorder and synthetic PCM; the latter finalizes without a
manual stop. No launcher has installed this scheduler.

An optional exact native acquisition owner can now be supplied by the offline
assembly. Only **after acknowledged recording start**, a fresh cached-context
check and a remaining-time check may it be armed. The full read window must fit
before the original recording stop; a slow start or context check never shifts
that deadline. Arming still sends no GET requests. Reads require a separate
explicit consumer demand through the narrowly bound API. The scheduler checks
the native acquisition status on each loop; only ordinary window expiry or
quota exhaustion is an accepted ending. Cancellation, disconnect or another
ending remains unconfirmed. The optional window is ended on every consumed
terminal path, including failure. It cannot be rearmed by replaying the schedule.

Recorded read counts and end reason are separate from the file verdict. A valid
recording with zero requested reads is not a passed supplemental-read test, and
a counted optional read is not proof of a successful reply.

## Offline native daemon assembly

`scripts/supplemental_recording_assembly.py` now coordinates the actual
`DaemonProcess`, `DaemonRuntime`, native recording manager, shared frame owner,
restricted API and finite schedule. It does not replace the scanner/runtime,
install CLI hooks, start anything on import, or provide a live command. The
ready/request interface is an **in-process fixture gate**, not an authenticated
or durable host/operator protocol.

The supported graph requires the same manager for the API, native event stream,
recording-file server and daemon shutdown. The original PCM fanout must contain
only its matching, initially empty router. An optional native PCMU server must
subscribe to the same audio transport. Existing services, changed bindings,
pre-existing PCM sinks and an already-started runtime are refused. This first
assembly rejects destination coordinators/reloaders, MQTT, remote services,
live-audio encoders and waterfall services; it does not silently remove them
from a normal product configuration. Consequently it is not a drop-in wrapper
for the ordinary CLI's complete construction path.

Readiness requires native startup, the expected cached scanner identity and a
supported cached display context. Readiness alone starts no recording and arms
no reads. The one explicit request creates a fixed monotonic recording plan and
durable owner journal. Successful finalization precedes the request for ordinary
native shutdown. A pre-start timeout cannot lose its stop request when native
signal-handler installation resets the signal controller. Cancellation and
failures request shutdown, retain evidence and never retry the scheduled start
or stop. A two-second worker join is bounded, but native shutdown or kernel I/O
can still block; an independently qualified process deadline is mandatory.

Local tests exercise the real native process, loopback scanner UDP, Unix API and
recording-file/event servers, PCM fanout and WAV/sidecar finalization. Additional
cases use actual localhost RTP and a real PCMU Unix consumer with synthetic RTSP
negotiation: FQK/DTM replies and timeouts coexist with the same eight received
audio packets, exactly decoded WAV bytes, continuing PSI and one audio session.
Native cleanup closes servers, sockets, the optional reader and the audio source.
These are not physical-scanner, real-browser, audible or installed-App results.

Separate parent-owned subprocess tests pin a Linux pidfd **before** releasing the
start gate. They exercise successful native shutdown, blocked scheduled stop,
blocked metadata publication, lost stop return and lost receipt-publication
return. The real native SIGTERM handler may request cleanup without causing exit;
the parent independently escalates to SIGKILL and reaps the exact child under its
deadline. Ordinary native `close()` may finalize a recording even when the
scheduled worker never acknowledged stop. Such complete-looking files, a stopped
receipt left before a lost return, and proven process exit are **not** promoted
to a successful recording verdict. No partial artifacts are deleted or repaired.

Still required before live use: the separately reviewed CLI/source assembly,
durable generation-bound operator gate, recording-aware host collection and
sealed plan, and isolated installed-container qualification. The offline recovery
policy below is not an installed permission. Existing handoff plans and their
idle/inventory checks remain unchanged.

## Offline recording-aware recovery policy and dispatch

`scripts/supplemental_recording_handoff.py` defines a separate pure policy and
private journal. `scripts/supplemental_recording_recovery.py` connects it to the
existing fixed-command executor, tracked CLI executions and exact init-process
receipt machinery. Tests use an injected local fake host, not Home Assistant or
Docker. No CLI entry point, background loop, automatic recording start or new
live host-plan schema is installed by these modules.

The new journal requires `prepare_recording` and entry schema 2. This is a
**journal format**, not the existing schema-2 browser-audio host plan. Each
reader rejects the other journal format, including an attempted schema-only
downgrade. The base policy retains its recording-idle rules, default 24-event
bound and original entry format. The recording journal allows at most 48
8-KiB entries and retains exclusive locking, no-follow paths, hash chaining,
file/directory fsync and no replay of previously emitted actions.

The recording contract binds a new UUID case to hashes of its old-file baseline,
recording root, audio endpoint and writer identity. Normal-App protection remains
unchanged. A future recording-aware collector must separately verify the
candidate's immutable source/image/options/profile protections and its complete
changing recording inventory. The old collector's full-inventory pin cannot be
used by omitting the recording root or pretending it is unchanged.

After candidate readiness and live init-process binding, a separate
`authorize_recording` event requires a fresh, complete, healthy, recording-idle
observation and the original pristine inventory. It durably permits one
generation and sets a fixed maximum 180-second recording deadline within the
original candidate window. This event starts nothing and is not acknowledgment
that recording began. Failed publication consumes the controller; reopening
cannot reauthorize the attempt or extend its deadline. A future operator adapter
still must bind its one-use child trigger to this durable permission.

Qualified file observations distinguish:

- **Pristine:** every original file and the exact original inventory still match.
- **Active/finalizing:** unchanged old files plus only the bounded, exact
  case/start-bound WAV and native metadata publication states.
- **Finalized:** separately verified complete content and timely native success
  acknowledgment, not merely the presence of a WAV or stopped receipt.
- **Retained:** scoped failed/partial artifacts preserved after independently
  proven writer exit. This is not a successful artifact verdict or an exclusion.
- **Unknown:** no authority to advance or dispatch recovery.

These are qualified collector inputs, not assertions authenticated by the policy.
The offline collector and checkpoint chain below now provide file collection and
reconstruction; integration with a sealed live host plan is still required. The policy
rejects another generation, changed contract, disappearing new evidence,
regressing file stages, altered final evidence and changes to preserved evidence
during normal-App restoration. Terminal retained failure cannot become a later
invented finalized success.

The candidate's active-recording flag remains truthful throughout. Only the
authorized candidate can be stopped with recording active: at its fixed bound,
an explicit finish request or a confirmed unhealthy state. Native shutdown may
finalize the writer, but submitting a stop never proves that it did so. Invalid
or missing observations cannot extend the recording recovery deadline. An
independent process-level termination mechanism remains necessary if collection,
native cleanup or the host service itself stalls.

Before either next scanner owner starts, the policy requires the previous init
process's durable exact-exit receipt, a separately completed tracked CLI
execution, fresh App state, idle jobs and all protections. A nonzero/lost CLI
reply is reconciled from observed state without retrying the command. A missing
container or stopped-looking App is insufficient. A healthy restored normal App
also requires the corresponding CLI execution to have exited.

The normal App may be restored while a failed recording is retained, provided
its scoped preservation and independent owner exit are both verified. The
terminal phase records **restoration** separately from recording outcome
`not_attempted`, `unconfirmed` or `verified`. None means audible quality,
successful supplemental replies, scanner continuity or complete live acceptance.

Every emitted command is journaled before a second fresh observation. For active
and finalizing files, two independently qualified samples may have different
growth-evidence hashes: normal audio growth must not consume and withhold the
one shutdown intent. Contract, stage, generation, truthful recording state and
all other safety preconditions must still match. Changes or lost replies consume
the intent without retry. This does not permit skipping either full file check.

Offline tests cover the actual journal/executor/tracked-process bridge using
fake host I/O, natural and requested/deadline shutdown, incomplete CLI or init
exit, lost replies, restart reconciliation, publication failure, mismatched
generations, inventory uncertainty, source/protection drift, expiry and format
separation. Previously sealed installed bundles and consumed cases are untouched.
Shared base modules only gained explicit subclass hooks; their default behavior
continues to be regression-tested. New source hashes must be independently
qualified before any future bundle installation.

## Offline protected-file collector and restart evidence

`scripts/supplemental_recording_protected.py` connects actual bounded filesystem
observations to the recording-aware policy's `Files` inputs. It does not launch,
stop or signal anything. It distinguishes a pristine root, growing recording,
metadata finalization, preserved partial output and acknowledged final content.
It never excludes the recording directory from protection or recaptures an
in-progress recording as part of a new pristine baseline.

Before recording, the collector can exclusively save the original baseline in
one private `0600` manifest inside a separate `0700` directory. File and directory
are fsynced. Every old file is rechecked before and after publication; ancestors,
directory identity, manifest identity, ownership and permissions are held or
rechecked without following links. Any incomplete publication remains for review.
The manifest's hash and recording contract must be pinned separately in a future
host plan; the manifest cannot authenticate itself. Root paths and old filenames
remain private and do not appear in failure messages.

Reload validates that original manifest against both external pins, even while
the current recording grows. The manifest is limited to 2 MiB, 4,093 old files,
16 MiB per old file and 64 MiB total. Before start it reserves room within that
total for the entire permitted new WAV and both native metadata publication
names, rather than exhausting the observation budget halfway through recording.
The reserved case prefix is forbidden among
old files, including hidden native metadata temporaries. Decoding rejects
duplicate fields, unknown fields, noncanonical JSON, unsafe paths, numeric
coercions and unsupported permissions. Existing inventory/monitor limits still
apply independently.

Active/finalizing observations use the qualified native monitor. Preserved
partial output is hashed in full twice and bracketed by complete monitor checks:
empty or malformed WAVs, incomplete metadata and the native intermediate
two-link metadata publication can be retained, but unrelated files, replacements,
unsafe links, changed old files or unstable reads cannot. This is preservation
evidence only. The recovery policy separately requires exact writer exit before
accepting it, and records the recording outcome as unconfirmed.

Finalized content additionally requires the native stopped snapshot and a
**separately authenticated successful-return acknowledgment** bound to the case,
generation, contract and start timestamp. A `stopped.json` receipt is not that
acknowledgment. The host still must verify the source, fixed deadline and complete
successful return; this collector does not turn a caller-supplied hash into
authentication. Audio duration and elapsed recording time must both fit the
contract. Complete WAV/sidecar hashes are checked again after the last progress
monitor because a same-size WAV rewrite would pass size-only continuity checks.
The root remains held across final verification. A good file is still not proof
of OS process exit, audible quality or normal-App restoration.

`scripts/supplemental_recording_checkpoints.py` durably retains active/finalizing
observations in a separate append-only hash chain. Each entry contains the exact
qualified file-proof hash and strictly decoded progress, bound to the original
contract, root and start expectation. File and directory fsync precede a returned
tip. The host must independently retain that exact count/hash tip before restart
recovery may rely on it. The chain has no recording or dispatch authority.

Reload checks every entry and every transition, including file identity and
nondecreasing length, metadata publication order, frozen published metadata and
temporary-name continuity. Shrinkage, replacement, stage regression, stale tips,
missing/extra entries, partial writes and lost publication returns are refused.
An additional on-disk entry cannot be silently adopted as an acknowledged tip.
No entry is overwritten, truncated, deleted or repaired. The chain is limited
to 192 entries of at most 16 KiB; exceeding the bound is uncertainty, not permission
to discard history. Collection, manifest operations and checkpoint operations
have five-second cooperative budgets; blocked kernel I/O still requires the
independent outer process deadline.

All private evidence directories must be prepared before the native owner pins
its ancestor identities. Creating a new sibling directory later can change an
ancestor's link count and correctly invalidate that protection.
This also applies to automated qualification: overlapping test sessions that
create/remove directories beneath shared temporary ancestors can invalidate one
another's evidence. Serialize these filesystem/lifecycle runs or isolate their
mount ancestry; do not relax the identity guard to obtain a passing test.

Local integration tests now combine actual native PCM files, original baseline
reloads, durable progress checkpoints and the recording-aware recovery journal.
They distinguish successful finalization, lost native acknowledgment, partial
output and missing process-exit evidence. App/CLI lifecycle and init witnesses
in these integration tests remain fake; this is not installed-container testing.
The source bundle, live observer, independent tip binding, native acknowledgment
authentication and restricted operator launch path are still not installed.

## Offline recording-aware host observation

`scripts/supplemental_recording_host.py` joins the existing Docker/Supervisor
observations with the actual recording collector. Its candidate uses an explicit
`CandidateSeal`, containing the fixed context/package/profile hashes plus the
recording contract. It is deliberately incompatible with the ordinary `AppSeal`.
Growing recording proofs accompany every observation separately; they are never
put into an old full-inventory field or omitted from protection. The normal App
continues to use its original complete recording inventory and pin.

The joined observer retains the existing version, image, container-incarnation,
Core, additional-owner, jobs, restart-policy and elapsed-time checks. It requires
the audio-specific network policy and cannot downgrade to reader-only networking.
Each running candidate's recording evidence must match its observed generation
and sealed contract. Changed source/settings pins are reported without executing
the native probe inside that unqualified App. Missing or concurrent observations
cannot reuse evidence left over from a previous read.

Cached native recording flags remain truthful and separate from artifact stage.
For example, finalized-looking files plus a missing native reply do not become
healthy/idle evidence. A stopped Docker container or retained partial artifact
does not supply the independent init-exit receipt required by recovery policy.

The file join checks that candidate recordings cannot overlap either App's
profile, context or data trees, or the normal recording root. Its stage and
acknowledgment inputs must come from separately qualified durable host evidence;
this component does not authenticate assertions or publish checkpoint tips.
Tests use synthetic Docker/Supervisor responses, fixed-path routing fixtures and
actual temporary growing/retained files. No host installation is implied.

One namespace constraint is explicit: a host path beneath
`/mnt/data/supervisor/media` is not the same path string as its native `/media`
alias. Their root-path hashes must not be compared as if equal, nor silently
rewritten. `scripts/supplemental_recording_projection.py` now prepares two explicit
canonical manifests from the same original baseline. It changes only the declared
root alias, preserving case, root inode/ownership, every original file, writer,
audio endpoint and duration bound. Both original manifest/contract hashes and the
projection hash remain distinct and must be independently pinned.

This is pure preparation, not a new filesystem capture or permission to adopt
new files. The original host manifest is never overwritten or resealed. The
mapping validator requires the exact running image/incarnation and a writable
bind from `/mnt/data/supervisor/media` to `/media`, refusing duplicate, parent,
nested or changed mounts. It does not collect a fresh inspect itself. A native
process must separately verify its complete baseline in its own namespace;
comparing declared manifest bytes on the host does not provide that observation.

The future sealed adapter still must qualify both namespace observations, native
success authentication and the independent host binding before a live start.
Projection tests use synthetic inspect data, not an installed container. The old
five-wrapper package inventory also does not qualify a future recording-specific
launch bundle by itself.

## Offline dedicated native construction

`scripts/supplemental_recording_construction.py` constructs the narrow candidate
directly from an explicit specification and independently pinned baseline/profile
inputs. It does **not** modify or wrap the ordinary daemon CLI, which constructs
destination/reload services even without configured destinations. The dedicated
specification cannot accept and silently discard ordinary daemon arguments.

Construction validates the endpoint, firmware/window/quota, writer identity,
maximum recording duration and complete original inventory. Socket and receipt
directories must already be private, empty and disjoint from recordings/profile
storage and each other. Explicit Unix socket paths do not inherit environment
defaults. DNS names and IPv4 addresses are supported without resolving them at
construction time; ephemeral RTP ports are restricted to loopback fixtures. A
future installed plan still must qualify its actual fixed RTP publication.

The constructed native services are the scanner/runtime, recording manager,
restricted API, events, saved-recording file server and same-source PCMU server.
There are no destination/reload, MQTT, remote-control, encoder or waterfall
services to disable after construction. No network sockets, threads, recording
or scanner commands start merely by constructing the object graph.

An explicit native `run()` starts the normal shared scanner/audio runtime;
readiness does not start recording or optional GET requests. The existing
assembly's separate one-use start request and explicit consumer demand remain
required. Pre-run construction failures close only objects already constructed.
Once native run is attempted, that lifecycle owns cleanup: this context does not
repeat an uncertain stop or finalization on exit. Independent process termination
and restoration remain mandatory.

New loopback integration tests construct the actual native network transports,
use synthetic RTSP negotiation plus real UDP RTP/Unix IPC, and verify exact
decoded WAV bytes with and without optional-read demand. They prove one shared
audio session, denied public recording mutations, preserved old recordings and
native cleanup. They do not qualify an installed CLI, authenticated operator
trigger, container guardian, physical scanning or audible playback.

## Offline independent host recording ledger

`scripts/supplemental_recording_binding.py` durably pins the original host/native
manifests, projection, separate source/plan hashes and host boot identity. This
ledger is independent of native receipt files and recording-progress storage. It
refuses Supervisor-managed paths; an eventual deployment must also prove the
host evidence directory is not mounted into either App or exposed to a client.

One exclusive, file-and-directory-fsynced start intent binds the exact candidate
generation, recovery-authorization evidence and fixed start/finish deadlines.
The start acknowledgment must arrive before the start deadline; completion must
arrive before the original finish deadline. Monotonic regressions, wrong cases,
changed generations/endpoints/contracts and duplicate intents are refused. A
publication failure or cancellation invalidates the live writer. Files are never
repaired, truncated, deleted or retried. Reload returns evidence only, never an
actionable controller—even if the ledger contains only preparation or a start
intent with no acknowledgment.

Returned progress tips are pinned only after complete checkpoint replay. Each
extension verifies both the newly returned tip and the earlier independently
pinned prefix, preventing a rehashed replacement history from superseding it.
An additional complete checkpoint not pinned by this ledger cannot be adopted
on restart. The host ledger is bounded to 197 entries of at most 8 KiB; the
existing progress chain retains its separate 192-entry bound. Reads and writes
have five-second cooperative budgets, not a substitute for outer termination.

Native start/completion success is still a separately authenticated input to
this component. A digest or `stopped.json` does not authenticate it. The ledger
does not dispatch recording, prove process exit, inspect finalized WAV content,
authorize App restoration or claim audible success. An acknowledged completion
and an abandoned case are distinct terminal records. A host reboot changes the
monotonic-clock domain and requires separate administrative recovery; it cannot
resume this ledger under a new boot ID.

Tests use actual private files and checkpoint chains, with synthetic namespace
bindings and native-success assertions. They exercise lost returns, partial
writes, fsync failure, cancellation, malformed chains, prior-history replacement,
unsafe links, changed directories, fixed deadlines and read-only restart. This
qualifies durable host binding offline; the native authentication channel and
installed source/guardian/operator integration remain the next live gates.

## Offline exact-child native-return channel

`scripts/supplemental_recording_channel.py` adds an anonymous Unix `SEQPACKET`
channel for the future native guardian. It has no listening pathname, public
API, process launcher, signal sender or recording command. The caller must pass
only the sender endpoint to its source-pinned child and close the unused endpoint
in each process. The receiver is bound while that exact owned child is alive,
using its parent relationship, start ticks and a retained pidfd.

Per-message kernel `SCM_CREDENTIALS` must match the child's PID, UID and GID.
Socketpair `SO_PEERCRED` alone would identify the creating parent after a fork,
so it is deliberately not used as the sender identity. A descendant with an
inherited descriptor cannot report as the expected child. Unexpected descriptor
passing is rejected and any installed descriptors are closed, including truncated
ancillary data. Messages are canonical, at most 8 KiB, and bound to the original
native manifest/contract, generation, projection and source hashes.

There are exactly two allowed success messages: started, then completed. Both
receipt and validation must finish before their fixed deadlines. Early, late,
duplicate, malformed, missing, wrong-peer or wrong-case reports consume that
receiver without retry. Sender operations have a maximum 200 ms cooperative
socket timeout; validation and publication do not extend the existing run window.
The channel cannot interrupt blocked kernel I/O or replace the outer guardian.

The optional **in-process** native assembly request now accepts a separately
constructed return sender. It validates the original baseline/writer/endpoint,
generation and sufficient remaining fixed window before accepting the request.
The scheduler reports start only after `FiniteRecordingOwner.start()` actually
returns. The assembly reports completion only after final content verification
and successful native cleanup. Normal callers that do not supply a sender retain
their existing behavior. Nothing is added to the public daemon API or CLI.

Isolated child tests now connect this path to the actual dedicated construction,
native lifecycle and localhost RTP writer. A lost return after writing
`started.json` produces no start acknowledgment. An exception after the native
process closes produces no completion acknowledgment even though `stopped.json`
and finalized output exist. Successful runs report the exact decoded sample
count and are separately reaped by the test parent. Kernel-credential identity
does **not** prove that the child executed the approved source; the installed
fixed launcher and full source inventory remain mandatory qualification gates.

This channel is not yet wired into an installed guardian, a durable operator
request or a recording-capable host plan. Receiving a report
is not proof of process exit, unchanged current files, audible quality or App
restoration. Tests do not contact the physical scanner.

## Offline projected return-to-host bridge

`scripts/supplemental_recording_bridge.py` joins the exact-child receiver to the
independent host ledger. It accepts only a new, already durable start intent with
the same source, projection, generation, native manifest and fixed deadlines.
Each operation replays the host evidence before consuming the next actual
kernel-authenticated receive. It does not accept a caller-supplied serialized
`Received` object as authentication or infer a lost return from native receipts.

The start's native plan must follow the host intent. The exact start expectation
has the same case, generation, endpoint and timestamp in both namespaces; only
the explicitly projected root/manifest/contract hashes differ. Completion maps
the acknowledgment to the original **host** contract, independently verifies
current host files and every old file, compares all native artifact fields to
the host result, and replays any independently pinned progress checkpoint.
An unpinned later checkpoint is not adopted. No fresh baseline is captured.

Receipt, verification and durable host publication must return before the fixed
deadline. A lost or late publication return consumes this bridge even if a
complete log entry exists. Neither that entry nor native `started.json` or
`stopped.json` enables another start/completion attempt. Cancellation also consumes
the bridge. These cooperative checks do not interrupt blocked I/O and still need
the independent outer guardian.

The qualification combines actual child processes, the dedicated native runtime,
localhost RTP, real WAV/metadata verification and the durable host log. Namespace
aliases are explicitly routed test fixtures, **not** installed mount-namespace
qualification. The bridge itself is a same-process adapter; the installed,
source-pinned cross-container relay/operator path remains a separate gate. It
does not grant authority to launch a scanner-owning process on the host.

If the start return is lost, the log remains start-intent-only and no successful
expectation is invented. Independent termination and conservative preservation
of unconfirmed output must still be qualified; missing acknowledgment must not
be mistaken for an idle recorder or permission to replay. Native/container-init
exit, restoration and audible quality remain separate from this bridge's result.

## Offline preservation after a lost start return

`scripts/supplemental_recording_preservation.py` provides a separate, read-only
failure path. The original `prepared.json`, `start-intent.json` and `started.json`
must agree with the sealed native baseline/projection, host intent, exact
generation, endpoint and fixed plan window. Bounded private files, no-follow
opens, strict canonical data, directory locking and two complete observations
reject unsafe or changing evidence. It does not scan for a convenient filename
or invent a timestamp if a required record is missing.

The result is a **preservation scope**, not a successful start acknowledgment.
It identifies the possible case-specific output for the existing retained-file
collector. Optional stop records may be incomplete, including empty files;
their bounded bytes are pinned but never interpreted as successful stop or
completion. The full original recording inventory and exact allowed new files
still require independent verification. No receipt is repaired or deleted.

The host log can close a start-intent-only case with
`preserve_unconfirmed_start`. Its successful-start and completion-acknowledgment
fields remain empty. The preservation scope has a distinct field, survives
read-only replay, and cannot be followed by start, progress or completion entries.
Lost publication returns and cancellation consume the writer without retry.

A real child-process failure test loses the return after native `started.json`
publication, independently reaps the child, scopes its unchanged receipts, and
verifies retained output through the original host projection. The recording
remains unconfirmed even if native shutdown finalized its files. This test does
not prove installed container-init exit or authorize restoration. Those remain
separate host-policy gates; missing/malformed required receipts still require
administrative review, never an inferred idle state or a fresh recording attempt.

## Offline explicit native launch preflight

`scripts/supplemental_recording_launch_plan.py` reads a bounded private
`launch.json` against independently supplied plan and source digests. Its closed
schema declares only the dedicated finite specification, original native baseline,
accepted profile pin, generation, projection and host-plan binding. Extra ordinary
daemon arguments, destination/reloader/MQTT/remote settings or environment input
are rejected, not silently removed or inherited.

Preflight verifies the original baseline and pristine files, the accepted profile's
deployment/configuration/source/state fingerprint, the exact scanner target and
the native read-window budget. It rechecks profile and plan bytes, requires existing
empty private socket/receipt directories and rejects overlap with immutable inputs
or recordings. No directory is created, no source is imported into the scanner's
profile storage, and no recording or runtime is started. Socket/DNS/runtime creation
is forbidden in the positive preflight fixture.

This is a launch-input reader, not an executable launcher or authorization scheme.
The fixed guardian must independently verify executable/source bytes and expected
plan digest before using it, then recheck relevant pins at the actual launch.
Source/plan hashes supplied by an untrusted caller do not authenticate themselves.
The operator protocol, cross-container relay, independent hard deadline and new
recording-capable host plan remain installation gates.

## Offline private native start gate

`scripts/supplemental_recording_control.py` connects the dedicated native
construction to a one-use ready/begin handshake. Two directional anonymous Unix
socket pairs carry per-message kernel credentials. Only receiving endpoints use
`SO_PASSCRED`, avoiding Linux auto-binding a name when such an endpoint sends.
Both sides pin live process identity and retain a pidfd; the guardian must bind
its child before releasing the child's initial launch gate.

Readiness means the native runtime is ready, the recorder is idle and supplemental
acquisition is unarmed. It does not create a recording or optional-read demand.
The separately source-authenticated host must durably record the intent first;
its hash alone is not authorization. Exactly one begin message must match the
original launch/profile/manifest/generation/source/projection/host-plan pins and
fit the unextended start, read and cleanup deadlines. Wrong peers, malformed or
late messages, cancellation and lost sends consume the attempt without retry.

The child installs and verifies parent-death `SIGKILL` on its main thread before
runtime construction. Its control worker requests recording only after the
authenticated begin. Native start and post-cleanup returns use the existing
exact-child return receiver. The execution path uses no ordinary CLI options,
implicit consumer or public operator API. Real fork/loopback tests verify native
PCM finalization, readiness-only cancellation, malformed begin and a delivered
begin whose guardian return is lost. Lost returns remain unconfirmed even when
the native receipts and recording files exist.

These are private offline functions, not an installed executable launcher. The
fixed source bundle, durable guardian launch guard, authenticated cross-container
relay and independent hard termination still require qualification. Parent-death
signals cannot bound a living but stuck guardian. A successful native return is
not child/container-init exit proof or permission to restore another owner.

## Required live ownership and recovery contract

The following remain live design gates, not installed host-service permissions.
The offline owner, monitor, recovery policy and API restriction above do not
install this contract:

| Phase | Required evidence and constraint |
| --- | --- |
| Before handoff | A new source-pinned case and explicit recording-capable plan; unchanged normal-App protection; separate candidate recording root; idle recording, fresh physical readiness, qualified RTP mapping and independent restoration supervision. |
| Before start | Fresh matching native/container identities; bounded old-file baseline; exact reserved template and root; one durably recorded start intent before dispatch. Existing browser playback must already have increasing counters and audible confirmation. |
| Start acknowledgment | Bind the one successful start to its generation, exact filename, start timestamp and endpoint. Unknown, late or malformed acknowledgment is not permission to start again. |
| Active window | One existing RTP/PCM owner fans out to the browser and recorder. The supplemental window/read cap cannot be extended. Recording progress, PSI/scanner freshness and stream faults remain distinct observations. |
| Stop/finalization | A bounded owner-controlled stop must drain the writer and publish a generation-bound stopped receipt before claiming finalization. Stop intent/acknowledgment uncertainty is preserved; no second writer or header-repair process is substituted. |
| After owner exit | Independently prove exact scanner-owning process exit, then inspect the new pair and every old file. Candidate shutdown must not depend on a working browser, desktop connection or a successful file-validation result. |
| Restoration and acceptance | Verify normal-App identity/health and its unchanged protections separately from the recording verdict. Preserve candidate evidence, including failed/partial files. Only after these checks may one bounded saved-file playback/listening test be offered. |

The active-file component now qualifies the native writer's intermediate states
locally. A future live contract still must bind those observations to the exact
writer/case and retain the independent termination deadline. It cannot substitute
a final-only inventory rule, blanket wildcard exclusion or always-true
recording-health flag.

The next implementation must also distinguish a failed artifact verdict from
ownership/recovery evidence: a truncated WAV does not prove that a scanner owner
is still alive, and a clean process exit does not prove that the WAV finalized.
No invalid artifact is silently accepted as the restored baseline. Failure
handling, allowed intermediate inventory changes, durable at-most-once dispatch,
and stopped/failed-case reconciliation need local fault-injection and isolated
platform qualification before another live handoff.

## Automated coverage and remaining gate

Local tests exercise malformed receipts, missing/extra fields, filename
collisions, metadata/WAV disagreements, file mutation, symlinks/hardlinks/FIFOs,
bounded reads, descriptor cleanup and sanitized failures. They also run the real
daemon recording manager on synthetic localhost RTP, checking decoded PCM bytes
and preserving older files.

Additional integration cases keep one real PCMU Unix consumer and the recorder
on the same RTP owner while native FQK or DTM acquisition succeeds or times out.
The finalized WAV/sidecar pass the content verifier in each case; subsequent
transport faults must not alter their frozen recording statistics or hashes.
These are local fixtures, not a real browser or physical scanner listening test.

The independent [manual audible saved-player check](web-dashboard.md#saved-player-browser-qualification)
also passed on 2026-09-22, using a private copy of an existing finalized recording.
It did not create a new recording or exercise this ownership path.

No live recording case is staged or authorized by these components. Activation
requires the separate reviewed ownership/recovery implementation above, a new
sealed case, installed-source/platform qualification and fresh user readiness.
All earlier hardware cases remain consumed and closed.
