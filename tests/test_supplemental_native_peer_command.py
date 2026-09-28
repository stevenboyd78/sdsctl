"""Native exec watch joined to full original passive command; offline only.

This is the existing three-process fixture plus a separately exec'd native
watcher. Source/runtime/Engine/placement facts remain explicitly synthetic.
Its full startup still uses earlier fixture stdin sequencing; this is NOT a
qualified installed fixed observer/outer command or an active App launcher.
"""

import select
import signal
import time
from pathlib import Path

import pytest

from . import test_supplemental_native_peer_ingress as ingress_tests
from . import test_supplemental_native_peer_parent as parent_tests
from . import test_supplemental_native_peer_watch as native_tests
from . import test_supplemental_recording_qualified_peer_command as command

layout, image_umask, supervised = command.layout, command.image_umask, command.supervised
image, configured, helper, joined = (
    command.image,
    command.configured,
    command.helper,
    command.joined,
)
binary = native_tests.binary
parent_launcher = parent_tests.parent_launcher
pytestmark = native_tests.pytestmark


def selected_watch(transport, binary, parent_launcher):
    if transport == "clone-parent-ingress":
        return lambda custody: parent_tests.parent_ingress(custody, binary, parent_launcher)
    launch = native_tests.native if transport == "inherited" else ingress_tests.ingress
    return lambda custody: launch(custody, binary)


@pytest.mark.parametrize(
    "joined",
    [dict(mode="release-command-pair", exit_after_result=True, staged_input=True)],
    indirect=True,
)
@pytest.mark.parametrize("transport", ["inherited", "socket-ingress", "clone-parent-ingress"])
def test_native_exec_spans_original_fixed_command_handoff_release_and_actual_exit(
    joined, monkeypatch, binary, transport, parent_launcher
):
    s = joined

    def arm():
        command.arm_original_watch(s, selected_watch(transport, binary, parent_launcher))
        s.h.expected_returncode = 75

    assert s.sender.send() is None
    result = command.finish(s, monkeypatch, arm)
    assert result["result"] == 75 and result["error"] is None
    assert result["retired"] and result["clocks_closed"] and result["fd_delta"] == 0
    assert result["original_dispatch"] and result["input_baseline_preserved"]
    assert s.custody.armed_watch is s.watch and s.plan_receipt
    assert s.h.reads == 12 and s.comparison.counts["container"] == 10
    assert s.pair.channel_delivery_attempted and not s.watch.closed
    files = {
        path.relative_to(s.case_root): path.read_bytes()
        for path in s.case_root.rglob("*")
        if path.is_file()
    }
    end = s.writer_listener.deadline
    for fd in (s.h.witness.fd, s.counterpart.fd, s.watch.fd):
        assert time.monotonic() < end
        assert select.select([fd], [], [], end - time.monotonic())[0] == [fd]
    assert s.h.child.wait(timeout=0) == 75
    assert s.observer.wait(timeout=0) == -signal.SIGKILL
    outcome = s.watch.finish()
    assert outcome.returncode == 11  # Peer loss, NOT recording/recovery success.
    assert outcome.writer is s.custody.identities[0]
    assert outcome.observer is s.custody.identities[1]
    assert outcome.deadline_ns == s.custody.deadline_ns
    assert files == {
        path.relative_to(s.case_root): path.read_bytes()
        for path in s.case_root.rglob("*")
        if path.is_file()
    }


@pytest.mark.parametrize(
    "joined", [dict(mode="release-command-pair", staged_input=True)], indirect=True
)
@pytest.mark.parametrize("transport", ["inherited", "socket-ingress", "clone-parent-ingress"])
@pytest.mark.parametrize("after", ["writer", "observer"])
def test_native_death_during_real_handoff_stops_originals_without_releasing_or_retrying(
    joined, monkeypatch, binary, transport, after, parent_launcher
):
    s = joined
    sent, releases = [], []
    endpoint_send = command.p.bootstrap.Endpoint.deliver

    def arm():
        command.arm_original_watch(s, selected_watch(transport, binary, parent_launcher))

    def endpoint(endpoint, channels):
        result = endpoint_send(endpoint, channels)
        sent.append(endpoint.role)
        if endpoint.role == after:
            signal.pidfd_send_signal(s.watch.fd, signal.SIGKILL)
            # Original handoff cutoff, not a new action/readiness allowance.
            end = min(s.writer_listener.deadline, s.observer_listener.deadline)
            assert time.monotonic() < end
            assert select.select([s.watch.fd], [], [], end - time.monotonic())[0]
        return result

    def release(*args):
        releases.append(True)
        raise AssertionError("Dead native watcher admitted passive release")

    monkeypatch.setattr(command.p.bootstrap.Endpoint, "deliver", endpoint)
    monkeypatch.setattr(command.p.bootstrap.Endpoint, "send_retirement", release)
    assert s.sender.send() is None
    with pytest.raises(command.delivery_tests.m.UnconfirmedDelivery) as failure:
        command.finish(s, monkeypatch, arm)
    assert str(failure.value) == command.delivery_tests.m.MESSAGE
    assert sent == (["writer"] if after == "writer" else ["writer", "observer"])
    assert not releases and s.plan_receipt and s.pair.channel_delivery_attempted
    assert s.custody.armed_watch is s.watch and s.watch.closed and s.watch.finished
    assert s.h.child.wait(timeout=3) == s.observer.wait(timeout=3) == -signal.SIGKILL
    files = {
        path.relative_to(s.case_root): path.read_bytes()
        for path in s.case_root.rglob("*")
        if path.is_file()
    }
    assert Path("startup-claim.json") in files and Path("plan.json") in files
    with pytest.raises(command.delivery_tests.m.UnconfirmedDelivery):
        command.delivery_tests.m.deliver_and_release_passive_writer(
            s.custody,
            s.watch,
            s.h.observer_identity,
            s.inputs,
            s.writer_listener,
            s.observer_listener,
        )
    assert not releases
    assert files == {
        path.relative_to(s.case_root): path.read_bytes()
        for path in s.case_root.rglob("*")
        if path.is_file()
    }
