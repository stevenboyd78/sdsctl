"""Both actual protocol endpoints in separate owned processes; no host actions.

Each process captures and retains its own actual clock/domain/pidfd/socket.
Docker cgroups/root eligibility and the read-only qualification callback are
explicit fixture substitutions, not installed source/confinement attestation.
"""

import json
import select

import pytest

from . import test_supplemental_recording_permission_sender as sender_tests

m = sender_tests.m
peer = sender_tests.peer
pytestmark = pytest.mark.parametrize("peer", ["pair"], indirect=True)


def test_actual_sender_and_receiver_agree_without_new_clock_or_service(peer):
    reviewed = []

    def qualify(review):
        assert review.observer is peer.target
        assert review.target is peer.observer
        assert review.template is peer.template
        assert review.baseline_sha256 == peer.baseline_sha256
        review.read()
        reviewed.append(review.challenge_sha256)

    obj = m.Sender(
        peer.template,
        peer.template.sha256,
        peer.baseline_sha256,
        peer.target,
        peer.observer,
        peer.domain,
        peer.timer,
        peer.channel,
        qualify,
    )
    try:
        original = peer.timer.original
        value = dict(
            template=json.loads(peer.template.raw),
            template_sha256=peer.template.sha256,
            baseline_sha256=peer.baseline_sha256,
        )
        peer.child.stdin.write(json.dumps(value).encode() + b"\n")
        assert sender_tests.review_tests.peer_tests.line(peer.child) == b"receiver-ready\n"
        review = obj.receive()
        assert not reviewed and not obj.write_attempted
        waiting = select.poll()
        waiting.register(peer.child.stdout.fileno(), select.POLLIN | select.POLLHUP)
        assert not waiting.poll(0)  # Receiver has not reported acceptance/consumption.
        assert review.remote_origin.before_ns > original.after_ns
        assert review.observer.pid != review.target.identity.pid
        obj.send()
        # Only this separately read receiver result establishes the test's
        # receiver outcome; Sender.write_complete was not its acknowledgment.
        outcome = json.loads(sender_tests.review_tests.peer_tests.line(peer.child))
        assert outcome == dict(
            approved=True,
            consumed=True,
            failed=False,
            challenge_sha256=review.challenge_sha256,
            original_clock_unchanged=True,
        )
        assert reviewed == [review.challenge_sha256]
        assert obj.write_complete and not obj.failed
        assert peer.timer.original is original
        peer.borrowed()
    finally:
        obj.close()
    peer.borrowed()
