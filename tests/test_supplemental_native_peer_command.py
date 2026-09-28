"""Native exec watch joined to full original passive command; offline only.

This is the existing three-process fixture plus a separately exec'd native
watcher. Source/runtime/Engine/placement facts remain explicitly synthetic.
Its full startup still uses earlier fixture stdin sequencing; this is NOT a
qualified installed fixed observer/outer command or an active App launcher.
"""

import select
import signal
import time

import pytest

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
pytestmark = native_tests.pytestmark


@pytest.mark.parametrize(
    "joined",
    [dict(mode="release-command-pair", exit_after_result=True, staged_input=True)],
    indirect=True,
)
def test_native_exec_spans_original_fixed_command_handoff_release_and_actual_exit(
    joined, monkeypatch, binary
):
    s = joined

    def arm():
        command.arm_original_watch(s, lambda custody: native_tests.native(custody, binary))
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
