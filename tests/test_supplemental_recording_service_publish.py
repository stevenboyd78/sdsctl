"""Private synthetic case files and fault injection, never an installed case."""

import fcntl
import importlib.util
import json
import os
import stat
import sys
from pathlib import Path
from threading import Thread

import pytest

from . import test_supplemental_recording_service_input as input_tests  # noqa: F401
from . import test_supplemental_recording_service_offer as offer_tests

NAME = "supplemental_recording_service_publish"
SPEC = importlib.util.spec_from_file_location(
    NAME, Path(offer_tests.m.__file__).with_name(NAME + ".py")
)
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)
setup = offer_tests.setup


@pytest.fixture
def case(setup, tmp_path, monkeypatch):
    root = tmp_path / "private-case"
    root.mkdir(mode=0o700)
    # Explicit temporary path alias, NOT actual installed path qualification.
    monkeypatch.setattr(m.intake.plans.Plan, "root", property(lambda _: root))
    offer = offer_tests.create(setup)
    return root, offer, setup[1], setup[2]


def fds():
    return set(os.listdir("/proc/self/fd"))


def denied(action):
    with pytest.raises(m.UnconfirmedPublication) as error:
        action()
    assert str(error.value) == m.MESSAGE and error.value.__suppress_context__


def snapshot(root):
    return {p.name: p.read_bytes() for p in root.iterdir()}


def test_exclusive_durable_publication_returns_original_read_only_caseplan(case, monkeypatch):
    root, offer, witness, _ = case
    publisher = m.Publisher(offer)
    assert list(root.iterdir()) == [] and not publisher.used
    calls, fsync = [], os.fsync

    def observed(fd):
        calls.append("directory" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file")
        fsync(fd)

    monkeypatch.setattr(m.os, "fsync", observed)
    before = fds()
    original = publisher.publish()
    try:
        assert calls == ["file", "directory", "file", "directory"]
        assert type(original) is m.intake.CasePlan
        assert original.recheck().raw == offer.plan.raw
        assert publisher.used and not publisher.failed and not offer.used
        assert not offer.closed and not offer.accepted and witness.closes == 0
        assert (root / "plan.json").read_bytes() == offer.plan.raw
        assert json.loads((root / m.CLAIM).read_bytes()) == {
            "schema": 1,
            "kind": "finite-recording-startup-claim-v1",
            "case": offer.plan.case,
            "boot": offer.plan.boot,
            "template_sha256": offer.template_sha256,
            "plan_sha256": offer.plan.sha256,
        }
        for path in root.iterdir():
            assert stat.S_IMODE(path.stat().st_mode) == 0o600 and path.stat().st_nlink == 1
        assert fcntl.fcntl(original._file, fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDONLY
        # Separate acceptance uses the independently supplied digest, not a
        # claim file interpreted as approval by this publisher.
        assert offer.accept(original.plan.sha256).raw == original.plan.raw
    finally:
        original.close()
    assert fds() == before


@pytest.mark.parametrize("kind", ["file", "directory", "symlink", "fifo", "plan", "claim"])
def test_existing_entry_prevents_all_new_publication_without_overwrite(case, kind):
    root, offer, witness, _ = case
    path = root / ("plan.json" if kind == "plan" else m.CLAIM if kind == "claim" else "existing")
    if kind == "directory":
        path.mkdir()
    elif kind == "symlink":
        path.symlink_to("missing")
    elif kind == "fifo":
        os.mkfifo(path)
    else:
        path.write_bytes(b"Preserve existing data")
    before = path.lstat()
    publisher = m.Publisher(offer)
    descriptors = fds()
    denied(publisher.publish)
    assert list(root.iterdir()) == [path] and path.lstat() == before
    assert publisher.used and publisher.failed and offer.closed and witness.closes == 0
    assert fds() == descriptors


@pytest.mark.parametrize("phase", [1, 2])
def test_partial_write_is_preserved_and_never_retried_or_repaired(case, monkeypatch, phase):
    root, offer, witness, _ = case
    write, writes = os.write, []

    def partial(fd, raw):
        writes.append(raw)
        return write(fd, raw[: len(raw) // 2] if len(writes) == phase else raw)

    monkeypatch.setattr(m.os, "write", partial)
    publisher = m.Publisher(offer)
    descriptors = fds()
    denied(publisher.publish)
    files = snapshot(root)
    assert len(writes) == phase and len(files) == phase
    assert files[m.CLAIM if phase == 1 else "plan.json"] == writes[-1][: len(writes[-1]) // 2]
    denied(publisher.publish)
    assert snapshot(root) == files and len(writes) == phase
    assert offer.closed and witness.closes == 0 and fds() == descriptors


@pytest.mark.parametrize("phase", [1, 2, 3, 4])
@pytest.mark.parametrize("after", [False, True])
def test_file_or_directory_fsync_uncertainty_preserves_every_byte(case, monkeypatch, phase, after):
    root, offer, witness, _ = case
    fsync, calls = os.fsync, []

    def fault(fd):
        calls.append(fd)
        if len(calls) != phase or after:
            fsync(fd)
        if len(calls) == phase:
            raise OSError("private-secret")

    monkeypatch.setattr(m.os, "fsync", fault)
    publisher = m.Publisher(offer)
    descriptors = fds()
    denied(publisher.publish)
    files = snapshot(root)
    assert set(files) == ({m.CLAIM} if phase <= 2 else {m.CLAIM, "plan.json"})
    denied(publisher.publish)
    assert snapshot(root) == files and len(calls) == phase
    assert publisher.failed and offer.closed and witness.closes == 0
    assert fds() == descriptors


@pytest.mark.parametrize("phase", [1, 2, 3, 4])
def test_original_offer_expiry_during_fsync_refuses_without_extending_wait(
    case, monkeypatch, phase
):
    root, offer, witness, now = case
    fsync, calls = os.fsync, []

    def expire(fd):
        fsync(fd)
        calls.append(fd)
        if len(calls) == phase:
            now[0] = offer.deadline

    monkeypatch.setattr(m.os, "fsync", expire)
    publisher = m.Publisher(offer)
    denied(publisher.publish)
    files = snapshot(root)
    assert len(calls) == phase and offer.closed and witness.closes == 0
    now[0] -= 1
    denied(publisher.publish)
    assert files == snapshot(root)


def test_two_second_publication_bound_is_separate_from_original_offer_wait(case, monkeypatch):
    root, offer, witness, now = case
    fsync = os.fsync

    def delayed(fd):
        fsync(fd)
        now[0] += m.MAX_SECONDS

    monkeypatch.setattr(m.os, "fsync", delayed)
    publisher = m.Publisher(offer)
    denied(publisher.publish)
    assert now[0] < offer.deadline and set(snapshot(root)) == {m.CLAIM}
    assert offer.closed and witness.closes == 0


@pytest.mark.parametrize("fault", ["rename_root", "mode", "hardlink", "changed_bytes", "extra"])
def test_changes_during_publication_refuse(case, monkeypatch, fault):
    root, offer, witness, _ = case
    fsync, calls = os.fsync, []

    def change(fd):
        fsync(fd)
        calls.append(fd)
        if len(calls) == 3:
            if fault == "rename_root":
                root.rename(root.with_name("preserved-original"))
                root.mkdir(mode=0o700)
            elif fault == "mode":
                (root / m.CLAIM).chmod(0o644)
            elif fault == "hardlink":
                os.link(root / m.CLAIM, root.with_name("preserved-link"))
            elif fault == "changed_bytes":
                (root / m.CLAIM).write_bytes(b"changed-private-data")
            else:
                (root / "unexpected").write_bytes(b"Preserve")

    monkeypatch.setattr(m.os, "fsync", change)
    publisher = m.Publisher(offer)
    descriptors = fds()
    denied(publisher.publish)
    assert publisher.failed and offer.closed and witness.closes == 0
    assert fds() == descriptors


@pytest.mark.parametrize("fault", ["root_link", "parent_link", "public_root", "missing_root"])
def test_invalid_or_linked_case_path_refuses_before_creating_files(case, monkeypatch, fault):
    root, offer, witness, _ = case
    publisher = m.Publisher(offer)
    if fault == "public_root":
        root.chmod(0o755)
    elif fault == "missing_root":
        root.rename(root.with_name("preserved-original"))
    elif fault == "root_link":
        preserved = root.with_name("preserved-original")
        root.rename(preserved)
        root.symlink_to(preserved, target_is_directory=True)
    else:
        link = root.parent / "parent-link"
        link.symlink_to(root.parent, target_is_directory=True)
        linked_root = link / root.name
        monkeypatch.setattr(m.intake.plans.Plan, "root", property(lambda _: linked_root))
        publisher = m.Publisher(offer)
    denied(publisher.publish)
    assert offer.closed and witness.closes == 0
    assert not list(root.parent.rglob("plan.json"))


def test_restart_cannot_overwrite_previous_publication_even_with_new_model(case):
    root, offer, witness, _ = case
    publisher = m.Publisher(offer)
    original = publisher.publish()
    original.close()
    files = snapshot(root)
    # Deliberately construct a forbidden replay to prove the exclusive files
    # refuse it; this is a unit test, not permission to retry a real case.
    replacement = m.offers.Offer(offer.template, offer.template_sha256, witness)
    denied(m.Publisher(replacement).publish)
    assert snapshot(root) == files and replacement.closed and witness.closes == 0


def test_accepted_offer_cannot_publish_after_acceptance(case):
    root, offer, witness, _ = case
    offer.accept(offer.plan.sha256)
    denied(lambda: m.Publisher(offer))
    assert offer.closed and list(root.iterdir()) == [] and witness.closes == 0


def test_intake_failure_after_durable_files_cannot_return_partial_success(case, monkeypatch):
    root, offer, witness, _ = case

    def fail(*args):
        raise OSError("private-secret")

    monkeypatch.setattr(m.intake, "CasePlan", fail)
    publisher = m.Publisher(offer)
    descriptors = fds()
    denied(publisher.publish)
    assert set(snapshot(root)) == {m.CLAIM, "plan.json"}
    assert publisher.failed and offer.closed and witness.closes == 0 and fds() == descriptors


def test_foreign_thread_cannot_publish_or_close_callers_original_clock(case):
    root, offer, witness, _ = case
    publisher, errors = m.Publisher(offer), []

    def foreign():
        try:
            publisher.publish()
        except BaseException as error:
            errors.append(error)

    thread = Thread(target=foreign)
    thread.start()
    thread.join(timeout=1)
    assert not thread.is_alive() and len(errors) == 1
    assert type(errors[0]) is m.UnconfirmedPublication
    assert publisher.failed and not offer.closed and witness.closes == 0
    denied(publisher.publish)
    assert list(root.iterdir()) == [] and offer.closed


def test_contended_publisher_lock_refuses_without_releasing_it(case):
    root, offer, witness, _ = case
    publisher = m.Publisher(offer)
    assert publisher.lock.acquire(blocking=False)
    denied(publisher.publish)
    assert publisher.failed and publisher.lock.locked()
    publisher.lock.release()
    assert offer.closed and witness.closes == 0 and not list(root.iterdir())


def test_publication_is_not_in_the_previous_helper_graph():
    source = Path(m.__file__).with_name("supplemental_recording_host_source.py").read_text()
    assert '"service_publish"' not in source


@pytest.mark.parametrize("error_type", [KeyboardInterrupt, SystemExit])
def test_interruption_preserves_claim_and_partial_plan_without_retry(case, monkeypatch, error_type):
    root, offer, witness, _ = case
    write, calls = os.write, []

    def interrupted(fd, raw):
        calls.append(raw)
        if len(calls) == 2:
            write(fd, raw[:10])
            raise error_type()
        return write(fd, raw)

    monkeypatch.setattr(m.os, "write", interrupted)
    publisher = m.Publisher(offer)
    before = fds()
    with pytest.raises(error_type):
        publisher.publish()
    assert publisher.failed and publisher.used and offer.closed
    assert (root / "plan.json").read_bytes() == offer.plan.raw[:10]
    assert witness.closes == 0 and fds() == before
    files = snapshot(root)
    denied(publisher.publish)
    assert files == snapshot(root) and len(calls) == 2


def test_lost_file_close_ack_is_not_retried(case, monkeypatch):
    root, offer, witness, _ = case
    close, calls, target = os.close, [], []

    def uncertain(fd):
        regular = stat.S_ISREG(os.fstat(fd).st_mode)
        calls.append(fd)
        close(fd)
        if regular and not target:
            target.append(fd)
            raise OSError("private-secret close acknowledgement lost")

    monkeypatch.setattr(m.os, "close", uncertain)
    publisher = m.Publisher(offer)
    before = fds()
    denied(publisher.publish)
    assert len(target) == 1 and calls.count(target[0]) == 1
    assert set(snapshot(root)) == {m.CLAIM}
    assert publisher.failed and offer.closed and witness.closes == 0
    assert fds() == before


def test_busy_case_directory_is_a_refusal_not_a_publication_retry(case):
    root, offer, witness, _ = case
    fd = os.open(root, m.intake.files.DIRECTORY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        publisher = m.Publisher(offer)
        denied(publisher.publish)
        assert publisher.failed and offer.closed and witness.closes == 0
        assert not list(root.iterdir())
    finally:
        os.close(fd)


def test_publication_never_opens_engine_or_runs_a_service(case, monkeypatch):
    root, offer, witness, _ = case

    def forbidden(*args, **kwargs):
        raise AssertionError("Private publication attempted an Engine action")

    monkeypatch.setattr(m.intake.plans.ordinary, "Docker", forbidden)
    original = m.Publisher(offer).publish()
    original.close()
    assert not offer.used and not offer.accepted and witness.closes == 0
    assert set(snapshot(root)) == {m.CLAIM, "plan.json"}


def test_real_clock_publication_preserves_original_namespace_handle(tmp_path, monkeypatch):
    root = tmp_path / "real-clock-private-case"
    root.mkdir(mode=0o700)
    monkeypatch.setattr(m.intake.plans.Plan, "root", property(lambda _: root))
    clock = m.intake.plans.clock.read()
    value = offer_tests.template_tests.value()
    value["plan"]["boot"] = clock.boot
    template = m.offers.template_codec.decode(value)
    witness = m.intake.plans.clock.ClockWitness(clock)
    original = offer = None
    before = fds()
    try:
        offer = m.offers.Offer(template, template.sha256, witness)
        original = m.Publisher(offer).publish()
        # Test-only structural acceptance; all App/contract data are synthetic.
        assert offer.accept(original.plan.sha256).raw == original.recheck().raw
        original.close()
        offer.close()
        assert fds() == before and not witness.closed
        assert os.fstat(witness.fd).st_ino == clock.namespace[1]
        offer.plan.check_clock(witness.read())
    finally:
        if original is not None:
            original.close()
        if offer is not None:
            offer.close()
        witness.close()
