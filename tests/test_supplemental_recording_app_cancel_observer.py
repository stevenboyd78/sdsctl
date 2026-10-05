"""Original App pre-cancel guards with explicitly synthetic observer/Engine facts.

The driver, journal, prepared ledger, source qualification and one-use policy
are real. Native actors and returned acknowledgment are synthetic in this file;
the separate actual-native/peer suite exercises original process custody.
"""

import time
from dataclasses import replace

import pytest

from . import test_supplemental_recording_app_driver_pristine as pristine

driver, m, begins = pristine.driver, pristine.driver.m, pristine.driver.begins
candidate, app, native, driver_case = (
    pristine.candidate,
    pristine.app,
    pristine.native,
    pristine.driver_case,
)
layout, image_umask, supervised, image, configured = (
    pristine.layout,
    pristine.image_umask,
    pristine.supervised,
    pristine.image,
    pristine.configured,
)
pytestmark = pristine.pytestmark


@pytest.fixture
def launch_case(native, monkeypatch):
    native.cancel_notices = []

    def observe(notice):
        s = native
        assert s.driver.native.cancel_attempted and not s.driver.native.retired
        assert s.driver.recording is s.run.begin_owner is None
        assert not s.driver.recording_attempted and not s.run.begin_attempted
        assert s.ledger.state.count == 1 and not s.ledger.state.closed
        assert s.journal.machine.state.authorization_generation is None
        assert not s.run.client.closed and not s.run.client.begun
        assert notice.history == tuple(m.base.encode(e) for e in s.journal.entries)
        s.cancel_notices.append(notice)
        return s.cancel_observe(notice)

    native.driver_native_observer = observe
    yield from driver.launch_case.__wrapped__(native, monkeypatch)


@pytest.mark.parametrize("close_lost", [False, True])
def test_selected_pre_cancel_observation_never_constructs_start_or_renews_clock(
    driver_case, monkeypatch, close_lost
):
    s = driver_case
    s.cancel_observe = lambda notice: notice.receipt
    original = s.plan.raw, s.plan.lease, s.startup.clock

    def action():
        begins.synthetic_actors(s, monkeypatch)
        pristine.prepare(s, monkeypatch, close_lost=close_lost)
        assert len(s.cancel_notices) == 1
        assert s.driver.recording is s.run.begin_owner is None
        assert not s.run.begin_attempted and not s.driver.recording_attempted
        with pytest.raises(m.operator.UnconfirmedOperator):
            s.driver.cancel_native()
        assert len(s.cancel_notices) == 1

    assert driver.run(s, monkeypatch, action, record=False).phase == "complete"
    assert (s.plan.raw, s.plan.lease, s.startup.clock) == original
    assert s.ledger.state.count == 1 and s.ledger.state.expected is None
    assert s.journal.machine.state.recording_outcome == "not_attempted"
    assert s.cycle.created == [pristine.platform.h.CONTROL["starting_normal"]]
    driver.assert_owners(s)


@pytest.mark.parametrize(
    "fault",
    [
        "lost",
        "wrong",
        "actors",
        "ready",
        "source",
        "callback",
        "late",
        "history",
        "ledger",
        "reentrant_cancel",
        "reentrant_begin",
        "interrupt",
    ],
)
def test_uncertain_pre_cancel_capture_consumes_slot_and_cannot_restore(
    driver_case, monkeypatch, fault
):
    s = driver_case
    offset, real = [0], time.monotonic
    applied = []
    monkeypatch.setattr(time, "monotonic", lambda: real() + offset[0])

    def observe(notice):
        if fault == "lost":
            applied.append(fault)
            raise OSError("PRIVATE lost pre-cancel acknowledgment")
        if fault == "interrupt":
            applied.append(fault)
            raise KeyboardInterrupt
        if fault == "wrong":
            applied.append(fault)
            return "0" * 64
        if fault == "actors":
            actors = s.run.ready.processes.actors
            s.run.ready.processes.actors = actors[:-1] + (
                replace(actors[-1], start_ticks=actors[-1].start_ticks + 1),
            )
        elif fault == "ready":
            s.run.ready.ready_raw += b" changed"
        elif fault == "source":
            s.container["Config"]["Cmd"][-1] = "0" * 32
        elif fault == "callback":
            s.driver.native_observer = lambda value: value.receipt
        elif fault == "late":
            offset[0] = 2.1
        elif fault == "history":
            s.journal.append(dict(kind="finish", now=s.service._now(), boot_id=s.plan.boot))
        elif fault == "ledger":
            s.ledger._poisoned = True
        elif fault == "reentrant_cancel":
            applied.append(fault)
            s.driver.cancel_native()
        elif fault == "reentrant_begin":
            applied.append(fault)
            s.driver.start_recording()
        applied.append(fault)
        return notice.receipt

    s.cancel_observe = observe

    def action():
        begins.synthetic_actors(s, monkeypatch)
        if fault == "interrupt":
            with pytest.raises(KeyboardInterrupt):
                s.driver.cancel_native()
        else:
            assert not s.driver.cancel_native()
        offset[0] = 0
        assert applied == [fault], "The intended mutation must actually execute"
        assert len(s.cancel_notices) == 1
        assert s.driver.native.uncertain and s.driver.native.cancel_attempted
        assert s.run.failed and s.run.client.closed
        assert s.driver.recording is s.run.begin_owner is None
        assert not s.run.begin_attempted and not s.driver.recording_attempted
        assert s.ledger.state.count == 1 and s.ledger.state.expected is None
        assert s.journal.machine.state.authorization_generation is None
        # Original callback identity is a permanent custody requirement. Its
        # substitution stops the driver; all other failures keep clock expiry.
        if fault != "callback":
            with pytest.raises(m.operator.UnconfirmedOperator):
                s.driver.cancel_native()

    if fault in ("callback", "reentrant_begin"):
        with pytest.raises(m.operator.UnconfirmedOperator):
            driver.run(s, monkeypatch, action, record=False)
    else:
        result = driver.run(
            s,
            monkeypatch,
            action,
            later=lambda _: driver.expire(s, monkeypatch),
            record=False,
        )
        assert result.phase == "review"
    assert len(s.cancel_notices) == 1
    assert applied == [fault]
    assert not s.driver.native.recovery_attempted and s.driver.native.reader is None
    assert s.driver.recording is None and s.ledger.state.count == 1


@pytest.mark.parametrize("fault", ["source", "ledger", "ready", "begin_owner", "expired"])
def test_pre_cancel_invalid_original_evidence_never_calls_observer(driver_case, monkeypatch, fault):
    s = driver_case
    s.cancel_observe = lambda notice: notice.receipt
    offset, real = [0], time.monotonic
    monkeypatch.setattr(time, "monotonic", lambda: real() + offset[0])

    def action():
        begins.synthetic_actors(s, monkeypatch)
        if fault == "source":
            s.container["Config"]["Cmd"][-1] = "0" * 32
        elif fault == "ledger":
            s.ledger._poisoned = True
        elif fault == "ready":
            s.run.ready.ready_raw += b" changed"
        elif fault == "begin_owner":
            s.run.begin_owner = object()
        elif fault == "expired":
            offset[0] = s.run.ready.ready_by - real() + 1
        assert not s.driver.cancel_native()
        offset[0] = 0
        assert s.driver.native.uncertain and s.driver.native.cancel_attempted
        assert s.run.failed and s.run.client.closed
        assert not s.cancel_notices
        assert s.ledger.state.count == 1 and not s.ledger.state.closed

    assert (
        driver.run(
            s,
            monkeypatch,
            action,
            later=lambda _: driver.expire(s, monkeypatch),
            record=False,
        ).phase
        == "review"
    )
    assert not s.cancel_notices and not s.driver.native.recovery_attempted
    assert s.driver.recording is None
    assert s.journal.machine.state.authorization_generation is None
    driver.assert_owners(s)
