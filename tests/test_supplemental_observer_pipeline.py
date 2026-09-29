"""Original observer phases without fixture stdin steps, OFFLINE only.

Initial fixture setup/Engine/path/runtime facts are synthetic. This is not a
fixed installed observer command, an active grant, or platform qualification.
All observer phase listeners are provisioned before its input exchange; their
one shared original two-second cutoff is never renewed for a later phase.
"""

import json
import os
import select
import signal
import time
from dataclasses import replace

import pytest

from . import test_supplemental_native_peer_command as native

command = native.command
layout, image_umask, supervised = native.layout, native.image_umask, native.supervised
image, configured, helper, joined = native.image, native.configured, native.helper, native.joined
binary, direct_launcher = native.binary, native.direct_launcher
reviewed_binary, pytestmark = native.reviewed_binary, native.pytestmark
SELECTION = dict(
    mode="release-command-pair",
    exit_after_result=True,
    staged_input=True,
    observer_join=True,
    automatic_observer=True,
)


@pytest.mark.parametrize("joined", [SELECTION], indirect=True)
def test_original_observer_needs_no_plan_handoff_or_completion_stdin(
    binary, direct_launcher, reviewed_binary, joined, monkeypatch
):
    s = joined
    original_plan, original_handoff = s.staged_plan, s.staged_handoff
    end = s.pipeline_end
    remaining = dict(after_setup=end - time.monotonic())
    assert original_plan.deadline == original_handoff.deadline == end
    writes = []
    original_command = command.transport.command

    def no_observer_signal(peer, value):
        if peer is s.observer:
            writes.append(value)
            pytest.fail("Observer phase needed a fixture stdin signal")
        return original_command(peer, value)

    monkeypatch.setattr(command.transport, "command", no_observer_signal)

    def arm():
        remaining["before_arm"] = end - time.monotonic()
        command.arm_original_watch(
            s,
            native.selected_watch(
                "sealed-direct-owner-ingress", binary, None, direct_launcher, reviewed_binary
            ),
        )
        remaining["after_arm"] = end - time.monotonic()
        s.h.expected_returncode = 75

    assert s.sender.send() is None
    remaining["after_permission"] = end - time.monotonic()
    result = command.finish(s, monkeypatch, arm)
    remaining["after_finish"] = end - time.monotonic()
    assert result["result"] == 75 and result["error"] is None
    assert result["retired"] and result["clocks_closed"] and result["fd_delta"] == 0
    assert s.plan_listener is original_plan and s.observer_listener is original_handoff
    assert original_plan.deadline == original_handoff.deadline == end == s.pipeline_end
    assert s.writer_listener.deadline <= end
    assert s.custody.armed_watch is s.watch and s.plan_receipt and not writes
    # Actual capture/arm and both delivery brackets, never a cached snapshot.
    assert s.h.reads == 10 and s.comparison.counts["container"] == 8
    files = {path.name: path.read_bytes() for path in s.case_root.iterdir() if path.is_file()}
    for role, fd in (
        ("writer", s.h.witness.fd),
        ("observer", s.counterpart.fd),
        ("native", s.watch.fd),
    ):
        assert time.monotonic() < end, remaining
        observed = select.select([fd], [], [], end - time.monotonic())[0]
        # Retain bounded role/exit diagnostics only; no private peer values.
        if observed != [fd]:
            os.set_blocking(s.h.child.stdout.fileno(), False)
            try:
                raw = os.read(s.h.child.stdout.fileno(), 1024)
            except BlockingIOError:
                raw = b""
            cleanup_returned = b'{"fixture_exit_ready":true}\n' in raw.splitlines(keepends=True)
            pytest.fail(
                f"{role} missed original cutoff; {remaining!r}; "
                f"fixture_cleanup_returned={cleanup_returned}; "
                f"writer_exit={s.h.child.poll()}; observer_exit={s.observer.poll()}"
            )
    assert s.h.child.wait(timeout=0) == 75
    # Either the original native watcher stops the observer first, or the
    # observer observes peer loss and refuses on its own. Neither is success.
    assert s.observer.wait(timeout=0) in (-signal.SIGKILL, 75)
    outcome = s.watch.finish()
    assert outcome.returncode in (0, 11)  # Both exits or peer loss; not recording.
    assert outcome.deadline_ns == s.custody.deadline_ns
    assert files == {
        path.name: path.read_bytes() for path in s.case_root.iterdir() if path.is_file()
    }
    assert not writes


@pytest.mark.parametrize("joined", [SELECTION | dict(exit_after_result=False)], indirect=True)
@pytest.mark.parametrize("fault", ["plan_channel_lost", "original_cutoff"])
def test_fixed_observer_refuses_without_plan_and_closes_its_owned_descriptors(joined, fault):
    s = joined
    end = s.pipeline_end
    # A predeclared test-only reap bound, not a renewed protocol/work deadline.
    reap_by = end + 1
    files = {path.name: path.read_bytes() for path in s.case_root.iterdir() if path.is_file()}
    reads = s.h.reads
    if fault == "plan_channel_lost":
        s.staged_plan.close()
    result = json.loads(command.transport.line(s.observer))
    assert result == dict(observer_command_refused=True, fd_delta=0)
    assert time.monotonic() < reap_by
    assert select.select([s.counterpart.fd], [], [], reap_by - time.monotonic())[0] == [
        s.counterpart.fd
    ]
    assert s.observer.wait(timeout=0) == 75
    if fault == "original_cutoff":
        assert time.monotonic() >= end
    assert s.staged_plan.deadline == s.staged_handoff.deadline == end
    assert s.pair is None and s.h.reads == reads
    assert not s.h.witness.exited()
    assert files == {
        path.name: path.read_bytes() for path in s.case_root.iterdir() if path.is_file()
    }


@pytest.mark.parametrize("joined", [SELECTION | dict(exit_after_result=False)], indirect=True)
def test_matching_observer_argv_pin_does_not_implicitly_select_the_new_command(joined):
    s = joined
    assert s.sender.send() is None
    announced = json.loads(command.transport.line(s.h.child))
    plan = command.p.startups.plans.load_bytes(announced["plan"].encode(), announced["sha256"])
    s.automatic_observer = False  # Decline selection, but keep its original expected argv pin.
    with pytest.raises(command.qualification.runtime_tests.m.launch.UnconfirmedHostLaunch):
        command.qualify_final(s, plan)
    assert s.comparison.counts["container"] == 0 and s.pair is None
    assert not hasattr(s, "watch")


@pytest.mark.parametrize("joined", [SELECTION], indirect=True)
@pytest.mark.parametrize("fault", ["selection", "baseline", "source"])
def test_observer_command_drift_stops_originals_without_release_or_deadline_renewal(
    binary, direct_launcher, reviewed_binary, joined, monkeypatch, fault
):
    s = joined
    end = s.pipeline_end
    sent = []
    release = command.p.bootstrap.Endpoint.send_retirement

    def release_seen(endpoint, receipt):
        sent.append(True)
        return release(endpoint, receipt)

    monkeypatch.setattr(command.p.bootstrap.Endpoint, "send_retirement", release_seen)

    def arm_and_change():
        command.arm_original_watch(
            s,
            native.selected_watch(
                "sealed-direct-owner-ingress", binary, None, direct_launcher, reviewed_binary
            ),
        )
        if fault == "selection":
            s.pair.observer.passive_observer = False
        elif fault == "baseline":
            s.pair.observer.observer_baseline_sha256 = "0" * 64
        else:
            path = s.h.root / s.pair.observer.HELPER / "supplemental_recording_peer_preparation.py"
            path.write_bytes(b"PRIVATE changed disposable source; must not run\n")

    assert s.sender.send() is None
    with pytest.raises(command.delivery_tests.m.UnconfirmedDelivery):
        command.finish(s, monkeypatch, arm_and_change)
    assert not sent and s.watch.closed and s.watch.finished
    assert s.pipeline_end == s.staged_plan.deadline == s.staged_handoff.deadline == end
    # Watch.close reaps only the watcher; it is not evidence of peer exit.
    # Observe both original handles with the unchanged phase cutoff first.
    for fd in (s.h.witness.fd, s.counterpart.fd):
        assert time.monotonic() < end
        assert select.select([fd], [], [], end - time.monotonic())[0] == [fd]
    assert s.h.child.wait(timeout=0) == -signal.SIGKILL
    assert s.observer.wait(timeout=0) in (-signal.SIGKILL, 75)
    assert s.custody.armed_watch is s.watch and s.pair.channel_delivery_attempted
    files = {path.name: path.read_bytes() for path in s.case_root.iterdir() if path.is_file()}
    assert {"startup-claim.json", "plan.json"} <= set(files)
    with pytest.raises(command.delivery_tests.m.UnconfirmedDelivery):
        command.delivery_tests.m.deliver_and_release_passive_writer(
            s.custody,
            s.watch,
            s.h.observer_identity,
            s.inputs,
            s.writer_listener,
            s.observer_listener,
        )
    assert not sent
    assert files == {
        path.name: path.read_bytes() for path in s.case_root.iterdir() if path.is_file()
    }


@pytest.mark.parametrize("joined", [SELECTION | dict(exit_after_result=False)], indirect=True)
def test_each_runtime_guard_freshly_observes_original_outer_identity(joined, monkeypatch):
    s = joined
    assert s.sender.send() is None
    announced = json.loads(command.transport.line(s.h.child))
    plan = command.p.startups.plans.load_bytes(announced["plan"].encode(), announced["sha256"])
    command.qualify_final(s, plan, collect=False)
    identities = command.p.domains.process
    original_read = identities.read_identity
    reads = []
    drift = False

    def observed(pid, cid):
        original = original_read(pid, cid)
        if pid == os.getpid():
            reads.append(True)
            if drift:
                return replace(original, start_ticks=original.start_ticks + 1)
        return original

    monkeypatch.setattr(identities, "read_identity", observed)
    for count in (1, 2):
        s.pair.observer._guard(s.pipeline_end)
        assert len(reads) == count  # Once PER guard, not cached between guards.
    drift = True
    with pytest.raises(command.qualification.runtime_tests.m.launch.UnconfirmedHostLaunch):
        s.pair.observer._guard(s.pipeline_end)
    assert len(reads) == 3 and s.comparison.counts["container"] == 0
    assert not hasattr(s, "watch")
