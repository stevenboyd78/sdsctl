# Finite supplemental acceptance launcher (internal, not released)

This is offline-tested acceptance infrastructure for the
[bounded acquisition owner](scanner-display-supplemental-acquisition.md) and
[explicit authenticated demand](scanner-display-supplemental-demand.md).
It is not continuous polling, a release feature, a normal startup option, or
authorization to repeat a closed hardware case. The ordinary CLI, default WebUI
factory selection, existing App staging modes, and HA card configuration remain
unchanged. The separate acceptance-only staging adapter described below explicitly
selects the guarded daemon and WebUI wrapper.

## Assembly and arming

`scripts/accept_supplemental_daemon.py` intercepts only the reviewed CLI
construction boundaries. It returns the original, exact native runtime and API,
then installs **one** acquisition owner's feed and its matching delivery service.
It explicitly validates the runtime/feed relationship after late assembly. It
does not subclass the runtime, create a secondary scanner, or replace an existing
feed. Missing/mismatched profiles, prepatched hooks, duplicate constructions and
repeated process runs are refused. The outer lifecycle closes both the owner and
its connection subscription, checks worker termination, and restores the hooks
and signal handler even when startup fails.

An exact firmware pin is mandatory. Startup does not arm acquisition. Readiness
requires the cached native identity plus current compatible PSI and an accepted,
matching profile. No extra identity probe, AST mode change, settings write, scoped
SQK/DQK command, or file trace is introduced. Global FQK 00–99 is still **not** the
physical F0/S0/D0 bank selection.

The separate `arm` operation verifies PID/start ticks through a Linux pidfd and
publishes an exclusive generation-bound request before signaling. Existing or
uncertain requests cannot be overwritten or replayed. The worker rechecks current
qualification after the operator request, with cancellation/deadline checks after
possibly slow preflight. Only then does it arm the existing owner once. A fresh
consumer must subsequently request authenticated demand; cached reads and an
armed-but-idle window cannot issue supplemental commands.

Defaults are a **600-second maximum operator wait**, a **64-second acquisition
window**, and **60 read opportunities**. The internal policy ceiling remains 75
seconds / 150 opportunities. Demand cannot extend the fixed window or quota,
reset quarantine, or create another acquisition owner on the same runtime.
Window expiration, quota exhaustion, cancellation, or a trigger failure requests
normal daemon shutdown. No retry or reconnect-based rearm is provided.

## Independent process deadline

`scripts/guard_supplemental_acceptance.py` is a separate Linux parent process.
Its deadline starts before launching the child and includes the chosen operator
wait, acquisition window and a fixed 20-second startup allowance. It never
extends that deadline based on consumer activity. At expiration/cancellation it
requests child termination, waits at most three seconds, then uses SIGKILL if
necessary and waits at most another two seconds. Signals are bound to a pidfd,
not a potentially recycled saved PID. HUP/INT/TERM stop the trial rather than
reload or restart it.

Before constructing scanner objects, the child validates its exact guardian
identity, installs Linux `PR_SET_PDEATHSIG(SIGKILL)`, then rechecks the parent.
An unexpectedly killed guardian therefore does not leave a scanner-owning child
running. These are process-level safeguards, not guarantees against kernel or
host failure. They cover the one native daemon process, not unrelated processes.

Both directories must be **new and persistent**. Reusing a guard or daemon case
fails; neither script generates a replacement case on restart. Future App
integration must use a case-specific path on persistent `/data`, not `/run` or a
fresh random path per container start. It must not erase consumed cases.

The source revision is recorded from the reviewed staging inventory. It is not
a self-authenticating string: source/archive hashing and deployment verification
remain the staging procedure's responsibility. Reports publish atomically as
private files without replacing prior evidence. They contain process/generation
metadata, outcome categories and read counts, not credentials or scanner values.

## Source-pinned App and WebUI staging

`scripts/stage_supplemental_acceptance.py` is a separate offline adapter, not an
option on the older runtime-subclass research modes. It reads an exact clean
commit archive, requires the two staging drivers to match that commit, and adds
the daemon launcher, guardian, and authenticated WebUI wrapper to the image.
All generated files and runtime bytes are inventoried. Verification reconstructs
the expected files from the commit, firmware pin and separately recorded case ID;
it does not accept the staged manifest as its own proof. Tampered, missing or extra
files are refused without deleting evidence.

A UUIDv4 case ID is chosen **once at staging time**, embedded in the App version,
and fixes the guard path at `/data/sdsctl-supplemental-acceptance-<case-id>`.
No startup random ID, PID-based directory, `/run` evidence, or restart-based rearm
is generated. A consumed case fails on restart, including failed initial launches.
The App daemon command starts the guardian, which starts the child with its parent
identity checks; it never bypasses the guardian. The arm helper must still run
inside that exact verified container's PID namespace and user identity.

The candidate uses a **separate local App slot**, `sds200_supplemental_acceptance`.
The existing `sds200_mimic_acceptance` installation, build context and image are
not updated or overwritten. The candidate has a distinct name, panel title and
MQTT/recording defaults. Its empty display configuration requires separately
reviewed provisioning of the accepted profile; do not blindly clone private App
data, credentials or initialize a replacement profile.

Only the default daemon, Ingress WebUI and native HTTPS WebUI executables are
changed in the staged runtime. Arguments, media command, authentication, profile
configuration and options/schema retain their normal contracts. Manual boot and
unmapped ports remain the staging defaults. There are no Pi, TUI, HA-card or
published-catalog changes. Before candidate startup, confirm every other scanner
owner is stopped. Recovery stops this candidate, proves exit and starts the
unchanged normal acceptance App; it does not rebuild that baseline. This avoids
depending on a local-image rebuild reproducing a previously verified image.

`scripts/accept_supplemental_web.py` patches only the native dashboard factory for
one CLI invocation, explicitly selecting delivery, demand and consumer wiring.
It requires exactly one existing authenticated path, rejects other CLI actions,
conflicting policy, prepatched/nested hooks and repeated factory construction,
then restores the original hook on return or failure. Experimental browser-device
trials are excluded from this first case. The native factory retains Ingress peer
checks, HTTPS login, display-only scope and origin protections. Page/asset loading
and cached frame reads do not arm the daemon or issue supplemental reads. The
consumer still needs the separate finite owner and authenticated demand admission.

Local fixture tests execute the generated entry, real guardian and child using
temporary paths and invalid-before-network daemon arguments. A second launch
cannot replace the first launch's evidence. Separate ASGI/Unix/native-loopback
tests check authenticated demand; these are **not physical scanner acceptance**.

## Stop is not restoration

The guard proves that its child exited; **it does not restore a Home Assistant
image or establish live dashboard health**. `restoration_verified` is always false
in its reports. `result.json` describes the acquisition outcome; `cleanup.json`
describes native worker/hook cleanup; `guard-result.json` describes process exit.
None is a user-observed scanner pass or an App restoration result.

## Next media check: live browser playback only (networking gate pending)

The existing host adapter is **reader-only, not audio-ready**. Its
`app_configuration()` check requires `50000/udp`, `50443/tcp` and `8443/tcp` to
be unpublished for both App roles. This prevents the scanner's incoming RTP
audio from reaching these bridge-networked containers. A healthy scanner
connection, an audio runtime marked running, a successful image qualification
or browser HTTP streaming does not establish RTP delivery. See the
[App RTP networking requirement](home-assistant-app.md#scanner-rtp-audio).

Do not advertise another finite audio case as ready under this unchanged
contract, or change ports after sealing a case. An audio-capable handoff needs a
separately reviewed, explicit networking contract before a new source-pinned
case is installed. It must qualify exactly container UDP `50000` to host UDP
`50000`, keep TCP dashboard/remote ports disabled, verify the actual Docker
mapping as well as Supervisor configuration, and reject unexpected mappings
or another running port/scanner owner. The normal App's original configuration
must remain pinned; do not silently change it to match the candidate. Preserve
the independent stop-before-start recovery and all file/recording checks.

Only after that offline policy and installed-network proof are qualified should
physical/audio readiness be requested. Before arming, require increasing daemon
and selected-player packet counters and the user's audible confirmation. Do
not create a recording merely to diagnose an idle-recording test's audio path.

The existing host-handoff guard requires both recording managers to remain idle
and hashes both recording inventories as protected state. Starting a recording
would intentionally violate that contract. Do not suppress `recording_active`,
omit the inventory pin, or reuse the older recording research case to make a
finite-media test pass.

The intended narrow trial is **live browser playback without recording**, after
the networking gate above is resolved. It needs a new source-pinned case and
fresh physical and audible readiness. Before arm, use the ordinary presentation's
Audio panel to establish playback and obtain the user's confirmation that transmissions are
audible. Do not select Mimic or otherwise request supplemental demand yet.
Then arm once, select one fresh Mimic consumer, and retain the same live-audio
session during the fixed read window. No capture, saved-file playback, scanner
setting change or additional audio stream is part of that window. If setup is
uncertain or the finite deadline expires, preserve the case; never rearm it.

Capture the selected browser player's packet/queue/RTP counters and the
independent read-window/exit/restoration receipts. Browser counters and the user's
report of audible transmissions establish different things and must be recorded
separately. A quiet channel is not proof of audible quality. The finite owner's
planned exit ends candidate playback; distinguish that expected end from an
interruption while the candidate is still serving the marked window. Independently
verify automatic normal-App restoration and unchanged recording/profile pins.

The existing [recording/media observer](supplemental-media-observer.md) requires
an active recording and is **not suitable** for this playback-only trial. Do not
invent a dummy recording binding or treat its failure as success. Concurrent
recording/finalization through the finite path needs a separate reviewed design
that permits exactly the intended new recording while preserving every old file
and maintaining independent recovery. Neither the earlier silent saved-player
fixture nor the playback-only trial qualifies that future recording path.

### Pre-arm audio setup result — 2026-09-22

The first finite playback-only case was handed off under the existing sealed
reader-only policy. One browser player remained on `Buffering` with zero packets;
a cached daemon observation independently reported zero packets/samples while
its audio runtime and scanner connection were running. Supervisor and Docker
both showed no published UDP port. This is a test-preparation/networking failure,
not an observed shared-reader interference or an audible-quality pass.

The player was stopped, then one `finish` notice was published to the existing
independent recovery service. No port or scanner setting was changed. The native
result recorded `cancelled`, `ever_armed: false` and `read_attempts: 0`; the guardian
confirmed clean child exit without a forced kill. The host service restored the
unchanged normal App, and an independent full audit verified healthy/idle normal
state, stopped candidate, both process-exit receipts, and unchanged image,
settings, profile and recording pins. The normal dashboard also returned to
Connected/current updates. The consumed case and all evidence remain preserved;
do not rearm or reuse it. No user listening result was requested or inferred.

## Offline host-handoff decision policy

`scripts/supplemental_handoff_policy.py` now implements the persistent decision
core for the separate-slot handoff. It deliberately has **no host adapter, network
client, scanner connection, process launcher, or operational CLI**. Nothing in
normal App startup imports it. The implementation and local fault tests do not
yet establish independent host recovery or authorize a physical trial.

The policy accepts only the normal and separate candidate App slugs above. Its
maximum action sequence is: stop normal, start candidate, stop candidate, start
normal. If an already observed candidate exits on its own, its stop action is
unnecessary. It cannot update, rebuild, uninstall, delete state or change another
App. A complete result requires a new normal container generation, verified idle
recording state and healthy cached daemon state; a running container is not enough.

A private 0700 directory is exclusively locked. Each bounded 0600 journal entry
contains a strict, versioned input event and the previous entry's hash. Events are
replayed through the same decision rules on restart, not trusted as arbitrary
saved phase/result labels. Every action's intent is written and fsynced before
the action can be returned. Reopening a journal never returns historical actions.
An interrupted write, unexpected entry, malformed/duplicate JSON key, symlink,
hardlink, permission change, replaced directory or broken event chain is refused;
evidence is not erased or repaired automatically. These hashes detect corruption,
not tampering by an administrator who controls the host and all journal bytes.

The journal binds one case, one host boot, both protected-identity digests and
observed container generations. The request expires 300 seconds after preparation;
each pending command has at most 120 seconds to reconcile. The candidate deadline
is fixed at 720 seconds after its start intent, and the whole case has a fixed
1,500-second ceiling. Restart cannot extend these deadlines. The child's tighter
600-second operator wait / 64-second acquisition window still apply independently.
Use host boot-relative elapsed time, not wall-clock dates, in the eventual adapter.

At-most-once intent is intentionally **not exactly-once command execution**. If the
guard dies after writing an intent but before sending it, it cannot safely guess
whether to send it again. Read-only reconciliation may advance when the intended
state is conclusively observed; otherwise the fixed deadline ends in administrator
review. The same applies to a lost command reply or a Supervisor job still running.
Normal is never started while candidate exit is unknown. A changed host boot,
image/source/options/protected-state pin, unexpected owner/generation or active
recording ends the automatic sequence. Such a review state does not claim recovery;
the candidate's separate finite child guard remains necessary.

The future host adapter must independently verify the meaning of every observation:

- `stopped` requires agreement between Supervisor, container state and process
  exit evidence. Missing responses and API errors are `unknown`, not stopped.
- Image/source/option/port/protected-profile digests must come from fresh validated
  evidence and a separately sealed baseline, not unchecked manifest assertions.
- `healthy` requires cached native running/connected/PSI/profile checks, plus no
  supplemental capability/worker for the restored normal App. It must not issue a
  scanner probe or acquire supplemental demand as a health check. Use unknown
  (`None`) while startup/readiness is not yet established; false means confirmed
  unhealthy. A confirmed unhealthy candidate is stopped even before readiness,
  then normal can be started only after candidate exit is proven.
- Supervisor jobs and issued command executions must be reconciled, including
  across guard restart. A client timeout does not prove the operation ended.
- Observations must be at most two seconds old. Before dispatch, take another
  fresh observation and match the action's precondition digest (which excludes
  only the sampling time). A mismatch consumes the intent without sending it.
- Run outside both scanner App containers under independently verified service
  supervision, installed before requesting a handoff. The local journal/process
  tests are not evidence of HAOS service or power-loss survival.

## Journal-to-dispatch bridge

`scripts/supplemental_handoff_executor.py` connects the journal to **injected**
observation and dispatch functions. It has no SSH, Docker socket, Supervisor
client, operational command-line entry point, or automatic service loop. The
caller must own one executor per open journal and supply bounded, qualified I/O.
The host adapter must include outstanding command executions and nested Supervisor
jobs in its observations, including after an adapter restart.

For each newly emitted action, the bridge first persists the intent, then obtains
a second fresh observation. Host boot, protected state, scanner ownership,
container generation, readiness, recording state and pending jobs must still match
the intent's preconditions. The second sample must be within two seconds of the
first, and before the operation and case deadlines. A changed or unavailable
sample withholds the command without making the consumed intent reusable. A
replaced journal directory also prevents dispatch.

Only fixed `ha apps start/stop` arguments for the two reviewed App slugs can reach
the dispatch callback. A successful callback means **submitted**, not started,
stopped or restored. An exception means unconfirmed; exception text is not returned
because it may contain private adapter output. A crash after persistence, a lost
reply, or a reopened journal never causes that intent to be sent again. Later
observations can establish the intended state or end the case in review. Local
tests use the real journal with fake I/O; they are not evidence of installed App
recovery.

## Private host protocol and execution tracking

`scripts/supplemental_handoff_host.py` supplies bounded local Docker Unix-socket
requests, strict Supervisor response decoders and a tracked dispatch callback.
It has **no installed service, automatic loop or operational CLI**. Access to a
Docker socket grants host administration even if the socket mount is read-only;
this module must remain a private administrative helper, not an App capability,
browser endpoint or arbitrary-command API.

The dispatcher only permits the four fixed start/stop commands already authorized
by the journal. It verifies the separately pinned CLI image and process generation,
creates a non-TTY/non-privileged execution in that exact CLI container, fsyncs its
execution ID into the case journal, rechecks identity and freshness, then attempts
start once. Reopening a pending intent, missing creation acknowledgement, lost
start response or a failed evidence write never authorizes a second attempt.
HTTP requests have time/output bounds, do not follow redirects or retry, and
return fixed errors rather than raw private response text. Construct the dispatcher
before polling so it can distinguish new intents from pending historical phases.

Execution inspection distinguishes **created**, **running**, and **not running
with a reported exit code**. A created execution has a null exit code; it is not
a successful completed command and does not clear pending work. A start request
may still be queued when that state is observed. The adapter explicitly requests
and verifies user `0` in the administrative CLI container; it does not rely on an
omitted default-user field.

Before collecting the next policy observation, `reconcile_executions()` persists
independently inspected process exits. Docker can expire old execution metadata,
so a previously recorded exit survives that expiry; missing metadata without an
observed exit remains uncertain. These records do **not** establish App success,
even for exit code zero. The adapter must still verify Supervisor jobs, App state,
process exit and cached readiness. The full four-command synthetic path, including
the execution records, fits within the existing bounded journal. See the upstream
[Docker execution API](https://docs.docker.com/reference/api/engine/version/v1.40/)
and [execution lifecycle implementation](https://github.com/moby/moby/blob/master/daemon/exec.go).

The jobs decoder checks every nested child, rejects duplicate identities and
missing or non-boolean completion fields, bounds tree size/depth, and refuses
ignored Supervisor safety conditions. App configuration decoding verifies the
exact slug/version, manual startup, protection, disabled automatic update/watchdog,
no host networking/PID namespace and unmapped trial ports. Nonempty private options are hashed,
not returned; a redacted empty options object cannot establish a matching pin.
That configuration digest is only one component of the eventual protected App
identity. It does not certify installed source/image, accepted profile, recording
state, other scanner owners or live health. The distinction follows the
[Supervisor App API](https://developers.home-assistant.io/docs/api/supervisor/endpoints/),
which can redact options depending on caller privileges.

Fresh, read-only host probes verified the jobs and normal-App configuration
decoders against actual Docker/CLI responses, with the normal App identity
unchanged. The probes used an SSH/curl transport adapter; the Unix HTTP transport
was separately exercised against a local Unix-socket fixture. An initial probe
stopped before execution when it found omitted user/null exit-code fields; that
execution was retained and not adopted or retried. Fixed probes used new executions.
No start/stop App operation, privileged helper installation or restoration test
is claimed by this protocol qualification.

## Source bytes and process-exit evidence

`scripts/supplemental_handoff_files.py` collects a bounded, read-only file inventory
for one explicit directory. It hashes content and records size, mode and ownership
without returning content. Directory traversal rejects symbolic links (including
ancestors), special files and multiply linked files. Descriptor/entry identity
checks reject replacement or mutation during a read. File count, total bytes,
per-file bytes, traversal depth and elapsed time are bounded. Errors are sanitized;
there is no copying, repair, App import, command execution or deployment interface.
This is not an atomic filesystem snapshot or a defense against a trusted host
administrator. The collector's users must compare consecutive observations and
separately reconstruct the expected source; an installed manifest is not proof.
Kernel-level I/O stalls still require the outer service deadline.

Read-only qualification against the running normal acceptance App established:

- All **371 context files**, including the manifest, matched a reconstruction
  using the staging driver from the exact previously installed source commit.
- All **360 installed source/assets** matched that reconstruction, without
  importing scanner runtime code as part of inventory collection.
- All **636 installed package files**, including 276 compiled Python files,
  matched the same preserved immutable image in a separate networkless,
  read-only, unprivileged inspection container. That disposable container exited
  and was confirmed absent; the scanner App's container identity stayed unchanged.

These counts describe that normal-App revision, not the new candidate. They
establish neither third-party dependency reproducibility nor a new candidate
image/protected-state pin. The cached native health check separately confirmed
running/connected/PSI state, idle recording, matching unchanged accepted profile,
and no advertised supplemental capability. It requested no scanner probe or
supplemental demand. These are baseline checks, not a restoration test.

Inspection of the installed Supervisor implementation confirmed that normal stop
removes an App's container by default. Consequently, a later missing-container
response cannot establish exit by itself. `scripts/supplemental_handoff_process.py`
adds a read-only Linux pidfd witness: bind the live Docker init process using its
PID, start ticks and exact host cgroup, then poll its kernel process handle for
exit. It sends no signals. The caller must run in both the host PID and host cgroup
namespaces and independently match the Docker image/container generation before
and after binding. Host PID access alone does not provide host-relative cgroup paths:
Docker defaults to a private cgroup namespace on cgroup v2. See the upstream
[Docker cgroup namespace documentation](https://docs.docker.com/engine/containers/runmetrics/#running-docker-on-cgroup-v2).
The parser accepts the observed HAOS unified Docker-scope layout; unknown cgroup
layouts, zombie-at-bind, changed identities and fd errors are refused.

A lost witness or a recycled/missing PID is **unknown**, not an exit receipt.
This distinction follows the [Linux pidfd contract](https://man7.org/linux/man-pages/man2/pidfd_open.2.html).
Local real-process tests verify exit readability even after reaping; they are not
host-container recovery tests. Neither this witness nor the inventory collector
has an operational controller or installs a privileged host helper.

## Durable process receipts and finite recovery loop

`scripts/supplemental_handoff_recovery.py` joins process witnesses, the private
journal, tracked command executions and the injected-observation executor. It is
**not an installed service or a complete host observer**. The independent reader
still has to establish fresh source/image/options/profile, recording, other-owner,
Supervisor/container/job and cached-health evidence. Process receipts add a gate;
they cannot replace those checks.

Before a stop submission, the original normal/candidate process binding is written
and fsynced: App slug, Docker generation, full container ID, host PID and start ticks.
The binding is checked against Docker before and after opening its pidfd. Only a
readable exact process handle can produce a durable exit receipt. That receipt must
be saved before the next owner can start. On helper restart, an exact still-live
process can be rebound. A process that disappeared before an exit was saved stays
unknown; a previously saved exit remains usable after Docker removes the container.
The newly restored normal process never overwrites the original stopped binding.

The finite loop also checks host boot and fixed deadlines when Docker/Supervisor
observations fail. No-op polling does not fill the bounded journal or extend any
deadline. A non-advancing clock hits a defensive poll-count ceiling, not a success
result. Every exit from the loop closes only its local witness handles; it neither
signals a process nor deletes the journal. Kernel/I/O stalls still require an
independently supervised outer deadline. The locked-owner request/finish interface
and private service assembly are described below; actual service installation
and controlled handoff qualification are still separate requirements.

Local fault tests exercise the full four-command path, durable exit/command replay,
lost replies, removal without exit evidence, helper restart, process exit racing
the binding write, failed journal writes, boot changes and unavailable observations.
The complete path fits within the existing 24-entry bound. These tests do not issue
App commands to a real host.

A new scanner-free HAOS fixture additionally verified an actual dummy-container
pidfd exit, durable receipt and journal replay. Its target exited normally; both
unprivileged, networkless temporary containers were confirmed absent, their private
evidence directory was retained, and the normal scanner App generation was unchanged.
An earlier fixture exited before binding and is retained as a failed, consumed case;
read-only diagnostics identified the private-cgroup namespace mismatch. The passing
fixture used a new case with both host namespaces. No scanner access, App handoff,
restoration test or installed recovery service is claimed by this qualification.

## Joined host observation and private operator input

`supplemental_handoff_observer.py` joins fixed Supervisor reads with independent
Docker image/container inspection and separately qualified file/cache collectors.
Installed versions, all container names, other scanner owners, nested jobs, Core
process generation, App configuration and native readiness must agree. Core's
CLI information response does **not** establish a running state: the collector
checks the actual Core container and its pinned generation. It repeats the App
inventory, container inventory, Core generation and jobs check before returning;
the entire observation must fit its two-second freshness budget. A known protected
file/configuration change reaches policy as a changed pin. Missing/ambiguous
responses remain unavailable, never proof of a stopped App or healthy daemon.
Scanner containers must also have Docker restart policy `no`; Supervisor's manual
boot setting alone does not establish that. A retained `exited` container can be
observed as stopped only with matching Supervisor state, exact image/container
identity, zero PID and stable exit metadata. Recovery still requires its durable
pidfd exit receipt before allowing the next owner. Initial service preparation
must require an absent candidate container and unused case state, not adopt an
old exited candidate. An unavailable native socket preserves the verified running
container identity with unknown health/recording, allowing early process binding
without granting readiness or recording-idle authority.

`supplemental_handoff_protected.py` reads the fixed host source context, running
container's qualified overlay package, four private profile inputs, and recording
contents. Source and profile limits remain unchanged. A reviewed recording root
can explicitly allow files up to 16 MiB, still within the 64 MiB total and existing
time/entry/depth budgets. It does not silently substitute timestamps for file
contents. A stopped App uses its separately verified immutable-image package
fingerprint; the observer must still inspect that image. Runtime package shadow
mounts, nested profile/media shadow mounts, wrong host mappings and unknown storage
drivers are refused. These private host paths are not a user-facing configuration
or a portable Docker-storage discovery API.
The candidate fingerprint additionally covers all three finite Python wrappers
and both exact non-writable executable entries. A matching runtime package alone
does not establish that its finite guardian/launch path is intact.

`supplemental_handoff_cached.py` uses the installed, independently qualified App
package to compare accepted/source/configuration bytes with the loaded daemon
profile and its scanner target. Its only IPC operations are hello, cached runtime,
recording status and cached display profile. It never requests supplemental
context/frame/demand, profile reload, scanner control or a probe. Profile files are
rechecked afterward; the collection has a 1.5-second bound. Recording-active and
confirmed unhealthy values are retained; invalid data is unconfirmed, not idle.
Candidate health additionally needs separate case/guardian evidence, which this
collector does not claim to supply.

The private cached reader uses five fixed versioned IPC requests and strict
correlation, framing, size and per-request time bounds. It does not import the
unrelated App startup/web/server graph. Profile paths come from separately sealed
options; accepted/source/configuration data and private permissions are still
checked through installed storage readers. The actual Unix socket peer PID and
start ticks are bound through `SO_PEERCRED` and a live pidfd. No PID signaling is
performed. The normal App read and complete protected-content inventory qualified
together in under one second on the test HAOS host, within the unchanged
two-second observation and one-second Docker transport deadlines. Earlier slow
import attempts remain failed/unconfirmed evidence; their execs are not replayed.

`supplemental_handoff_app_read.py` executes only the two sealed cached/guardian
collectors in an already qualified App incarnation, checks exec creation and exit,
then rechecks the container. Candidate readiness additionally uses
`supplemental_handoff_guard_state.py`: its private reports must match the case,
source, finite budgets and actual IPC peer; the recorded guardian must still be
that peer's live parent. Both pidfds and process start times are rechecked. A
readiness file alone cannot prove health. These checks do not arm the reader,
negotiate supplemental demand or establish that normal restoration occurred.

`supplemental_handoff_operator.py` provides a private local one-shot request/finish
inbox. The service remains the sole writer of its locked journal. An atomic,
exclusive 0600 notice binds the case, host boot, baseline digest and a fresh
boot-relative issuance time (30 seconds maximum age). It cannot execute commands,
arm acquisition, overwrite an earlier request or turn an expired case into a new
one. Durable journal events prevent reconsumption after restart. Interrupted or
ambiguous publication is retained for review rather than deleted for a retry.
`RecoverySession` can consume these notices, but invalid operator input cannot
disable the independent expiry/recovery path or confer dispatch authority.

These components are not yet an installed operational recovery service. Read-only
HAOS qualifications confirmed actual CLI/Docker response shapes, matching cached
profile/runtime state, and exact source/package/profile/recording content hashes.
The normal App incarnation stayed unchanged and temporary helpers were absent
afterward. The source context is owned by a different UID: the first
capability-stripped file helper correctly refused access. A fresh read-only helper
with `DAC_READ_SEARCH` completed without changing permissions. Administrative
Docker-socket access and host-file read capabilities must not be exposed publicly.
The service assembly below now supplies the finite loop and sealed-plan interface.
Live candidate guardian qualification and full controlled handoff/recovery remain
separate requirements; read-only qualification cannot substitute for either.

The private candidate image was also built and inspected on the development host.
All staged runtime assets and finite wrappers matched, and an isolated,
networkless image test run passed (browser-engine tests require the separate
development-host environment). This does not establish an installed HA candidate.
Containerd's image-index ID can differ from the classic Docker image-config ID;
any transferred image must be independently inspected and pinned on its destination.

## Private finite service assembly

`scripts/supplemental_handoff_service.py` assembles the qualified readers, process
witnesses, executor and one-shot inbox. It is not a shipped daemon feature, public
API or automatically installed service. An administrator must first review an
exact private plan: host boot, App/CLI/Core images and generations, installed App
versions, other scanner owners, protected input hashes, and all fourteen helper
module hashes. The plan is bounded, private, hash-pinned and rejects unknown keys.
Profile inputs and recording roots must remain separate across both Apps.

The launch builder produces one finite systemd service with no restart, a 1,510
second outer deadline and an exact-container cleanup command. Its helper uses
host PID **and** cgroup namespaces, no network, a read-only filesystem/code bundle,
bounded resources, a read-only host-data view, and write access only to its private
case directory. Docker-socket access remains administrative authority even when
the socket mount is read-only; this is never a security boundary against a hostile
host administrator. `DAC_READ_SEARCH` allows reading the separately owned source
context without changing its permissions.

Fresh preparation requires the candidate container to be absent and its guardian
case not to exist. It does not adopt a retained candidate or clear old evidence.
Supervisor's existing root-owned App data directory may be 0755; private case
directories and reports retain 0700/0600 requirements. Preparation verifies the
full normal baseline and binds its actual init-process pidfd **before** publishing
the exclusive ready report. No App changes occur without a fresh bound operator
request. Starting this service neither starts the candidate nor arms acquisition.

The journal remains the sole dispatch authority. Expiry recovery works without a
finish notice, including when the inbox contains invalid input. A completed or
expired case cannot replay an App command. The exclusive outcome report claims
restoration only for a policy-complete result; interruption, lost evidence, an
outer timeout or an unavailable observer must not be reported as success. Preserve
the journal, notices and reports for review rather than resetting the case.

Service-assembly tests use real local journals/inboxes and fake host I/O. They
cover no-request expiry, the four-command handoff/recovery path, changed boot or
baseline, unqualified code, invalid finish input, disjoint storage and terminal
re-entry. These are not evidence of a live App handoff or scanner acceptance.

## Installed private candidate and no-request service qualification

The separate candidate was subsequently installed **stopped** on the acceptance
HAOS host, with manual boot, watchdog/automatic updates disabled and all host ports
unmapped. Supervisor rebuilt the local App, so its destination image was inspected
and pinned independently rather than assuming the desktop image ID would carry
over. All staged runtime assets and five launcher files matched. Its networkless
image run passed 325 selected tests, with two browser-engine checks skipped.
The post-test bounded inventory returned unconfirmed; two fresh independent
read-only inventories then matched the pre-test image fingerprint. The failed
completion check remains preserved, not relabeled as a successful run.

The candidate received an exact copy of the approved profile source, new local
endpoint/source identities, new accepted-profile state, separate empty recording
storage and a deployment with upload disabled. Credentials, browser state and
recordings were not cloned. Only its validated options were changed. Supervisor's
unversioned API uses the v1 `/addons` routes despite the CLI's user-facing `apps`
name; the initial `/apps` request received a definite 404, unchanged options were
confirmed, and the correct route was then used without repeating provisioning.

Two complete host observations qualified in approximately 1.15 seconds each,
within the unchanged two-second limit. An earlier unconfirmed context inventory
remains a consumed failed preparation, not a partially accepted plan.

A **different**, no-request fixture case exercised the actual independent service
on HAOS. After the launching SSH session ended, it bound the existing normal
process, waited its fixed request period and expired after approximately 301
seconds. Replaying its journal showed preparation, process binding and a final
expiry observation, with **zero App commands** and no operator notices. Its helper
container was gone, all protected pins were unchanged, the normal App remained
healthy in the original incarnation, and the candidate remained stopped. The
report correctly said restoration was not verified: no handoff occurred, so this
proves independent no-request expiry, **not** live recovery from a candidate.

The acquisition case was subsequently consumed by the finite physical trial
below. Neither it nor the expired no-request fixture may be reused.

## First finite live trial: reads bounded, automatic restoration failed

The source-pinned, separate candidate was handed scanner ownership only after the
independent host service recorded the normal process exit. It reached cached
native/guardian readiness, was armed once through the pidfd-bound helper and
received one authenticated Mimic browser consumer. The daemon reported
`window_expired` with **52 read attempts**, below its 60-attempt limit. The browser
displayed supplemental day/time during the trial; that observation does not prove
complete Favorites-field parity or that every attempt succeeded. The physical
scanner observation is recorded separately from these software results.

The guardian recorded a clean daemon exit (status 0), without a forced kill, and
the host service durably recorded the candidate process exit. However, the
unchanged normal App supervisor treats any unrequested child exit as unexpected,
including status 0. It consequently exited the candidate container with status 2,
and Home Assistant reported the candidate App as `error`. The host observer
refused that state, so it did **not** automatically start the normal App. This is
a failed end-to-end restoration trial, not an automatic-recovery pass.

Administrative recovery first stopped the independent service, then submitted
one stop for the already-exited candidate. This removed its container but did not
clear Supervisor's `error` state. A separate read-only audit retained that state
as **unknown**, never relabelled it stopped, and checked container absence plus
the original durable process-exit receipts. The sealed code, options, profiles,
recordings, Core, other scanner owners and idle jobs were checked before one
normal-App start. A new normal generation subsequently passed cached native
health and idle-recording checks with unchanged protected pins. The normal
dashboard was verified updating. Bounded inventory reads that failed remain
preserved separately from fresh successful observations.

No original journal entry was rewritten, no successful service outcome was
fabricated, and the candidate was not restarted or rearmed. Its case is closed
for reuse, including its persistent guard state. Supervisor still retaining an
error for this inactive test slot is not evidence of a running scanner owner.

Before another physical trial, qualify the acceptance-only shutdown integration
through the outer App supervisor and Supervisor's actual resulting state. Tests
now explicitly preserve the normal App rule that an unsolicited status-0 child
exit is still a failure; do not weaken that production behavior to make this test
pass. A corrected trial requires a new case and newly sealed image/plan. Audio,
recordings, Pi/TUI behavior, HA-card delivery and full field parity were not
qualified by this finite clock/global-Favorites trial.

### Acceptance-only completion witness

The private staging adapter now patches only its generated outer App supervisor.
The repository's normal supervisor and normal App staging remain unchanged:
unsolicited child exits, including status 0, still fail there. The private loop
retains the normal signal/reload handling, sibling checks and ordered, bounded
cleanup; completion is not a restart or rearm mechanism.

The staged loop binds the actual owned guardian while it is live, then binds its
native child as readiness becomes available without blocking other App checks.
Both process identities include parentage, start ticks and open pidfds. A clean
return requires both processes to have exited, the actual guardian return code
to be 0, consistent private reports bound to the staged source and generation,
and no web/media/native-web child failure. Only the expected finite outcomes
(`window_expired`, `quota_exhausted`, or never-armed `operator_wait_expired`) are
accepted. Arm receipts, quota limits and the guardian's non-forced child exit
must agree. Missing, replaced, publicly readable, linked or inconsistent reports
fail closed. The witness performs no scanner reads, writes or signals and makes
no restoration claim.

Scanner-free regression uses real disposable guardian/child processes and the
actual generated outer supervisor. It checks success, premature completion,
asynchronous readiness, source/identity mismatches and damaged evidence. The
combined supplemental, staging and App-supervisor suite passed 3,879 tests before
the additional staging-boundary checks. These are local software results, not
proof of Supervisor's live resulting state or automatic restoration. Packaged
container qualification and a new sealed physical trial are separate gates.

The packaged-image check subsequently passed eleven scanner-free scenarios:
three expected finite outcomes, cancellation, short quota evidence, missing
result, forced-kill evidence, each of the three sibling failures, and SIGTERM.
The installed staged supervisor and shipped guardian ran real synthetic child
processes in disposable network-disabled containers. Expected completion and
SIGTERM exited 0; invalid evidence and sibling failures exited 2. All owned
children exited and the installed package/wrappers remained unchanged.

A separate scanner-free fixture then ran once through the **actual Home
Assistant Supervisor**, in the inactive supplemental test slot. Every installed
non-bytecode runtime file and all five wrappers matched the reviewed staged
source. Its own HA-built package fingerprint was retained because compiled
bytecode differs between builds. The fixture launched only synthetic processes,
not a scanner daemon or web/media service. Its container exited 0 and Supervisor
reported `stopped`; this also replaced the old cached error with a real observed
state transition. The normal App kept the same process generation, and all old
candidate data/options/case files were unchanged. The old build context was
archived intact before replacing the inactive test image.

This proves the corrected finite completion is recognized as a clean App exit;
it does **not** prove an automatic scanner-ownership handoff or normal-App
restoration. The fixture's case is consumed. The earlier failed live case remains
closed and its physical observation unconfirmed. A new source-pinned native
case and independently sealed restoration plan are required for that retest.

### Finite live retest: automatic restoration verified

On September 22, 2026, a new native case pinned to source
`1fb69d7967813f35214d20fb2cc24330794b8993` completed the full independent
handoff and restoration path. The host service recorded the original normal
process exit before starting the candidate. The candidate reached cached native
and guardian readiness, received one explicit pidfd-bound arm, and ended with
`window_expired`: **34 read attempts**, within the 64-second window and
60-attempt cap. An attempt count is not a count of successful reads.

One operator sequencing error is retained with the evidence: the first browser
consumer was selected before arm and stopped with the conservative unconfirmed
demand warning. The native case still contained only its waiting readiness
report, with no arm or armed receipt; the acquisition gate rejects unarmed
reads. That stopped browser session was closed, not retried. After the initial
native arm, one fresh consumer displayed current scanner day/time and current
global Favorites quick-key states. This is not acceptance of the LCD F0/S0/D0
rows or full field parity. Future trials must arm before selecting a consumer.

The guardian confirmed native exit 0 without a forced kill. The independent
host service recorded the candidate process exit, observed Supervisor's stopped
state and automatically started the unchanged normal App. Its journal reached
`complete` / `restored` and its outcome reported `restoration_verified: true`.
No manual stop, finish request, recovery start or replay was needed.

A separate post-completion, read-only audit verified both durable process-exit
receipts, a new healthy normal generation with recording idle, the stopped
candidate, idle Supervisor jobs, unchanged protected pins, Core running and the
other scanner owners stopped. The finite service exited successfully and its
helper container was absent. The restored normal browser was independently
verified connected and updating; both temporary candidate tabs were closed.
The consumed case and all earlier failure evidence remain preserved.

These software and automatic-restoration checks passed. After the marked
window, the user separately confirmed: **"Scanning stayed normal."** The physical
observation therefore passed as well; it was not inferred from software results.
This trial did not exercise audio, recording, Pi/TUI behavior, HA-card
delivery, scanner controls, scanner settings or power-loss recovery, and does
not enable supplemental reads in the normal App or a published release.

## Scanner-free host-runtime qualification

`scripts/supplemental_handoff_runtime.py` builds two fixed disposable fixture
commands; it does not execute them. Each fixture needs a new UUIDv4 and an already
installed immutable image ID. The command is bounded below 8,000 bytes. This avoids
embedding a whole supervisor program in an SSH command: the examined HAOS
Dropbear version has a 9,000-byte command limit, and read-only transport probes
confirmed that an 8,000-byte command succeeded while a 10,000-byte command was
rejected. See the upstream
[Dropbear command limit](https://github.com/mkj/dropbear/blob/DROPBEAR_2026.93/src/sysoptions.h).
That finding strongly supports, but does not recover the discarded error output
from, the earlier failed oversized helper launch. That old fixture is not retried.

The fixtures run fixed Python standard-library code in a networkless, read-only,
unprivileged container with no mounts, ports, Docker socket or scanner access.
One completes after its launching SSH connection ends. The other ignores TERM
and exceeds its systemd deadline. A supervised, exact-container stop operation
must then remove that disposable container; the Docker client's exit alone is not
proof of container exit. Unit creation is not container readiness, so independent
read-only inspection must wait for the exact container without repeating launch.
Only exact standalone worker log markers count, not markers embedded in systemd's
printed command arguments.

Both fixture modes were exercised on the acceptance host. Normal completion
continued after SSH disconnect, and the forced-deadline case removed its exact
container. The normal scanner App's image and start identity were unchanged.
The completion fixture finished before a successful running-container inspection;
its journal and final state establish completion, not a complete running-state
inspection. The expiry fixture additionally has a running-state isolation check.
These results qualify the tested supervision mechanism only. They do **not**
install a restoration service, exercise App handoff, prove power-loss recovery,
or replace sealed-baseline, host-observer and tracked-command-adapter review.

Staging a verified build context does not build, install or start an App and does
not install a host restoration guard. The 2026-09-22 finite trial above exercised
these boundaries for its one consumed case. Each future live case must freshly
satisfy the same setup requirements; none of its old one-shot actions may be
replayed:

1. A new clean source-pinned staging inventory from the separate adapter, followed
   by image and installed-source verification. Existing research/staging modes
   do not select this path, and offline context verification is not image proof.
2. A fresh read-only baseline of the actual App owner, versions, options, ports,
   profile and recordings. Historical restored versions are not current proof.
3. A separate host-side restoration deadline, installed before candidate startup,
   that outlives the App/container and starts the unchanged verified normal
   acceptance App only after the separate candidate is confirmed stopped. Verify
   both Supervisor and container/process state; preserve the normal image/context
   throughout the trial. No rebuild/downgrade, second scanner owner, normal-image
   assumption, credential change, or recording cleanup is allowed. Unknown stop
   or start outcomes require reconciliation, not blind replay.
4. Offline fault tests for that staging/restoration adapter, followed by fresh
   physical readiness for a narrow WebUI clock/global Favorites continuity check.
   Open one fresh consumer after explicit arm; do not repeatedly reopen a stopped
   consumer to chase success. Audio, recording, TUI, and HA-card checks are separate.

The prior `shared-reader-audio-retest-20260921.IH9sCC` hardware case remains
`CLOSED_NO_RETRY`. Its operational helpers must not be executed or rearmed.
