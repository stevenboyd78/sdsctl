"""Accepted Startup's retained clock must join its equally decoded case plan.

Actual Startup/ClockWitness and authenticated anonymous peer transport are used.
The peer echoes a protocol receipt; this is NOT independent CLI/native evidence,
active scope, App launch or installed runtime qualification.
"""

from dataclasses import replace

import pytest

from . import test_supplemental_recording_app_actual_service as service
from . import test_supplemental_recording_service_cli_channel as channel

bwrap, staged, mapped = service.bwrap, service.staged, service.mapped
layout, image_umask, supervised = service.layout, service.image_umask, service.supervised
image, configured = service.image, service.configured
candidate, app, native, launch_case, service_case = (
    service.candidate,
    service.app,
    service.native,
    service.launch_case,
    service.service_case,
)
pytestmark = service.pytestmark


def test_original_accepted_clock_is_borrowed_without_reconstruction_or_renewal(
    service_case, monkeypatch
):
    s = service_case
    timer, plan = s.startup.clock, s.plan
    assert s.service.clock_witness is timer
    assert timer.original is not plan.original_clock
    assert channel.m.plans._same_plan_value(timer.original, plan.original_clock)
    original = timer, timer.original, timer.fd, plan.raw, s.service.clock_witness
    with channel.protocol(monkeypatch, plan=plan, timer=timer) as c:
        current = channel.notice(plan)
        channel.send(c.child, dict(mode="echo"))
        assert c.link.observe(current) == current.receipt
        assert channel.line(c.child) == b"sent"
        assert c.link.timer is timer and c.link.sequence == 1
        assert not s.driver.used and s.journal.machine.state.authorization_generation is None
    assert not timer.closed and not timer.failed
    assert original == (timer, timer.original, timer.fd, plan.raw, s.service.clock_witness)
    s.service._context()


@pytest.mark.parametrize("fault", ["origin", "clock", "value", "link_origin"])
def test_borrowed_original_clock_cannot_be_replaced_after_link_capture(
    service_case, monkeypatch, fault
):
    s = service_case
    timer, plan = s.startup.clock, s.plan
    origin = timer.original
    with channel.protocol(monkeypatch, plan=plan, timer=timer) as c:
        replacement = None
        try:
            if fault == "clock":
                replacement = channel.m.clock.ClockWitness(plan.original_clock)
                c.link.timer = replacement
            elif fault == "link_origin":
                c.link._clock_origin = replace(origin)
            else:
                timer.original = replace(
                    origin, **({"boottime_ns": origin.boottime_ns + 1} if fault == "value" else {})
                )
            channel.denied(lambda: c.link.observe(channel.notice(plan)))
            assert c.link.failed and c.link.sequence == 0
            assert s.journal.machine.state.authorization_generation is None
        finally:
            # Restore only the mutated test field for the ORIGINAL service's
            # fixture cleanup; the failed Link stays consumed and is not retried.
            timer.original = origin
            if replacement is not None:
                replacement.close()
    assert not timer.closed


def test_nonidentical_clock_value_is_refused_before_link_acquires_handles(
    service_case, monkeypatch
):
    s = service_case
    original = s.startup.clock.original
    changed = replace(original, boottime_ns=original.boottime_ns + 1)
    witness = channel.m.clock.ClockWitness(changed)
    try:
        with (
            pytest.raises(channel.m.UnconfirmedExchange),
            channel.protocol(monkeypatch, plan=s.plan, timer=witness),
        ):
            pytest.fail("Changed original clock must not be admitted")
        assert not witness.closed and not s.startup.clock.closed
        assert s.startup.clock.original is original
    finally:
        witness.close()
