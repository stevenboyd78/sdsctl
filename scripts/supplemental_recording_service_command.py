#!/usr/bin/env python3
"""Distinct finite preparation command; no App, scanner or recording action.

Wait for original-peer permission before opening the independently pinned
baseline or reading the host. Capture a NEW continuing service clock only after
that read, then require separate final-plan acceptance before passive assembly.
This command never consumes operator notices or runs the assembled service.
Independent source/input qualification and outer termination remain mandatory.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ENTRYPOINT = "/opt/sdsctl-recording-host/supplemental_recording_service_command.py"
MODE = "--prepare-idle-service"
MESSAGE = "Finite service preparation ended; preserve this case and do not retry."
MILESTONE = (
    "Finite idle service prepared and retired; no App, scanner or recording action selected."
)

if __name__ == "__main__":
    try:
        allowed = (
            len(sys.argv) == 6
            and sys.argv[5] == MODE
            and sys.flags.isolated == sys.flags.dont_write_bytecode == 1
            and os.geteuid() == os.getegid() == 0
            and os.getcwd() == "/"
            and Path(__file__) == Path(ENTRYPOINT)
        )
    except Exception:
        allowed = False
    if not allowed:
        print(MESSAGE, file=sys.stderr)
        raise SystemExit(64)
    sys.path.insert(0, "/opt/sdsctl-recording-host")

try:
    import supplemental_recording_permission_probe as peer
    import supplemental_recording_service_startup as startup
except Exception:
    if __name__ == "__main__":
        print(MESSAGE, file=sys.stderr)
        raise SystemExit(75) from None
    raise

require, permission = peer.require, peer.permission


def baseline_root(case):
    """Fixed read-only input path, distinct from declaration and writable case."""
    startup.plans.base.identifier(case, case=True)
    return Path("/mnt/data/sdsctl-recording-baseline-" + case)


def _accepted_assembly(owner, docker):
    # Preserve the original offer, reserving one complete poll AND assembly
    # budget. Late/missing acceptance is refusal, not a successful idle service.
    # The fixed poll ceiling also bounds a stopped userspace clock. Kernel I/O
    # still requires the independently provisioned outer supervisor.
    deadline = owner.offer.deadline
    poll_by = deadline - 2 * startup.acceptance.MAX_SECONDS
    for _ in range(151):
        require(time.monotonic() < poll_by)
        original = owner.poll()
        if original is not None:
            require(original is owner.original)
            require(time.monotonic() < deadline - startup.acceptance.MAX_SECONDS)
            with owner.idle_service(docker) as service:
                require(service.original is original and service.clock_witness is owner.clock)
                require(not service.used and not service.failed and not service.closed)
                # Deliberately no run(), inbox consumption, dispatch, native
                # startup, launch preparation or recording phase selection.
            return
        remaining = poll_by - time.monotonic()
        require(remaining > 0)
        time.sleep(min(0.1, remaining))
    require(False)


def prepare_idle_service(root, template_sha256, baseline_sha256, observer_identity):
    """One permission -> baseline -> service origin -> acceptance -> idle join.

    All preflight peer handles remain retained passively after consumption,
    without refreshing or reusing their expired authority. The new service
    clock and its separately accepted original plan govern assembly. Cleanup
    retires the service/journal before startup's clock, then the preflight
    borrowers before their clock, and finally the declaration. Partial files
    survive every error. Exit75 and stdout are NOT recording/restore receipts.
    """
    cleanup, problem = [], None
    try:
        require(type(observer_identity) is peer.process.ProcessIdentity)
        startup.plans.base.digest(baseline_sha256)
        original = peer.declaration.Declaration(root, template_sha256)
        cleanup.append(original.close)
        template = original.recheck()
        target = peer.current_identity()
        require(target.pid != observer_identity.pid)
        timer = peer.clock.ClockWitness(peer.clock.read())
        cleanup.append(timer.close)
        witness = peer.process.ProcessWitness(observer_identity)
        cleanup.append(witness.close)
        domain = permission.domains.ZeroDomain(timer.original, witness)
        cleanup.append(domain.close)
        end = (
            timer.original.after_ns / peer.clock.NS
            + permission.WAIT_SECONDS
            - permission.IO_SECONDS
        )
        case = peer.declaration.codec._read(template.raw)["plan"]["case"]
        connection = peer.PeerConnection(peer.peer_root(case), end)
        cleanup.append(connection.close)
        receiver = permission.Permission(
            template,
            template_sha256,
            baseline_sha256,
            target,
            witness,
            domain,
            timer,
            connection.channel,
        )
        cleanup.append(receiver.close)
        original.recheck()
        connection.recheck()
        receiver.wait()
        original.recheck()
        connection.recheck()
        owner = startup.Startup(original)
        cleanup.append(owner.close)
        # Docker construction performs no I/O; all host/cache reads remain
        # inside Permission.prepare_service's single original guarded scope.
        docker = startup.plans.ordinary.Docker()
        receiver.prepare_service(owner, baseline_root(case), docker)
        require(owner.clock is not timer and owner.clock.original is not timer.original)
        require(owner.clock.original.before_ns >= timer.original.after_ns)
        _accepted_assembly(owner, docker)
    except BaseException as error:
        problem = error
    finally:
        peer._cleanup(cleanup, problem)
    # AFTER successful borrower and owner cleanup, not merely publication or
    # sender write. A lost stdout acknowledgment never permits a retry.
    try:
        print(MILESTONE, flush=True)
    except Exception:
        raise permission.UnconfirmedPermission(permission.MESSAGE) from None
    return 75


if __name__ == "__main__":
    try:
        root = Path(sys.argv[1])
        require(str(root) == sys.argv[1] and root.is_absolute() and ".." not in root.parts)
        code = prepare_idle_service(
            root, sys.argv[2], sys.argv[3], peer.parse_identity(sys.argv[4])
        )
    except Exception:
        print(MESSAGE, file=sys.stderr)
        raise SystemExit(75) from None
    raise SystemExit(code)
