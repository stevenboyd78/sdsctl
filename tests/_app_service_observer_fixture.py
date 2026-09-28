"""Separate read-only observer for owned offline App service test processes.

This is not an installed launcher. Synthetic Engine/root/container facts arrive
over private test pipes. Original production Link/CliCustody consume the real
journal and actual native actor handles. No writer lock or actions are exposed.
"""

import os
import socket
import sys
import time
from contextlib import ExitStack
from dataclasses import asdict
from pathlib import Path


def main():
    repository = Path(__file__).resolve().parents[1]
    sys.path[:0] = [str(repository / name) for name in ("scripts", "src", "tests")]
    import supplemental_recording_service_cli_channel as channel
    from _native_observer_process_fixture import read, reply

    m = channel.custody_module
    apps, links, engine = m.apps, m.deadlines.links, m.engine
    config = read()
    plan = m.plans.decode(config["plan"])
    m.plans.Plan.root = property(lambda _: Path(config["root"]))
    engine.SOCKET = Path(config["socket"])
    engine.ROOT_UID, engine.ROOT_GID = os.geteuid(), os.getegid()
    channel.ROOT_UID = links.domains.ROOT_UID = os.geteuid()
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
    original_generation = apps.platform.generation

    def generation(value, *, name, image):
        if name == apps.platform.CLI:
            assert value["Id"] == config["cli"] and image == plan.cli_image
            return plan.cli_generation
        if name == "app_" + apps.NORMAL:
            assert value["Id"] == config["normal"] and image == plan.normal.image
            return plan.normal_generation
        return original_generation(value, name=name, image=image)

    apps.platform.generation = generation
    projection = m.plans.projection
    projection.NATIVE_MEDIA = Path(config["native_media"])
    projected = projection.Projection(
        next(item for item in plan.layouts if item.slug == apps.CANDIDATE),
        projection.recording._decode(bytes.fromhex(config["host_manifest"])),
        projection.recording._decode(bytes.fromhex(config["native_manifest"])),
    )
    plan.check_projection(projected)
    helper = apps.processes.ProcessIdentity(**config["helper"])

    def actor(pid, cid):
        assert cid == config["candidate_cid"]
        fields = Path(f"/proc/{pid}/stat").read_text().rpartition(") ")[2].split()
        assert fields[0] in ("R", "S", "I")
        return engine.namespace.Actor(
            pid,
            1 if pid == config["candidate_pid"] else pid,
            int(fields[1]),
            int(fields[19]),
            cid,
            tuple(tuple(pair) for pair in config["container_namespaces"])
            + engine.namespace.Witness._host_domains(),
        )

    engine.namespace.read = actor
    with ExitStack() as cleanup:
        witness = apps.processes.ProcessWitness(helper)
        cleanup.callback(witness.close)
        clock = m.plans.clock.ClockWitness(plan.original_clock)
        cleanup.callback(clock.close)
        domain = links.domains.ZeroDomain(clock.original, witness)
        cleanup.callback(domain.close)
        clock_link = links.ObserverClock(plan, clock, domain, helper)
        cleanup.callback(clock_link.close)
        watch = m.deadlines.DeadlineWatch(clock_link)
        cleanup.callback(watch.close)
        endpoint = engine.Endpoint()
        cleanup.callback(endpoint.close)
        endpoint.connect(deadline=time.monotonic() + 1).close()
        custody = apps.AppCustody(watch, endpoint)
        cleanup.callback(custody.close)
        observer = m.CliCustody(custody, projected)
        cleanup.callback(observer.close)
        channels = channel.Channels(
            socket.socket(fileno=int(sys.argv[1])), socket.socket(fileno=int(sys.argv[2]))
        )
        cleanup.callback(channels.close)
        for sock in (channels.incoming, channels.outgoing):
            sock.setblocking(False)
            sock.set_inheritable(False)
        link = channel.Link(channels, plan, clock, witness, role="observer")
        cleanup.callback(link.close)
        if config.get("fault") in ("lost_native_ack", "exit_native_ack"):
            send = link._send

            def lost_native_ack(value, end):
                if value["kind"] == channel.NATIVE_KIND:
                    # Drop only the outbound acknowledgement AFTER the real
                    # custody checks and independent native handles succeeded.
                    if config["fault"] == "exit_native_ack":
                        reply(dict(observer_exit_after_native_capture=True))
                        os._exit(73)  # Deliberate loss of this owned fixture process.
                    return channel.base.encode(value)
                return send(value, end)

            link._send = lost_native_ack
        reply(dict(ready=True, owner=os.getpid(), events=len(observer._snapshot)))
        while True:
            request = read()
            operation = request["operation"]
            if operation == "close":
                break
            if operation in ("dispatch", "candidate", "native"):
                status = observer.poll()
                assert not status.capture_failed and not status.inspection_failed
                fn = {
                    "dispatch": link.acknowledge,
                    "candidate": link.acknowledge_candidate,
                    "native": link.acknowledge_native,
                }[operation]
                receipt = fn(observer)
                reply(dict(receipt=receipt, sequence=link.sequence))
            elif operation == "verify":
                status, native = observer.poll(), observer.poll_native()
                assert native is not None
                reply(
                    dict(
                        owner=os.getpid(),
                        sequence=link.sequence,
                        candidate=link.candidate_attempted,
                        native=link.native_attempted,
                        executions=[asdict(item) for item in status.executions],
                        normal_exited=status.apps.normal_exited,
                        candidate_exited=status.apps.candidate_exited,
                        workers=sorted(native.exited),
                        helper_exited=status.apps.deadline.helper_exited,
                        failed=status.capture_failed or status.inspection_failed or link.failed,
                    )
                )
            else:
                raise AssertionError("Unknown local observer operation")
    reply(dict(closed=True))


if __name__ == "__main__":
    main()
