"""Published startup through idle service, with explicit synthetic host boundaries.

Declaration/claim/acceptance, original local clock, journals and notice files
are real. The pre-existing host fixture supplies SYNTHETIC installed metadata,
Engine, cached normal health and process receipts. This is an assembly test,
not installed qualification, a new service command or scanner/audio acceptance.
"""

import json
import os
from contextlib import ExitStack
from dataclasses import replace

import pytest

from . import test_supplemental_recording_idle_service as services
from . import test_supplemental_recording_service_startup as startups
from ._supplemental_fixture_budget import integer_budget

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


@pytest.fixture(
    params=["external_baseline", "template_baseline", "owned_service", "persisted_service"]
)
def accepted(before_handoff, tmp_path, monkeypatch, request):
    s = before_handoff
    s.owned_service = request.param in ("owned_service", "persisted_service")
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
            "budget": integer_budget(times),
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
            assert not owner.used and owner.clock is None and list(root.iterdir()) == []
            if request.param == "external_baseline":
                # The older independent preflight plan is NOT the service
                # plan. Its full host read precedes the one service origin.
                s.baseline = s.before.read().observation
                owner.prepare()
            else:
                command = m.launch.normal_read.Sample(s.plan, s.docker).command

                def cached(docker, seal, selected, generation):
                    # Only the synthetic transport boundary is replaced. The
                    # new complete reader derives its own preflight plan from
                    # the retained template and a separate temporary clock.
                    assert docker is s.docker and seal.pin == s.plan.normal.pin
                    assert selected == command and generation == s.plan.normal_generation
                    assert owner.clock is None and not list(root.iterdir())
                    s.cached_calls.append((seal.slug, generation))
                    s.after_cached()
                    return m.plans.ordinary.NativeState(generation, *s.host.native_state)

                monkeypatch.setattr(m.launch.normal_read.cached, "_read_probe", cached)
                if request.param == "persisted_service":
                    # Already sealed ORIGINAL inventory, not a fresh snapshot.
                    # The fixture aliases real temporary files to host paths;
                    # no actual /mnt/data source is opened or provisioned.
                    manifest = tmp_path / "original-manifest"
                    manifest.mkdir(mode=0o700)
                    stored = s.projected.host
                    protected = startup.plans.projection.recording
                    raw = protected.manifest_bytes(
                        stored.baseline,
                        stored.writer,
                        stored.contract.audio_endpoint_sha256,
                        maximum_recording_seconds=stored.contract.maximum_recording_seconds,
                    )
                    path = manifest / "baseline.json"
                    path.write_bytes(raw)
                    path.chmod(0o600)
                    owner.prepare_service_from_baseline(manifest, stored.manifest_sha256, s.docker)
                    s.projected = owner.projected
                else:
                    owner.prepare_service(s.projected, s.docker)
                s.baseline = owner.baseline
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
def service(accepted, monkeypatch, request):
    s = accepted
    root = s.plan.root
    s.dispatch_notices, s.dispatch_fault = [], None

    def observe(notice):
        # Synthetic observer receipt only. Actual separate-peer custody and
        # lost acknowledgments are exercised by service_cli_process tests.
        assert s.service.dispatch is s.session.dispatch
        assert s.service.clock_witness is s.startup.clock
        assert notice.history == tuple(m.base.encode(item) for item in s.journal.entries)
        s.dispatch_notices.append(notice)
        if notice.stage == s.dispatch_fault:
            raise OSError("PRIVATE missing evidence acknowledgment")
        return notice.receipt

    observer = observe if getattr(request, "param", None) == "observed" else None

    def candidate_files(selected, container):
        assert selected == s.projected.layout
        assert container is None or container["Name"] == "/app_" + m.base.CANDIDATE
        s.captures.append(m.base.CANDIDATE)
        return s.static

    monkeypatch.setattr(m.plans.host.candidate_static, "collect", candidate_files)
    with ExitStack() as resources:
        if not s.owned_service:
            (root / "journal").mkdir(mode=0o700)
            (root / "inbox").mkdir(mode=0o700)
            s.journal = resources.enter_context(m.launch.bootstrap.Journal(root / "journal"))
            s.journal.append(s.plan.preparation(s.baseline, s.projected))

        def assemble():
            if s.owned_service:
                s.service = resources.enter_context(
                    s.startup.idle_service(s.docker, dispatch_observer=observer)
                )
                s.journal = s.service.journal
            else:
                s.service = m.IdleService(
                    s.original,
                    s.projected,
                    s.journal,
                    s.docker,
                    clock_witness=s.startup.clock,
                    dispatch_observer=observer,
                )
            journal = s.journal

            def append(kind, **fields):
                # Never append to the inherited preflight fixture's journal.
                return journal.append(
                    dict(
                        kind=kind,
                        now=m.plans.clock.read().boottime_ns / m.plans.clock.NS,
                        boot_id=s.plan.boot,
                        **fields,
                    )
                )

            s.append = append
            s.inbox, s.before = s.service.inbox, s.service.transfer
            return s.service.session

        with services.cancellation.owned_session(s, monkeypatch, assemble):
            try:
                yield s
            finally:
                if not s.owned_service:
                    s.service.close()
    # The production assembly context also owns journal/Inbox cleanup. The
    # borrowed startup clock and declaration outlive every service resource.
    assert s.service.closed and s.journal.fd == -1
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


@pytest.mark.parametrize("service", ["observed"], indirect=True)
def test_same_accepted_owner_keeps_evidence_hook_through_all_four_app_commands(service):
    s = service
    originals = s.session, s.session.executor, s.service.dispatch, s.service.processes
    assert not s.dispatch_notices
    services.test_single_owner_polls_explicit_request_cancel_and_normal_restoration(s)
    assert originals == (s.session, s.session.executor, s.service.dispatch, s.service.processes)
    assert [(n.phase, n.stage) for n in s.dispatch_notices] == [
        (phase, stage)
        for phase in (
            "stopping_normal",
            "starting_candidate",
            "stopping_candidate",
            "starting_normal",
        )
        for stage in ("before_create", "before_start")
    ]
    assert not s.startup.closed and not s.borrowed_clock.closed
    assert s.journal.machine.state.recording_outcome == "not_attempted"


@pytest.mark.parametrize("service", ["observed"], indirect=True)
@pytest.mark.parametrize("stage", ["before_create", "before_start"])
def test_lost_ack_in_original_accepted_service_cannot_start_or_retry(service, stage):
    s = service
    s.dispatch_fault = stage
    services.publish(s, "request")
    # The executor retains an uncertain intent for reconciliation rather than
    # throwing or silently retrying it. Exercise two bounded polls, not a full
    # fifteen-minute recovery lifetime with a no-op test wait.
    result = s.service.coordinator.poll(lambda _: pytest.fail("Unexpected nested wait"))
    assert result.phase == "stopping_normal" and result.outcome == "dispatch_unconfirmed"
    assert not s.engine.sent
    assert s.service.dispatch.used == {"stopping_normal"}
    assert len(s.dispatch_notices) == (1 if stage == "before_create" else 2)
    notices = tuple(s.dispatch_notices)
    again = s.service.coordinator.poll(lambda _: pytest.fail("Unexpected nested wait"))
    assert again.phase == "stopping_normal" and tuple(s.dispatch_notices) == notices
    entries = tuple(s.journal.entries)
    with pytest.raises(m.base.UnsafeHandoff):
        s.service.dispatch(("ha", "apps", "stop", m.base.NORMAL, "--raw-json"), s.plan.case)
    assert tuple(s.journal.entries) == entries and not s.engine.sent
    assert s.journal.machine.state.recording_outcome == "not_attempted"
    assert not s.startup.closed and not s.borrowed_clock.closed


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
