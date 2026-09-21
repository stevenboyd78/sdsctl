# Finite supplemental acceptance launcher (internal, not deployed)

This is offline-tested acceptance infrastructure for the
[bounded acquisition owner](scanner-display-supplemental-acquisition.md) and
[explicit authenticated demand](scanner-display-supplemental-demand.md).
It is not continuous polling, a release feature, a normal startup option, or
authorization to repeat a closed hardware case. The ordinary CLI, WebUI factory
selection, App staging script, and HA card configuration remain unchanged.

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

## Stop is not restoration

The guard proves that its child exited; **it does not restore a Home Assistant
image or establish live dashboard health**. `restoration_verified` is always false
in its reports. `result.json` describes the acquisition outcome; `cleanup.json`
describes native worker/hook cleanup; `guard-result.json` describes process exit.
None is a user-observed scanner pass or an App restoration result.

No new App image has been staged or deployed using these scripts. Before the
first live trial, all of the following still need an explicit reviewed setup:

1. A new clean source-pinned staging inventory containing both launchers, the
   matching runtime, a fixed persistent case path, and opt-in authenticated WebUI
   consumer wiring. The existing research/staging modes do not select this path.
2. A fresh read-only baseline of the actual App owner, versions, options, ports,
   profile and recordings. Historical restored versions are not current proof.
3. A separate host-side restoration deadline, installed before candidate startup,
   that outlives the App/container and restores the verified normal acceptance
   source only after the candidate is confirmed stopped. No second scanner owner,
   normal-image assumption, credential changes, or recording cleanup is allowed.
   Unknown stop/rebuild outcomes require reconciliation, not blind replay.
4. Offline fault tests for that staging/restoration adapter, followed by fresh
   physical readiness for a narrow WebUI clock/global Favorites continuity check.
   Open one fresh consumer after explicit arm; do not repeatedly reopen a stopped
   consumer to chase success. Audio, recording, TUI, and HA-card checks are separate.

The prior `shared-reader-audio-retest-20260921.IH9sCC` hardware case remains
`CLOSED_NO_RETRY`. Its operational helpers must not be executed or rearmed.
