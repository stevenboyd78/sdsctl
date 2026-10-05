"""Actual accepted Startup and borrowed original service journal/clock.

The fixed native AppLaunch/Start/finalized chain runs while the original accepted
service assembly holds its lifetime. The service action loop, original CLI/Link
exchange and recovery are NOT exercised here. Platform, idle publication and
host/probe metadata remain synthetic; no installed or physical test is claimed.
"""

from contextlib import contextmanager
from dataclasses import replace

import pytest

from . import test_supplemental_recording_app_actual_launch as actual
from . import test_supplemental_recording_app_driver_startup as startup

bwrap, staged, mapped = actual.bwrap, actual.staged, actual.mapped
layout, image_umask, supervised = actual.layout, actual.image_umask, actual.supervised
image, configured = actual.image, actual.configured
app, native, original_launch = actual.app, actual.native, actual.original_launch
pytestmark = actual.pytestmark
preflight = actual.actual.fixed.preflight


@pytest.fixture
def candidate(supervised, image, configured, monkeypatch, request, tmp_path, staged, mapped):
    yield from startup.setup_candidate(
        supervised,
        image,
        configured,
        monkeypatch,
        request,
        tmp_path,
        source_tree=staged.layout,
        projection_factory=lambda protected, case: preflight.seed_projection(
            mapped, protected, case
        ),
    )


@pytest.fixture
def launch_case(native, monkeypatch, mapped):
    owner = native.accepted_owner
    # The accepted owner and service are actual. Original idle publication and
    # App/Engine metadata are still the explicitly synthetic lower fixture.
    owner.app_idle_publication_used = True
    with contextmanager(startup.launch_cases.setup_launch_case)(
        native, monkeypatch, owner=owner
    ) as s:
        s.spec = replace(
            s.spec,
            host="127.0.0.1",
            control_port=mapped.scanner.socket.getsockname()[1],
            rtsp_port=mapped.rtsp.port,
            rtp_bind_address="127.0.0.1",
            rtp_bind_port=0,
            read_window_seconds=1,
            max_read_attempts=2,
            ready_timeout=120,
        )
        s.publish = lambda: preflight.m.publish_launch(
            owner, s.original, specification=s.spec, profile_sha256=mapped.profile
        )

        @contextmanager
        def service_factory(state):
            assert state is s
            with owner.idle_service(s.docker) as service:
                s.assembled_service = service
                yield service

        s.service_factory = service_factory
        yield s


@pytest.mark.parametrize("original_launch", ["finalized"], indirect=True)
def test_original_accepted_startup_borrows_clock_and_journal_through_actual_native_exit(
    original_launch, mapped, request, monkeypatch
):
    s, io = original_launch
    owner, service = s.accepted_owner, s.assembled_service
    original = owner.original, owner.clock, owner.offer, service.session, s.journal
    assert owner is s.startup and owner.accepted and owner.service_used
    assert service.clock_witness is owner.clock and service.original is owner.original
    assert owner.original.plan is s.plan and service.journal is s.journal

    def no_renewal(*args, **kwargs):
        pytest.fail("Original service custody must not repoll or renew its acceptance")

    monkeypatch.setattr(owner, "poll", no_renewal)
    monkeypatch.setattr(owner, "accepted_input", no_renewal)
    actual.test_original_app_authorization_reaches_actual_recording_without_inferred_recovery(
        original_launch, mapped, request
    )
    assert original == (owner.original, owner.clock, owner.offer, service.session, s.journal)
    assert service.session.journal is s.journal
    assert not owner.closed and not owner.clock.closed and not service.closed
    assert not service.used  # Assembly custody is NOT the service action/recovery loop.
    assert not s.witness.exited() and s.journal.machine.state.recording_outcome == "unconfirmed"
