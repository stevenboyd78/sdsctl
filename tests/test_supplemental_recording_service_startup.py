"""Original real local clock + private files; no installed/real App authority."""

import importlib.util
import json
import os
import select
import subprocess
import sys
from pathlib import Path
from threading import Thread

import pytest

from . import test_supplemental_recording_service_clock_link as links
from . import test_supplemental_recording_service_declaration as inputs
from . import test_supplemental_recording_service_submit as sends

NAME = "supplemental_recording_service_startup"
SPEC = importlib.util.spec_from_file_location(NAME, Path(inputs.m.__file__).with_name(NAME + ".py"))
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)


@pytest.fixture
def case(tmp_path, monkeypatch):
    root, source = tmp_path / "empty-case", tmp_path / "declaration"
    root.mkdir(mode=0o700)
    source.mkdir(mode=0o700)
    value = inputs.templates.value()
    value["plan"]["boot"] = m.plans.clock.read().boot
    template = m.declaration.codec.decode(value)
    path = source / m.declaration.NAME
    path.write_bytes(template.raw)
    path.chmod(0o600)
    monkeypatch.setattr(m.declaration, "declaration_root", lambda _: source)
    monkeypatch.setattr(m.plans.Plan, "root", property(lambda _: root))
    # No Engine endpoint or original App is consulted in these startup tests.
    monkeypatch.setattr(m.plans.ordinary, "Docker", lambda *_a, **_k: pytest.fail("No Engine"))
    original = m.declaration.Declaration(source, template.sha256)
    try:
        yield root, source, template, original
    finally:
        original.close()


def denied(action):
    with pytest.raises(m.UnconfirmedStartup) as error:
        action()
    assert str(error.value) == m.MESSAGE and error.value.__suppress_context__


def submit(startup):
    sends.m.Submission(startup.original, startup.expected, startup.original.plan.sha256).submit()


def test_constructor_claims_only_original_declaration_without_clock_or_write(case, monkeypatch):
    root, _, template, original = case
    before = inputs.fds()
    monkeypatch.setattr(m.plans.clock, "read", lambda: pytest.fail("Constructor captured clock"))
    startup = m.Startup(original)
    assert original.startup_owner is startup
    assert not startup.used and not startup.accepted and startup.clock is None
    assert startup.template.raw == template.raw and list(root.iterdir()) == []
    startup.close()
    assert inputs.fds() == before and not original.closed
    denied(lambda: m.Startup(original))
    denied(startup.prepare)


def test_prepare_wait_accept_keeps_actual_original_clock_and_files_until_owner_closes(case):
    root, _, template, declaration = case
    before = inputs.fds()
    startup = m.Startup(declaration)
    try:
        original = startup.prepare()
        clock, origin, plan = startup.clock, startup.clock.original, original.plan
        assert original is startup.original and plan.raw == startup.offer.plan.raw
        assert startup.offer.original_clock is origin and not startup.accepted
        assert m.plans._same_plan_value(plan.original_clock, origin)
        assert plan.raw == template.preview(origin).raw
        assert startup.poll() is None and startup.poll() is None
        assert {p.name for p in root.iterdir()} == {"startup-claim.json", "plan.json"}
        submit(startup)
        assert startup.poll() is original and startup.accepted and startup.offer.accepted
        assert startup.accepted_input() is original and startup.accepted_input() is original
        assert startup.clock is clock and clock.original is origin and not clock.closed
        plan.check_clock(clock.read())
        assert not original._closed and not declaration.closed
        assert {p.name for p in root.iterdir()} == {
            "startup-claim.json",
            "plan.json",
            "startup-acceptance.json",
        }
    finally:
        startup.close()
    assert clock.closed and original._closed and not declaration.closed
    assert inputs.fds() == before
    startup.close()
    denied(startup.accepted_input)


@pytest.mark.parametrize("action", ["poll", "accepted_input"])
def test_no_success_before_prepare_and_no_replay(case, action):
    root, _, _, declaration = case
    startup = m.Startup(declaration)
    denied(getattr(startup, action))
    assert startup.failed and startup.closed and not startup.used
    assert list(root.iterdir()) == [] and not declaration.closed
    denied(startup.prepare)
    denied(lambda: m.Startup(declaration))


@pytest.mark.parametrize("action", ["prepare", "accepted_input"])
def test_waiting_state_cannot_be_rearmed_or_used_as_acceptance(case, action):
    _, _, _, declaration = case
    before = inputs.fds()
    startup = m.Startup(declaration)
    original = startup.prepare()
    denied(getattr(startup, action))
    assert startup.failed and startup.closed and startup.clock.closed and original._closed
    assert not declaration.closed and inputs.fds() == before


def test_duplicate_poll_after_acceptance_does_not_acknowledge_again(case):
    _, _, _, declaration = case
    startup = m.Startup(declaration)
    startup.prepare()
    submit(startup)
    assert startup.poll() is startup.original
    denied(startup.poll)
    assert startup.failed and startup.closed
    denied(startup.accepted_input)


@pytest.mark.parametrize(
    "field", ["clock", "offer", "publisher", "original", "reader", "declaration", "template"]
)
def test_replaced_attributes_never_adopted_or_closed_instead_of_originals(case, field):
    _, _, _, declaration = case
    before = inputs.fds()
    startup = m.Startup(declaration)
    original = startup.prepare()
    clock = startup.clock
    setattr(startup, field, object())
    denied(startup.poll)
    assert clock.closed and original._closed and not declaration.closed
    assert inputs.fds() == before


@pytest.mark.parametrize(
    "stage", ["capture", "witness", "offer", "publisher", "publication", "reader"]
)
@pytest.mark.parametrize("error", [OSError, KeyboardInterrupt])
def test_assembly_failure_closes_acquired_original_handles_and_preserves_files(
    case, monkeypatch, stage, error
):
    root, _, _, declaration = case
    before = inputs.fds()
    startup = m.Startup(declaration)

    def failure(*_a, **_k):
        raise error("private-secret")

    module, name = {
        "capture": (m.plans.clock, "read"),
        "witness": (m.plans.clock, "ClockWitness"),
        "offer": (m.offers, "Offer"),
        "publisher": (m.publication, "Publisher"),
        "publication": (m.publication.Publisher, "publish"),
        "reader": (m.acceptance, "Acceptance"),
    }[stage]
    with monkeypatch.context() as patch:
        patch.setattr(module, name, failure)
        if error is OSError:
            denied(startup.prepare)
        else:
            with pytest.raises(error):
                startup.prepare()
    assert startup.failed and startup.closed and startup.used and not declaration.closed
    assert inputs.fds() == before
    files = {p.name: p.read_bytes() for p in root.iterdir()}
    assert set(files) == ({"startup-claim.json", "plan.json"} if stage == "reader" else set())
    denied(startup.prepare)
    assert files == {p.name: p.read_bytes() for p in root.iterdir()}


def test_original_offer_expiry_closes_startup_without_new_clock_or_wait(case, monkeypatch):
    _, _, _, declaration = case
    before = inputs.fds()
    startup = m.Startup(declaration)
    original = startup.prepare()
    origin = startup.clock.original
    monkeypatch.setattr(m.offers.time, "monotonic", lambda: startup.offer.deadline)
    denied(startup.poll)
    assert startup.clock.original is origin and startup.clock.closed and original._closed
    assert inputs.fds() == before and not declaration.closed


def test_declaration_changed_while_waiting_invalidates_entire_chain(case):
    _, source, _, declaration = case
    before = inputs.fds()
    startup = m.Startup(declaration)
    original = startup.prepare()
    (source / m.declaration.NAME).chmod(0o644)
    denied(startup.poll)
    assert startup.clock.closed and original._closed and declaration.failed
    assert inputs.fds() < before  # Reader also released its own declaration handles.


def test_bad_acceptance_is_preserved_and_does_not_start_any_service(case):
    root, _, _, declaration = case
    before = inputs.fds()
    startup = m.Startup(declaration)
    startup.prepare()
    path = root / m.acceptance.NAME
    path.write_bytes(b"private-secret")
    path.chmod(0o600)
    denied(startup.poll)
    assert path.read_bytes() == b"private-secret"
    assert inputs.fds() == before and not declaration.closed


@pytest.mark.parametrize("fault", ["reader", "clock", "declaration", "original", "accepted"])
def test_change_during_last_accepted_clock_guard_is_not_returned(case, monkeypatch, fault):
    _, _, _, declaration = case
    before = inputs.fds()
    startup = m.Startup(declaration)
    original = startup.prepare()
    submit(startup)
    assert startup.poll() is original
    actual, calls = startup.clock.read, []

    def changed():
        result = actual()
        calls.append(result)
        if len(calls) == 2:
            if fault == "original":
                startup.original = object()
            elif fault == "accepted":
                startup.accepted = False
            else:
                getattr(startup, fault).failed = True
        return result

    monkeypatch.setattr(startup.clock, "read", changed)
    denied(startup.accepted_input)
    assert len(calls) == 2 and startup.failed and original._closed
    assert inputs.fds() == before


def test_lost_acceptance_return_does_not_allow_reconstruction_or_second_submission(
    case, monkeypatch
):
    root, _, _, declaration = case
    before = inputs.fds()
    startup = m.Startup(declaration)
    startup.prepare()
    submit(startup)
    actual = startup.reader.poll

    def lost():
        assert actual() is startup.offer.plan
        raise OSError("private-secret")

    monkeypatch.setattr(startup.reader, "poll", lost)
    denied(startup.poll)
    assert startup.offer.accepted and not startup.accepted and startup.failed
    assert (root / m.acceptance.NAME).is_file() and inputs.fds() == before
    denied(startup.accepted_input)
    denied(lambda: m.Startup(declaration))


def test_foreign_thread_cannot_close_original_owner(case):
    _, _, _, declaration = case
    startup, errors = m.Startup(declaration), []
    before = inputs.fds()
    startup.prepare()

    def foreign():
        for action in (startup.poll, startup.close):
            try:
                action()
            except BaseException as error:
                errors.append(error)

    thread = Thread(target=foreign)
    thread.start()
    thread.join(timeout=1)
    assert not thread.is_alive() and len(errors) == 2
    assert all(type(error) is m.UnconfirmedStartup for error in errors)
    assert startup.failed and not startup.closed and not startup.clock.closed
    denied(startup.poll)
    assert inputs.fds() == before and not declaration.closed


def test_original_clock_is_closed_last_even_when_earlier_cleanup_ack_is_lost(case, monkeypatch):
    _, _, _, declaration = case
    before, calls = inputs.fds(), []
    actual = m.acceptance.Acceptance.close

    def lost(reader):
        calls.append("reader")
        actual(reader)
        raise OSError("private-secret")

    monkeypatch.setattr(m.acceptance.Acceptance, "close", lost)
    startup = m.Startup(declaration)
    startup.prepare()
    actual_clock_close = startup.clock.close

    # Cleanup retains original bound callbacks, not public replacement fields.
    def closed():
        calls.append("clock")
        actual_clock_close()

    startup._cleanup[0] = closed
    denied(startup.close)
    startup.close()
    assert calls == ["reader", "clock"] and startup.clock.closed
    assert not declaration.closed and inputs.fds() == before


@pytest.mark.parametrize("valid", [False, True])
def test_separate_original_startup_owner_with_independent_live_observer(case, monkeypatch, valid):
    """Real local processes/clocks/pidfds; synthetic Docker cgroup/App metadata.

    The subprocess command and temporary path aliases are TEST ONLY. This does
    not qualify an installed command or distinct Docker time namespaces.
    """
    root, source, template, _ = case
    before = inputs.fds()
    observer = m.plans.clock.ClockWitness(m.plans.clock.read())
    process, domain, link, received = None, None, None, None
    code = r"""
import json, os, select, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import supplemental_recording_service_startup as startup
root, source = Path(sys.argv[2]), Path(sys.argv[3])
# Synthetic temporary aliases, NOT an installed source or path qualification.
startup.plans.Plan.root = property(lambda _: root)
startup.declaration.declaration_root = lambda _: source
def forbidden(*args, **kwargs):
    raise AssertionError("No Engine or service during startup")
startup.plans.ordinary.Docker = forbidden
def report(kind, pin):
    print(json.dumps({"kind": kind, "plan_sha256": pin}), flush=True)
owner = None
with startup.declaration.Declaration(source, sys.argv[4]) as declaration:
    try:
        owner = startup.Startup(declaration)
        original = owner.prepare()
        pin = original.plan.sha256
        report("prepared", pin)
        while owner.poll() is None:
            time.sleep(0.02)
        assert owner.accepted_input() is original
        original.plan.check_clock(owner.clock.read())
        report("accepted", pin)
        # Test-only pause lets the observer recheck the SAME live original
        # clock owner before its planned exit; never extends an offer deadline.
        assert select.select([sys.stdin], [], [], 5)[0]
        assert sys.stdin.readline() == "finish\n"
    except startup.UnconfirmedStartup:
        report("refused", pin)
        raise SystemExit(75)
    finally:
        if owner is not None:
            owner.close()
"""
    child = subprocess.Popen(
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            code,
            str(Path(m.__file__).parent),
            str(root),
            str(source),
            template.sha256,
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    def message():
        assert select.select([child.stdout], [], [], 5)[0]
        return json.loads(child.stdout.readline())

    try:
        prepared = message()
        assert prepared["kind"] == "prepared"
        # The parent verifies exact bytes against its separately held template;
        # it does not treat the child's claimed digest as independent review.
        raw = (root / "plan.json").read_bytes()
        pin = m.declaration.codec.hashlib.sha256(raw).hexdigest()
        plan = m.plans.load_bytes(raw, pin)
        template.check_plan(plan, plan.original_clock)
        assert pin == prepared["plan_sha256"]

        # Real stat identity and original pidfd; only Docker cgroup membership
        # is synthetic so this local unit test cannot certify an HAOS container.
        process_module = links.m.domains.process
        actual_identity = process_module.read_identity
        cid = "a" * 64

        def identity(pid, container_id):
            if pid != child.pid:
                return actual_identity(pid, container_id)
            return process_module.process_identity(
                pid,
                container_id,
                Path(f"/proc/{pid}/stat").read_text(),
                f"0::/system.slice/docker-{container_id}.scope\n",
            )

        monkeypatch.setattr(process_module, "read_identity", identity)
        monkeypatch.setattr(links.m.domains, "ROOT_UID", os.geteuid())
        process = process_module.ProcessWitness(identity(child.pid, cid))
        domain = links.m.domains.ZeroDomain(observer.original, process)
        link = links.m.ObserverClock(plan, observer, domain, process.identity)
        assert link.read().namespace == observer.original.namespace
        received = m.acceptance.intake.CasePlan(root, pin)
        sender_pin = template.sha256 if valid else "f" * 64
        sends.m.Submission(received, sender_pin, pin).submit()
        assert message() == {"kind": "accepted" if valid else "refused", "plan_sha256": pin}
        if valid:
            assert not process.exited()
            link.read()
            child.stdin.write("finish\n")
            child.stdin.flush()
        assert child.wait(timeout=5) == (0 if valid else 75)
        assert child.stderr.read() == "" and process.exited()
        links.denied(link.read)  # Cannot reconstruct equivalence after original exit.
        assert (root / m.acceptance.NAME).is_file()
        assert (root / "plan.json").read_bytes() == raw
    finally:
        if child.poll() is None:
            child.terminate()  # Only this test's own child, never a real helper/App.
            child.wait(timeout=5)
        for stream in (child.stdin, child.stdout, child.stderr):
            stream.close()
        for resource in (received, link, domain, process, observer):
            if resource is not None:
                resource.close()
    assert inputs.fds() == before
