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

The sole recognized mode is `--offline-original-peer-watch-v1`, followed by the
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

The local SIGSTOP tests show progress independence from stopped peers, not
cgroup-freeze, installed systemd, or Docker qualification. Host-wide freeze,
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

The next offline join must also resolve **actual descriptor ingress** from the
qualified outer into the independently placed host process. Do not assume that
the development machine's transient-service options exist on the audited host:
upstream systemd v256's service setter supports standard-I/O descriptors and
path-based `OpenFile`, but has no `ExtraFileDescriptors` branch. The audited
installed v256.17's backports were not established by that source inspection.
Opening paths is not a substitute for transferring original pidfds. A bounded,
authenticated control-socket transfer is a candidate for offline design; it is
not implemented by this inherited-fd ABI and must not be silently selected as
an installed launch path.
[Upstream v256 service setter](https://raw.githubusercontent.com/systemd/systemd/v256/src/core/dbus-service.c)
