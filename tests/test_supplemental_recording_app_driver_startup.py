"""Actual declaration/offer/acceptance/assembly through the original App driver.

Preflight host observation and initial App transfer, idle publication/metadata,
Engine/native return/exit/file facts remain synthetic. The original accepted
Startup, its plan and clock, service/driver and all three recovery joins are
actual. This is NOT installed source, supervision or hardware qualification.
"""

from contextlib import ExitStack

import pytest

from . import test_supplemental_recording_app_driver_pristine as pristine
from . import test_supplemental_recording_app_launch as launch_cases
from . import test_supplemental_recording_service_startup as startups

failure, driver = pristine.failure, pristine.driver
m, base, launch = driver.m, driver.base, driver.launch
app, native, driver_case = driver.app, driver.native, driver.driver_case
layout, image_umask, supervised, image, configured = (
    driver.layout,
    driver.image_umask,
    driver.supervised,
    driver.image,
    driver.configured,
)
pytestmark = pytest.mark.parametrize("candidate", ["app_native"], indirect=True)


@pytest.fixture
def candidate(supervised, image, configured, monkeypatch, request, tmp_path):
    startup = m.inputs.publication.startup
    root, source = tmp_path / "host-execution", tmp_path / "declaration"
    root.mkdir(mode=0o700)
    source.mkdir(mode=0o700)
    monkeypatch.setattr(launch.plans.Plan, "root", property(lambda self: root))
    monkeypatch.setattr(startup.declaration, "declaration_root", lambda _: source)
    with ExitStack() as cleanup:

        def decode(value, projected):
            value = value.copy()
            limits = value.pop("deadlines")
            value.pop("original_clock")
            template = startup.declaration.codec.decode(
                dict(
                    schema=1,
                    kind=startup.declaration.codec.KIND,
                    plan=value,
                    budget=dict(
                        ready_seconds=int(limits["ready_by"] - limits["issued_at"]),
                        stop_seconds=int(limits["stop_by"] - limits["issued_at"]),
                    ),
                )
            )
            path = source / startup.declaration.NAME
            path.write_bytes(template.raw)
            path.chmod(0o600)
            declaration = cleanup.enter_context(
                startup.declaration.Declaration(source, template.sha256)
            )
            owner = startup.Startup(declaration)
            cleanup.callback(owner.close)

            def preflight(reader):
                # Explicit lower host/static/native metadata boundary only.
                p = reader.plan
                now = launch.plans.clock.read().boottime_ns / launch.plans.clock.NS
                files = launch.bootstrap.recording.Files(
                    p.candidate.contract.sha256, "pristine", p.candidate.contract.baseline_sha256
                )
                observed = launch.bootstrap.recording.Observation(
                    now,
                    base.App(p.normal.pin, "running", p.normal_generation, True, False),
                    base.App(p.candidate.pin, "stopped"),
                    True,
                    True,
                    True,
                    files,
                )
                reader.used = True
                return launch.bootstrap.recovery.Sample(p.boot, now, observed)

            monkeypatch.setattr(launch.PreHandoffHost, "read", preflight)
            owner.prepare_service(projected, launch.plans.ordinary.Docker())
            assert owner.poll() is None
            startups.submit(owner)
            assert owner.poll() is owner.original
            assert owner.accepted_input() is owner.original
            decode.owner = owner
            return owner.original.plan

        for s in driver.candidates.setup_candidate(
            supervised, image, configured, monkeypatch, request, decode=decode
        ):
            s.accepted_startup, s.accepted_owner = True, decode.owner
            s.case_plan = decode.owner.original
            assert s.projected is decode.owner.projected
            yield s


@pytest.fixture
def launch_case(native, monkeypatch):
    owner = native.accepted_owner
    # The existing native-input fixture supplies real private files but its
    # idle publication provenance is still synthetic. Do not claim otherwise.
    owner.app_idle_publication_used = True
    yield from launch_cases.setup_launch_case(native, monkeypatch, owner=owner)


@pytest.mark.parametrize("route", ["finalized", "preserved", "pristine"])
def test_accepted_original_startup_drives_all_recovery_routes(driver_case, monkeypatch, route):
    s = driver_case
    owner = s.startup
    originals = owner.original, owner.clock, owner.offer, owner.publisher, owner.reader
    callbacks = s.original_callbacks

    def renewed(*args, **kwargs):
        pytest.fail("An assembled service cannot repoll or renew its startup offer")

    monkeypatch.setattr(owner, "poll", renewed)
    monkeypatch.setattr(owner, "accepted_input", renewed)
    for method in ("inspect", "accept", "_check"):
        monkeypatch.setattr(owner.offer, method, renewed)
    action = {
        "finalized": lambda: driver.prepare_recovery(s, monkeypatch),
        "preserved": lambda: failure.prepare_preservation(s, monkeypatch),
        "pristine": lambda: pristine.prepare(s, monkeypatch),
    }[route]
    assert driver.run(s, monkeypatch, action, record=route != "pristine").phase == "complete"
    assert owner.accepted and owner.service_used and owner._service_active
    assert not owner.failed and not owner.closed and not owner.clock.closed
    owner._input()
    owner._binding()
    assert originals == (owner.original, owner.clock, owner.offer, owner.publisher, owner.reader)
    assert s.service.clock_witness is owner.clock and s.service.original is owner.original
    assert s.original_callbacks == callbacks
    assert (
        s.journal.machine.state.recording_outcome
        == {"finalized": "verified", "preserved": "unconfirmed", "pristine": "not_attempted"}[route]
    )
    driver.assert_owners(s)
