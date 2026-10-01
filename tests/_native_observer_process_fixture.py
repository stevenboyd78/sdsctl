"""Disposable test observer; never an installed command or recovery procedure.

The parent supplies synthetic Engine/container/path facts for local owned test
children. This private stdin/stdout test control is NOT the authenticated service
Link. Actual process/clock/pidfd ownership and NativeCustody run in this process.
No App dispatch, scanner command, journal write, signal or recovery is exposed.
"""

import json
import os
import sys
import time
from contextlib import ExitStack
from dataclasses import asdict
from pathlib import Path


def read():
    raw = sys.stdin.buffer.readline(262145)
    assert raw.endswith(b"\n") and len(raw) <= 262144
    return json.loads(raw)


def reply(value):
    print(json.dumps(value, separators=(",", ":")), flush=True)


def main():
    # Only the reviewed local checkout is imported by the test harness.
    repository = Path(__file__).resolve().parents[1]
    sys.path[:0] = [str(repository / "scripts"), str(repository / "src")]
    import supplemental_recording_service_native_custody as m

    apps, links = m.apps, m.deadlines.links
    config = read()
    plan = links.plans.decode(config["plan"])
    links.plans.Plan.root = property(lambda self: Path(config["root"]))
    m.engine.SOCKET = Path(config["socket"])
    m.engine.ROOT_UID, m.engine.ROOT_GID = os.geteuid(), os.getegid()
    links.domains.ROOT_UID = os.geteuid()
    aliases = {int(pid): cid for pid, cid in config["identities"].items()}

    def identity(pid, cid):
        assert aliases[pid] == cid
        return apps.processes.process_identity(
            pid,
            cid,
            Path(f"/proc/{pid}/stat").read_text(),
            f"0::/system.slice/docker-{cid}.scope\n",
        )

    apps.processes.read_identity = identity
    projection = m.dispatch.binding.projection
    projection.NATIVE_MEDIA = Path(config["native_media"])
    projected = projection.Projection(
        next(item for item in plan.layouts if item.slug == apps.CANDIDATE),
        projection.recording._decode(bytes.fromhex(config["host_manifest"])),
        projection.recording._decode(bytes.fromhex(config["native_manifest"])),
    )
    plan.check_projection(projected)
    helper = apps.processes.ProcessIdentity(**config["helper"])
    with ExitStack() as cleanup:
        witness = apps.processes.ProcessWitness(helper)
        cleanup.callback(witness.close)
        clock = links.plans.clock.ClockWitness(plan.original_clock)
        cleanup.callback(clock.close)
        domain = links.domains.ZeroDomain(clock.original, witness)
        cleanup.callback(domain.close)
        link = links.ObserverClock(plan, clock, domain, helper)
        cleanup.callback(link.close)
        watch = m.deadlines.DeadlineWatch(link)
        cleanup.callback(watch.close)
        endpoint = m.engine.Endpoint()
        cleanup.callback(endpoint.close)
        endpoint.connect(deadline=time.monotonic() + 1).close()
        custody = apps.AppCustody(watch, endpoint)
        cleanup.callback(custody.close)
        observer = None
        reply(dict(owner=os.getpid(), status=asdict(custody.poll())))
        while True:
            request = read()
            operation = request["operation"]
            if operation == "candidate":
                custody.capture_candidate(request["generation"])
                reply(asdict(custody.poll()))
            elif operation == "native":
                candidate = custody.poll().candidate
                cid, pid = candidate.process.container_id, candidate.process.pid
                synthetic = tuple(tuple(item) for item in config["container_namespaces"])
                real_domains = m.engine.namespace.Witness._host_domains()

                def actor(
                    pid_to_read,
                    container,
                    *,
                    cid=cid,
                    pid=pid,
                    synthetic=synthetic,
                    real_domains=real_domains,
                ):
                    assert container == cid
                    fields = (
                        Path(f"/proc/{pid_to_read}/stat").read_text().rpartition(") ")[2].split()
                    )
                    assert fields[0] in ("R", "S", "I")
                    return m.engine.namespace.Actor(
                        pid_to_read,
                        1 if pid_to_read == pid else pid_to_read,
                        int(fields[1]),
                        int(fields[19]),
                        cid,
                        synthetic + real_domains,
                    )

                m.engine.namespace.read = actor
                host = m.dispatch.binding.Binding(
                    projected, plan.candidate_runtime.source, plan.sha256, plan.boot
                )
                command = m.dispatch.execution.Command(
                    str(plan.native_root / "launch/launch.json"),
                    request["launch_sha256"],
                    plan.candidate_runtime.source,
                    plan.lease["ready_by"],
                )
                pins = m.dispatch.Pins(host, command, candidate.generation, candidate.process)
                observer = m.NativeCustody(custody, pins, request["reports"])
                cleanup.callback(observer.close)
                assert not {fd for _, fd, _ in observer._retained}.intersection(
                    fd for _, fd, _ in custody._retained
                )
                reply(dict(binding=asdict(observer.binding), retained=observer._retained))
            elif operation == "poll":
                status = observer.poll() if observer is not None else custody.poll()
                value = asdict(status)
                if observer is not None:
                    value["exited"] = sorted(status.exited)
                reply(value)
            elif operation == "close_endpoint":
                endpoint.close()
                reply(dict(closed=True))
            elif operation == "close":
                break
            else:
                raise AssertionError("Unknown test operation")
    reply(dict(closed=True))


if __name__ == "__main__":
    main()
