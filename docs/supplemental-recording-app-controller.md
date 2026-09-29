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
| Read-only peer library inventory | Explicit peer handoff `Layout` | Separate 101-module closure including retained inputs/listeners, supervised descriptor delivery and original writer intake; requires a distinct expectations kind and explicit collector opt-in, never inferred by old callers |
| Separate peer runtime expectations | Independently pinned `Expectations` declaration | Clock-free template digest and explicit writer/observer image, source, interpreter, environment, argv and confinement pins; pure comparison, not observed qualification or consent |
| Retained peer input files | Separate read-only `Inputs` owner | Same original startup declaration and exact canonical expectations file, retained no-symlink paths and complete fresh reads; externally authenticated pins remain required |
| Per-role runtime comparison | External `PeerRuntimeQualification` | Original plan and selected original init/pidfd, full existing two-second runtime/confinement collection against that role's pins; no fabricated observer plan, launch, action grant or paired continuing-lifetime claim |
| Paired runtime comparison | External `PeerRuntimePair` | Both original role collectors and distinct original init witnesses, one plan/template/declaration/Engine client, two full fresh collections in a shared two-second window; no cached success, launch or continuing-lifetime authority |
| Original peer termination | Explicit outer `Custody` and `arm` | Both runtime comparisons and zero-offset domains, duplicate original peer handles, immutable BOOTTIME deadline and a separate kernel-only watcher; narrowly scoped peer stops, not App actions, native exits or restoration |
| One-use private descriptor delivery | Explicit bootstrap `Endpoint` and outer `deliver` | Kernel-authenticated original three-process context, separate descriptor handoff before Link evidence exchanges, both full runtime reads before/after under one shared bound and original watcher cancellation on failure; transport receipts, not Ready or App permission |
| Original writer channel intake | Uninstalled `supplemental_recording_writer_channel.receive` | Accepted baseline-derived original Startup, same continuing writer clock and original inputs, one-use bounded descriptor intake before journal assembly; no reconstructed writer, action grant or installed launcher |
| Retained private connection | Uninstalled `supplemental_recording_peer_connection.Connection` | One SEQPACKET connect through retained no-symlink directories to the exact original live peer; unchanged private pathname, socket flags and shared finite cutoff; no listener creation, reconnect, input provenance or active admission |
| Retained private listener | Uninstalled `supplemental_recording_peer_listener.Listener` and explicit `deliver_retained` | One exclusive socket in an already provisioned empty private leaf, one expected-peer accept, preserved pathname, and original listener cutoffs carried through both supervised descriptor handoffs; no installation authentication, replacement listener, App action or recovery grant |
| Explicit CLI evidence hook | Original Startup/IdleService/TrackedDispatch | Callback selected at construction and pinned across create/start, phase handoffs and recovery; no callback during assembly, new owner, peer authentication or action grant implied |
| Explicit pre-native candidate custody | Original AppService and original peer Link/CliCustody | Separate one-use exchange before launch publication; independently retained candidate init agrees with original journal and two observed CLI exits; no Ready or action grant implied |
| Read-only candidate assembly | `AppIdleCandidate` and explicit App `prepare_candidate` | Original running idle service, accepted Startup, borrowed clock and init witness; native App publication pins retained without dispatch or full qualification |
| Explicit service/native handoff | `AppService` and `AppNativePhase` | One reserved original IdleService, one top-level loop and original inbox lock; original prepared ledger checked before publication; independent Operator retained before cancellation |
| Explicit recording-phase handoff | `AppRecordingPhase` | Native cancellation retired before AppStart; exact returned start, original active inputs, progress checkpoints, separate finalized/preserved recovery routes and unchanged session clock |
| Native launch | `AppLaunch` | Original accepted Startup, candidate generation, published launch input, host journal, clock, init witness and full BootstrapHost |
| Native ready | `NativeReadyQualification` | Actual returned Ready and the one-time inventory of the four declared private sockets |
| Explicit pre-begin native custody | Original AppStart and original peer Link/CliCustody | One comparison of original authenticated Ready/dispatch/history to independently captured workers, before recording authorization or begin; no action grant |
| Explicit pre-cancel native custody | Original AppNativePhase and original peer Link/CliCustody | Same one-use comparison before withdrawing original transport; no AppStart, authorization, ledger intent or begin is created |
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
digest made under another profile kind are refused. Existing source module sets
and the passive preparation command remain unchanged.

All recording source profiles also reject special permission bits or group/other
write access on the selected source roots and every descendant directory,
including empty directories. Read-only files are insufficient when their parent
directory permits replacement. This policy is checked during the same
descriptor-based traversal as file hashing, on both complete reads; nothing is
chmodded or repaired by the observer. File-only digest schemas and generic data/
recording inventories are unchanged. External ancestor permissions, directory
ownership provenance and immutable-image qualification remain the outer owner's
responsibility; this check is not a new trust grant.

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
older 83-module controller profile and all four legacy module sets remain unchanged
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

`supplemental_recording_peer_host_source.Layout` separately names the 101-module
peer handoff library closure. It includes the original 90 modules plus the
retained input, connection and listener owners, bootstrap, outer runtime and
termination helpers, supervised delivery and original writer intake. Its roots
and module set are explicit constants, checked against the complete static
import graph; the observed files never determine which modules are accepted.
Complete product assets, double-read drift checks, private-file rules and all
combined read bounds remain included. Its inventory is standard-library-only.

This digest kind is deliberately **not** accepted by the original runtime
expectations kind or the default collector policy. A separately tagged handoff
declaration and explicit collector selection, described below, are both required.
All earlier source profiles reject the larger bundle. Local isolated import tests run the reviewed
repository code, not observed image files, with process creation, signalling,
network sends/binds and low-level file mutation blocked. They cover inventory,
roots and complete imports with and without PySerial and timerfd APIs. The
new inventory and adjacent source/expectations tests also pass on actual Python
3.11 and 3.14; interpreter/API compatibility is still not installed provenance.

The peer bundle also has a disposable staged-import fixture containing exactly
these helper files, the complete product source and an explicit PySerial copy.
An isolated child verifies loaded module origins before using them; deliberately
available checkout/installed copies cannot satisfy a missing staged import.
Tests withhold each newly required helper, the product package and PySerial,
refuse helper/package symlinks back outside the stage, and reject the older
permission/command modules that this library bundle does not contain. Source and
dependency inventories must remain unchanged, with no bytecode generated.
These are reviewed fixture imports with active operations blocked, not a fixed
production entrypoint, independent installation authentication or an installed
peer lifetime. Default runtime comparison still rejects this new inventory kind.

The same isolated staged-import fixture now separately selects the 104-module
preparation graph used by the passive writer/observer commands. Missing each
added preparation/permission module refuses even with a good checkout copy
visible; older 101-module bundles still cannot import those absent helpers.
Both source profiles retain their exact full/root closure and no-bytecode,
unchanged-source/dependency checks. The separate service command remains absent
from both profiles. Importing an explicitly inventoried passive helper does not
execute its command, qualify fixed-path exec/startup, or grant its action scope.

No library name is an admitted active entrypoint. A future fixed launcher must
explicitly select its final reviewed graph and input contract, authenticate the
installation and pins independently, retain the original startup and peer owners,
and satisfy the separate outer/platform, App-action and recovery-custody gates.
Hashing this larger bundle does not complete those steps or enable a human trial.

### Separate writer and observer runtime comparison

The uninstalled `supplemental_recording_service_runtime_expectations` codec
defines a distinct, bounded, canonical declaration. It pins the clock-free
startup template and the joint source-profile kind, plus **two explicit** role
records. Each record supplies image, source, interpreter and environment hashes,
an exact argv fingerprint, complete Engine configuration fingerprint, base-image
environment hash, architecture, timezone and hostname. Both roles use the same
reviewed joint source graph; their other pins may differ. Identical expected
images still require two records and do not prove two original processes.

The declaration's digest must be independently authenticated before observing
either peer. Its writer runtime must exactly match the original template's helper
runtime. A final-plan comparison checks the entire original template/plan/clock
join without reading a clock, resealing a plan or changing the original helper
pin to describe the observer. Strict fields and native types, canonical JSON,
duplicate-key rejection and byte limits apply. No defaults, file paths, live
process IDs or observed facts are inferred. No old plan, permission or template
decoder accepts this new document as its own format.

Argv fingerprints use the canonical object with schema `1`, kind
`finite-recording-peer-argv-v1`, and an `argv` list containing the exact argument
strings. They are not hashes of shell command text. The supplied tuple is bounded
to 32 printable ASCII arguments and 8192 bytes including terminators. An expected
startup command must be known before observing the process; its pin cannot be
learned from the observed cmdline, and a pre-start declaration must not depend
on a final clock-derived plan hash or its own digest. Command equality is not an
active-entrypoint review or action grant.

The newer `finite-recording-peer-handoff-runtime-expectations-v1` kind keeps
the same complete, closed record shape but requires the separate 101-module
handoff source kind. `decode_peer_handoff` is its explicit constructor; the
original `decode` still accepts only the original 90-module declaration. Neither
constructor accepts the other's kind or mismatched graph. Authenticated byte
loading validates either exact tagged document against its independently supplied
digest; it never infers a new pin or changes an old declaration in place.

`PeerRuntimeQualification(..., peer_handoff=True)` separately opts into the
newer read-only graph. An exact boolean True and the newer declaration are both
necessary. Omitting the selector, providing a truthy substitute, or supplying
an older declaration refuses instead of widening the default policy. Each
collector retains its original graph module and selection alongside its other
role bindings; mutation after capture fails. Both full source reads use that
same graph and unchanged elapsed-time limits. Command fingerprints and the fixed
isolated Python prefix remain required, but membership in either inventory is
still **not admission of an active entrypoint**. There is no new command,
permission kind, runtime launch, App action or recovery authority.

The newer selection is exercised through both original collectors, retained
private input files/listeners, actual descriptor delivery, Links and the already
armed original watcher in one offline fixture. A changed handoff source file
after arming prevents delivery and cancels those original peers. The fixture's
Engine/image/command/cgroup provenance is still synthetic; real process and
file operations do not turn it into installed qualification. The older 83/90
module inventories, passive commands and default collector behavior remain closed.

The separate uninstalled `supplemental_recording_peer_inputs.Inputs` reader
retains that expectations document from a fixed `expectations.json` in a
case-specific private sibling directory. It borrows the original read-only
startup `Declaration`; neither input is placed in the writable publication case.
The caller must authenticate both expected digests **independently, before
observing the peers**. The reader never learns a pin from whatever file happens
to exist and never creates, copies, repairs, renames or deletes input files.

Every ancestor is opened without following symlinks. The unchanged `0700` leaf
must contain only a single-link `0600` regular file, bounded by the expectations
codec's byte limit. Original descriptors, file and directory identities, flags,
path bindings, canonical bytes, expected digest and exact template/expectations
objects are retained. Every recheck freshly reads the complete original file
and startup declaration. Replacement files, equal recreated objects, foreign
descriptor reuse, changed metadata or uncertain reads refuse permanently.
Cleanup retires only owned original descriptors; the borrowed declaration is
not closed by this reader (it can independently fail its own read checks).
The ownership inventory is kept separately from checked working slots: adding,
removing or substituting a slot cannot nominate a foreign descriptor for cleanup
or hide an owned original. The retained `CasePlan` reader follows the same rule,
including fresh original status flags and non-inheritability checks. Cleanup
rejects foreign process/thread/credential custody, never closes an unrelated inode
reusing an original number, and never retries an uncertain close. These guards
do not authenticate installation provenance or defend against arbitrary trusted
code rewriting the private ownership inventory in the same process.

The complete canonical expectations/template relationship is validated when
the original input owner is constructed. Subsequent rechecks compare the same
exact record types, immutable bytes and independently supplied hash; they do not
decode an unchanged template into another syntax-only plan twice on every read.
This follows the retained Declaration's existing policy. Both original files,
descriptors, path bindings, metadata and input identities are still freshly
read and checked on every call, under the same original deadline. No filesystem
or runtime observation is cached, no replacement record is adopted, and changed
bytes (including mutable substitutes) permanently refuse. New mutation tests
exercise the difference between reusing validated syntax and reusing stale data.

Each complete input read has a two-second maximum. An explicit enclosing
deadline narrows that budget, including nested startup declaration reads;
standalone declaration reads retain their existing two-second default. The
reader captures no service clock and grants no continuing runtime authority.
Blocking filesystem calls still need the separately enforced outer/platform
bound. Ownership and permissions do not establish installation provenance.

### Exclusive pre-start input publication

The uninstalled `supplemental_recording_peer_provision.publish` library now
provides the corresponding creation step. A trusted provisioning caller supplies
both complete canonical documents and their **independently authenticated**
digests. The publisher validates both documents and their exact template/runtime
join before any filesystem write. It never obtains expected pins from installed
files, running peers or Engine, and it does not create a clock-derived plan.

Only the fixed startup and peer-input sibling directories can be created. Both
must be absent, as must the corresponding writable handoff case. An existing
empty directory, file, symlink, FIFO or historical case is a refusal, not an
opportunity to adopt or repair it. Parent traversal retains no-follow directory
descriptors; the selected parent must have the caller's root ownership, no
special mode bits and no group/other write access. The new leaves must be exactly
`0700`, with a single exclusively created `0600` file each. The publisher neither
changes umask nor chmods an existing path. Each file and directory is fsynced,
then both original files are read back with their identities and contents
rechecked before returning a receipt. Short writes are handled explicitly.

The entire operation, including cleanup, shares a two-second elapsed-time budget;
an earlier enclosing deadline can only shorten it. All partial/complete files
are **left in place** after an error, interruption, late completion or lost
acknowledgment. There is no rollback, deletion or retry mechanism. Publication
of two directories is not atomic, so no peer may start before the independent
owner verifies the completed pair. The receipt describes what was published;
it does not retain file custody or prove that files stayed unchanged afterward.
The existing `Declaration` and `Inputs` readers must still retain and recheck
the originals using the caller's independent pins.

Local tests exercise real filesystem creation and reopening with those readers,
canonical/digest/join rejection before mutation, either target already existing,
historical-case preservation, symlinks and shared parent modes, short/failed
writes, fsync failure, interruption, deadline expiry, altered content, extra files,
hardlinks, path replacement and descriptor reuse. They use synthetic documents
and temporary directories, not authenticated installed runtime evidence.

An additional end-to-end fixture publishes the complete pair before constructing
the original `Startup`, then uses those same retained files through the baseline
read, original clock capture, independent acceptance, three-process descriptor
handoff, ordinary Link exchange and idle-service assembly. It checks that neither
input's contents or identity/permission/modification metadata changes. Access
timestamps may legitimately advance during reads. The original startup/clock
owners are retained through assembly; the fixture never runs the service or
contacts a scanner. Host/Engine facts and runtime pins remain synthetic, so this
is an integration regression, not installed launch or hardware qualification.

This module is **not** added to any selected source profile or command. Direct
execution refuses. It does not authenticate its caller, source installation,
ancestor trust or expected digests; it does not qualify the newer handoff graph,
provision a listener, select an entrypoint, grant App actions, bound blocked
kernel I/O or designate recovery custody. Those remaining integration gates
still apply before a new scanner/audio trial or enabling the Mimic clock.

The explicit `peer_delivery.deliver_from_inputs` variant requires this original
owner and both retained listeners. Both already qualified collectors must use
the **same input-owned Expectations and original Template**, not values adopted
or re-created after qualification. Every handoff guard rereads those inputs under
the unchanged complete delivery cutoff, before and after both full paired
runtime comparisons and each descriptor transfer. Missing inputs cannot select
the older listener-only path. Uncertainty after original custody binding cancels
the same armed peer watcher. Success leaves input/listener owners with the caller;
it is still not Ready, independent input authenticity or permission for App work.

Tests join real private files to both collectors before capture, then use real
disposable processes, sockets and descriptor transfers. They cover input changes
before delivery, between peers and after the final runtime comparison. Expected
pins, Engine configuration, image and installation facts remain synthetic. No
existing source graph or passive command admits this reader or join, and these
tests do not qualify an installed launcher.

The receiving writer's explicit `writer_channel.receive_from_inputs` counterpart
borrows the same input owner and original private `Connection`. Inputs must use
the actual Startup's original Declaration and Template, and the connection must
retain the exact outer witness. Its original construction cutoff narrows the
entire writer intake, including fresh input reads, accepted-startup checks,
transport and final checks. Neither missing inputs nor an uncertain connection
can fall back to raw-socket intake. Received descriptors are retired on failure;
borrowed owners remain caller-owned and may invalidate themselves on failed
rechecks. No replacement Startup, clock, journal or retry is constructed.

A three-process regression reads the real private expectations file before
the original writer's baseline read, retains the actual first continuing service
clock through received descriptors and Link, then assembles the passive original
service. It also checks changed input after receipt, connection/path/peer drift,
and missing inputs. Outer listener provisioning and runtime eligibility in this
fixture remain synthetic; the separate supervised-pair fixture covers retained
listeners and the original watcher. These complementary tests are not a single
installed end-to-end launch or permission for active App operations.

The external `qualify_supplemental_recording_peer_runtime.PeerRuntimeQualification`
performs the existing complete read-only collection for **one selected role**:
original process/pidfd, exact command, image, Engine confinement and configuration,
root/proc/namespace bindings, actual kernel privileges/environment, joint source,
and supervised interpreter/dependencies. It retains the two-second collection
limit and original readiness cutoff. The same caller-owned plan remains unchanged.
The writer and observer records cannot be swapped, silently refreshed, or replaced
by an equal new declaration after capture. Failed comparisons consume the collector.
It neither launches nor terminates a process, sends an App command, or closes the
caller's borrowed process handle.

`PeerRuntimePair` can retain these two original collectors together. It requires
distinct original PIDs, container IDs and witnesses; one original plan, template,
declaration and Engine client; and the exact writer/observer role order. Neither
observed role may be the external verifier itself. Every call performs both full
collections and checks both original bindings before, between and after them.
One unchanged two-second absolute deadline is passed through both collectors;
the second cannot start a fresh two-second allowance after the first finishes.
An outer deadline may narrow the ordinary collector's limit but cannot extend it.
That same deadline now reaches both complete source reads, each underlying tree
traversal, and both interpreter/dependency snapshots. Their standalone default
budgets remain unchanged; an explicit outer bound can only shorten them. Expiry
after a first snapshot refuses before another starts. A file-read timeout closes
owned descriptors and returns no partial inventory. These cooperative checks do
not interrupt a blocked filesystem syscall or qualify a watchdog.
Loss, mismatch, contention or timeout consumes the paired comparison without
closing borrowed peers or granting any recovery authority. An earlier successful
comparison is never a cached substitute for the next full read. This remains a
bounded observation interval, not an atomic filesystem snapshot or an independent
mechanism for terminating blocked kernel I/O.

This narrow adapter currently describes separately observed container **init**
processes under the fixed writer name and its `-observer` counterpart. It does not
qualify an arbitrary descendant as that init, choose how the future peers launch
or communicate, or qualify the outer observer's own runtime. Its argv must use
the fixed isolated Python prefix and an explicitly inventoried joint helper file,
but a match alone does not make that file an admitted active entrypoint. No active
entrypoint is selected here. The ordinary helper collector retains its original
plan-helper/name/command/source policies, and passive preparation remains passive.
The new declaration and outer adapter are outside all existing observed source
module sets; this does not silently add two modules to a qualified image.

Local tests exercise each role with a real disposable child pidfd, source/runtime
fixture files and actual process environment. Engine data, cmdline, root routing,
confinement facts and kernel-privilege evidence are explicitly synthetic. The
observer's image/interpreter/environment pins intentionally differ from the plan's
writer pins, exposing accidental reuse. Tests cover role swaps, declaration/plan
replacement, changed source/runtime/configuration/command, actual child exit,
expired collection, and refusal to invoke Engine mutations. Additional paired
tests retain two real child pidfds and exercise both full file/environment reads,
actual loss of either child, loss between collections, one-sided failure, changed
owner objects and the shared non-renewable deadline. Engine/root/command/kernel
privilege facts in that fixture are still synthetic. This is **not** installed
image provenance, independent termination or an end-to-end active launch. Both
original peers still need a reviewed, independently supervised lifetime before
any action.

### Retained private bootstrap connection

The separate `supplemental_recording_peer_connection.Connection` owns the
receiving side of a private bootstrap connection. Its caller must already have
an independently authenticated original peer witness and a separately provisioned
listener. Every ancestor directory is retained without following symlinks; the
leaf must remain mode `0700` and contain only the original mode `0600`
`bootstrap.sock`. Directory/name replacement, altered leaf metadata or entries,
changed descriptor flags, peer loss and mismatched kernel credentials refuse.
Ancestor installation trust is not inferred from these observations.

Construction, its single nonblocking connect, and all rechecks share an original
two-second maximum, narrowed by the caller's absolute deadline. A full socket
backlog, uncertain connect, timeout or interruption is not retried. The caller
must pass this **same deadline** into the descriptor handoff and retain the
connection owner until its borrowers finish. No new clock is created. Checks
neither consume queued bootstrap messages nor install received descriptors;
the separate Endpoint still checks actual message credentials and descriptor
contents. Connection failure retires only its owned original handles, not a
foreign descriptor reused at the same number. Socket paths and caller-owned
process witnesses are never removed or closed.

Local tests use real private directories, independent processes, pidfds and
socket credentials. A three-process test also joins this connection to the
original accepted writer Startup, bounded descriptor intake, Link exchange and
passive service assembly. Docker identities, listener provisioning and runtime
declaration provenance remain fixture-supplied, not installed qualification.
This module is outside all existing observed source profiles and commands.
It creates no listener, input grant, journal, service action or recovery owner.
The fixed launcher, authenticated installation/input provisioning and independent
outer/platform bound remain required before any live trial.

### Retained private listener and supervised handoff

The separate `supplemental_recording_peer_listener.Listener` supplies the other
side of that transport boundary. An independently qualified owner supplies an
already provisioned, empty `0700` directory and the original authenticated peer
witness. Every directory component is retained without symlinks. The listener
exclusively binds the fixed name `bootstrap.sock`, pins that inode through an
`O_PATH` handle, sets only that retained inode to `0600`, then begins listening.
It does not change the process-wide umask. Existing entries are never adopted,
overwritten, repaired or unlinked, and the socket pathname remains after close
or failure for evidence review. These observations do not protect against
another trusted root process or authenticate installation provenance.

The single accept attempt verifies actual kernel peer credentials against the
same original live witness, then closes the listening socket so no later peer
can be admitted. The accepted socket remains owned by this Listener and is only
borrowed by the handoff. Descriptor flags, private paths and original peer remain
checked without consuming messages or their rights. Construction, accept and
all rechecks share one immutable two-second-or-earlier cutoff. Timeout, uncertain
accept, replaced paths, peer loss or cleanup uncertainty never permit a retry.
Kernel stalls still require independently enforced outer/platform termination.

The explicit `peer_delivery.deliver_retained` variant requires two accepted
Listener owners bound to the **exact witnesses** in the original runtime pair.
Both listener cutoffs narrow the entire delivery window, including both fresh
paired runtime collections. Listener owners, paths, sockets and witnesses are
rechecked between handoffs and at completion. Any uncertainty after original
custody binding cancels the same original armed Watch, stopping both original
peers; it never discovers replacements. Successful delivery leaves the Watch
armed and both listeners caller-owned. Replaying a completed delivery is refused
without treating the earlier receipt as fresh authority.

Disposable-process tests exercise actual Listener/Connection/Endpoint descriptor
receipt, plus the supervised two-listener handoff and unchanged Links. Original
pidfds, socket credentials, file descriptors, kernel timers and termination are
real; Engine/image/cgroup/command/input provenance is still fixture-supplied.
Neither this library nor its join is in an installed source profile or command.
No App mutation, journal, Ready grant, recovery owner or live-test admission is
added. Authenticated directory/input provisioning and the fixed installed
launcher remain separate requirements.

### Explicit outer peer termination

The uninstalled `supplemental_recording_peer_termination` library provides a
separate, narrowly scoped termination mechanism. It is not an active launcher,
an installed outer runtime, an App recovery owner, or a new permission codec.
No current source inventory, passive command or existing entrypoint selects it.

`Custody` consumes one capture attempt on the original `PeerRuntimePair`.
It requires the outer owner's own continuing `ClockWitness` and two distinct
live `ZeroDomain` witnesses for the original writer and observer. The outer
clock is not reconstructed from, or relabeled as, the writer's decoded clock.
Both complete runtime collections and domain checks share one two-second
capture bound, additionally limited by the original readiness cutoff. The
pair's optional outer cutoff can narrow but never extend its existing limit.
Capture duplicates the original pidfds and namespace handles and arms a kernel
`CLOCK_BOOTTIME` timer at the original `recover_by` value, without a new lease.
Capture alone starts and signals nothing; borrowed owners are never closed.

Arming is a separate one-use operation requiring the explicit
`terminate-original-recording-peers-only-v1` scope. This string selects the
allowed operation; it is **not authenticated user consent or an active App
action grant**. Old preflight permission kinds, readiness words and source
hashes are not accepted in its place. Arming rechecks both original runtimes
and domains in another bounded original-readiness window, verifies signaling
permission, and forks only from a single-threaded outer process with the normal
child-reaping policy. Neither arbitrary commands nor caller-selected PIDs,
replacement handles or deadlines can be supplied to the arming function.

The child drops unrelated inherited descriptors, application signal handlers
and signal masks before acknowledging readiness. Its fixed loop polls only
the retained peer pidfds, outer-owner pidfd, cancellation pipe and original
absolute timer. Either peer's exit, outer-owner loss, cancellation or timer
expiry causes it to attempt SIGKILL independently against both original peers.
There is no post-deadline grace, process discovery, numeric-PID signal fallback,
Engine call, scanner command, journal write or recovery replay. Closing the
outer copy of capture handles does not disarm the child. A failed arm after
custody transfer stops both bound peers and preserves the case.

The returned `Watch` reaps only its own watcher. It has no deadline extension or
disarm API. Closing it means cancellation. A frozen watcher is stopped through
its own retained pidfd; an unexpected watcher return makes the outer owner
stop both original peers and report uncertainty. A failed signal to one must
not skip the other. Return codes distinguish both peers already exited, original
deadline, peer loss, outer-owner loss and cancellation; none claims successful
recording, original native exit or restored App health. A SIGKILL request cannot
guarantee prompt exit from uninterruptible kernel I/O.

Local tests use real disposable child processes, pidfds, time namespaces,
BOOTTIME timers and signals. Frozen writer, frozen observer, both frozen,
cancellation, either peer's exit, watcher failure, timer changes, failed arm,
descriptor cleanup and unchanged deadlines are covered. The core parent-loss
test uses a separate disposable designated-parent process so the test runner
can directly reap the watcher; public arming separately binds the real caller's
pidfd. Engine/container/image/command/confinement facts remain synthetic.
The test-only short plan is created before its template and expectations;
the production 1500-second total limit is unchanged.

This still does **not** independently bound a killed/frozen watcher while its
outer owner is also blocked, qualify the outer executable/proc environment,
select an installed fixed entrypoint, qualify installed peer communication, or
designate exclusive recovery custody before mutation. Those require the final
platform-supervised topology and failure-recovery integration. Killing the
remaining observer after writer loss does not authorize a new observer to adopt
the journal, replay uncertain commands, or restore normal App ownership.

### Separate private descriptor delivery

The uninstalled `supplemental_recording_peer_bootstrap` library addresses a
specific topology gap: two separately launched container init processes cannot
receive an outer process's anonymous sockets through ordinary Docker start.
Its one-use `Endpoint` borrows an **already connected** private Unix SEQPACKET
socket. It neither creates a listener/path nor qualifies that path's installation.
Both connected-peer credentials and per-message `SCM_CREDENTIALS` must match
the original remote process. Forwarding through a different process on the same
connected socket is refused. Three distinct original process/container identities,
the final plan, independently authenticated runtime-declaration digest and
recipient role are bound to the canonical message context.

The recipient creates a fresh challenge. The outer process delivers exactly two
anonymous directional SEQPACKET descriptors using a separate `SCM_RIGHTS`
exchange, and requires an acknowledgment of the exact offer. Received handles
must already have the expected nonblocking, close-on-exec and direction-specific
credential options. Flags and socket types are not repaired into eligibility.
Malformed, extra, truncated, noncanonical, stale-context or wrong-sender messages
refuse; every installed descriptor is closed on rejection. Extra queued work or
EOF is checked before acknowledgment. A receipt is **not** a bilateral commit:
lost acknowledgment or a subsequent contradiction still makes delivery uncertain.
Neither receipt is a Ready, original evidence-custody acknowledgment, App action
grant, or authorization to retry.

Original borrowed clock/process/socket objects and namespace handles are checked
throughout. All three processes must have the same user/time/time-for-children
namespaces required by `Link`; coincident numeric clocks or two zero-offset proofs
do not relax that restriction. The original two-second handshake and readiness
cutoff cannot be renewed. An optional outer deadline may only narrow them.
Cleanup retires owned namespace handles/received descriptor copies, not borrowed
processes, clocks or connections. No new peer is discovered after loss.

The outer-only `supplemental_recording_peer_delivery.deliver` joins this transport
to the original `PeerRuntimePair`, original `Custody`, and the **exact Watch object
returned by its successful arm**. A reconstructed equal watcher is refused.
There is one delivery-attempt slot on the original pair. Both complete runtime
collections bracket both handoffs within one shared two-second/original-ready
window, which is also passed into each endpoint. The original owners, live
namespace proofs, watcher and immutable recovery deadline remain bound between
steps. Original sender copies are retired before success; a foreign descriptor
reusing an owned numeric fd is detached from its stale socket wrapper, not closed.
The receipt remains provisional through all retirement. Afterward the original
input/listener bindings, live original peers and watcher, and the same complete
cutoff are checked again. Cleanup does not start a new budget or make the last
paired runtime result a continuing success cache. Input/path drift, peer/watcher
loss, or expiry at this final boundary cancels the original Watch and returns
no receipt. A process interruption during retirement does not skip the other
owned channel direction or disappear behind an earlier ordinary error, including
when interruption happens during cancellation. These cooperative checks still
require independently qualified platform supervision for blocked kernel I/O.

Once that exact original pair/watcher binding has been accepted, any failure
cancels the already armed watcher and stops both original peers. This includes
one-sided delivery, lost/wrong acknowledgment, changed runtime, peer loss, expired
budget and owned-resource cleanup failure. It never creates a replacement peer,
reopens a journal, repeats a delivery, sends an App command or claims restoration.
Unrelated input objects are refused without adopting or closing them. On success
the same watcher remains armed and caller-owned; transport success does not release
any action gate. The existing `Link` still rejects descriptor passing in its own
messages, and passive preparation/permission formats remain unchanged.

Local tests use three actual disposable processes with connect/accept credentials,
descriptor passing and `Link` credential/descriptor guards, including wrong actual senders,
bad/truncated descriptors, packet faults, missing acknowledgments, timeouts and
leak/reused-fd checks. Joined tests run both complete runtime reads, then actual
delivery, Link construction and independent original-peer termination in one
lifetime. File/environment reads are real; Engine, image, cgroup, root, command
and kernel-privilege eligibility remain explicitly synthetic. Fixture source is
not the running installed image. These modules are now named only by the separate
read-only peer library inventory, **not a command-selected source graph**.
Final-retirement regressions join retained input files and listeners with those
same paired runtime reads and armed original watcher. They inject expiry, input
or path drift, actual peer/watcher loss, and cleanup/cancellation interruptions
after otherwise successful handoffs. No requirement is relaxed to permit these
fixtures; failures retain their original consumed attempt and do not admit retry.
The qualified private connection, fixed active command,
outer/runtime/input provenance, independent platform bound, separate action scope
and exclusive original recovery owner still require integration before live use.

### Original writer startup and independent observer clock

The uninstalled `supplemental_recording_writer_channel.receive` joins descriptor
intake to the writer's **actual original accepted Startup**. It will not take a
serialized plan or recreate a writer ClockWitness. The accepted baseline and
projection must still be the originals from the pre-handoff read, and service
or App publication must not already have begun. An independently authenticated
runtime-declaration digest is an explicit comparison input; computing that digest
from received bytes would not establish its provenance.

One attempt is consumed on the original Startup. Initial checks, descriptor
exchange and final rechecks share the same two-second/original-ready cutoff;
an optional external deadline can only shorten it. Changed owner references,
changed inputs, partial delivery, expired budgets and contradictory attempt state
refuse. Owned received descriptors are retired on failure; a foreign descriptor
reusing a numeric fd is detached from the stale wrapper, not closed. Borrowed
clocks, witnesses and connections remain caller-owned, although a failed Startup
check can invalidate Startup's own inputs. No journal is created by this join,
and neither success nor a receipt admits service actions or a second attempt.
The qualified caller must retain Startup until Link/service borrowers and the
returned Channels have been closed. The existing passive command does not select
this new module, and no observed source graph has been expanded to include it.

`Link` now distinguishes the roles' clock ownership. The **writer** still must use
the unchanged original startup sample (allowing its equal immutable decoded plan
representation). The **observer** retains its own actual continuing ClockWitness,
whose initial sample may precede or follow the writer's publication. It does not
reconstruct or relabel the writer's clock. Both roles still require the original
same user/time/time-for-children namespace handles, unchanged clock object and
sample, and fresh readings valid against the original plan and recovery deadline.
This does not enable different clock namespaces, inferred offsets or deadline
renewal. Clock origin provenance remains a launcher responsibility.

The three-process test keeps the real baseline-derived writer Startup alive
through delivery, Link exchange and passive service/journal assembly. The outer
and observer each retain their own locally sampled clock. Broader actual/staged
observer tests also use an independently sampled observer clock. Private files,
processes, namespace handles, clocks and fd transfer are real; host/Engine facts,
cgroups, installed images and authenticated input provenance remain synthetic.
An already established private connection and fixture stdin configuration are
not installed provisioning. The fixed command and authenticated per-role input
distribution, platform bound and distinct App action gate remain required.

### Read-only service preparation

The original `Startup.idle_service` assembly now accepts an explicitly supplied
`dispatch_observer`, which is passed unchanged to its one original
`TrackedDispatch` at construction. The service retains that callback throughout
its lifetime and rejects removing or replacing either dispatcher reference.
The dispatcher also pins the callback for each complete create/start attempt,
including across the acknowledgment and subsequent Engine inspection. Changing
both callback attributes together cannot bypass the remaining evidence gate.
Assembly never calls the observer or sends an App command. Invalid callbacks
refuse before the new journal/inbox directories are created. The existing
passive command supplies no callback and remains action-free.

This is an integration hook, not an authentication or action-permission gate.
The eventual qualified active caller must bind the real peer, original plan,
clock and source/runtime, establish independent custody, and separately admit
the explicit action scope. An arbitrary callable or matching receipt cannot
provide that authority. No dispatcher/session replacement or late attachment
after intent is supported.

Separate-process tests now exercise both the standalone dispatcher and one
original `IdleService` assembly: the child retains the actual plan file, clock,
projection, journal, session and dispatcher; the parent has independent App/CLI
custody and the authenticated evidence link. All four fixed CLI phases, lost
replies and last-moment generation/exec changes are covered. These tests still
use synthetic local Engine/App facts, not real App commands or an installed
service. The child test drives journal events explicitly, not an installed
startup or native recording loop.

Complementary accepted-startup tests cover all four service preparation routes,
normal completion, withheld commands, callback substitution and cleanup. App
driver compositions retain the same callback across native/recording handoffs
and finalized, preserved and never-authorized recovery. Their observer receipts
and native/host boundary facts are explicitly synthetic; they are not a second
claim of independent installed custody or successful hardware recording.

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

An optional `candidate_observer` is selected and pinned when the original
`AppService` is constructed. Assembly does not invoke it. The explicit native
handoff consumes its one observation slot under the existing inbox lock,
**before** retiring idle ownership and before publishing native launch inputs.
The exact plan, candidate generation/process and journal prefix are bound to
the receipt. Original candidate, callback, clock, history and prepared ledger
are rechecked before publication. A missing/wrong/late reply retires the idle
path into an uncertain native phase without publishing or launching; the same
session can still expire. It cannot retry or silently return to idle.

The original authenticated `Link` can supply this callback via
`observe_candidate`; the observer explicitly calls `acknowledge_candidate` on
its original `CliCustody`. This has a distinct message kind and one slot, only
after the first four CLI exchanges. It does not enlarge the eight CLI-exchange
limit or allow a CLI receipt to acknowledge candidate capture. Both sides retain
the original peer, plan, clock, credentials, nonce and digest checks. The
unchanged two-second exchange budget is additionally capped by the original
readiness deadline; nested candidate capture inherits the remaining budget.

The observer reconstructs the journal from its independently held directory,
requires a candidate-idle history with no native launch/recording intent, and
requires its own terminal metadata for both initial commands. The journal's
candidate identity must agree with two independent Engine reads and the newly
retained live candidate pidfd. No journal write, command, Ready or recording
authorization follows from that observation. Lost reply leaves any acquired
handles available for factual observation, including after writer loss, but
does not permit a second capture or replacement RecoverySession.

Separate-process tests exercise this exchange between the actual original
writer/observer, interleaved with all eight CLI boundaries. App/native metadata
and operations remain synthetic. Complementary original App-driver tests cover
the pre-publication hook through all three recovery routes, using explicitly
synthetic callback receipts. This is not yet a single installed end-to-end
native lifetime. The active launcher must require this selection together with
separate action scope, qualified runtimes, native-worker custody and authenticated
Ready; optional development callbacks cannot substitute for those gates.

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

When the service has selected `native_observer`, cancellation first consumes
its own cancellation slot and obtains the same original native-custody receipt
used by the pre-begin route. It does **not** construct `AppStart` merely to obtain
that receipt: the begin slot stays unused, the ledger stays prepared, and no
recording authorization is written. Original Ready, actors, journal, ledger,
source/runtime qualification and callback identity are rechecked around the
exchange. The original readiness limit and two-second exchange limit cannot be
extended. Consuming cancellation before the callback also prevents reentrant
cancellation or a recording handoff. Unknown or lost custody acknowledgment
marks the original phase uncertain and closes its acquired transport; the same
session may expire to review but cannot infer pristine recovery. A successful
custody exchange followed by a lost transport-close return still requires
actual original exits and unchanged files, just like the unobserved development
composition. An active launcher must explicitly select this observer.

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

The sole input publication also creates `launch/guardian` as an empty private
directory, while retaining its original inode/ownership/mode identity in the
publication receipt. Only the explicit one-child link-count change is admitted
for its parent; preexisting directories, uncertain creation, extra entries or a
second publication are refused without deleting residue. The actual guardian
requires this directory before it can write its one-use `launch-claimed.json`.

Original durable launch permission precedes Engine create/attach. Once the
actual Ready returns, the controller transitions to the socket-aware reader
before confirming Ready under a fresh complete source/runtime/host/probe
bracket. It never reuses the pre-launch assumption that the sockets directory
is empty. The original readiness timestamp and two-second confirmation bound
are not extended.

That same one-way readiness transition admits exactly the guardian claim. Its
canonical context, source pin, guardian PID/start ticks and watchdog deadline
must match the original authenticated Ready and plan. Component source metadata
in that file does not replace the independent full source/runtime inventories.
The original directory and claim bytes/inode are then retained. Post-begin
checks reread those exact bytes and identities without reacquiring the claim or
polling an expired readiness window. A claim alone is never readiness,
authorization, recording success or restoration evidence.

`AppStart` then consumes a distinct one-use slot on that original AppLaunch.
Fresh checks precede the durable recording authorization and ledger intent.
Only the returned intent permits the existing Relay to send begin. A sent
begin is not a received recording-start acknowledgment, completed recording,
verified file, process exit, or recovered App.

### Explicit original-Ready/native-custody join

An optional `native_observer` is selected and pinned at original `AppService`
construction. The original recording phase passes its guarded callback to the
one original `AppStart`. Assembly invokes nothing. The selected callback runs
once inside the existing fresh source/runtime/host/probe qualification bracket,
**before** durable `authorize_recording`, ledger start intent, or Relay begin.
The callback is pinned across its return, including in the service wrapper;
replacing both public callback and comparison fields during a reply cannot
permit begin. The default uninstalled compositions remain unchanged; an active
launcher must explicitly select the independent exchange and its separate
action scope.

`NativeNotice` binds the original launch pins, private dispatch execution and
chain digest, Ready bytes digest and original Ready proof, original probe
execution/request, four exact actors, and the original journal prefix. The
writer refreshes its **authenticated Ready's original namespace Witness** both
before and after the callback. It does not construct that Witness from an
observer report. It also rechecks original callback, journal, prepared ledger,
and the unchanged readiness deadline before the surrounding qualification can
permit authorization. Wrong, missing, late or interrupted acknowledgment closes
the original launch transport, consumes the attempt, and retains actor evidence;
it never retries, renews readiness or infers recording success.

The private `Link` has a distinct one-time native-custody message kind after the
candidate exchange and before any recording authorization. It does not consume
or replace one of the eight CLI evidence slots. Only comparison digests and
three bounded local worker PID/start-time hints cross the channel, not journal
content, paths, action instructions, or full process/namespace descriptions.
The original peer credentials, challenge, request digest and remaining original
two-second/readiness interval remain required.

`CliCustody` reconstructs this notice from its independently held journal,
original plan/projection and retained candidate. Both initial CLI commands must
already have independently observed terminal zero results. The held history
must still describe `candidate_running` with exactly one matching Ready event,
no recording authorization, no finish or operator exit. The observer recomputes
the Ready proof from that event and the comparison hashes; this is a consistency
check, **not** independent Ready authentication. The existing `NativeCustody`
then captures all four actual kernel handles through the original private
dispatch chain, two Engine inspections and process mapping. Its remaining
deadline can only shorten; the command deadline is the original conservative
MONOTONIC lease, not the raw BOOTTIME policy cutoff.

Independently captured execution, dispatch and actors must reproduce the
writer's exact receipt. Once captured, handles remain retained even if a later
comparison fails or the acknowledgment is lost. Factual `poll_native()` remains
available after helper loss; it neither takes the journal lock nor creates a
replacement writer/recovery session. Unknown capture, helper exit or a matching
receipt cannot stand in for worker exits, native returns or restoration.

Tests join the original service-to-begin path with synthetic acknowledgments,
and separately exercise real distinct writer/observer processes, private
journal/dispatch files, Unix transport, kernel handles and owned process trees.
App/Engine/cgroup/Ready facts remain explicitly synthetic. Faults cover changed
Ready/probe/dispatch/actor/history facts, worker loss, bad replies, interrupted
callbacks and lost acknowledgment. These are complementary tests, not a complete
installed or actual-native recording lifetime qualification.

An additional isolated recorder composition now retains original App and native
custody through **actual** Ready reception, PCM recording, returned completion,
file validation and worker exit. Both same-process and separate-observer-process
variants use their own Engine endpoint and kernel handles. Their independently
mapped actors must equal the original Ready actors before the test owner begins.
Polling continues after Ready and the observer's endpoint close and after the
original helper exits, without further Engine requests or numeric-PID reopening.
Lost completion/exit messages and a contradictory final Engine exit remain
unconfirmed even when a valid WAV and actual exited workers exist. Pre-begin
withdrawal produces no recording; a frozen recorder and a dead helper are not
reported as native-worker exits.

These tests use only owned local processes and loopback scanner/audio fixtures.
Their App/container/root metadata is synthetic. The separate observer is driven
by a private **test-only** stdin/stdout harness, not the authenticated service
`Link`, and the host authorization is a fixture. They do not yet join the full
AppService/journal/peer protocol and recovery lifetime or qualify installed
source/runtime provenance. Those remaining joins must not be inferred from the
successful isolated recorder and independent-handle evidence.

The original App qualification pipeline is additionally tested with a complete
copy of that executable native/product source rather than sentinel source files.
Both source and interpreter inventories are pinned only after test-image setup,
before any original plan or owner. The same original AppLaunch/AppStart succeeds
with those bytes and refuses post-Ready product/helper changes or unsafe helper
directory permissions before authorization, ledger start intent or Relay begin.
Its native Ready/Relay/platform facts remain synthetic; this does not yet prove
that the App-published inputs execute the native recorder in one full lifetime.

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

The continuing Offer likewise validates the complete template-to-plan relationship
at construction, then checks the same Template's exact immutable bytes and digest,
every pinned Plan field and nested type, and the unchanged original clock values
at each boundary. It does not repeatedly decode equivalent plans during native
publication. Startup uses its retained Declaration's freshly checked digest; it
does not substitute a new declaration or skip its file reads. Clock observations,
ownership, poisoning and deadlines remain fresh checks before and after I/O.
Tests cover in-place clock/plan changes, equal-looking wrong types, modified files
and expiry during native publication. The original two-second publication budget
is unchanged, including when coverage instrumentation is enabled.

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

Additional unprivileged namespace tests consume the original App-published
launch bytes at the exact `/data`, `/media`, `/opt` and `/usr/local` paths, with
no path or digest rewriting. Actual accepted profile files and old-recording
baseline accompany the complete staged source. All mounts are read-only except
the original private guardian directory in the claim test. Actual guardian
claim creation succeeds once; a second attempt preserves the original and
refuses. Profile, recording, launch or source-permission drift is refused.
These tests exercise native preflight/claim consumption, not an authenticated
Engine/Ready, installed image provenance or permission to begin a recording.

A further namespace composition runs the exact fixed operator command with
that original App publication and actual native children. Only original private
guardian/socket/receipt directories are writable; media remains read-only.
Owned loopback scanner/RTSP peers replace hardware. The actual readiness frame,
native return context, claim and watchdog cutoff match the original plan; all
three worker pidfds report exit after deliberate pre-begin pipe withdrawal.
No recording begins and no supplemental command runs. This proves fixed-path
native consumption, not the remaining authenticated AppService/Engine/observer
and recovery lifetime or installed interpreter qualification.

The original App readiness reader is also exercised with actual native frames
received by the existing Ready parser over a private Unix-HTTP Engine fixture.
It retains real worker pidfds and original dispatch inputs; complete source
checks bracket the App claim/socket inventories. Changed claims or unsafe helper
directory permissions refuse subsequent qualification before begin. Engine
responses, container/cgroup/root-credential mapping and Startup/image provenance
remain synthetic. No recorded audio, full service/recovery lifetime or live
scanner acceptance is claimed by these readiness-only tests.

The simulated Engine's creator thread now remains alive through native stdin
withdrawal and child reap even when the recording relay fails before begin.
Previously, that thread could exit first, causing `bwrap --die-with-parent` to
replace an otherwise clean native refusal with a secondary SIGKILL. An actual
process regression withdraws the original Ready transport while the fixture
expects begin: the exchange still fails, the native command exits with refusal
status 70, and no begin, scanner read or new recording occurs. The original
plan, clock and older recording remain unchanged. This test is included in the
required namespace CI profile. Its cleanup-order fix does not relax the clock's
5 ms observation limit, any original deadline, or expected failure outcomes;
it does not establish the cause of earlier post-begin CI failures or qualify an
installed Engine owner.

The separate in-process native recording assembly now retains test-only source
locations before its worker consumes a schedule refusal. An actual original
control-lock contention case starts the recorder but refuses the one optional
arm, shuts down, preserves prior/new recording evidence and rejects reuse. A
waiting test operator distinguishes that already-consumed refusal from the
fault it has not yet injected; neither the wait limit nor the production
failure-bit boundary changes. This isolates one possible early-refusal path,
not the cause of the earlier CI pre-arm timeout. See the
[native assembly qualification](scanner-display-supplemental-recording.md#offline-native-daemon-assembly).

The original `AppLaunch` is then tested through that actual Engine transport and
Ready parser, retaining its sole durable dispatch claim and real journal. A
changed guardian claim, unsafe helper directory, or premature recording receipt
refuses readiness publication and consumes the attempt. The original `AppStart`
also refuses recording authorization when its selected observer acknowledgment
is lost. These failures do not authorize a retry or discard the original worker
handles.

A recording variant makes only the fixture's predeclared recording directory
writable, keeping all other media, profile, source and launch inputs read-only.
Original App authorization and ledger intent drive the real native recorder via
the existing Relay. Synthetic RTP produces a checked 1,280-sample WAV and actual
start/completion/exit returns. A separately captured original `Operator` retains
duplicate worker pidfds; `AppAuthorizedFinalized` joins those returns and files,
then publishes the real worker-exit evidence to the original journal. The
candidate init stays alive: this is not App restoration or a full service/
independent-observer/recovery lifetime. Dropping the completion return leaves
the host result unconfirmed despite a finalized WAV and exited workers. The
host-health/probe, initial handoff, Engine platform and image metadata in this
composition remain explicitly synthetic.

The accepted-startup composition now retains the original `CasePlan`, clock,
session and journal through this actual native recording chain. Its service-loop
variant starts the original native phase inside the running service, finalizes
the recording, and exercises hard-deadline refusal without a replacement writer
or renewed lease. A real completed WAV and worker exit still leave the host
outcome unconfirmed while candidate init remains live.

A further offline composition starts from the original request publisher and
initial fixed dispatches. Owned harmless normal/candidate processes provide
actual retained init-exit evidence, and the same original service drives the
real native recording and finalized-file recovery reader. Fresh terminal Engine
inspections bracket each immutable file read, including the pre-dispatch recheck.
When both the original candidate pidfd and synthetic App metadata say stopped,
the existing policy issues only the fixed normal-start command; it does not stop
an already exited original candidate again. Contradictory running metadata or
unexpected recording output withholds restoration and expires the original case.
These are test-only App/CLI and restored-health responses, not live restoration.
An additional composition retains a separate read-only observer process before
the first request. Its original `AppCustody`, `CliCustody`, clock/deadline watch,
private journal reader and separately authenticated fixture Engine endpoint
remain owned by that process. Actual kernel-authenticated `Link` exchanges
bracket both initial CLI commands, candidate capture, native capture and the
one restoration command. No notice history is supplied over the fixture control
pipe: the observer reconstructs it from its own held journal. It retains the
actual worker/init handles through finalized recording and recovery. Dropping
only its native acknowledgment after successful capture leaves those facts
available but prevents recording authorization, ledger intent and begin; the
original writer expires without retrying or inferring restoration. This is
still a test-only process launcher with synthetic platform and image facts,
not qualified installed supervision or permission for a live test.

The original service also exercises the two non-success recovery entrances with
actual native processes and file reads. Pre-begin cancellation produces real
transport EOF and worker/init exits, then verifies the unchanged recordings
through `NeverAuthorized`; no recording authorization or receipt is created.
Confirmed-start abandonment closes the original ledger without a completion
acknowledgment. The isolated native fixture can still finalize a WAV while its
completion return is dropped: `Preserved` retains those bytes but the result
stays **unconfirmed**, never a successful recording. A still-live original init
or an unexpected additional file withholds restoration on both routes. The
caller prepares the private progress directory even if no active sample runs.

The separate authenticated observer also joins the abandoned-recording and
pristine-cancellation routes, including retained worker handles and the above
refusal cases. Pristine cancellation selects its own pre-cancel exchange without
constructing `AppStart`. A lost acknowledgment after the observer captured the
actual workers preserves its factual handles but leaves the writer uncertain;
there is no authorization, recording, retry or inferred restoration. These
fixtures join the real original service/peer/native lifetime, not installed
platform provenance, qualified active execution or action permission.
An additional deliberate observer-process exit after native capture also
withholds acknowledgment on both recording and cancellation routes. Kernel
peer-exit evidence is real, but the lost observer's handles are not replaced or
described as continuing custody; only the original writer's retained handles
remain, and the original case expires without restoration.

A separate loss test lets native work and its acknowledgment finish, then exits
the owned observer before restoration. Finalized, abandoned and pristine routes
all retain their original writer/session and refuse the first restoration
dispatch through the original peer `Link`. No restoration CLI execution is
created or started. Actual native output and exited workers do not replace the
dead observer or turn the resulting review state into restored service.

The same three successful fixture lifetimes also run the observer from a
disposable copy of the closed joint source bundle and an explicit PySerial copy.
The isolated test bootstrap removes installed package search paths, refuses
undeclared third-party imports, and checks all imported private, product and
dependency module origins. Complete source, dependency and bootstrap inventories
are unchanged before and after execution. Private, product and dependency
modules resolve only through their staged roots, before execution. Withholding
the staged channel module, product package or PySerial dependency refuses before
peer readiness rather than falling back to installed packages or another local
checkout. This packaging check does **not** qualify the interpreter, installed image, bootstrap or
active command. Both independently qualified peer runtimes remain required.

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
bounded execution, and journal-safe failure recovery. The joint read-only source
profile names this watch, but no installed command selects it; passive
preparation stays passive.

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
`DeadlineWatch`. The joint source inventory includes it; no command selects it.
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

The explicit candidate exchange above joins capture to the App driver's native
publication boundary. This prerequisite does **not** authenticate an
action grant, retain native worker or CLI execution custody, transfer a journal
lock, or start recovery. Init exit is not worker exit, absence of a replacement
App, recording completion, or restoration. The remaining active integration must
preserve a single journal/dispatch owner. It must not reconstruct a new recovery session merely because these
read-only handles report an exit. Independent bounds on the observer itself
also remain required.

## Independent native-worker custody prerequisite (uninstalled)

`supplemental_recording_service_native_custody.NativeCustody` adds a one-use,
read-only native-process capture to the **same original** `AppCustody`. It is
included by the explicit joint source inventory, but no installed command
selects it. The helper, candidate
init, guardian, native recording worker and watchdog must still be live, the
retained normal init must have exited, and the original readiness cutoff must
not have passed. A failed or interrupted capture consumes the slot but leaves
the borrowed App/deadline evidence intact; it cannot be retried.

An optional containing-exchange deadline can only shorten the capture's
two-second limit. The same resulting deadline is passed to dispatch reads and
both Engine inspections and is checked around kernel capture and at final
validation; the custody operation cannot start a fresh allowance. Invalid or
expired supplied limits consume the native slot
without Engine reads. Expiry during inspection closes only newly acquired native
duplicates, preserving the original App handles for factual exit observation.
This is an elapsed-time check, not independent protection against an indefinitely
blocked observer; outer execution supervision remains necessary.

The capture joins the original plan/projection, candidate generation and init,
fixed command path/source/deadline, and all three private dispatch intents.
The command's MONOTONIC deadline must equal the conservative lease converted
from the original host clock window; the BOOTTIME policy cutoff is not
interchangeable with it, and neither deadline is renewed during capture.
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

The focused custody tests use actual disposable process trees, retained kernel handles, private
dispatch records and credential-checked local Unix transport. Docker metadata,
container credentials/membership and namespace mapping are explicit fixtures;
the fixed native command is not executed. Both ordinary peer and actual sender
credential paths are covered, along with wrong identities/descriptors, changed
commands/dispatch history, partial interrupts, and independent worker exits.
Shorter/longer outer deadlines, invalid/expired limits, and expiry at either
Engine inspection are covered without widening the production time limits.
These tests do not qualify distinct installed containers or actual scanner I/O.
The additional actual-recorder composition described above supplies separate
Ready/PCM/return/file evidence in both same-process and separate-process observer
variants, without changing these synthetic container or qualification limits.

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
existing command selects this observer; the joint read-only inventory only names
its source. No `IdleService` gains an observer or new authority by default. The optional legacy
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

Accepted startup keeps its original `ClockWitness`; decoding the acknowledged
case plan produces an equal immutable clock value, not the same Python object.
The link accepts that canonical value equality once and pins the exact original
borrowed clock and its original value object thereafter. Replacing either object
or changing even one clock value is refused. This does not reconstruct a clock,
extend a deadline, or relax the peer/namespace and fresh clock checks.

Requests contain a bounded canonical notice description and receipt, the exact
plan digest, an incrementing sequence, a fresh challenge and a short absolute
BOOTTIME interval. They do **not** carry journal paths, journal content, arbitrary
commands or filesystem data. The observer rereads its original held journal,
reconstructs the notice and checks its digest, then performs `CliCustody`'s
independent capture before acknowledging. Replies bind the complete request,
challenge and exact captured receipt. Each case permits at most eight CLI exchanges
(two per fixed App action), plus the distinct one-time candidate- and
native-custody exchanges described above. Policy and original dispatcher one-use rules still decide which
actions, if any, are allowed.

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

## Original writer intake and passive dispatcher assembly (uninstalled)

The explicit `supplemental_recording_writer_channel.prepare_idle_from_inputs`
path now joins the previously separate preparation pieces in one call:

1. Read the same retained, independently pinned startup and peer inputs.
2. Receive channel descriptors through the original private connection, using
   the actual baseline-derived, accepted `Startup` and its original clock.
3. Construct the writer's `Link` to the original observer and wire that exact
   `Link.observe` method into the original service dispatcher.
4. Assemble only the preparation journal/inbox, then retire the service and
   journal before the Link and received channel copies.

The original connection cutoff bounds the complete join. Passive assembly can
accept an enclosing deadline only to **shorten** its existing two-second budget;
it cannot renew an expired channel or extend the accepted offer. Blocking kernel
I/O still needs independent outer supervision. Both input files, the original
connection, clock/plan bindings and peer liveness are rechecked around assembly.
The join neither yields an active service nor calls `run`, consumes an inbox,
sends a CLI evidence request or performs an App/scanner/recording action.

The original transport receipt is returned only after passive retirement and a
final recheck of the same startup, accepted plan/clock, private input files,
bootstrap connection and original peer handles, within the unchanged cutoff.
Closing the last received socket cannot hide changed input, lost peers or an
invalidated startup. The check neither reopens retired resources nor acquires a
replacement owner or acceptance lock while the original service holds it. It is
not Ready, action permission, a recording result, restoration, or proof of an
installed launcher. Failure preserves complete and partial preparation files;
the original intake slot stays consumed. There is no retry, replacement clock,
reopened journal or alternate observer. Caller-owned startup/input/connection
owners remain with the caller, though their own failed validation can invalidate
their resources. Cleanup never closes a foreign descriptor that reused one of
the received descriptor numbers.

The integrated test starts with the exclusively published input pair, uses
three actual local processes and kernel descriptor delivery, and checks the
same dispatcher callback and ordered retirement. Host/Engine/runtime facts are
still synthetic. Both the legacy and separately tagged handoff expectations
reach this same real original-writer path through exclusive publication; this
does not upgrade the raw outer fixture to full installed runtime qualification.
Fault tests cover input changes, peer exit, expiration before
or during assembly, changed callbacks, partial assembly, interruptions, cleanup
failure, original-custody loss during final retirement and descriptor reuse.
No older source inventory or permission format is enlarged, and no installed
command selects this preparation function.

### Launcher ordering and the passive writer boundary

The fixed peer commands must be determined before sealing their runtime
expectations. Putting the expectations digest in the very argv that declaration
fingerprints creates a circular dependency. Likewise, per-case private inputs
must remain outside the immutable image whose digest they declare. Pre-start
arguments can pin the case, role, template, baseline and original outer identity;
they cannot depend on future peer PIDs or the writer's final clock/plan.

An independently qualified original outer owner must supply the expectations
pin and exact peer identities through a distinct authenticated preparation
exchange **before** the baseline read. Private permissions or a hash computed
from the observed file are not authentication. This exchange cannot reuse or
enlarge the older passive permission kinds or grant App action/recovery scope.
Any preliminary runtime comparison needs its own explicitly preliminary context;
it cannot be substituted for final accepted writer-plan qualification.

The existing retained Connection and Listener have a two-second construction
cutoff. Do not stretch that connection across baseline collection and final
acceptance, reset its cutoff, or reconnect it for another phase. Preparation
needs a separately identified, one-use channel and finite original preflight
budget. Only after the original Startup creates its post-baseline clock and
receives separate final acceptance may the existing final descriptor handoff
begin, under that plan's ready limit and its own original two-second cutoff.
Keep the same original process witnesses across these phases; no reconstructed
Startup, replacement clock, new peer incarnation or cached success is admissible.
The separate input exchange and fixed passive writer below now exercise this
writer-side order. The separately selected passive observer below also joins
its original input/plan/handoff owners. Installed outer/observer entrypoints,
independently supervised preliminary/final runtime qualification and active
launch authority remain unqualified; passive commands are not their substitutes.

### Authenticated clock-free input preparation (uninstalled)

`supplemental_recording_peer_preparation` uses its own
`finite-recording-peer-input-preparation-v1` message kind and
`authenticate-retained-peer-inputs-only-v1` scope. Its fixed private directory
is `/mnt/data/sdsctl-recording-preparation-CASE-ROLE`, distinct from the final
descriptor handoff. The original retained Connection/Listener has a one-use
preparation slot, consumed even on failed validation; its original two-second
cutoff covers the exchange and sender's final retirement checks. There is no
reconnect, deadline extension, pathname removal, field repair or fallback.

The original outer supplies its independently authenticated Expectations digest
and counterpart process identity. They are not learned from receiver argv or
by hashing a local observed file. Each packet binds the original requester and
outer incarnations, case, role, template, baseline input pin and fresh nonce;
canonical byte comparison distinguishes booleans/floats from integer fields.
Retained pidfds, socket/path identity, per-message credentials and original
zero-offset namespace evidence are checked. Descendant senders, extra fields,
wrong contexts, ancillary rights and truncation are rejected. Unexpected rights
are retired without touching caller-owned descriptors. Root credentials alone
do not independently qualify the outer installation or platform.

The receiver opens the private input file once using the digest received from
that original outer, and retains the same Inputs and counterpart ProcessWitness
through the caller's context. Both roles are supported, but only the explicitly
tagged peer-handoff Expectations can be used; old passive declarations cannot
enable this protocol. The receiver borrows its original Declaration, clock,
Connection and outer witness. It creates no baseline read, Startup, service
clock, plan, journal, App action or recording. Files and consumed attempt state
are preserved on failure; owned resources are retired, including on interruption.

**A receiver acknowledgment is not bilateral completion or permission.** The
outer can still refuse because an acknowledgment is lost/invalid or a retained
input changes during final retirement. Receiver context entry does not establish
that outer result. Neither side's transport result supplies preflight permission,
final Ready, App-action admission, termination rights or recovery authority.
Later callers must independently validate those boundaries and recheck the same
retained inputs/peers; they cannot rely on a saved preparation success.

The actual three-process writer fixture now joins exclusive input publication,
this original-outer preparation, the real private persisted baseline reader,
original writer baseline/clock creation,
separate final acceptance, final descriptor intake, exact Link dispatcher callback
and passive service retirement. The original outer process and writer-side peer
witnesses continue across both exchanges. The preparation socket is retired
before baseline collection; a distinct final channel gets its own original cutoff
instead of renewing the preparation channel. The writer's continuing service
clock is still created only after the complete baseline read; the separate
preparation clock is never substituted for it.

The baseline digest in that exchange now comes from the pre-existing sealed
inventory, not an arbitrary fixture placeholder or a hash of observed files.
The same digest is used by `prepare_service_from_baseline`; its read-only
projection continues into the accepted original plan and dispatcher. Wrong
digest and changed-file tests stop before the host reader, service clock or plan
publication, preserving the failed file instead of resealing or replacing it.

These are real local sockets, credentials, pidfds, clocks and private files,
not installed-container qualification. Baseline input provenance, Engine/runtime
pins, cgroup routing, separate acceptance and outer supervision remain synthetic
in this fixture. The existing 83/90/101-module inventories and older commands
do not select the preparation module. The separate 104-module profile and passive
writer below cover that source and writer entrypoint only. Independently bounded
outer/platform lifetime, explicit App action scope and exclusive recovery owner
remain required.

### Separate preflight admission on the original preparation owners (uninstalled)

`peer_preparation.prepare_writer` now joins those retained inputs to the existing
`Permission` baseline/startup scope. The original outer must issue that permission
separately, over its distinct private preflight channel. The input acknowledgment
does not substitute for it. The adapter requires the same original preparation
clock, outer witness, counterpart witness, Declaration, Template and input owner;
it will not adopt replacement objects or a renewed deadline. The independently
pinned manifest is read from the fixed baseline directory, not an input-provided
path. The permission channel and its retained private path are rechecked as well.

This consumes only the existing preflight permission's unchanged operation:
one complete baseline/host read followed by the original Startup's post-read clock
and publication. The permission's original two-second-or-earlier consume bound
covers input/path/peer checks, the read, publication and final scope retirement.
The separate continuing service clock does not renew that allowance. Changed
inputs, lost peers, path replacement, expiration or interruption poison the
original owners, preserve any partial files and do not permit another attempt.
Borrowed preparation resources remain caller-owned. Return is the same
**unaccepted** Startup input, not final acceptance, a service, Ready or an App grant.

The real three-process fixture additionally performs input authentication, a
separate actual Sender/Permission exchange, guarded original writer preparation,
independent final acceptance, descriptor/Link dispatch and passive retirement.
The original outer, writer and counterpart processes span all phases. The
preflight channel has its own original deadline derived from the retained
preparation clock, rather than extending the retired two-second input socket.
Fault tests distinguish refused permission before the first host read, input or
peer loss during the read, and drift after permission-scope retirement; even a
late result cannot return an apparently usable Startup.

The fixture's outer qualification callback, Engine/runtime and acceptance inputs
are still synthetic. This is not installed qualification or a completed launcher.
The new adapter and its imports remain outside the older source selections and
commands. The separate fixed passive entrypoint below covers their full closure,
but neither it nor the old preflight permission supplies the independent
outer/platform, action and recovery gates.

### Fixed passive peer-writer command and source selection (uninstalled)

The separately admitted writer-side ordering is now available for **offline
qualification only**, with one exact entrypoint shape:

```text
/usr/local/bin/python -I -B /opt/sdsctl-recording-host/supplemental_recording_peer_preparation.py CASE TEMPLATE_SHA256 BASELINE_SHA256 OUTER_PID:START_TICKS:CONTAINER_ID --prepare-idle-peer-writer
```

The guard requires that exact installed path, isolated/no-bytecode Python, root
UID/GID, `/` working directory and exact mode/argument count. Ordinary checkout
invocation refuses before private input reads or project imports. This is not an
installation instruction: no image, App, active controller or catalog is changed.

The command's argv contains neither its Expectations digest nor future peer PIDs
or a final plan/clock. That avoids the expectations/argv fingerprint cycle. It
receives the independently pinned Expectations and original counterpart from the
authenticated outer over the preparation channel, and explicitly requires the
new `finite-recording-peer-preparation-runtime-expectations-v1` kind. Private
inputs remain outside immutable source/image contents. The command does not
authenticate its own launcher, outer identity, deployment or source merely by
matching those supplied values.

The matching `PreparationProfile` in `supplemental_recording_peer_host_source`
has 104 modules: the closed 101-module graph plus preparation, permission probe
and service permission. Its tagged inventory is distinct. The older 83/90/101
profiles and their defaults remain unchanged. Both runtime collectors require
the matching new declaration and exact `preparation=True` selection; combining
that selector with `peer_handoff=True`, coercing booleans, or supplying older
declarations refuses. Static-closure and passive-import tests cover the complete
new graph; observed source is hashed, never imported to qualify itself.

The qualified outer must provision both the input and preflight listeners before
the command starts, and retain its original input endpoint through the receiver's
final checks; immediate sender close can race those checks. The writer likewise
retains its completed input socket passively
until the separate original-outer permission arrives, so an early close cannot
race the outer's final input checks. It then retires that socket before baseline
reading; its original two-second exchange is never reused or renewed. Permission
uses the same original preparation clock and the existing one-use short read
scope. Only the complete baseline read creates the continuing service clock.
Separate acceptance remains bounded by the original offer, with a fixed polling
ceiling and time reserved for final intake/assembly.

Final descriptor intake uses the distinct fixed private path
`/mnt/data/sdsctl-recording-peer-handoff-CASE-writer`. The same original Startup,
inputs, outer and counterpart continue into `prepare_idle_from_inputs`; its
original connection bound covers descriptor intake, Link/dispatcher wiring and
passive retirement. Cleanup closes service/journal/Link before their original
Startup, then input and preparation borrowers before their clocks/declaration.
Lost peers, changed inputs, interruptions or late/failed retirement preserve
partial files, poison the original owner and never report a usable result.

The command never consumes an inbox, sends Ready, runs the service, requests
evidence, dispatches an App action, launches native work or starts a recording.
Exit 75 and its sanitized milestone text are not action, recording, restoration
or installed-qualification receipts. The real local three-process fixture joins
the actual command function through separate Sender/Permission, manifest/host
read, acceptance and final descriptor/dispatcher cleanup. Command and fault
tests also check descriptor retirement and unchanged original failure files.
The fixture retains the outer's preflight channel passively through final handoff,
without requiring a test-only baseline-complete message from the writer. Both
directions keep their original exchange bounds; passive descriptor retention is
not additional protocol authority. Engine/runtime/cgroup routing, outer review and acceptance provenance are still
synthetic. A fully isolated installed-command/outer lifetime and explicit active
action/recovery admission remain separate unfinished gates.

### Original outer preflight qualification for the passive peer writer

`PeerWriterPreflightQualification` in the outer-only
`qualify_supplemental_recording_service.py` connects the original outer's existing
`Sender`/`Review` protocol to the fixed passive peer-writer command above. It
borrows the actual retained `Inputs`, original outer clock/domain and writer
pidfd. The declaration must explicitly select the 104-module preparation kind;
older 90/101-module declarations and the old probe/service command policies are
not upgraded. The independently expected writer argv fingerprint, configuration,
image, source, interpreter, environment and effective confinement all retain
their complete existing checks. Matching these inputs does not authenticate the
installation that supplied them or qualify the outer itself.

This is a **pre-baseline** comparison. Its private preview uses the original
outer clock only and is never published, accepted, passed off as the later
writer's continuing clock, or substituted for final paired runtime qualification.
The one-attempt callback is bounded by the original outer window and one shared
two-second allowance, including final review and private input checks. The
existing `Sender` separately retains its own original two-second write bound.
Neither scope is renewed after a slow collection or partial failure.

Complete canonical input reads bracket the full runtime collection and follow
the final review. Intermediate checks retain the same private descriptors,
flags, path/inode bindings and immutable decoded objects. An initial Python 3.11
coverage run demonstrated that repeating full canonical decoding at every proc
guard could exhaust the finite allowance. The integration now avoids that
redundant parsing while preserving complete boundary reads, intermediate custody
checks and the unchanged deadline. No timeout, source/confinement check or
coverage requirement was relaxed to make the test pass.

The local process fixture joins the actual full qualifier, original `Sender`
and independently clocked child `Permission`, including its one-use consume
scope. The scope is empty in this fixture: this is not the complete fixed writer
command, baseline read, accepted plan, active recording or recovery. Source and
runtime bytes, private inputs, stream credentials, clocks and pidfds are real;
Engine/kernel metadata, root/cgroup routing, argv observation and installed
input provenance are synthetic. Changed inputs (including after review), lost
custody, obsolete policy, interruption or an expired shared bound refuse before
permission delivery. Partial files remain preserved. These narrow fixtures remain
useful separately; the following combined fixture now joins their actual process
flows, without qualifying an installed outer/observer lifetime.

The complete 104-module profile is also exercised by the existing two-role
runtime pair and original pidfd/timerfd termination fixture. Both collectors
share one unchanged two-second window; loss of the writer during the observer's
read cannot yield a successful pair. Explicitly scoped cancellation, original
absolute deadline (including frozen disposable peers) and designated-parent loss
exercise the same original targets. These are separate offline component joins,
not qualification of an installed outer command or permission to arm one.

### Combined preflight, original writer and final paired comparison fixture

The offline `test_supplemental_recording_qualified_peer_command.py` fixture now
executes the full outer preflight and actual passive writer function in separate
processes, alongside an independent descriptor-receiving observer. Every protocol
owner is created after exec, not inherited across a fork. The existing synthetic
host fixture also creates an inert disposable process, which is retired before
the command begins so watcher cancellation cannot orphan it. It is not an active
native worker or evidence of installed App behavior.

The same original outer retains its private Inputs, clock/domain and writer
pidfd from input delivery through actual `Sender`/`Review`, full runtime
collection and permission delivery. The writer then performs its real private
persisted-baseline read and complete host-reader logic before creating its own
continuing clock and publishing its plan. The outer preview is never substituted
for that later plan. Original private input and permission descriptors remain
passively retained until final handoff, without renewing their old exchange
budgets. The same writer Startup receives an independently published acceptance
message, original descriptors and Link, builds its passive dispatcher and closes
its own original resources. There is no inbox consumption, Ready, service run,
App action, audio, recording or recovery. The fixture checks descriptor balance,
original dispatcher/clock/plan identity and preserved failure files.

The fixture also preserves refusal evidence when the outer fails before reading
the writer's result. Original stdout/stderr read handles survive nested teardown;
only bounded, nonblocking post-exit reads supply known-script source locations.
Unread plans, input values and arbitrary exception text are never reported.
The existing cleanup observer retains the first bounded source-location snapshot
before later sanitation can obscure it. A real pre-permission half-close regression
checks normal writer exit, no baseline read/publication, original cutoff retention
and descriptor balance. This diagnoses fixture refusals; it does not change the
production protocol, extend deadlines, prove a historical intermittent failure's
cause, or qualify an installed command.

The paired variant performs **fresh full writer and observer runtime collections
against the actual newly published writer plan before submitting acceptance**.
Both share one unchanged two-second bound, including surrounding private input
checks. It does not reuse either the earlier preflight success or its temporary
clock. Post-permission writer source drift, changed observer configuration and
loss of the original observer prevent acceptance and service construction.
Other joined faults cover preflight source/runtime/argv/input refusal before any
baseline host read, a changed persisted baseline after a successful permission,
and input drift after plan publication. No failed case is replayed or cleaned up
to appear unused.

On runtimes with Linux timerfd support, the combined fixture also captures
actual `Custody` and arms the separate kernel watcher before acceptance. Capture
and arming each repeat the full paired comparison rather than caching the first
success. The same original outer clock/domain and both original process handles
remain bound through acceptance, descriptor delivery and passive writer cleanup.
Explicit cancellation then terminates only those two disposable original peers;
all case files remain unchanged. This is cancellation, not successful recovery
or a disarmed deadline. Runtimes without timerfd skip this case rather than
claiming to have exercised it.

These are process-integration tests, **not installed qualification**. The fixed
writer function executes checked-out code under explicit fixture path aliases;
its installed command argv, image/root/cgroup/Engine/kernel metadata and input
provenance remain synthetic. Inventoried source/runtime files are actually read
and never imported; they are not claimed to be the executing image. The observer
comparison argv in this original variant is deliberately inert, not an admitted
fixed observer command. The separately selected passive pipeline below uses a
distinct exact command policy, without supplying installed provenance.
Final acceptance is an explicit test decision with real file publication, not a
claim of complete platform approval. The outer is still the test process, not an
independently qualified installed supervisor. Independent outer/platform lifetime,
fixed outer/observer source and interpreter provenance,
explicit active-action admission and exclusive recovery remain separate gates.

### Original observer inputs and separate final-plan delivery (uninstalled)

The preparation library now has a separately selected, read-only final-plan
exchange for an observer that already retained its original authenticated inputs.
It uses `finite-recording-observer-plan-delivery-v1` and
`retain-original-writer-plan-only-v1`, on the distinct private path
`/mnt/data/sdsctl-recording-observer-plan-CASE`. The original completed input
connection is never reused or given another deadline. Each new listener/connection
has its own original two-second bound and one consumed exchange slot.

The outer's `send_observer_plan` borrows an independently reviewed, retained
`CasePlan`, the original private Inputs, clock and writer/observer witnesses.
It requires the exact local owners retained only after the earlier observer-input
send completed its retirement checks. A replacement clock captured before the
writer plan is still refused; timestamp ordering alone cannot establish custody.
It sends only the expected plan digest, bound to the case, template, original
three-process identities, baseline and expectations pins, role and fresh nonce.
It neither learns the expected plan from disk nor carries plan bytes or file
descriptors. Full runtime/source/publication and outer/platform qualification
remain caller obligations; a private sender's credentials are not those proofs.

`receive_observer_plan` requires the exact declaration, input owner, clock and
original sample, local identity object, outer witness and writer witness retained
by the earlier observer input exchange. Equal replacement owners are refused.
It reads and retains the fixed writer case using the digest received from that
same outer, validates the complete template/plan/runtime-pin relation, and checks
the writer's original clock against actual zero-offset domain custody and current
readings of the observer's own original clock. It does not create a new writer
clock from a reported Window. The final plan must postdate that observer's
original preparation sample. No startup, baseline read or journal is created.

The sender rechecks inputs, original peers, plan and time around the exchange
and after retiring its owned namespace observations. The receiver's retention
acknowledgment is **not sender completion, startup acceptance, Ready, an App
action grant or recovery authority**. A sender-side failure after that ack still
prevents acceptance and handoff. Both sides preserve files and their consumed
attempts; the caller keeps borrowed originals alive until their later users close.

The combined three-process fixture now authenticates the real observer's Inputs,
delivers the actual writer's later plan using this protocol, then joins those
same owners to real descriptor intake and passive Link construction/retirement.
Fixture pipes carry sequencing only, not the future plan or its expected digest
to the observer. Full paired runtime comparison precedes this delivery and
fixture acceptance. Faults include malformed context, wrong pins, ancillary
rights, a descendant sender, expiry, source-file drift, missing continuity,
replacement owners, replay and sender-retirement failure after receiver ack.

The watched variant arms the same original kernel-only peer watcher before this
plan delivery. It then uses the existing supervised descriptor-delivery adapter,
including both full fresh paired runtime/source collections before and after
handoff in their original shared two-second bound. The watcher stays armed through
the writer's passive dispatcher retirement. A source change before or during
handoff cancels that original watcher; only its original peers are stopped and
all case files survive. That successful variant uses fixture-provisioned final
bootstrap sockets, not qualified installed listeners or an outer launcher.

A separate regression now joins the existing retained `Listener`/`Connection`
owners and `deliver_from_inputs` in that same process flow. It exposes an
**expected refusal**, not a successful retained-launcher qualification: the
passive writer closes its bootstrap connection during its own retirement before
the outer can finish its final paired comparison. The original listener detects
EOF within the unchanged delivery deadline, cancels the same watcher and stops
only the original peers. Original inputs, process witnesses and case files are
preserved. The regression does not ignore EOF, replace owners, add a sleep or
renew a deadline to manufacture success. The passive command is still an
action-free preparation command; a continuing writer lifetime and its explicit
admission/retirement protocol must be joined separately before this can become
an installed or human-test candidate.

`retained_idle_from_inputs` now separates that passive lifetime from immediate
retirement. It yields only the original transport receipt, not a service, Link,
dispatcher or clock. The same private inputs, original Startup/clock, bootstrap
connection, observer witness and dispatcher binding stay alive through the
caller's scope. Assembly, body, exit validation and ordered service/Link/channel
retirement all share the original connection cutoff. Exit refuses changed input,
callback, used/closed/failed service or expired custody; no journal is reopened
and no action is dispatched. `prepare_idle_from_inputs` remains the immediate
wrapper, so the fixed passive command has not gained a continuing or active path.

A further actual-process variant uses this scope through an explicitly
**fixture-only writer interposition**. The outer's real retained listeners and
`deliver_from_inputs` complete both fresh paired collections while those same
writer borrowers remain alive. Only afterward does the fixture send its bounded
completion message and let the passive writer retire. Source drift before or
during delivery cancels the same watcher and stops only its original peers.
The fixture pipe is sequencing, **not authenticated installed completion or an
App grant**, and this variant does not qualify the unchanged fixed command's
immediate-retirement path. The earlier negative regression still proves that
path refuses. Keeping a fixture peer alive does not qualify an installed
retirement protocol or independent outer/platform supervision.

A further separately selected **offline library join** replaces that fixture
completion pipe with an authenticated passive retirement message. Both original
writer bootstrap Endpoints must select
`retire-original-passive-writer-only-v1` before descriptor delivery; the scope
is included in the context digest, so the legacy descriptor-only exchange cannot
be silently upgraded. The same Endpoint, namespace descriptors, receipt object,
nonce, original clock and complete original cutoff survive the final phase.
No new connection, replacement clock or renewed timeout supplies retirement.

`deliver_and_release_passive_writer` completes both full fresh paired
collections, retires every sender-owned channel copy and the observer bootstrap
Endpoint, and rechecks the original input/listener/pair/watcher owners before
sending the writer's receipt-bound release. The watcher remains armed. All
pre-release faults, including final sender-retirement failure, cancel the same
watcher and stop only the original two peers. `prepare_idle_until_released`
holds its same passive service/Link/dispatcher inside the bounded scope until
that original outer message arrives, then performs the existing ordered
retirement and input/clock/peer checks. Missing, malformed, extra, wrong-credential,
ancillary-rights, changed-receipt or replayed frames cannot admit work. The old
immediate passive preparation and receipt-only caller scope are unchanged.

This is deliberately a **one-way permission to retire**, not evidence that the
writer received the message, retired cleanly or exited. Once sent, the writer
may retire immediately; the sender does not race that permission with another
live-writer check or claim receiver success from a successful send. Actual exit
evidence, exclusive recovery, outer lifetime, installed source/runtime provenance
and active admission remain separate. The process fixture selects the new
library function explicitly; it is not installed command provenance. Engine/runtime
metadata, image and command provenance are still synthetic. Fault tests retain
the distinction between successful transport and receiver refusal, preserve
files, and cancel the original watcher instead of calling that recovery success.

This remains offline integration: paths/cgroups, Engine/kernel/argv and installed
pin/publication provenance are synthetic. No fixed observer or outer entrypoint,
installed platform supervision, active App grant or exclusive recovery has been
enabled. Existing source-profile module sets and the original immediate writer
selection are unchanged; these operations are not selected for live use.

### Separately selected fixed passive-release writer (uninstalled)

The existing fixed preparation entrypoint now has a second, exact
`--prepare-retained-idle-peer-writer` selection. It shares the unchanged isolated
interpreter/root/cwd/fixed-path gate and the same bounded arguments as the old
immediate command, but explicitly retains the original accepted writer through
its authenticated passive release. It does not infer selection from incoming
messages, environment variables or observed input fields. The old
`--prepare-idle-peer-writer` remains immediate and does not accept the continuing
retirement context. Both stay action-free and return status 75 rather than
claiming recording, restoration or installed qualification.

The independent preflight collector must separately opt in with
`passive_retirement=True` and compare the complete exact new argv against its
already authenticated command pin. Its default still refuses the new argv, even
when those argv bytes match the input declaration. Non-boolean selectors,
changed selections after construction, old-command/new-selection mismatches
and all changed argument fields refuse before qualifying the Engine. The
original input owner, clock, baseline pin, challenge and two-second collection
bound remain unchanged. This is still the explicit closed 104-module preparation
profile; no source module set or legacy inventory is expanded. Changed source
bytes require new independently authenticated source pins in any future install.

An actual three-process variant now calls that fixed retained command function
without substituting its preparation function or using a stdin completion
message. It joins original authenticated inputs/preflight, writer-owned baseline
and clock, final plan delivery, full paired comparisons, kernel watcher,
descriptor handoff, original passive service/dispatcher and authenticated release
through retirement. The fixture explicitly forbids the old immediate preparation
function in this variant. Success, source drift, missing/bad release and late
sender-cleanup failures all exercise these joins. Installed argv, image/kernel/
Engine/path metadata and final publication remain synthetic; the fixture is not
a qualified installed launcher, independent outer/platform supervisor or active
App grant. Observer/outer selection, provenance and exclusive recovery remain
required before a live test candidate exists.

A separate actual-exit variant lets that command's original writer process exit
with status 75 after its real ordered cleanup. The already armed watcher observes
that exact pidfd becoming readable and stops the original observer, without a
fixture completion message, cancellation byte, explicit parent kill or renewed
wait. Both peer exits and the watcher's peer-loss outcome are independently
observed within the original handoff cutoff; case files remain unchanged. This
proves that offline lifetime join only. A passive status 75 and a peer-loss stop
do not establish successful recording, native/App exit or exclusive recovery.

### Original received observer through passive handoff (uninstalled)

The plan receiver now retains its exact local input, original observer clock,
original sample, local identity, outer/writer witnesses and preparation context
on the CasePlan it actually received. `retained_idle_observer` joins only those
same owners to the separately provisioned final observer connection and actual
Link. An equal reconstructed plan, input reader, clock or peer witness cannot
replace them. The original CasePlan has one consumed descriptor-intake slot,
including on refusal; the earlier input/plan channels are never renewed.

This context yields only a bootstrap receipt. It neither exposes its Link nor
exchanges active CLI/candidate/native messages, creates a Startup/journal or
admits any App action. Intake, caller scope, complete fresh input/plan checks,
Link retirement and owned descriptor retirement share the original final
connection cutoff and original plan ready limit. The existing original-channel
retirement routine protects foreign reused descriptors. Borrowed owners remain
the caller's responsibility and are rechecked after owned channels close.

The actual three-process fixture joins this observer to the separately selected
fixed passive writer and original outer watcher. Its normal-retirement variant
observes the original observer exit and the watcher's resulting stop of the
writer, with case files unchanged. Replacement owners, context/input drift,
replay, original-cutoff expiry, cleanup failure and interruption are tested
without granting new time or action scope. **This original variant's observer
phase sequencing uses fixture stdin; it is not an installed completion protocol
or fixed observer entrypoint.** The separate passive pipeline below removes those
later stdin steps. Runtime/image/Engine provenance and the outer's platform lifetime
remain synthetic/unqualified. This reduces fixture-only owner assembly, not the
requirements for independent supervision, active admission or exclusive recovery.

### Explicit pre-exec input socket staging (uninstalled)

The existing private listener has a separate `Listener.stage` selection for
provisioning its socket **before** the intended process exists. It publishes
only the same private socket in an independently provisioned empty leaf; it
cannot accept a peer or authenticate inputs yet. The outer must independently
capture the original live process witness after launch, then call `bind_peer`
once. No expected PID is learned from a socket, and a queued unexpected process
causes refusal, not discovery or a replacement accept.

This is one continuing listener owner, not a replacement listener. The original
two-second-or-earlier cutoff includes path provisioning, exec/startup, binding,
accept and the authenticated input exchange. Binding keeps every original
descriptor, pathname and deadline; it adds no wait, retry or grace period. The
legacy constructor still requires its live peer before any path write. Invalid,
expired, repeated or changed binding closes only owned originals and preserves
the created socket as failure evidence. Staging cannot restart a closed case.

An actual subprocess fixture now starts both writer-role and observer-role input
receivers with stdin disabled and no startup signal. Each connects immediately
to the already-created socket and queues before peer binding. The same original
outer then binds its independently captured witness and performs the existing
authenticated preparation-profile input exchange, using the original clocks,
counterpart and pins. A separate full-command variant joins this staging owner
to original preflight, baseline, publication, paired qualification, watcher,
passive handoff/release and independently observed writer exit. Old command argv
and explicit source-profile selection are unchanged; changed source bytes still
require independently authenticated new source pins.

Neither fixture is installed launch qualification. The small exec fixture uses
synthetic private configuration/path/cgroup mappings and an explicit fixture
retirement message **after** authentication. The full-command fixture still uses
stdin for its earlier host setup and observer sequencing; only the small input
fixture eliminates the launch barrier. Neither supplies an installed outer/observer
entrypoint, proves the complete installed startup fits two seconds, or resolves
independent platform lifetime and active action/recovery admission. No extra wire
permission, baseline permission reuse or new App authority is introduced.

### Separately selected passive observer pipeline (uninstalled)

The preparation entrypoint has a distinct exact
`--prepare-held-idle-peer-observer` selection. It accepts only the original case,
independently pinned template/baseline digests and original outer identity, behind
the unchanged isolated-interpreter, fixed-path, root and cwd gate. It obtains
the expected Inputs digest and writer witness through the authenticated input
exchange, then obtains the final writer plan through its separate authenticated
plan exchange. No future plan, expected Inputs pin, completion message or writer
identity is supplied through stdin or additional command arguments.

The outer must provision all three private phase listeners before launch. The
first connection's original two-second cutoff bounds this entire observer
pipeline, including plan delivery and descriptor intake. Later connections can
only narrow it, and final intake also retains the original plan readiness limit.
Completed input/plan sockets remain passively owned until final cleanup, without
being reused or treating their retired exchanges as fresh evidence. One original
observer clock, original input/plan owners and original outer/writer witnesses
continue through the existing `retained_idle_observer` and Link.

After intake it waits only for original peer-pidfd readiness or that unchanged
cutoff. Either causes sanitized refusal and owned-resource retirement, never a
success receipt. It sends no active Link messages, creates no Startup/journal,
performs no App/Engine/baseline write, and cannot grant recording or recovery.
An independently armed original watcher must still account for peer exits.
`Watch.close()` reaps only its watcher; callers independently observe both
original peer handles rather than treating watcher retirement as peer-exit proof.

The read-only runtime collector must explicitly opt in with
`passive_observer=True`, the independently pinned baseline digest and the original
outer identity. It compares the complete exact argv to its separately authenticated
expectations. Default selection refuses the new command even when its digest is
pinned. Non-boolean flags, wrong roles/profiles, changed selection/baseline/outer
owners and source drift refuse; successful source comparison never grants App
scope. The preparation source closure remains 104 modules, with changed bytes
requiring new independent source pins. Old command defaults are unchanged.
The original outer identity is freshly observed at construction and at every
inherited runtime guard. Repeated immutable runtime/name accessors within that
same guard do not each repeat the observation; no result is cached across guards
or complete collections, and every guard retains its original cutoff.

The actual subprocess fixture now executes this command function after its
explicitly synthetic initial setup, closes the original stdin descriptor, and
forbids later plan/handoff/retirement signals. It joins the original passive writer,
fresh paired comparisons, preprovisioned listeners, sealed-image direct-owner
native watcher, authenticated release and independently observed original exits.
Actual Custody capture owns its first full paired comparison. This automatic
variant does not add an unused standalone fixture snapshot immediately before
capture: capture, native arming and both delivery brackets each perform their
unchanged complete fresh checks. Nothing is published or released before capture
and arming succeed. The ordinary standalone comparison variants stay unchanged.
Initial source/argv/Engine/path/confinement provenance remains synthetic. Separate
faults exercise original-cutoff expiry, lost plan channel, explicit selection
refusal and post-qualification selection/baseline/source drift, with no release
or retry on uncertainty and failure files preserved. Descriptor counts are checked
when the observer performs its own cleanup; native-killed peers require actual
pidfd exit evidence, not an invented cleanup receipt.
Timed test diagnostics distinguish command/fixture cleanup from Python process
shutdown; a final fixture marker is never an exit receipt. The test still requires
all original process handles to become readable before its original cutoff, and
late exit is preserved as a failed qualification rather than receiving more time.
Initial Python 3.14 joins encountered delivery refusals or reached command
cleanup without actual process exit before the original cutoff. Those failures
are retained. Measured phase costs identified repeated pure decoding of the
already-validated, unchanged Inputs/template relationship. Inputs now verifies
the original immutable records and independent byte pins while still freshly
rereading both original files and checking every original descriptor, path,
clock and deadline. Mutation regressions refuse replacement or changed records;
no runtime or filesystem observation is cached. The corrected automatic pipeline
passes its local 16-case suite on Python 3.13 and 3.14, including all three native
build variants and actual original peer exits within the unchanged two seconds.
This is local fixture evidence, not installed timing qualification. Earlier
delivery refusals and late shutdowns whose first cause was not captured remain
unresolved; passing corrected runs do not retroactively explain them. Test-only
delivery diagnostics retain reviewed source locations, never private values.

The automatic observer also joins native-watchdog loss after each actual peer
handoff. The full paired comparison, original Custody and sealed direct-owner
watcher remain real; only the original watcher is deliberately killed, using its
retained pidfd. Delivery must refuse before passive writer release, both original
peer exits must be observed within the original two-second pipeline cutoff, and
the consumed custody must refuse retry without changing the original failure
files. The observer may be killed by exact-handle cleanup or independently refuse
on original peer loss; neither outcome is successful work. This extends the
earlier fixture-sequenced watchdog-loss checks to the new automatic path, not to
an installed outer command, platform service or a broader failure domain.

A further full-flow fault join runs that same original Custody/automatic peer
pipeline inside a disposable outer process, rather than substituting inert
peer processes for the lifetime check. The independent fixture driver retains
the original writer, observer and sealed native-watch pidfds before deliberately
killing only the original outer, either after arming or after either actual
descriptor handoff. It also holds a duplicate cancellation writer, so cancellation
EOF cannot stand in for the tested outer-loss event. Both original peer exits
and native exit 12 are observed before the unchanged pipeline cutoff; retained
case-file bytes are checked before and after, and no passive writer release or
replacement outer/attempt occurs. Merely receiving the handles does not make
the driver their parent: it verifies that distinction before loss and reaps
only after its own disposable subreaper adopts them. Exact-handle fallback
cleanup is separate and never counts as passing evidence.

Initial setup is still synthetic and uses a separate fixture setup/reaping
ceiling; that ceiling is **not** an installed import/startup bound or an extension
of the original two-second work window. No cgroup freeze, platform service,
installed command or active permission is added. This closes the earlier gap
between automatic full-command flow and individual outer-death testing, not
whole-domain failure, startup qualification or exclusive recovery.

This removes fixture-only **phase sequencing**, not the initial fixture setup,
installed exec/source provenance, fixed outer launcher or independent platform
supervision. Declaration loading precedes the first connection cutoff and still
needs independently bounded startup; no test proves installed exec/import/loading
fits two seconds. Active App admission and exclusive recovery remain separate
gates, and this passive command is not a candidate for a human scanner/audio test.

### Staged original-outer and peer source execution, offline only

An additional process fixture copies reviewed checkout sources into a separate
tree and records their hashes before executing the fixed **test** entrypoint.
Its outer process runs the existing full automatic passive pipeline, including
the genuine preflight/final paired checks, original Custody, sealed native watch,
both handoffs, passive refusal/retirement and actual original peer exits. The
original two-second work cutoff is unchanged. Dynamic, static and UBSan native
builds execute; no cgroup freeze or live App action is involved.

The existing admitted peer preparation profile remains exactly 104 modules.
Seven additional outer-policy modules form a separately measured 111-module
**test candidate** closure, not a newly admitted source kind. Six legacy files
are needed only by the existing test bootstrap, and remain explicitly separate.
Three of those use direct file loaders without registering their module objects
in `sys.modules`; counting that table alone missed their execution. The staged
outer's execution audit checks both normal imports and direct file loaders
against the independently recorded file origins/hashes. The same explicit
test-only bootstrap now installs that check separately in the actual writer
and observer, before either child's first private or product import. Their
original command environments and phase owners remain unchanged; this is not
an inherited environment switch or production command option. Per-process
private source-name logs survive an observer's native termination, without an
atexit dependency or retained report descriptor. Those logs are source evidence,
not exit or cleanup receipts. All three native build variants still require
the actual original peer exits before the same two-second work cutoff.
Negative tests exercise all three bootstrap roles and refuse an outside source
or changed staged bytes before their code executes. Matching module counts do
not stand in for origin checks, and the original checkout remains available as
a fallback candidate during the positive outer test.

This joins actual passive flow to staged **outer and peer** execution, not a production
Python sandbox or installed entrypoint/source qualification. Test scaffolding,
interpreter/dependency and installed child-command provenance, Engine/runtime facts,
input publication and initial startup supervision remain synthetic or separately
unqualified. The Python execution audit is test-only and does not certify its
own launcher or an independently authenticated installed manifest. No source
profile, command selector, App admission or exclusive recovery rule is expanded.

## Remaining gates before a human scanner/audio test

### Offline host-side native prototype

The separately built [original-peer native watcher](../scripts/native/README.md)
now provides an actual exec-based, kernel-handle-only join to original Custody
in disposable local process fixtures. A private absolute BOOTTIME timer keeps
the original cutoff even if the caller disarms its own duplicate timer. The
fixtures cover original full comparisons, peer/outer loss, stopped peers,
cancellation, descriptor closure and malformed handoffs. Native target stops
are checked before caller fallback cleanup where that distinction matters.

A separate offline native ingress mode now joins one authenticated UNIX
SEQPACKET/SCM_RIGHTS transfer to that same watcher. Three independently inherited
standard-I/O anchors (private original-outer socket, original outer pidfd and
time namespace) bind six transferred handles; sender credentials, exact anchor
identity, one-packet-then-EOF and the original absolute readiness cutoff are
required. Wrong senders, substituted anchors, missing/excess rights, truncation,
trailing messages and silent/incomplete handoffs are refused without readiness
or authority over unconfirmed received targets. The original outer remains
responsible for its own retained peers on any startup failure. Actual passive
command tests join this transport to full comparisons, staged input, fixed
writer release and observed exits. These tests use local posix_spawn, **not**
systemd, and do not qualify the audited host's standard-I/O descriptor API or
independent service lifetime. See the native README for its precise failure
model and source references; no older entrypoint/source selection is enlarged.

An additional real-process fixture now separates original-outer death/freeze
from native-watcher death/freeze, including outer loss before target ingress.
The original outer constructs and reaps its own unmodified Watch; a disposable
fixture subreaper adopts only after that outer dies. Original private BOOTTIME
timers, signal-vs-exit distinctions, retained handles and one-attempt boundaries
remain intact. Full-command faults also kill the native watcher after each real
handoff: original peer retirement occurs without passive writer release, and
case files survive refusal/retry. This is not cgroup placement or installed
supervision proof, nor a simultaneous-failure guarantee.

The native README records an investigated scope-placement route that could keep
the original parent/reaping owner while moving its child outside the outer's
freeze domain. Upstream v256 supports a transient-scope PIDFD property, but stock
`systemd-run` has a numeric-PID retry fallback that is **not** acceptable here.
Deeper source inspection also finds that its cgroup attachment checks the pidref
then migrates by numeric PID, an explicitly acknowledged race. The PIDFD property
does not solve parent-death/adoption/reuse during an in-flight move; this route
is **not admitted** under the current exact-process contract. A post-move check
cannot undo affecting an unrelated replacement. See the native README's source
references; do not silently relax this requirement or claim installed proof.
No systemd action is implemented or selected. Independently qualified exact
placement must precede target ingress/native readiness under the unchanged
original cutoff; a scope job, fixture barrier, source observation, or monotonic
scope runtime timer cannot stand in for that proof or the original BOOTTIME
bound. Installed support and a qualified pre-readiness failure owner are still
separate gates. A service-owned alternative would instead need its genuine
separate child owner and exit evidence; never loosen the direct-child Watch.

An actual test-only clone3 helper now preserves that direct parent through
`CLONE_PARENT | CLONE_PIDFD`, transfers the original kernel-created handle and
joins the existing native ingress, Custody and full passive-command flow. The
helper is reaped separately; the original outer still reaps the native watcher
through the unchanged Watch. A separate variant exercises direct creation into
the fixture's retained **existing current** cgroup directory, without creating
or moving any cgroup. That is not outside-freeze-domain placement or installed
permission. No entrypoint/source inventory selects this test helper. Original
child accounting if the helper dies before its report, protected independent
cgroup provisioning, source/runtime pins and platform failure supervision remain
gates; see the native README. No numeric-PID migration or old-clone fallback is
introduced, and no active App/recovery authority comes from these tests.

A second actual creation fixture now avoids the helper-report interval: a
small C-only clone/exec boundary runs inside the original outer, and the kernel
writes its original child pidfd directly into that owner's retained memory.
The child never returns to Python; the same native ingress, Custody, Watch and
full passive-command paths consume the original handle without PID reopening.
Failed returns and pending Python interruption before ctypes returns still
retire/reap the exact child from that retained output. Original clocks, finite
cutoffs, one-attempt behavior and failure files remain intact. This removes the
separate helper's accounting gap for this test route; it does not qualify the
outer's death/freeze supervision, independent placement, installed source or
runtime. Same-existing-group creation and the separate process lifetime tests
remain local evidence only. No installed selector/build/App loads the shared
fixture, and it grants no active action or recovery authority. See the native
README's direct-owner experiment and remaining platform requirements.

An additional offline fixture binds earlier expected native-build bytes to a
sealed executable memfd and the original direct-owner clone/exec boundary. The
original readiness cutoff covers bounded copy, seal, re-hash and clone admission;
there is no pathname or unsealed fallback. Actual source-path replacement,
forbidden mutation and pre-adoption interruption tests retain original Custody,
exact-child cleanup and consumed-attempt boundaries. The full passive-command
join exercises both handoffs, release/exit and native loss with this sealed image.
The separate original-outer lifetime fixture uses the same sealed route for
pre-ingress outer loss and individual-process outer/native death or stop, while
retaining original timers and exact-child ownership; no cgroup test is replayed.
This is not independent installed publication/runtime or post-exec `/proc` image
inspection: the native dumpability restriction remains intact. Compiler/libc,
dynamic dependencies and the original outer's runtime still require independent
qualification. The native README records the exact evidence and limitations;
no active entrypoint/source selection, policy or action permission changes.

An explicit opt-in local cgroup fixture now joins that direct-owner route to a
fresh watcher subtree and the existing lifetime/Watch checks. Only new disposable
processes/groups are involved; original outer and independent exact-handle
cleanup owner remain outside the injected freeze. Kernel freeze completion is
observed, not inferred from a write. Original deadlines, uncertainty, observed
exits/reaping and empty-only group removal remain required. A full original
Custody join also confirms that submitting cancellation to a frozen watcher is
not success and does not renew the deadline or permit retry. Synthetic Engine
facts stay synthetic. These tests need explicit local approval and are skipped
by ordinary CI; they do not qualify installed platform placement or the whole
outer's cgroup-failure boundary. See the native README for scope and limitations.

This is an **offline prototype, not an installed host supervisor**. No existing
entrypoint or source inventory selects it. The static build is a candidate for
a host without Python, not authenticated compiler/libc/kernel or installed
placement evidence. Original independent runtime/publication checks, outside-
freeze-domain placement, qualified outer loss/failure handling, active action
admission and exclusive recovery remain required below. Building locally does
not authorize installation or any App/scanner trial.

### Outstanding qualification

1. Complete real platform/publication and independent-observer lifetime
   qualification. Accepted startup, request/initial dispatch, original service,
   actual isolated native recording/WAV/worker exits and finalized recovery now
   join in one offline fixture. App publication provenance, platform/CLI replies
   and restored health remain synthetic. A separate authenticated observer now
   joins finalized, abandoned-recording and pristine-cancellation paths with real
   Link, journal, clock and worker handles. Pristine cancellation captures native
   custody without constructing a recording owner or consuming its begin slot.
   Installed evidence remains separate; do not infer the missing joins.
2. Bind the action-capable controller/observer source inventory to reviewed,
   independently supervised entrypoints with interpreter/dependency provenance for **both** peers
   and a distinct explicit action-scope grant. Passive preparation must remain
   passive; neither its old permission nor a CLI evidence receipt is that grant.
   The separate peer expectations and paired read-only collectors now provide
   explicit comparison inputs and a shared collection bound; they do not
   qualify installed provenance or admit an active entrypoint. The separate fixed
   passive writer/observer commands and their 104-module selection do not admit
   App actions or supply qualified installed outer/observer runtimes. The separate
   termination library now exercises both original peer handles and an absolute
   kernel timer, but the qualified
   outer lifetime, authenticated channel join, active action scope and exclusive
   failure/recovery owner still need integration.
3. A fresh isolated fixed-command end-to-end lifetime joining actual native
   returns, full source/runtime pins and original independent observer protocol,
   with independently bounded termination.
4. Installed provenance and measured collection timing, plus a reviewed
   fresh-case staging procedure that preserves historical containers/evidence.
5. Fresh human readiness for a finite scanner/audio trial. Historical readiness
   or an earlier successful test is not permission to replay a consumed case.
