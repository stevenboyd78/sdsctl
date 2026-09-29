# Original-peer native watcher — offline prototype

This separately built Linux executable is **uninstalled and not selected by any
App, Python entrypoint, image source profile, active permission, or release**.
It is the first offline part of the proposed host supervisor, not that complete
supervisor. It does not launch peers or provide App admission/recovery.

The [source](supplemental_peer_watch.c) joins the existing original
[Custody and Watch](../supplemental_recording_peer_termination.py) in
[disposable-process tests](../../tests/test_supplemental_native_peer_watch.py).
The tests re-collect both runtime comparisons inside the original two-second
capture window, retain original clocks/handles/plan, then exec this program with
stdin unused. Engine, cgroup and runtime-publication facts remain synthetic.

## Offline build

Use the [Makefile](Makefile) with an explicitly selected absolute scratch
`BUILD_DIR`. It produces `supplemental-peer-watch`, a static PIE, outside the
checkout. There is deliberately no install, clean, service, container, package
publication, or host configuration target. A C11 compiler, 64-bit `time_t`, Linux
headers and static libc development files are required. No compiler or library
is automatically installed. Local binary artifacts are not committed.

The test suite also builds dynamic and undefined-behavior-trapping PIEs as
development comparisons. Exact native exit-code checks prevent a sanitizer
crash from being counted as an expected malformed-input refusal. It checks
the static build has no ELF interpreter or needed shared library, with RELRO,
immediate binding and a non-executable stack. Static linking removes the host
Python/shared-loader dependency; it does **not** authenticate compiler, libc,
ELF bytes, architecture, kernel ABI, source publication, or actual host runtime.
Those must be independently pinned and reviewed before any installed proposal.

A second [full-command fixture](../../tests/test_supplemental_native_peer_command.py)
joins the actual original preflight, staged input listener, baseline, retained
observer plan, paired re-collections, native exec watch, descriptor delivery,
fixed passive writer release, and independently observed writer/observer exits.
Original files and cutoffs survive the join. Its earlier setup still uses
fixture stdin; passing does not qualify complete installed exec startup or
fixed outer/observer selection. No Python watchdog is substituted for the
native executable in this variant.

## Narrow, experimental ABI

The inherited-handle mode is `--offline-original-peer-watch-v1`, followed by the
**original absolute BOOTTIME** recovery cutoff and readiness cutoff in integer
nanoseconds, and the original clock witness's canonical 32-hex boot ID. There
are no PID arguments, relative durations, retries, operation commands, paths to
private inputs, or recovery tokens. This mode name selects behavior, **not
authenticated user consent**. A caller could lie about a cutoff or descriptor;
the independently qualified launcher remains responsible for binding these
inputs to original Custody. The tests are not such an installed launcher.

Inherited descriptor ABI:

| FD | Required original object |
| --- | --- |
| 3, 4 | Distinct writer and observer pidfds |
| 5 | Distinct outer-owner pidfd |
| 6 | Nonblocking cancellation pipe, read-only end |
| 7 | Different nonblocking readiness pipe, write-only end |
| 8 | Retained original outer time-namespace descriptor |

The test launcher collision-safely duplicates these objects, execs an already
opened local executable via fd9, and uses an empty environment. The native code
closes stdin/stdout/stderr and every fd above8, including the executable and any
accidentally inherited cancellation writer. It validates pidfd kind/permission,
distinct kernel identities, pipe access modes, time namespace type and current/
children namespace equality, boot ID, and finite cutoffs. Readiness must still
be within the original two-second window; recovery cannot exceed the existing
1500-second ceiling. These checks cannot establish original runtime provenance.

After validation it arms its **own private** nonperiodic BOOTTIME timer at the
unchanged recovery cutoff. The original owner cannot consume or re-arm that
timer through an inherited duplicate. BOOTTIME includes suspend time but is not
a wake-from-suspend alarm; the absolute cutoff is not renewed on resume.
[Linux timerfd documentation](https://man7.org/linux/man-pages/man2/timerfd_create.2.html)

One readiness byte is emitted, then the program waits only on retained pidfds,
the cancellation pipe and its kernel timer. It sends SIGKILL independently to
both original peers on peer loss, outer loss, cancellation or deadline. It never
falls back to a numeric PID, process group, discovery or Engine stop command.
Pidfds avoid the PID-reuse race; successful signaling is not proof of exit.
[Linux pidfd signal documentation](https://man7.org/linux/man-pages/man2/pidfd_send_signal.2.html)

Exit reasons use the existing Watch contract: `0` both peers observed exited;
`10` deadline; `11` peer loss; `12` outer loss; `13` cancellation. None is recording
success, actual native-worker exit, App restoration, or recovery permission.
`64` is an unrecognized/malformed pre-commit invocation; `70` is uncertainty
after target commitment and independent stop attempts. Failed readiness always
closes the caller's original one-attempt case; it cannot justify re-exec/retry.
An original peer or outer exiting during exec also stops survivors without a
new PID lookup. Handled termination signals produce uncertainty and attempt
both stops. Atomic `ppoll` signal-mask exchange avoids a lost-signal wait race.

## One-shot original-handle ingress

The separate `--offline-original-peer-ingress-v1` mode takes the **same original
absolute cutoffs and boot ID**, but starts with only three independently supplied
anchors. No peer stop targets are inherited:

| FD | Launcher-supplied original anchor |
| --- | --- |
| 0 | Original outer's unnamed, connected, nonblocking UNIX SEQPACKET receiver |
| 1 | Independently retained original outer pidfd |
| 2 | Original time-namespace descriptor |

These anchors must come from a qualified launcher. They are not discovered from
incoming traffic. Before waiting, the executable validates the original time
domain and remaining finite budget, the live outer handle, socket kind and
unnamed endpoints, and already-enabled `SO_PASSCRED`. It compares `SO_PEERCRED`
with the retained outer's PID and receiver's UID/GID, then requires matching
per-message `SCM_CREDENTIALS`. An inherited socket alone does not authenticate
the child that actually sends a packet. The outer must still be live through
authentication so a recycled numeric PID cannot establish the match. This is
not a defense against a privileged trusted principal capable of forging
credentials or replacing the launcher inputs.

One packet contains the exact ASCII bytes `original-peer-handles-v1`, without
a terminator, one six-descriptor `SCM_RIGHTS` array in the inherited ABI's order,
and one credentials record. Both truncation flags, unexpected ancillary data,
missing/excess descriptors and trailing packets are refused. The sender must
close its write half after that packet. The receiver waits for that EOF within
the **unchanged readiness cutoff**, using a private absolute BOOTTIME timer and
the original outer pidfd. It never accepts a second attempt. Received outer and
namespace handles must duplicate the independent anchors. Collision-safe
descriptor remapping then joins the same target validation, private recovery
timer, readiness byte, and original-peer termination code as inherited mode.
[Linux UNIX socket semantics](https://man7.org/linux/man-pages/man7/unix.7.html),
[Linux recvmsg flags](https://man7.org/linux/man-pages/man2/recvmsg.2.html)

Ingress rejection is `64` and confers **no signal authority** over unconfirmed
received targets. Exiting closes received descriptors and queued references
owned by this receiver; the original outer must independently retire its own
peers. Only after authentication and the common distinct-target commit can a
failure trigger their native stop attempts (`70`). Callers must close their
extra receiver copies: otherwise unread queued rights can keep a readiness pipe
alive after rejection. Native process exit and absence of readiness are checked
independently; a pipe timeout is never inferred success. A post-cutoff bounded
failure observation is not an extension of the readiness/admission window.

The [ingress fixtures](../../tests/test_supplemental_native_peer_ingress.py)
exec real native processes and transfer original Custody handles both before
and after exec. The full passive-command fixture also selects this transport
explicitly, retaining original comparisons, baseline, release and actual exits.
No existing installed entrypoint or source inventory selects this mode.

This three-anchor form is informed by upstream systemd v256's
`StandardInputFileDescriptor`, `StandardOutputFileDescriptor`, and
`StandardErrorFileDescriptor` setters and direct stdio duplication paths. It is
**not** `StandardInput=fd:`, named socket activation, path-based `OpenFile`, or
the newer `ExtraFileDescriptors` API. The local test uses `posix_spawn`, not
systemd. Exact installed v256.17 behavior, descriptor flags, authenticated
transient service admission and outside-freeze-domain placement remain
unqualified; no systemd/HAOS calls, service installation or native deployment
are performed by these tests.
[Upstream v256 service setter](https://raw.githubusercontent.com/systemd/systemd/v256/src/core/dbus-service.c),
[Upstream v256 exec stdio setup](https://raw.githubusercontent.com/systemd/systemd/v256/src/core/exec-invoke.c)

## Original parent and native lifetime

The [lifetime fixture](../../tests/test_supplemental_native_peer_lifetime.py)
now forks an actual original outer, which execs the native ingress and constructs
the **unmodified direct-child Watch** itself. Original handles and absolute
cutoffs are passed once. Only a disposable fixture driver is a subreaper; pytest
and the host are not changed. The driver receives the original native pidfd,
never reopens a reported PID, and retains an extra cancellation writer so EOF
cannot hide the outer-pidfd loss path.

Five real process faults are distinguished:

- Outer death before target ingress: native refusal `64`, no target authority;
  the independent original fixture owner retires its own peers.
- Outer death after readiness: native peer stops and exact exit `12`.
- Frozen outer: native stops both peers at the original BOOTTIME cutoff and
  exits `10`. The still-live outer remains its parent; the driver cannot reap it.
  Resuming the outer allows that original owner to reap through `Watch.finish`.
- Native SIGKILL: the original outer reaps its child and independently stops
  both peers, retaining uncertainty rather than treating the signal as success.
- Frozen native: the outer's own private original-deadline timer triggers peer
  and native stops. The original outer reaps, again reporting uncertainty.

Only after outer death may the disposable driver adopt/reap the orphan. Numeric
PIDs are used to reap known original children, never for signaling or discovery.
Exit status is checked separately from pidfd readability. Failure observation
after the cutoff never extends readiness, work, or recovery permission. These
tests use synthetic container identities, not runtime/cgroup qualification.
They do not prove survival of simultaneous parent/native failure or host freeze.

The full-command fixture separately kills the actual native watcher after each
real descriptor handoff. Existing delivery guards refuse the remaining phase,
the original outer retires both peers, and no passive writer-release message is
sent. The consumed attempt and original case files survive a rejected retry.
This joins failure handling to original Custody/full comparisons and the real
passive command; it still does not supply an active App grant or installed proof.

### Investigated scope route, not an admitted launcher

`Watch.finish` uses `waitpid` for its original direct child. A systemd **service**
would own its process instead; a nonchild pidfd is not reaping authority. Do not
weaken that owner/type boundary or manufacture exit status from readability.
An alternative is a separately qualified **scope**: upstream v256 manages
externally created processes and leaves exit collection to their original parent.
Scope success is not a native exit receipt.
[Upstream v256 scope semantics](https://raw.githubusercontent.com/systemd/systemd/v256/man/systemd.scope.xml)

Upstream v256's transient scope setter accepts `PIDFDs` as an array of Unix
descriptors. That suggests a way to place the **original live child** separately
without changing its parent. It is not proof of installed v256.17 behavior,
permission, cgroup independence, immutable placement or source provenance.
[Upstream v256 PIDFD scope property](https://raw.githubusercontent.com/systemd/systemd/v256/src/core/dbus-scope.c)

Further source inspection finds an additional blocker: `unit_attach_pids_to_cgroup`
verifies the pidref, then passes its **numeric PID** to `cg_attach`; upstream
explicitly identifies that migration as racy. A PIDFD property alone therefore
does not establish atomic exact-process placement. Keeping an unreaped direct
child while its parent lives does not settle parent death, adoption/reaping and
an in-flight manager action. Post-move validation cannot undo moving an unrelated
replacement. The current route is **not admitted** by this original-handle
contract; no fixture or installed trial may silently waive this gap.
[Upstream v256 cgroup attachment](https://raw.githubusercontent.com/systemd/systemd/v256/src/core/cgroup.c),
[Upstream v256 numeric migration](https://raw.githubusercontent.com/systemd/systemd/v256/src/shared/cgroup-setup.c)

Stock v256 `systemd-run` retries with numeric PIDs when its pidfd property is
unsupported. That fallback conflicts with this one-attempt original-handle
contract; do not use it as-is or silently retry. A future qualified route must
refuse unsupported descriptors, ambiguous replies, late completion, existing
units and replacement jobs. No scope/service or D-Bus action is performed here.
[Upstream v256 scope launch fallback](https://raw.githubusercontent.com/systemd/systemd/v256/src/run/run.c)

Any eventual parent-preserving route must retain original Custody and clocks,
exec the original child with only its three anchors, withhold target ingress,
then independently
establish that child's exact fresh scope/source/runtime/namespace and placement
outside all peer/outer freeze and kill domains; then send the one original
target packet and require native readiness before the **same original** cutoff.
Scope job completion alone is not this evidence. The fixture's `G` barrier is
only test sequencing, never a placement receipt. Failed or incomplete placement
must leave targets with their independent original owner, not make a replacement
launch acceptable. The outer's own qualified placement/termination and the
pre-readiness loss interval still require an independent platform contract.

`RuntimeMaxSec` cannot replace the native BOOTTIME timer: upstream scope expiry
is calculated from a monotonic activation timestamp, not our original clock.
[Upstream v256 scope timer](https://raw.githubusercontent.com/systemd/systemd/v256/src/core/scope.c)

### Parent-preserving creation experiment (test-only)

The [clone3 process fixture](../../tests/test_supplemental_native_peer_parent.py)
and its [small C helper](../../tests/fixtures/native_parent_spawn.c) now exercise
`CLONE_PARENT | CLONE_PIDFD`: the helper creates the native watcher as a direct
child of the original outer, then transfers the kernel-created pidfd over its
private authenticated socket. The helper cannot reap that child (`ECHILD`);
the original outer independently verifies its own parenthood with `waitid`,
and the **unchanged Watch** later reaps it exactly once. The reported PID is
never reopened. The helper's own exit is checked and reaped separately before
the one-shot target ingress is sent. No readiness or action is inferred from
creation, receipt, or helper exit. All phases share the original startup cutoff.

A separately compiled test variant supplies `CLONE_INTO_CGROUP` with a retained
read-only directory descriptor for the fixture's **existing current** cgroup.
It tests creation directly in that existing group, not migration. It does not
create, change, freeze or delegate a cgroup. Non-cgroup directories are refused
without falling back to unplaced creation. Environments without write authority
in their own current group skip this particular test explicitly; a skip is not
qualification. This variant proves neither an independent failure domain nor
permission to provision one. Ordinary parent tests do not set that flag.
[Linux clone3 flag semantics](https://man7.org/linux/man-pages/man2/clone.2.html)

The original Custody fixture and full passive-command flow both use this actual
clone/handle-transfer/native-ingress path. They preserve original comparisons,
timers, owners, case files, one attempt and release/exit distinctions. Failures
after the original child report stop and reap that child and the original peers;
native death after each real handoff still prevents passive release. These are
actual local kernel/process checks, with explicitly synthetic Engine/runtime
facts. No production source profile, entrypoint, build target or App selects
the test helper, and it is not a separately qualified native host launcher.

Fault injection also checks a rejected target send between successful creation
and returning the native handle to the caller. Until return, the ingress fixture
itself owns that child and must stop/reap it before propagating the failure.
Both direct-spawn and original clone-pidfd paths are tested; no handle ownership
is lost in that interval and the consumed attempt does not release readiness.

The incomplete report interval is still an important gate: losing the helper
before the original child handle is delivered must not orphan an untracked
child, cause discovery/reopening of a PID, or grant any peer action. The native
ingress itself has a finite original cutoff, but that alone does not qualify
the outer's complete child accounting and failure/reaping path. Protected
original target-cgroup provisioning, outside-domain independence, namespace and
binary/source/runtime authentication also remain separate requirements. Do not
promote same-group test success into independent-platform qualification.

### Direct original-owner creation experiment (test-only)

The [direct fixture](../../tests/test_supplemental_native_peer_direct.py) avoids
that helper-report interval instead of accepting lost-child discovery. A small
[C-only clone/exec boundary](../../tests/fixtures/native_direct_spawn.c), loaded
only into the disposable test owner, calls `clone3(CLONE_PIDFD)` there. The
kernel writes the child's close-on-exec pidfd directly into that original
owner's retained memory. No helper process, `CLONE_PARENT`, pidfd report, PID
reopening, numeric-PID migration or old-clone fallback is involved. The existing
`OriginalChild`, native ingress, full Custody comparisons and **unchanged Watch**
then consume that exact handle and original absolute cutoffs.

The child stays entirely in the small C path until native exec: collision-safe
copies become the same three standard-I/O anchors and opened executable; other
descriptors are closed. `CLONE_CLEAR_SIGHAND` resets caught handlers, and the
child clears its signal mask without entering Python, running atfork callbacks,
allocating interpreter objects, or sharing the parent's memory/descriptor table.
These flags and the returned pidfd location follow the
[Linux clone3 contract](https://man7.org/linux/man-pages/man2/clone.2.html).
They do not authenticate the loaded fixture, libc, compiler or installed runtime.

Failure tests retain the original kernel output even when C reports failure,
Python is interrupted before adopting `OriginalChild`, or the C caller queues
a signal whose Python handler raises **before ctypes returns**. Cleanup uses
that pidfd to stop, observe and reap the exact child. No readiness is released,
the original peers are retired, consumed Custody cannot retry, descriptors
balance and original plan/deadline bytes remain unchanged. A rejected group
descriptor creates no child; an explicit same-**existing**-group variant tests
birth through the retained directory without creating, moving or freezing a
group. Same-group success is still not independent placement.

The full passive-command fixture selects this route explicitly alongside the
earlier experiments, including native death after each real descriptor handoff.
The lifetime fixture also creates its Watch inside the original outer and
separates outer death/freeze from native death/freeze; only its disposable
independent subreaper can adopt after outer death. Direct creation removes the
separate helper's unreported-child window in this experiment, not the original
outer's own death/freeze/platform failure requirements. No production source
inventory, entrypoint, native build target or App selects this shared fixture.
It is not a proposal to install a Python-loaded shared library on HAOS. A fixed
qualified outer must incorporate an equally accountable native creation
boundary, with independently protected placement, complete runtime/source
provenance and exclusive recovery, before any installed or hardware trial.

### Opt-in local cgroup-freeze integration

The [disposable cgroup fixture](../../tests/test_supplemental_native_peer_cgroup.py)
joins direct original-owner creation and the existing lifetime driver to actual
local cgroup v2 freezing. It requires explicit `SDSCTL_OFFLINE_CGROUP_TEST=1`
approval on a delegated local workstation; default test runs skip it. Permission
bits, ordinary CI success, or this documentation do not authorize opting in.
There is no sudo, service/controller change, migration, or fallback on refusal.

Only fresh temporary groups are created below the caller's current scope. The
native watcher is born into the new leaf via `CLONE_INTO_CGROUP`; the test freezes
only its fresh parent, observes both parent and descendant `frozen=1`, and checks
that the original outer, driver, peers and independent guardian remain outside
that subtree. It NEVER freezes the existing caller scope or an ancestor. Exact
directory handles restrict writes/removal to the new groups. No `cgroup.procs`
write, numeric-PID migration, `cgroup.kill`, or thaw operation is used.

Before fault injection, the independently surviving guardian receives original
native/outer/peer pidfds through a credential-checked private socket. It does not
reopen reported PIDs. The original outer's existing two-second readiness and
four-second recovery cutoffs remain unchanged. It independently stops peers and
reaps its frozen native child at the original deadline, reporting uncertainty
rather than successful work. The test verifies this before guardian fallback
cleanup. A deliberate guardian **exception** after observed freeze checks exact-
handle fallback cleanup; it is not a claim to survive guardian process death.

A second join uses full original Custody comparisons and the existing eight-
second short test plan. Cancellation written to a frozen watcher is not treated
as success. The independently retained original timer still expires; exact-peer
exit and native reap are observed, uncertainty is retained, original plan bytes
and deadlines stay unchanged, and consumed Custody cannot retry. Engine/runtime
identities in this fixture remain synthetic. Neither fixture changes production
timeouts or active source selection. Empty-only removal follows observed exits;
if cleanup is uncertain, the fixture fails and retains populated groups.

These are local watcher-subtree freeze tests across dynamic, static and UBSan
builds. They do NOT qualify installed independent placement, freezing the whole
original-outer domain, simultaneous owner loss, host-wide suspension, fixed
active entrypoints, platform provenance, App actions, or recovery authority.

## Failure model and remaining gates

The original qualified outer must independently stop its retained peers if the
watcher cannot start, rejects its inputs, or dies. No one can infer readiness
from a spawn or a successful byte write alone. Live source/placement checks,
original deadline checks and watcher liveness remain necessary before release.

The watcher itself must be placed and supervised **outside every peer and outer
freeze/kill domain** by a separately qualified host mechanism. Forking or execing
it from a container does not accomplish that. A frozen cgroup includes its
descendants; a watcher inside that subtree stops executing too.
[Linux cgroup v2 documentation](https://docs.kernel.org/admin-guide/cgroup-v2.html)

The local SIGSTOP tests show separate progress when peers or one owner stop.
The explicit opt-in cgroup tests above additionally cover one freshly isolated
watcher subtree, not installed systemd or Docker qualification. Host-wide freeze,
loss of scheduling, watcher SIGKILL/OOM/kernel failure and uninterruptible I/O
remain outside this executable's guarantee. Even correct SIGKILL dispatch does
not certify actual exit. A second qualified mechanism/outer failure contract
and independently observed exact-peer/native/App exits are still required.

No arbitrary restored-App or recording recovery is attempted here. Fixed
outer/observer entrypoints, protected descriptor transfer from a real host
launcher, independent lifetime/provenance, explicit action-scope admission,
exclusive recovery, installed timing, and a fresh approved finite test procedure
remain gates. Existing closed cases, Apps, scanner, Pis, and recordings are not
inputs to these tests and must not be touched. Installation or an isolated HAOS
trial requires separate specific approval.

The next offline join must bind this ingress to a **fixed qualified outer and
independently supervised launch/lifetime**, including launch failure and watcher
death handling. Source/binary/interpreter/runtime publication pins and active
App admission/exclusive recovery cannot be inferred from socket authentication.
Opening process paths or reopening PIDs is never a substitute for transferring
original handles. No current offline process result qualifies an installed
launch path or permits a fresh hardware trial.
