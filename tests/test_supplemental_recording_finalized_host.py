"""Real authorization/journal, synthetic metadata/files/exit.

Separate original observer tests cover the complete metadata collectors. No
installed-platform, current native-health or independent recovery claim.
"""

from dataclasses import replace
from threading import Thread
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_authorized_finalized as authority

m = authority.m
layout, tree, routing, projection, binding, directory, prepared, setup, joined, begun, completed = (
    authority.layout,
    authority.tree,
    authority.routing,
    authority.projection,
    authority.binding,
    authority.directory,
    authority.prepared,
    authority.setup,
    authority.joined,
    authority.begun,
    authority.completed,
)


@pytest.fixture
def host(completed, monkeypatch):
    c = completed
    authority.publish(c)
    c.collected.files = m.launch.bootstrap.recording.Files(
        c.s.plan.candidate.contract.sha256, "finalized", "f" * 64, c.s.run.pins.generation
    )
    c.host = m.FinalizedHost(c.reader)
    c.metadata_calls, c.identity_calls = [], []

    def container(name):
        c.identity_calls.append(name)
        return dict(Id=c.s.run.pins.init.container_id)

    monkeypatch.setattr(c.host.docker, "container", container)

    def observer():
        def read():
            c.metadata_calls.append("read")
            boot, began_at = c.host._clock()
            return m._StaticHostSnapshot(
                boot,
                began_at,
                c.host._clock()[1],
                m.base.App(c.s.plan.normal.pin, "stopped"),
                m.base.App(c.s.plan.candidate.pin, "running", c.s.run.pins.generation),
                True,
                True,
            )

        return SimpleNamespace(read=read)

    monkeypatch.setattr(c.host, "_observer", observer)
    return c


def denied(c):
    authority.history.begins.denied(c.host.read)
    assert c.host.failed and not c.operator.closed and c.s.journal.fd >= 0


def test_current_metadata_joins_original_files_without_policy_changes(host):
    c = host
    original = c.s.journal.machine.state, tuple(c.s.journal.entries), c.s.plan.raw
    for _ in range(2):
        sample = c.host.read()
        assert sample.observation.files is c.collected.files
        assert sample.observation.candidate.healthy is None
        assert sample.observation.candidate.recording is None
        assert sample.observation.normal.state == "stopped"
        assert sample.observation.jobs_idle and sample.observation.other_owners_stopped
        assert sample.observation.sampled_at <= sample.now
    assert c.metadata_calls == ["read", "read"]
    assert c.identity_calls == ["app_" + m.base.CANDIDATE] * 4
    assert original == (c.s.journal.machine.state, tuple(c.s.journal.entries), c.s.plan.raw)


@pytest.mark.parametrize("state", ["stopped", "unknown"])
def test_observed_state_is_not_invented_from_finalized_files(host, monkeypatch, state):
    c = host
    original = c.host._observer

    def observer():
        def read():
            value = original().read()
            return replace(value, candidate=m.base.App(value.candidate.pin, state))

        return SimpleNamespace(read=read)

    monkeypatch.setattr(c.host, "_observer", observer)
    sample = c.host.read()
    assert sample.observation.candidate.state == state
    assert sample.observation.candidate.healthy is None
    assert not c.s.prepared.witness.exited()  # Explicit metadata fixture only.


@pytest.mark.parametrize("change", ["normal_pin", "candidate_pin", "jobs", "other_owners"])
def test_changed_platform_evidence_stays_visible_to_existing_policy(host, monkeypatch, change):
    c, original = host, host.host._observer

    def observer():
        def read():
            value = original().read()
            if change.endswith("_pin"):
                key = change.removesuffix("_pin")
                return replace(value, **{key: replace(getattr(value, key), pin="a" * 64)})
            return replace(value, **{"jobs_idle" if change == "jobs" else "other_stopped": False})

        return SimpleNamespace(read=read)

    monkeypatch.setattr(c.host, "_observer", observer)
    sample = c.host.read()
    if change.endswith("_pin"):
        assert getattr(sample.observation, change.removesuffix("_pin")).pin == "a" * 64
    else:
        assert not getattr(
            sample.observation, "jobs_idle" if change == "jobs" else "other_owners_stopped"
        )


@pytest.mark.parametrize("where", ["before", "after"])
def test_same_name_replacement_candidate_cannot_supply_a_sample(host, monkeypatch, where):
    c = host
    calls = []

    def changed(name):
        calls.append(name)
        return dict(
            Id="a" * 64 if where == "before" or len(calls) == 2 else c.s.run.pins.init.container_id
        )

    monkeypatch.setattr(c.host.docker, "container", changed)
    denied(c)


@pytest.mark.parametrize("fault", ["boot", "generation", "health", "recording", "late"])
def test_incoherent_or_late_metadata_never_returns_a_sample(host, monkeypatch, fault):
    c, original = host, host.host._observer

    def observer():
        def read():
            value = original().read()
            if fault == "late":
                authority.history.continuity.advance(monkeypatch, 3)
                return value
            if fault == "boot":
                return replace(value, boot="a" * 32)
            key = "healthy" if fault == "health" else fault
            return replace(
                value,
                candidate=replace(
                    value.candidate, **{key: "a" * 64 if key == "generation" else False}
                ),
            )

        return SimpleNamespace(read=read)

    monkeypatch.setattr(c.host, "_observer", observer)
    denied(c)


def test_close_only_retires_this_observer(host):
    c = host
    c.host.close()
    c.host.close()
    denied(c)
    assert not c.reader.closed and not c.s.start.closed
    assert c.reader.read() is c.collected


def test_no_metadata_reader_before_actual_exit_publication(completed):
    c = completed
    authority.history.begins.denied(lambda: m.FinalizedHost(c.reader))
    assert not c.reader.failed and not c.reader.closed and not c.operator.closed
    authority.publish(c)


@pytest.mark.parametrize("fault", ["plan", "route", "reader_closed", "caller_closed"])
def test_original_binding_cannot_be_replaced_or_retired(host, fault):
    c = host
    if fault == "plan":
        c.host.plan = replace(c.host.plan)
    elif fault == "route":
        c.host.docker.path = "/PRIVATE/replacement.sock"
    elif fault == "reader_closed":
        c.reader.close()
    else:
        c.s.start.close()
    denied(c)
    assert not c.metadata_calls and not c.identity_calls


@pytest.mark.parametrize("fault", ["close", "history", "late"])
def test_mid_file_read_cannot_publish_a_drifted_host_sample(host, monkeypatch, fault):
    c, original = host, host.reader.read

    def changed():
        result = original()
        if fault == "close":
            c.host.close()
        elif fault == "history":
            # A no-change tick is deliberately not persisted. Inject a real
            # policy entry instead (synthetic receipt, not a process claim).
            count = len(c.s.journal.entries)
            c.s.journal.append(
                dict(
                    kind="process_exited",
                    now=c.operator._clock(),
                    boot_id=c.s.plan.boot,
                    generation=c.s.run.pins.generation,
                )
            )
            assert len(c.s.journal.entries) == count + 1
        else:
            authority.history.continuity.advance(monkeypatch, 3)
        return result

    monkeypatch.setattr(c.reader, "read", changed)
    denied(c)


def test_nested_metadata_read_does_not_block_or_close_borrowed_reader(host, monkeypatch):
    c, original = host, host.host._observer

    def observer():
        def read():
            value = original().read()
            denied(c)
            return value

        return SimpleNamespace(read=read)

    monkeypatch.setattr(c.host, "_observer", observer)
    denied(c)
    assert not c.reader.closed and not c.reader.failed


def test_foreign_thread_cannot_sample_metadata(host):
    errors = []

    def other():
        try:
            host.host.read()
        except m.UnconfirmedHostBegin:
            errors.append(True)

    worker = Thread(target=other)
    worker.start()
    worker.join(2)
    assert not worker.is_alive() and errors == [True]
    assert host.host.failed and not host.metadata_calls and not host.identity_calls
    assert not host.reader.failed and not host.operator.closed


def test_actual_observer_factory_keeps_original_routes_seals_and_fresh_collectors(
    host, monkeypatch
):
    c = host
    observer = m.FinalizedHost._observer(c.host)
    assert type(observer) is m._StaticHostObserver
    assert observer.docker is c.host.docker
    assert observer.supervisor.docker is c.host.docker
    assert observer.supervisor.image == c.s.plan.cli_image
    assert observer.supervisor.incarnation == c.s.plan.cli_generation
    assert observer.seals == {m.base.NORMAL: c.s.plan.normal, m.base.CANDIDATE: c.s.plan.candidate}
    assert observer.versions == dict(c.s.plan.installed_versions)
    assert observer.other == frozenset(c.s.plan.other_scanner_apps)
    assert (observer.core_image, observer.core_generation, observer.core_version) == (
        c.s.plan.core_image,
        c.s.plan.core_generation,
        c.s.plan.core_version,
    )
    assert observer.network == m.plans.ordinary.AUDIO_NETWORK
    for slug in (m.base.NORMAL, m.base.CANDIDATE):
        state = observer.read_native(slug, c.s.run.pins.generation)
        assert state.healthy is None and state.recording is None
    calls = []

    def collect(layout, container):
        calls.append((layout, container))
        return object()

    monkeypatch.setattr(m.plans.host.static, "collect", collect)
    monkeypatch.setattr(m.plans.host.candidate_static, "collect", collect)
    candidate = dict(Id=c.s.run.pins.init.container_id)
    for _ in range(2):
        observer.collect_files(m.base.NORMAL, None)
        observer.collect_files(m.base.CANDIDATE, candidate)
        observer.collect_files(m.base.CANDIDATE, None)
    assert calls == [
        (next(v for v in c.s.plan.layouts if v.slug == slug), container)
        for _ in range(2)
        for slug, container in (
            (m.base.NORMAL, None),
            (m.base.CANDIDATE, candidate),
            (m.base.CANDIDATE, None),
        )
    ]
    count = len(calls)
    # Calling the internal callback directly bypasses read()'s sanitized
    # exception wrapper, but must still reject before the collector runs.
    with pytest.raises(m.UnconfirmedHostBegin):
        observer.collect_files("unrelated_app", None)
    with pytest.raises(m.UnconfirmedHostBegin):
        observer.collect_files(m.base.CANDIDATE, dict(Id="0" * 64))
    assert len(calls) == count


@pytest.mark.parametrize("fault", ["stage", "contract", "generation"])
def test_wrong_file_provenance_is_not_a_host_sample(host, fault):
    changes = dict(
        stage={"stage": "active"},
        contract={"contract_sha256": "a" * 64},
        generation={"generation": "a" * 64},
    )
    host.collected.files = replace(host.collected.files, **changes[fault])
    denied(host)
