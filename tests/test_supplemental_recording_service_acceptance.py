"""One-use private startup intake with synthetic plans and temporary real files."""

import fcntl
import importlib.util
import json
import os
import sys
from pathlib import Path
from threading import Thread

import pytest

from . import test_supplemental_recording_service_publish as publishers

NAME = "supplemental_recording_service_acceptance"
SPEC = importlib.util.spec_from_file_location(
    NAME, Path(publishers.m.__file__).with_name(NAME + ".py")
)
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)
setup, case = publishers.setup, publishers.case


@pytest.fixture
def ready(case):
    root, offer, witness, now = case
    publisher = m.publication.Publisher(offer)
    original = publisher.publish()
    reader = m.Acceptance(publisher)
    try:
        yield root, offer, witness, now, publisher, original, reader
    finally:
        reader.close()
        original.close()
        offer.close()


def denied(action):
    with pytest.raises(m.UnconfirmedAcceptance) as error:
        action()
    assert str(error.value) == m.MESSAGE and error.value.__suppress_context__


def submit(ready, raw=None):
    root, offer, *_ = ready
    if raw is None:
        raw = m.acceptance_bytes(offer.template_sha256, offer.plan.sha256)
    path = root / m.NAME
    path.write_bytes(raw)
    path.chmod(0o600)
    return path


def test_missing_message_is_pending_then_exact_one_use_structural_acceptance(ready):
    root, offer, witness, _, publisher, original, reader = ready
    before, descriptors = publishers.snapshot(root), publishers.fds()
    assert reader.poll() is reader.poll() is None
    assert not reader.used and not reader.failed and not offer.used
    assert publishers.snapshot(root) == before
    path = submit(ready)
    untouched = path.stat()
    assert reader.poll() is offer.plan
    assert reader.used and reader.accepted and offer.accepted and not reader.failed
    assert publisher.acceptance_owner is reader
    assert original.recheck().raw == offer.plan.raw
    assert path.stat().st_mtime_ns == untouched.st_mtime_ns
    assert publishers.fds() == descriptors and witness.closes == 0
    files = publishers.snapshot(root)
    denied(reader.poll)
    assert reader.failed and offer.closed and publishers.snapshot(root) == files
    assert original.recheck().raw == offer.plan.raw


@pytest.mark.parametrize("kind", ["template", "plan"])
@pytest.mark.parametrize("bad", [None, True, "private-secret", b"a" * 64])
def test_message_builder_requires_exact_digest_types(kind, bad):
    pins = {"template": "a" * 64, "plan": "b" * 64} | {kind: bad}
    denied(lambda: m.acceptance_bytes(pins["template"], pins["plan"]))


@pytest.mark.parametrize(
    "fault",
    [
        "template",
        "plan",
        "schema",
        "kind",
        "unknown",
        "duplicate",
        "noncanonical",
        "empty",
        "large",
    ],
)
def test_invalid_message_is_consumed_and_never_replaced(ready, fault):
    root, offer, witness, _, _, original, reader = ready
    value = json.loads(m.acceptance_bytes(offer.template_sha256, offer.plan.sha256))
    if fault in ("template", "plan"):
        value[fault + "_sha256"] = "a" * 64
    elif fault == "schema":
        value["schema"] = True
    elif fault == "kind":
        value["kind"] = "other-protocol"
    elif fault == "unknown":
        value["private-secret"] = "hidden"
    raw = m.intake.plans.base.encode(value)
    if fault == "duplicate":
        raw = raw.replace(b'"schema":1', b'"schema":1,"schema":1')
    elif fault == "noncanonical":
        raw = json.dumps(value, indent=2).encode()
    elif fault == "empty":
        raw = b""
    elif fault == "large":
        raw = b"x" * (m.MAX_BYTES + 1)
    path = submit(ready, raw)
    before = publishers.fds()
    denied(reader.poll)
    assert reader.used and reader.failed and not reader.accepted and offer.closed
    assert path.read_bytes() == raw and witness.closes == 0 and publishers.fds() == before
    denied(reader.poll)
    assert original.recheck().raw == offer.plan.raw


@pytest.mark.parametrize("kind", ["public", "hardlink", "symlink", "fifo", "directory"])
def test_untrusted_file_shape_refuses_without_following_or_blocking(ready, kind):
    root, offer, witness, *_, reader = ready
    if kind == "symlink":
        outside = root.parent / "outside-message"
        outside.write_bytes(b"private-secret")
        (root / m.NAME).symlink_to(outside)
    elif kind == "fifo":
        os.mkfifo(root / m.NAME, 0o600)
    elif kind == "directory":
        (root / m.NAME).mkdir(mode=0o700)
    else:
        path = submit(ready)
        if kind == "public":
            path.chmod(0o644)
        else:
            os.link(path, root.parent / "outside-link")
    descriptors = publishers.fds()
    denied(reader.poll)
    assert reader.used and reader.failed and offer.closed and witness.closes == 0
    assert publishers.fds() == descriptors


def test_directory_lock_contention_stays_pending_without_consuming_submission(ready):
    root, offer, witness, *_, reader = ready
    submit(ready)
    fd = os.open(root, m.intake.files.DIRECTORY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert reader.poll() is None
        assert not reader.used and not reader.failed and not offer.used
    finally:
        os.close(fd)
    assert reader.poll() is offer.plan and witness.closes == 0


def test_expiry_while_message_absent_cannot_be_renewed(ready):
    root, offer, witness, now, *_, reader = ready
    now[0] = offer.deadline
    denied(reader.poll)
    assert reader.failed and offer.closed and not reader.accepted
    submit(ready)
    now[0] -= 1
    denied(reader.poll)
    assert witness.closes == 0 and not offer.accepted


@pytest.mark.parametrize("fault", ["overwrite", "replace", "chmod", "unlink"])
def test_change_during_message_read_refuses_before_acceptance(ready, monkeypatch, fault):
    root, offer, witness, *_, reader = ready
    path = submit(ready)
    inode = path.stat().st_ino
    pread = os.pread

    def changed(fd, size, offset):
        data = pread(fd, size, offset)
        if os.fstat(fd).st_ino == inode:
            if fault == "overwrite":
                path.write_bytes(b"changed-private-data")
            elif fault == "replace":
                path.rename(root / "preserved-message")
                submit(ready)
            elif fault == "chmod":
                path.chmod(0o644)
            else:
                path.rename(root / "preserved-message")
        return data

    monkeypatch.setattr(m.os, "pread", changed)
    descriptors = publishers.fds()
    denied(reader.poll)
    assert reader.failed and reader.used and not offer.accepted and offer.closed
    assert publishers.fds() == descriptors and witness.closes == 0


def test_original_plan_change_during_message_read_refuses(ready, monkeypatch):
    root, offer, witness, *_, reader = ready
    path = submit(ready)
    inode, pread = path.stat().st_ino, os.pread

    def changed(fd, size, offset):
        raw = pread(fd, size, offset)
        if os.fstat(fd).st_ino == inode:
            (root / "plan.json").write_bytes(b"Preserve changed plan")
        return raw

    monkeypatch.setattr(m.os, "pread", changed)
    denied(reader.poll)
    assert not reader.accepted and not offer.accepted and witness.closes == 0


def test_lost_message_close_ack_withholds_success_and_does_not_retry_close(ready, monkeypatch):
    _, offer, witness, *_, reader = ready
    path = submit(ready)
    inode, close, counts = path.stat().st_ino, os.close, []

    def lost(fd):
        targeted = os.fstat(fd).st_ino == inode
        close(fd)
        if targeted:
            counts.append(fd)
            raise OSError("private-secret")

    monkeypatch.setattr(m.os, "close", lost)
    descriptors = publishers.fds()
    denied(reader.poll)
    assert len(counts) == 1 and reader.failed and not reader.accepted and offer.closed
    assert offer.used and publishers.fds() == descriptors and witness.closes == 0


def test_lost_accept_return_is_sticky_even_when_offer_consumed(ready, monkeypatch):
    _, offer, witness, *_, reader = ready
    submit(ready)
    accept = offer.accept

    def lost(pin):
        accept(pin)
        raise OSError("private-secret")

    monkeypatch.setattr(offer, "accept", lost)
    denied(reader.poll)
    assert offer.used and offer.accepted and offer.closed
    assert reader.failed and not reader.accepted and witness.closes == 0
    denied(reader.poll)


def test_second_intake_cannot_claim_same_publisher_even_before_input(ready):
    *_, publisher, original, reader = ready
    denied(lambda: m.Acceptance(publisher))
    assert publisher.acceptance_owner is reader and not reader.failed
    assert reader.poll() is None
    reader.close()
    denied(lambda: m.Acceptance(publisher))
    assert original.recheck() is reader.plan


def test_foreign_thread_cannot_poll_or_close_borrowed_custody(ready):
    root, offer, witness, *_, reader = ready
    submit(ready)
    errors = []

    def foreign():
        for action in (reader.poll, reader.close):
            try:
                action()
            except BaseException as error:
                errors.append(error)

    thread = Thread(target=foreign)
    thread.start()
    thread.join(timeout=1)
    assert not thread.is_alive() and len(errors) == 2
    assert all(type(error) is m.UnconfirmedAcceptance for error in errors)
    assert reader.failed and not reader.accepted and not reader.closed
    assert not offer.closed and witness.closes == 0
    denied(reader.poll)
    assert offer.closed


def test_intake_never_writes_journal_or_dispatches_engine_actions(ready, monkeypatch):
    root, offer, witness, *_, reader = ready
    submit(ready)
    files = publishers.snapshot(root)

    def forbidden(*args, **kwargs):
        raise AssertionError("Startup intake attempted a write or Engine action")

    monkeypatch.setattr(m.os, "write", forbidden)
    monkeypatch.setattr(m.os, "fsync", forbidden)
    monkeypatch.setattr(m.intake.plans.ordinary, "Docker", forbidden)
    assert reader.poll() is offer.plan
    assert publishers.snapshot(root) == files and witness.closes == 0


def test_acceptance_is_not_in_qualified_helper_graph():
    source = Path(m.__file__).with_name("supplemental_recording_host_source.py").read_text()
    assert '"service_acceptance"' not in source


@pytest.mark.parametrize("present", [False, True])
def test_leftover_pending_submission_never_counts_as_acceptance(ready, present):
    root, offer, witness, *_, reader = ready
    if present:
        submit(ready)
    path = root / ".pending-startup-acceptance"
    path.write_bytes(b"Preserve incomplete publication")
    denied(reader.poll)
    assert reader.failed and not reader.accepted and not offer.accepted and offer.closed
    assert witness.closes == 0 and path.read_bytes() == b"Preserve incomplete publication"


def test_two_second_read_limit_cannot_be_extended_by_valid_input(ready, monkeypatch):
    _, offer, witness, now, *_, reader = ready
    path = submit(ready)
    inode, pread = path.stat().st_ino, os.pread

    def delayed(fd, size, offset):
        raw = pread(fd, size, offset)
        if os.fstat(fd).st_ino == inode:
            now[0] += m.MAX_SECONDS
        return raw

    monkeypatch.setattr(m.os, "pread", delayed)
    denied(reader.poll)
    assert now[0] < offer.deadline and offer.closed and not offer.accepted
    assert reader.failed and witness.closes == 0


@pytest.mark.parametrize("error_type", [KeyboardInterrupt, SystemExit])
def test_interrupted_submission_is_preserved_and_not_reconsumed(ready, monkeypatch, error_type):
    root, offer, witness, *_, reader = ready
    path = submit(ready)
    inode, pread = path.stat().st_ino, os.pread

    def interrupted(fd, size, offset):
        if os.fstat(fd).st_ino == inode:
            raise error_type()
        return pread(fd, size, offset)

    files, descriptors = publishers.snapshot(root), publishers.fds()
    monkeypatch.setattr(m.os, "pread", interrupted)
    with pytest.raises(error_type):
        reader.poll()
    assert reader.failed and reader.used and offer.closed and not offer.accepted
    assert publishers.fds() == descriptors and witness.closes == 0
    assert files == publishers.snapshot(root)
    denied(reader.poll)


@pytest.mark.parametrize("member", ["publisher", "original", "offer", "plan"])
def test_replaced_custody_is_not_adopted_or_closed(ready, member):
    _, offer, witness, _, _, original, reader = ready
    submit(ready)
    setattr(reader, member, object())
    denied(reader.poll)
    assert reader.failed and not reader.accepted and witness.closes == 0
    assert offer.closed
    assert original.recheck().raw == offer.plan.raw


def test_real_clock_publication_and_submission_keep_same_original_owner(tmp_path, monkeypatch):
    root = tmp_path / "real-clock-acceptance"
    root.mkdir(mode=0o700)
    monkeypatch.setattr(m.intake.plans.Plan, "root", property(lambda _: root))
    clock = m.intake.plans.clock.read()
    value = publishers.offer_tests.template_tests.value()
    value["plan"]["boot"] = clock.boot
    template = m.offers.template_codec.decode(value)
    witness = m.intake.plans.clock.ClockWitness(clock)
    original = reader = offer = None
    descriptors = publishers.fds()
    try:
        offer = m.offers.Offer(template, template.sha256, witness)
        publisher = m.publication.Publisher(offer)
        original = publisher.publish()
        reader = m.Acceptance(publisher)
        assert reader.poll() is None
        path = root / m.NAME
        path.write_bytes(m.acceptance_bytes(template.sha256, original.plan.sha256))
        path.chmod(0o600)
        assert reader.poll() is offer.plan
        reader.close()
        original.close()
        offer.close()
        assert publishers.fds() == descriptors and not witness.closed
        offer.plan.check_clock(witness.read())
        assert os.fstat(witness.fd).st_ino == clock.namespace[1]
    finally:
        if reader is not None:
            reader.close()
        if original is not None:
            original.close()
        if offer is not None:
            offer.close()
        witness.close()
