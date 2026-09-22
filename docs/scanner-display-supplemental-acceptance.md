# Finite supplemental acceptance launcher (internal, not deployed)

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

Staging a verified build context does not build, install or start an App and does
not install a host restoration guard. Before the first live trial, all of the
following still need an explicit reviewed setup:

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
