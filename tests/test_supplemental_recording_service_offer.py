"""Offline borrowed-clock state model; synthetic clocks are NOT qualification."""

import importlib.util
import sys
from dataclasses import replace
from pathlib import Path
from threading import Thread

import pytest

from . import test_supplemental_recording_service_template as template_tests

NAME = "supplemental_recording_service_offer"
SPEC = importlib.util.spec_from_file_location(
    NAME, Path(template_tests.m.__file__).with_name(NAME + ".py")
)
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)


class FakeWitness:
    """Explicit test double: never evidence of a retained kernel descriptor."""

    def __init__(self, original):
        self.original = original
        self.reads = self.closes = 0
        self.hook = None
        self.observed = replace(
            original,
            before_ns=original.before_ns + m.plans.clock.NS,
            boottime_ns=original.boottime_ns + m.plans.clock.NS,
            after_ns=original.after_ns + m.plans.clock.NS,
        )

    def read(self):
        self.reads += 1
        if self.hook:
            self.hook()
        return self.observed

    def close(self):
        self.closes += 1


@pytest.fixture
def setup(monkeypatch):
    template = m.template_codec.decode(template_tests.value())
    witness = FakeWitness(template_tests.clock())
    now = [11.01]
    monkeypatch.setattr(m.plans.clock, "ClockWitness", FakeWitness)
    monkeypatch.setattr(m.time, "monotonic", lambda: now[0])
    return template, witness, now


def create(setup):
    template, witness, _ = setup
    return m.Offer(template, template.sha256, witness)


def denied(action):
    with pytest.raises(m.UnconfirmedOffer) as error:
        action()
    assert str(error.value) == m.MESSAGE and error.value.__suppress_context__


def test_same_proposal_one_accept_and_original_caller_owned_clock(setup):
    template, witness, _ = setup
    offer = create(setup)
    proposed = offer.inspect()
    assert offer.inspect() is proposed
    assert proposed.raw == template.preview(witness.original).raw
    assert offer.original_clock is witness.original
    assert offer.deadline == witness.original.after_ns / m.plans.clock.NS + 15
    assert offer.accept(proposed.sha256) is proposed
    assert offer.used and offer.accepted and not offer.failed
    assert witness.reads == 5
    offer.close()
    offer.close()
    assert offer.closed and witness.closes == 0


@pytest.mark.parametrize("bad", [None, True, b"a" * 64, "private-secret", "a" * 64])
def test_wrong_template_pin_refuses_before_clock_read(setup, bad):
    template, witness, _ = setup
    denied(lambda: m.Offer(template, bad, witness))
    assert witness.reads == witness.closes == 0


@pytest.mark.parametrize("bad", [None, {}, b"private-secret", "private-secret"])
def test_wrong_template_type_refuses(setup, bad):
    template, witness, _ = setup
    denied(lambda: m.Offer(bad, template.sha256, witness))
    assert witness.reads == witness.closes == 0


def test_serialized_clock_or_subclass_cannot_replace_original_witness(setup):
    template, witness, _ = setup

    class Subclass(FakeWitness):
        pass

    for bad in (witness.original, vars(witness), Subclass(witness.original), None):
        denied(lambda bad=bad: m.Offer(template, template.sha256, bad))
    assert witness.reads == witness.closes == 0


@pytest.mark.parametrize("bad", [None, True, b"a" * 64, "private-secret", "a" * 64])
def test_bad_acceptance_is_consumed_and_cannot_be_replaced(setup, bad):
    offer = create(setup)
    pin = offer.inspect().sha256
    denied(lambda: offer.accept(bad))
    assert offer.failed and offer.used and not offer.accepted
    denied(lambda: offer.accept(pin))
    denied(offer.inspect)
    offer.close()
    assert setup[1].closes == 0


@pytest.mark.parametrize("action", ["inspect", "accept"])
def test_success_is_not_replayable_or_inspectable_after_acceptance(setup, action):
    offer = create(setup)
    pin = offer.plan.sha256
    offer.accept(pin)
    denied(lambda: offer.inspect() if action == "inspect" else offer.accept(pin))
    assert offer.used and offer.failed


@pytest.mark.parametrize("where", ["construct", "inspect", "accept"])
def test_original_fifteen_second_deadline_cannot_restart(setup, where):
    template, witness, now = setup
    offer = None if where == "construct" else create(setup)
    now[0] = witness.original.after_ns / m.plans.clock.NS + 15
    if where == "construct":
        denied(lambda: m.Offer(template, template.sha256, witness))
    else:
        denied(lambda: offer.inspect() if where == "inspect" else offer.accept(offer.plan.sha256))
        now[0] -= 1
        denied(offer.inspect)
    assert witness.closes == 0


def test_earlier_original_readiness_caps_offer_wait(setup):
    _, witness, now = setup
    value = template_tests.value()
    value["budget"]["ready_seconds"] = 5
    template = m.template_codec.decode(value)
    offer = m.Offer(template, template.sha256, witness)
    assert offer.deadline == offer.plan.lease["ready_by"] < 15
    now[0] = offer.deadline
    denied(lambda: offer.accept(offer.plan.sha256))
    assert not offer.accepted and offer.used and witness.closes == 0


@pytest.mark.parametrize("when", [1, 2])
def test_expiry_during_either_acceptance_clock_read_is_sticky(setup, when):
    offer = create(setup)
    _, witness, now = setup
    target = witness.reads + when

    def expire():
        if witness.reads == target:
            now[0] = offer.deadline

    witness.hook = expire
    denied(lambda: offer.accept(offer.plan.sha256))
    assert offer.failed and offer.used and not offer.accepted
    assert witness.closes == 0


@pytest.mark.parametrize("field", ["boot", "namespace", "boottime_ns", "before_ns", "after_ns"])
def test_changed_or_expired_clock_observation_refuses(setup, field):
    offer = create(setup)
    witness = setup[1]
    changes = {
        "boot": "d" * 32,
        "namespace": (1, 403),
        "boottime_ns": witness.observed.boottime_ns + m.plans.clock.NS,
        "before_ns": witness.original.before_ns - 1,
        "after_ns": int(offer.deadline * m.plans.clock.NS),
    }
    # Bypass Window's constructor intentionally to exercise validation at intake.
    object.__setattr__(witness.observed, field, changes[field])
    denied(lambda: offer.accept(offer.plan.sha256))
    assert offer.failed and not offer.accepted and witness.closes == 0


@pytest.mark.parametrize("field", ["template", "clock_witness", "original_clock", "plan"])
def test_equal_replacements_do_not_replace_original_objects(setup, field):
    offer = create(setup)
    replacements = {
        "template": m.template_codec.Template(offer.template.raw),
        "clock_witness": FakeWitness(offer.original_clock),
        "original_clock": replace(offer.original_clock),
        "plan": m.plans.load_bytes(offer.plan.raw, offer.plan.sha256),
    }
    setattr(offer, field, replacements[field])
    denied(offer.inspect)
    assert offer.failed and setup[1].closes == 0


@pytest.mark.parametrize("when", ["before", "during_final_read"])
@pytest.mark.parametrize("change", ["template", "plan", "origin", "deadline", "close"])
def test_mutation_before_or_during_final_clock_read_prevents_acceptance(setup, when, change):
    offer = create(setup)
    pin = offer.plan.sha256
    witness = setup[1]

    def mutate():
        if change == "template":
            object.__setattr__(offer.template, "raw", b"private-secret")
        elif change == "plan":
            object.__setattr__(offer.plan, "firmware", "private-secret")
        elif change == "origin":
            witness.original = replace(witness.original)
        elif change == "deadline":
            offer.deadline += 1
        else:
            offer.close()

    if when == "before":
        mutate()
    else:
        target = witness.reads + 2
        witness.hook = lambda: mutate() if witness.reads == target else None
    denied(lambda: offer.accept(pin))
    assert offer.failed and offer.used and not offer.accepted and witness.closes == 0


@pytest.mark.parametrize("error_type", [ValueError, OSError, KeyboardInterrupt, SystemExit])
def test_uncertain_clock_read_poisoning_never_closes_borrowed_witness(setup, error_type):
    offer = create(setup)
    witness = setup[1]

    def failure():
        raise error_type("private-secret")

    witness.hook = failure
    if issubclass(error_type, Exception):
        denied(lambda: offer.accept(offer.plan.sha256))
    else:
        with pytest.raises(error_type):
            offer.accept(offer.plan.sha256)
    assert offer.used and offer.failed and not offer.accepted
    witness.hook = None
    denied(offer.inspect)
    offer.close()
    assert witness.closes == 0


@pytest.mark.parametrize("action", ["inspect", "accept", "close"])
def test_foreign_thread_poisoning_cannot_transfer_ownership(setup, action):
    offer = create(setup)
    errors = []

    def invoke():
        try:
            getattr(offer, action)(*([offer.plan.sha256] if action == "accept" else []))
        except BaseException as error:
            errors.append(error)

    thread = Thread(target=invoke)
    thread.start()
    thread.join(timeout=1)
    assert not thread.is_alive() and len(errors) == 1
    assert type(errors[0]) is m.UnconfirmedOffer
    assert offer.failed and not offer.accepted
    denied(offer.inspect)
    offer.close()
    assert setup[1].closes == 0


@pytest.mark.parametrize("action", ["inspect", "accept"])
def test_contended_lock_does_not_wait_or_release_someone_elses_lock(setup, action):
    offer = create(setup)
    assert offer.lock.acquire(blocking=False)
    denied(lambda: offer.inspect() if action == "inspect" else offer.accept(offer.plan.sha256))
    assert offer.lock.locked() and offer.failed and not offer.accepted
    offer.lock.release()
    denied(offer.inspect)


def test_no_external_action_or_clock_replacement_from_offer(setup, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Offer attempted an external action")

    monkeypatch.setattr(m.plans.clock, "read", forbidden)
    monkeypatch.setattr(m.os, "open", forbidden)
    monkeypatch.setattr(m.plans.ordinary, "Docker", forbidden)
    offer = create(setup)
    assert offer.accept(offer.inspect().sha256) is offer.plan
    offer.close()
    assert setup[1].closes == 0


def test_offer_is_outside_qualified_helper_command_allowlist():
    source = Path(m.__file__).with_name("supplemental_recording_host_source.py").read_text()
    assert '"service_offer"' not in source


def test_valid_but_expired_clock_sample_refuses_even_with_earlier_monotonic_stub(setup):
    offer = create(setup)
    witness = setup[1]
    original = witness.original
    delta = 16 * m.plans.clock.NS
    witness.observed = replace(
        original,
        before_ns=original.before_ns + delta,
        after_ns=original.after_ns + delta,
        boottime_ns=original.boottime_ns + delta,
    )
    original.check_later(witness.observed)  # Otherwise valid, same clock domain.
    denied(lambda: offer.accept(offer.plan.sha256))
    assert offer.failed and not offer.accepted and witness.closes == 0


def test_original_process_owner_cannot_be_changed(setup):
    offer = create(setup)
    offer.owner = offer.owner[0] + 1, offer.owner[1]
    denied(offer.inspect)
    assert offer.failed and setup[1].closes == 0


def test_failed_constructor_leaves_original_clock_cleanup_with_caller(setup):
    template, witness, _ = setup

    def failure():
        raise OSError("private-secret")

    witness.hook = failure
    denied(lambda: m.Offer(template, template.sha256, witness))
    assert witness.reads == 1 and witness.closes == 0


def test_real_original_clock_is_retained_after_structural_acceptance_and_model_close():
    # Real local read-only kernel handles; all App/contract data remain synthetic.
    # This is not an installed-helper or independent-approval qualification.
    original = m.plans.clock.read()
    value = template_tests.value()
    value["plan"]["boot"] = original.boot
    template = m.template_codec.decode(value)
    witness = m.plans.clock.ClockWitness(original)
    fd = witness.fd
    try:
        offer = m.Offer(template, template.sha256, witness)
        plan = offer.inspect()
        assert offer.accept(plan.sha256) is plan
        offer.close()
        assert witness.fd == fd and not witness.closed and not witness.failed
        plan.check_clock(witness.read())
        assert m.os.fstat(fd).st_ino == original.namespace[1]
    finally:
        witness.close()
    assert witness.closed and witness.fd == -1
