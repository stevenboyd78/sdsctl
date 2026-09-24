"""One-attempt independent-digest transport using temporary private files."""

import fcntl
import importlib.util
import os
import stat
import subprocess
import sys
from pathlib import Path
from threading import Thread

import pytest

from . import test_supplemental_recording_service_acceptance as readers

NAME = "supplemental_recording_service_submit"
SPEC = importlib.util.spec_from_file_location(
    NAME, Path(readers.m.__file__).with_name(NAME + ".py")
)
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)
setup, case, ready = readers.setup, readers.case, readers.ready
snapshot, fds = readers.publishers.snapshot, readers.publishers.fds


def sender(ready):
    _, offer, _, _, _, original, _ = ready
    return m.Submission(original, offer.template_sha256, offer.plan.sha256)


def denied(action):
    with pytest.raises(m.UnconfirmedSubmission) as error:
        action()
    assert str(error.value) == m.MESSAGE and error.value.__suppress_context__


def test_complete_publication_is_separate_from_original_owner_acceptance(ready, monkeypatch):
    root, offer, witness, _, _, original, reader = ready
    submission = sender(ready)
    calls, fsync = [], os.fsync

    def synced(fd):
        calls.append("directory" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file")
        fsync(fd)

    monkeypatch.setattr(m.os, "fsync", synced)
    before = fds()
    digest = submission.submit()
    path = root / m.acceptance.NAME
    assert digest == m.intake.plans.base.checksum(readers.m.object_json(path.read_bytes()))
    assert calls == ["file", "directory"] and submission.used and not submission.failed
    assert path.stat().st_nlink == 1 and stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not (root / m.PENDING).exists() and not offer.used
    assert reader.poll() is offer.plan
    assert not original._closed and witness.closes == 0 and fds() == before
    files = snapshot(root)
    denied(submission.submit)
    assert snapshot(root) == files


def test_genuine_reader_lock_waits_before_single_publication_within_same_attempt(
    ready, monkeypatch
):
    root, offer, _, _, _, _, reader = ready
    submission = sender(ready)
    files = snapshot(root)
    waits, writes = [], []
    actual_write = os.write
    with m.publication.protected._private_directory(root, exclusive=False) as held:

        def release(seconds):
            assert submission.used and not submission.failed and snapshot(root) == files
            waits.append(seconds)
            fcntl.flock(held, fcntl.LOCK_UN)

        def once(fd, raw):
            writes.append(raw)
            return actual_write(fd, raw)

        monkeypatch.setattr(m.time, "sleep", release)
        monkeypatch.setattr(m.os, "write", once)
        submission.submit()
    assert waits == [m.LOCK_POLL_SECONDS] and writes == [submission.raw]
    assert submission.used and not submission.failed and reader.poll() is offer.plan


def test_persistent_reader_lock_expires_original_sender_attempt_without_writing(ready, monkeypatch):
    root, _, _, now, _, _, _ = ready
    submission = sender(ready)
    files = snapshot(root)
    waits = []

    def wait(seconds):
        waits.append(seconds)
        now[0] += 0.5

    monkeypatch.setattr(m.time, "sleep", wait)
    with m.publication.protected._private_directory(root, exclusive=False):
        denied(submission.submit)
    assert len(waits) == 4
    assert submission.used and submission.failed and snapshot(root) == files
    denied(submission.submit)


def test_lock_poll_ceiling_does_not_need_a_moving_test_clock(ready, monkeypatch):
    root, _, _, _, _, _, _ = ready
    submission = sender(ready)
    waits = []
    monkeypatch.setattr(m.time, "sleep", waits.append)
    with m.publication.protected._private_directory(root, exclusive=False):
        denied(submission.submit)
    assert waits == [m.LOCK_POLL_SECONDS] * m.MAX_LOCK_POLLS
    assert not (root / m.PENDING).exists() and not (root / m.acceptance.NAME).exists()


@pytest.mark.parametrize("error", [OSError, KeyboardInterrupt, SystemExit])
def test_interrupted_lock_wait_is_consumed_without_reopening_or_writing(ready, monkeypatch, error):
    root, _, witness, _, _, original, _ = ready
    submission = sender(ready)
    calls = []

    def interrupted(seconds):
        calls.append(seconds)
        raise error("private-secret")

    monkeypatch.setattr(m.time, "sleep", interrupted)
    with m.publication.protected._private_directory(root, exclusive=False):
        if error is OSError:
            denied(submission.submit)
        else:
            with pytest.raises(error):
                submission.submit()
    assert calls == [m.LOCK_POLL_SECONDS] and submission.failed and submission.used
    assert not (root / m.PENDING).exists() and not original._closed and witness.closes == 0


def test_directory_busy_after_file_creation_cannot_be_caught_as_initial_contention(
    ready, monkeypatch
):
    root, _, _, _, _, _, _ = ready
    submission = sender(ready)
    calls = []

    def failed(fd, raw):
        calls.append(raw)
        raise m.publication.protected.DirectoryBusy("private-secret AFTER creation")

    monkeypatch.setattr(m.os, "write", failed)
    monkeypatch.setattr(m.time, "sleep", lambda *_: pytest.fail("Post-write error retried"))
    denied(submission.submit)
    assert calls == [submission.raw] and (root / m.PENDING).exists()
    assert submission.failed and submission.used


def test_wrong_independent_final_plan_pin_refuses_before_any_write(ready):
    root, offer, witness, _, _, original, _ = ready
    files = snapshot(root)
    denied(lambda: m.Submission(original, offer.template_sha256, "a" * 64))
    assert snapshot(root) == files and not original._closed and witness.closes == 0


def test_wrong_template_pin_is_not_inferred_or_repaired_from_claim(ready):
    root, offer, witness, _, _, original, reader = ready
    submission = m.Submission(original, "a" * 64, offer.plan.sha256)
    submission.submit()
    readers.denied(reader.poll)
    assert not reader.accepted and not offer.accepted and witness.closes == 0
    assert (root / m.acceptance.NAME).is_file()


@pytest.mark.parametrize("existing", ["message", "pending", "other"])
def test_existing_files_prevent_overwrite_and_reconstructed_submission(ready, existing):
    root, _, witness, _, _, original, _ = ready
    name = {"message": m.acceptance.NAME, "pending": m.PENDING, "other": "preserve"}[existing]
    (root / name).write_bytes(b"Preserve original evidence")
    files = snapshot(root)
    denied(sender(ready).submit)
    denied(sender(ready).submit)  # Test forbidden reconstruction, never a real retry.
    assert snapshot(root) == files and not original._closed and witness.closes == 0


def test_partial_write_preserves_pending_and_receiver_refuses(ready, monkeypatch):
    root, offer, witness, _, _, original, reader = ready
    write = os.write
    monkeypatch.setattr(m.os, "write", lambda fd, raw: write(fd, raw[:10]))
    submission = sender(ready)
    before = fds()
    denied(submission.submit)
    assert (root / m.PENDING).read_bytes() == submission.raw[:10]
    assert not (root / m.acceptance.NAME).exists()
    readers.denied(reader.poll)
    assert not offer.accepted and not original._closed and witness.closes == 0
    assert fds() == before


@pytest.mark.parametrize("point", ["file_sync", "link", "unlink", "directory_sync"])
@pytest.mark.parametrize("after", [False, True])
def test_uncertain_publication_preserves_files_and_never_retries(ready, monkeypatch, point, after):
    root, offer, witness, _, _, original, reader = ready
    fsync, link, unlink = os.fsync, os.link, os.unlink
    seen = []

    def perform(kind, action, *args, **kwargs):
        if kind == point:
            seen.append(kind)
            if after:
                action(*args, **kwargs)
            raise OSError("private-secret")
        return action(*args, **kwargs)

    monkeypatch.setattr(
        m.os,
        "fsync",
        lambda fd: perform(
            "directory_sync" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file_sync", fsync, fd
        ),
    )
    monkeypatch.setattr(m.os, "link", lambda *a, **kw: perform("link", link, *a, **kw))
    monkeypatch.setattr(m.os, "unlink", lambda *a, **kw: perform("unlink", unlink, *a, **kw))
    submission = sender(ready)
    before = fds()
    denied(submission.submit)
    files = snapshot(root)
    assert len(seen) == 1 and submission.failed
    denied(submission.submit)
    assert files == snapshot(root) and len(seen) == 1 and fds() == before
    if m.PENDING in files:
        readers.denied(reader.poll)
        assert not offer.accepted
    else:
        # Delivery acknowledgment is ambiguous, not permission to resubmit:
        # the original reader may consume an already fully published message.
        assert reader.poll() is offer.plan
    assert witness.closes == 0 and not original._closed


def test_reader_cooperates_with_writer_lock_and_cannot_see_partial_message(ready, monkeypatch):
    _, offer, witness, *_, reader = ready
    fsync, observations = os.fsync, []

    def check(fd):
        fsync(fd)
        observations.append(reader.poll())
        assert not reader.used and not offer.used

    monkeypatch.setattr(m.os, "fsync", check)
    sender(ready).submit()
    assert observations == [None, None]
    assert reader.poll() is offer.plan and witness.closes == 0


def test_two_second_sender_bound_does_not_renew_original_owner_deadline(ready, monkeypatch):
    root, offer, witness, now, _, original, reader = ready
    fsync = os.fsync

    def delayed(fd):
        fsync(fd)
        now[0] += m.acceptance.MAX_SECONDS

    monkeypatch.setattr(m.os, "fsync", delayed)
    denied(sender(ready).submit)
    assert now[0] < offer.deadline and (root / m.PENDING).exists()
    readers.denied(reader.poll)
    assert not reader.accepted and not original._closed and witness.closes == 0


def test_lost_final_close_ack_is_not_retried_and_does_not_close_plan(ready, monkeypatch):
    root, offer, witness, _, _, original, reader = ready
    close, recorded = os.close, []

    def uncertain(fd):
        info = os.fstat(fd)
        destination = root / m.acceptance.NAME
        matches = destination.exists() and destination.stat().st_ino == info.st_ino
        close(fd)
        if matches:
            recorded.append(fd)
            raise OSError("private-secret close acknowledgement")

    before = fds()
    submission = sender(ready)
    with monkeypatch.context() as fault:
        fault.setattr(m.os, "close", uncertain)
        denied(submission.submit)
    assert len(recorded) == 1 and fds() == before and submission.failed
    assert reader.poll() is offer.plan
    assert not original._closed and witness.closes == 0


@pytest.mark.parametrize("error_type", [KeyboardInterrupt, SystemExit])
def test_interruption_preserves_own_pending_file_and_does_not_retry(ready, monkeypatch, error_type):
    root, offer, witness, _, _, original, reader = ready
    write = os.write

    def interrupted(fd, raw):
        write(fd, raw[:10])
        raise error_type()

    monkeypatch.setattr(m.os, "write", interrupted)
    submission = sender(ready)
    before = fds()
    with pytest.raises(error_type):
        submission.submit()
    assert submission.failed and submission.used and fds() == before
    assert (root / m.PENDING).read_bytes() == submission.raw[:10]
    readers.denied(reader.poll)
    assert not offer.accepted and not original._closed and witness.closes == 0


def test_foreign_thread_cannot_submit_or_close_original_custody(ready):
    root, _, witness, _, _, original, _ = ready
    submission, errors = sender(ready), []
    files = snapshot(root)

    def foreign():
        try:
            submission.submit()
        except BaseException as error:
            errors.append(error)

    thread = Thread(target=foreign)
    thread.start()
    thread.join(timeout=1)
    assert not thread.is_alive() and len(errors) == 1
    assert type(errors[0]) is m.UnconfirmedSubmission
    assert submission.failed and files == snapshot(root)
    assert not original._closed and witness.closes == 0


def test_submission_never_captures_or_relabels_clock_or_uses_engine(ready, monkeypatch):
    _, offer, witness, *_, reader = ready

    def forbidden(*args, **kwargs):
        raise AssertionError("Submission attempted clock capture or Engine access")

    monkeypatch.setattr(m.intake.plans.clock, "read", forbidden)
    monkeypatch.setattr(m.intake.plans.ordinary, "Docker", forbidden)
    sender(ready).submit()
    assert not offer.used and witness.closes == 0
    assert reader.poll() is offer.plan  # Explicit fake retained clock in this test.


def test_submitter_is_outside_qualified_helper_allowlist():
    source = Path(m.__file__).with_name("supplemental_recording_host_source.py").read_text()
    assert '"service_submit"' not in source


def test_separate_process_submission_rejoins_original_real_clock_owner(tmp_path, monkeypatch):
    root = tmp_path / "separate-submitter-case"
    root.mkdir(mode=0o700)
    monkeypatch.setattr(m.intake.plans.Plan, "root", property(lambda _: root))
    clock = m.intake.plans.clock.read()
    value = readers.publishers.offer_tests.template_tests.value()
    value["plan"]["boot"] = clock.boot
    template = m.acceptance.offers.template_codec.decode(value)
    witness = m.intake.plans.clock.ClockWitness(clock)
    offer = original = reader = None
    descriptors = fds()
    try:
        offer = m.acceptance.offers.Offer(template, template.sha256, witness)
        publisher = m.publication.Publisher(offer)
        original = publisher.publish()
        reader = m.acceptance.Acceptance(publisher)
        assert reader.poll() is None
        code = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import supplemental_recording_service_submit as submit
root = Path(sys.argv[2])
# Explicit temporary alias, never an installed path or App qualification.
submit.intake.plans.Plan.root = property(lambda _: root)
def forbidden(*args, **kwargs):
    raise AssertionError("Submission cannot capture clocks or call Engine")
submit.intake.plans.clock.read = forbidden
submit.intake.plans.ordinary.Docker = forbidden
with submit.intake.CasePlan(root, sys.argv[4]) as original:
    print(submit.Submission(original, sys.argv[3], sys.argv[4]).submit())
"""
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                "-c",
                code,
                str(Path(m.__file__).parent),
                str(root),
                template.sha256,
                offer.plan.sha256,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        expected = m.acceptance.acceptance_bytes(template.sha256, offer.plan.sha256)
        assert result.stderr == "" and result.stdout.strip() == m.intake.plans.base.checksum(
            m.acceptance.object_json(expected)
        )
        assert not offer.used and reader.poll() is offer.plan
        reader.close()
        original.close()
        offer.close()
        assert fds() == descriptors and not witness.closed
        assert os.fstat(witness.fd).st_ino == clock.namespace[1]
        offer.plan.check_clock(witness.read())
    finally:
        if reader is not None:
            reader.close()
        if original is not None:
            original.close()
        if offer is not None:
            offer.close()
        witness.close()
