"""Published startup through idle service, with explicit synthetic host boundaries.

Declaration/claim/acceptance, original local clock, journals and notice files
are real. The pre-existing host fixture supplies SYNTHETIC installed metadata,
Engine, cached normal health and process receipts. This is an assembly test,
not installed qualification, a new service command or scanner/audio acceptance.
"""

import json
import os
from dataclasses import replace

import pytest

from . import test_supplemental_recording_idle_service as services
from . import test_supplemental_recording_service_startup as startups

m, startup = services.m, startups.m
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
) = (
    services.layout,
    services.tree,
    services.routing,
    services.projection,
    services.binding,
    services.directory,
    services.prepared,
    services.joined,
    services.before_handoff,
)


@pytest.fixture
def accepted(before_handoff, tmp_path, monkeypatch):
    s = before_handoff
    root, source = tmp_path / "startup-case", tmp_path / "startup-declaration"
    root.mkdir(mode=0o700)
    source.mkdir(mode=0o700)
    value = json.loads(s.plan.raw)
    times = value.pop("deadlines")
    value.pop("original_clock")
    template = startup.declaration.codec.decode(
        {
            "schema": 1,
            "kind": startup.declaration.codec.KIND,
            "plan": value,
            "budget": {
                "ready_seconds": int(times["ready_by"] - times["issued_at"]),
                "stop_seconds": int(times["stop_by"] - times["issued_at"]),
            },
        }
    )
    path = source / startup.declaration.NAME
    path.write_bytes(template.raw)
    path.chmod(0o600)
    monkeypatch.setattr(startup.declaration, "declaration_root", lambda _: source)
    monkeypatch.setattr(m.plans.Plan, "root", property(lambda _: root))
    with startup.declaration.Declaration(source, template.sha256) as original:
        owner = startup.Startup(original)
        try:
            # The synthetic fixture's independent preflight plan is NOT the
            # service plan. Its full host read precedes the one service origin;
            # never replace this baseline with a post-acceptance observation.
            s.baseline = s.before.read().observation
            assert not owner.used and owner.clock is None and list(root.iterdir()) == []
            owner.prepare()
            assert owner.poll() is None
            startups.submit(owner)
            assert owner.poll() is owner.original
            s.original = owner.accepted_input()
            s.plan = s.original.plan
            s.startup, s.declaration = owner, original
            s.startup_offer, s.borrowed_clock = owner.offer, owner.clock
            assert 0 <= s.plan.deadlines.issued_at - s.baseline.sampled_at <= 2
            assert s.plan.raw == template.preview(owner.clock.original).raw
            assert not (root / "journal").exists()
            yield s
        finally:
            owner.close()


@pytest.fixture
def service(accepted, monkeypatch):
    s = accepted
    root = s.plan.root
    (root / "journal").mkdir(mode=0o700)
    (root / "inbox").mkdir(mode=0o700)

    def candidate_files(selected, container):
        assert selected == s.projected.layout
        assert container is None or container["Name"] == "/app_" + m.base.CANDIDATE
        s.captures.append(m.base.CANDIDATE)
        return s.static

    monkeypatch.setattr(m.plans.host.candidate_static, "collect", candidate_files)
    with m.launch.bootstrap.Journal(root / "journal") as journal:
        journal.append(s.plan.preparation(s.baseline, s.projected))
        s.journal = journal

        def append(kind, **fields):
            # Later native/recording fixtures must append to THIS original
            # service journal, never the inherited preflight fixture journal.
            return journal.append(
                dict(
                    kind=kind,
                    now=m.plans.clock.read().boottime_ns / m.plans.clock.NS,
                    boot_id=s.plan.boot,
                    **fields,
                )
            )

        s.append = append

        def assemble():
            s.service = m.IdleService(
                s.original, s.projected, journal, s.docker, clock_witness=s.startup.clock
            )
            s.inbox, s.before = s.service.inbox, s.service.transfer
            return s.service.session

        with services.cancellation.owned_session(s, monkeypatch, assemble):
            try:
                yield s
            finally:
                s.service.close()
                # All service cleanup precedes startup/clock and declaration
                # cleanup, including exceptions and uncertain transitions.
                assert not s.startup.closed and not s.borrowed_clock.closed
                assert s.original.recheck() is s.plan and not s.declaration.closed


def test_independent_acceptance_is_not_operator_request_or_service_authority(accepted):
    s = accepted
    assert s.startup.accepted and s.startup_offer.accepted
    assert {p.name for p in s.plan.root.iterdir()} == {
        "startup-claim.json",
        "plan.json",
        "startup-acceptance.json",
    }
    assert not hasattr(s, "service") and not hasattr(s, "journal")
    assert s.baseline.sampled_at <= s.plan.deadlines.issued_at
    assert len(s.cached_calls) == 1 and s.idle_reads == 0


def test_actual_published_plan_and_original_baseline_assemble_passively(service):
    s = service
    assert s.service.original is s.startup.original is s.original
    assert s.service.clock_witness is s.startup.clock is s.borrowed_clock
    assert s.service.plan is s.plan and s.journal.machine.baseline == s.baseline
    assert s.journal.machine.created_at == s.plan.deadlines.issued_at
    assert s.journal.machine.hard_deadline == s.plan.deadlines.recover_by
    assert not s.engine.sent and not list(s.inbox.path.iterdir())
    assert len(s.journal.entries) == 1
    assert not s.service.native_attempted and not s.service.recording_attempted


@pytest.mark.parametrize("route", ["complete", "expiry", "interrupted", "lost_notice", "lost_read"])
def test_original_published_owner_survives_every_existing_idle_route(service, monkeypatch, route):
    s = service
    preserved = {
        name: (s.plan.root / name).read_bytes()
        for name in ("startup-claim.json", "plan.json", "startup-acceptance.json")
    }
    services.test_borrowed_startup_clock_survives_original_service_paths(s, monkeypatch, route)
    assert s.service.original is s.startup.original
    assert s.journal.machine.baseline == s.baseline
    assert s.journal.machine.created_at == s.plan.deadlines.issued_at
    assert s.journal.machine.hard_deadline == s.plan.deadlines.recover_by
    assert preserved == {name: (s.plan.root / name).read_bytes() for name in preserved}
    assert not s.startup.closed and not s.declaration.closed


@pytest.mark.parametrize("offset", [-3, 1])
def test_invalid_original_sample_cannot_be_replaced_by_acceptance(accepted, offset):
    s = accepted
    invalid = replace(s.baseline, sampled_at=s.plan.deadlines.issued_at + offset)
    with pytest.raises(m.plans.UnconfirmedPlan):
        s.plan.preparation(invalid, s.projected)
    assert not (s.plan.root / "journal").exists()
    assert not (s.plan.root / "inbox").exists()
    assert s.original.recheck() is s.plan and not s.borrowed_clock.closed


@pytest.mark.parametrize("field", ["jobs_idle", "core_running", "other_owners_stopped"])
def test_accepted_plan_cannot_make_bad_baseline_safe(accepted, field):
    s = accepted
    invalid = replace(s.baseline, **{field: False})
    with pytest.raises(m.plans.UnconfirmedPlan):
        s.plan.preparation(invalid, s.projected)
    assert not (s.plan.root / "journal").exists()
    assert s.startup.accepted and not s.borrowed_clock.closed


@pytest.mark.parametrize("part", ["normal_generation", "normal_pin", "files"])
def test_baseline_must_match_all_original_plan_pins(accepted, part):
    s = accepted
    if part == "files":
        invalid = replace(s.baseline, files=replace(s.baseline.files, evidence_sha256="f" * 64))
    else:
        key = "generation" if part == "normal_generation" else "pin"
        invalid = replace(s.baseline, normal=replace(s.baseline.normal, **{key: "f" * 64}))
    with pytest.raises(m.plans.UnconfirmedPlan):
        s.plan.preparation(invalid, s.projected)
    assert not (s.plan.root / "journal").exists()


def test_service_does_not_poll_acceptance_again_or_extend_the_offer(service, monkeypatch):
    s = service
    original_clock_read = m.plans.clock.read
    old_deadline, old_plan = s.startup_offer.deadline, s.plan.raw

    def after_offer():
        # Explicit simulated elapsed time, NOT a real timing qualification.
        value = original_clock_read()
        amount = 20 * m.plans.clock.NS
        return replace(
            value,
            before_ns=value.before_ns + amount,
            after_ns=value.after_ns + amount,
            boottime_ns=value.boottime_ns + amount,
        )

    def forbidden(*_):
        pytest.fail("Acceptance was retried after service assembly")

    monkeypatch.setattr(m.plans.clock, "read", after_offer)
    monkeypatch.setattr(s.startup, "poll", forbidden)
    monkeypatch.setattr(s.startup, "accepted_input", forbidden)
    services.test_single_owner_polls_explicit_request_cancel_and_normal_restoration(s)
    assert s.startup_offer.deadline == old_deadline and s.plan.raw == old_plan
    assert s.journal.machine.baseline == s.baseline and not s.borrowed_clock.closed


@pytest.mark.parametrize("problem", [OSError, KeyboardInterrupt, SystemExit])
def test_cleanup_exception_does_not_release_startup_before_service_handles(service, problem):
    s = service
    closed = []

    def uncertain():
        assert not s.borrowed_clock.closed and not s.startup.closed
        os.fstat(s.borrowed_clock.fd)
        closed.append(True)
        raise problem("private fixture failure")

    s.service._cleanup.append(uncertain)
    expected = m.UnconfirmedOperator if problem is OSError else problem
    with pytest.raises(expected):
        s.service.close()
    assert closed == [True] and s.service.closed
    assert s.session.processes.closed and s.inbox.closed
    assert s.journal.fd >= 0 and s.original.recheck() is s.plan
    assert not s.borrowed_clock.closed and not s.startup.closed
