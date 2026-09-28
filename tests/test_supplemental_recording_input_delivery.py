"""Original real input files precede both supervised disposable peer collectors.

No installed provenance: template/digests/Engine/image facts remain synthetic.
No scanner/App work. Inherited fixtures own every child, directory and handle.
"""

from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_listener_delivery as listeners
from . import test_supplemental_recording_peer_runtime_pair as runtime_pair

m, original, lifetime = listeners.m, listeners.original, listeners.lifetime
layout, image_umask, supervised = listeners.layout, listeners.image_umask, listeners.supervised
image, configured, pair = listeners.image, listeners.configured, listeners.pair
inputs, custody, short_budget = listeners.inputs, listeners.custody, listeners.short_budget
helper, peer_processes, joined = listeners.helper, listeners.peer_processes, listeners.joined
pytestmark = listeners.pytestmark


@pytest.fixture(autouse=True)
def input_source(tmp_path, monkeypatch):
    state = SimpleNamespace(owner=None, declaration=None)
    declaration_module = m.peer_inputs.declarations
    monkeypatch.setattr(m.peer_inputs, "ROOT_UID", m.peer_inputs.os.geteuid())

    def read(template, expected):
        assert state.owner is None
        startup_root, root = tmp_path / "startup", tmp_path / "inputs"
        for directory, name, raw in (
            (startup_root, declaration_module.NAME, template.raw),
            (root, m.peer_inputs.NAME, expected.raw),
        ):
            directory.mkdir(mode=0o700)
            path = directory / name
            path.write_bytes(raw)
            path.chmod(0o600)
        case = declaration_module.codec._read(template.raw)["plan"]["case"]
        declaration_root, inputs_root = (
            declaration_module.declaration_root,
            m.peer_inputs.inputs_root,
        )
        monkeypatch.setattr(
            declaration_module,
            "declaration_root",
            lambda c: startup_root if c == case else declaration_root(c),
        )
        monkeypatch.setattr(
            m.peer_inputs, "inputs_root", lambda c: root if c == case else inputs_root(c)
        )
        state.declaration = declaration_module.Declaration(startup_root, template.sha256)
        state.owner = m.peer_inputs.Inputs(state.declaration, root, expected.sha256)
        return state.owner.template, state.owner.recheck()

    monkeypatch.setattr(runtime_pair, "read_peer_inputs", read)
    try:
        yield state
    finally:
        if state.owner is not None:
            state.owner.close()
        if state.declaration is not None:
            state.declaration.close()


def call(joined, state, *, supplied=None):
    return m.deliver_from_inputs(
        joined.custody,
        joined.watch,
        joined.local,
        state.owner if supplied is None else supplied,
        joined.listeners["writer"],
        joined.listeners["observer"],
    )


def test_real_original_inputs_bound_both_collectors_and_every_handoff_guard(
    joined, input_source, monkeypatch
):
    owner = input_source.owner
    assert joined.pair.obj.writer.expectations is owner.expectations
    assert joined.pair.obj.observer.expectations is owner.expectations
    assert joined.pair.obj.writer.template is owner.template
    calls, check = [], m.peer_inputs.Inputs.recheck

    def recheck(value, *, deadline=None):
        assert value is owner
        calls.append(deadline)
        return check(value, deadline=deadline)

    monkeypatch.setattr(m.peer_inputs.Inputs, "recheck", recheck)
    original.start(joined)
    result = call(joined, input_source)
    assert result.declaration_sha256 == owner.expected
    assert all(peer["received"] for peer in original.results(joined).values())
    assert len(calls) >= 6 and set(calls) == {joined.listener_end}
    assert not owner.closed and not input_source.declaration.closed and not joined.watch.closed


@pytest.mark.parametrize(
    "fault", ["file", "closed", "copied-value", "copied-template", "pin", "raw-value"]
)
def test_uncertain_or_recreated_inputs_stop_original_pair_before_descriptor_delivery(
    joined, input_source, monkeypatch, fault
):
    owner = input_source.owner
    supplied = None
    if fault == "file":
        (owner.root / m.peer_inputs.NAME).write_bytes(b"changed")
    elif fault == "closed":
        owner.close()
    elif fault == "copied-value":
        owner.expectations = m.peer_inputs.codec.load_bytes(owner.raw, owner.expected)
    elif fault == "copied-template":
        owner.template = m.peer_inputs.declarations.codec.load_bytes(
            owner.template.raw, owner.template.sha256
        )
    elif fault == "pin":
        owner.expected = "0" * 64
    else:
        supplied = owner.expectations
    monkeypatch.setattr(
        m.bootstrap.links, "pair", lambda: pytest.fail("No descriptors on uncertain input")
    )
    original.refused(lambda: call(joined, input_source, supplied=supplied))
    assert joined.watch.closed
    lifetime.termination.exited(joined.pair)


def test_missing_required_inputs_are_not_the_older_listener_only_variant(joined):
    original.refused(
        lambda: m.deliver_from_inputs(
            joined.custody,
            joined.watch,
            joined.local,
            None,
            joined.listeners["writer"],
            joined.listeners["observer"],
        )
    )
    assert joined.watch.closed
    lifetime.termination.exited(joined.pair)


@pytest.mark.parametrize("stage", ["after-writer", "after-final-read"])
def test_late_input_change_cancels_original_watch_not_a_cached_success(
    joined, input_source, monkeypatch, stage
):
    owner = input_source.owner

    def change():
        (owner.root / m.peer_inputs.NAME).write_bytes(b"changed")

    if stage == "after-writer":
        deliver = m.bootstrap.Endpoint.deliver

        def handoff(endpoint, channels):
            receipt = deliver(endpoint, channels)
            if endpoint.role == "writer":
                change()
            return receipt

        monkeypatch.setattr(m.bootstrap.Endpoint, "deliver", handoff)
    else:
        collect = m.termination.peers.PeerRuntimePair._collect_before
        calls = []

        def qualify(pair, end):
            collect(pair, end)
            calls.append(end)
            if len(calls) == 2:
                change()

        monkeypatch.setattr(m.termination.peers.PeerRuntimePair, "_collect_before", qualify)
    original.start(joined)
    original.refused(lambda: call(joined, input_source))
    assert joined.watch.closed and owner.closed and not input_source.declaration.closed
    lifetime.termination.exited(joined.pair)
