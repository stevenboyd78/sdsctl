"""Actual accepted Startup/clock/assembly; synthetic host/Engine transport.

No native generation or launch is claimed. This separately verifies that the
new App input handoff borrows the real service's continuing original custody.
"""

import pytest

from . import test_supplemental_recording_app_launch as launches
from . import test_supplemental_recording_idle_continuity as clocks
from . import test_supplemental_recording_startup_assembly as assembly

m = launches.m
(
    layout,
    tree,
    routing,
    projection,
    binding,
    directory,
    prepared,
    joined,
    before_handoff,
    service_case,
) = (
    assembly.layout,
    assembly.tree,
    assembly.routing,
    assembly.projection,
    assembly.binding,
    assembly.directory,
    assembly.prepared,
    assembly.joined,
    assembly.before_handoff,
    assembly.service_case,
)


def test_real_service_custody_survives_offer_expiry_without_reacceptance(service_case, monkeypatch):
    s = service_case
    owner = assembly.accept(s)
    owner.app_idle_publication_used = True  # Publication has its own real-file tests.
    plan, clock, deadline = owner.original.plan, owner.clock, owner.offer.deadline
    with owner.idle_service(s.docker) as service:
        assert m._service_custody(owner, plan) is None
        with monkeypatch.context() as patch:
            clocks.advance(patch, 20)
            assert m.time.monotonic() > deadline
            assert m._service_custody(owner, plan) is None
            assert service.clock_witness is owner.clock is clock
            assert owner.offer.deadline == deadline and owner.original.plan is plan
            assert not service.used  # Borrowing input custody runs no service/action.
    assert service.closed and not clock.closed
    assert len(s.cached_calls) == 1  # Only the original preparation read.
    with pytest.raises(m.qualification.launch.UnconfirmedHostLaunch):
        m._service_custody(owner, plan)  # Retired borrower is never reanimated.


def test_accepted_owner_alone_does_not_supply_service_custody(service_case):
    owner = assembly.accept(service_case)
    owner.app_idle_publication_used = True
    with pytest.raises(m.qualification.launch.UnconfirmedHostLaunch):
        m._service_custody(owner, owner.original.plan)


def test_original_deadline_still_refuses_late_new_assembly(service_case, monkeypatch):
    s = service_case
    owner = assembly.accept(s)
    owner.app_idle_publication_used = True
    clocks.advance(monkeypatch, 20)
    with pytest.raises(assembly.m.UnconfirmedStartup), owner.idle_service(s.docker):
        pytest.fail("Expired offer must not construct a new service borrower")
    assert owner.closed and owner.clock.closed
    assert not (s.root / "journal").exists()
