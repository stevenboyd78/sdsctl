"""Original real input files precede both supervised disposable peer collectors.

No installed provenance: template/digests/Engine/image facts remain synthetic.
No scanner/App work. Inherited fixtures own every child, directory and handle.
"""

import signal
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


@pytest.mark.parametrize("pair", [True], indirect=True)
def test_explicit_handoff_graph_reaches_both_original_collectors_and_supervised_delivery(
    joined, input_source, monkeypatch
):
    """New graph selection is joined to existing real transport, not a receipt shim."""
    declarations = m.termination.peers.declarations
    assert declarations._read(input_source.owner.raw)["kind"] == declarations.PEER_KIND
    for collector in joined.pair.qualifiers.values():
        assert collector.source is declarations.peer_source and collector.peer_handoff is True
        assert collector.expectations is input_source.owner.expectations
        assert collector.template is input_source.owner.template
    test_real_original_inputs_bound_both_collectors_and_every_handoff_guard(
        joined, input_source, monkeypatch
    )
    assert all(joined.pair.counts[role]["container"] == 8 for role in ("writer", "observer"))
    for role in ("writer", "observer"):
        lifetime.transport.command(joined.pair.children[role], dict(mode="link"))
        assert lifetime.transport.line(joined.pair.children[role]) == "linked"


@pytest.mark.parametrize("pair", [True], indirect=True)
def test_handoff_source_change_after_arm_refuses_delivery_and_cancels_originals(
    joined, input_source, helper
):
    # Alter the selected inventory file, not a process expectation or timeout.
    qualifier = joined.pair.qualifiers["writer"]
    path = helper.root / qualifier.HELPER / "supplemental_recording_writer_channel.py"
    path.write_bytes(b"raise RuntimeError('CHANGED_SOURCE_MUST_NOT_EXECUTE')\n")
    original.refused(lambda: call(joined, input_source))
    assert joined.watch.closed and joined.pair.obj.channel_delivery_attempted
    lifetime.termination.exited(joined.pair)


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


@pytest.mark.parametrize("fault", ["deadline", "input", "listener", "peer-exit", "watch-exit"])
def test_final_retirement_cannot_return_a_stale_delivery_receipt(
    joined, input_source, monkeypatch, fault
):
    """Both transfers/readbacks succeeded, but the complete operation has not.

    The original inputs, two retained listeners, full paired runtime reads,
    pidfds and armed kernel watcher are real parts of this fixture. No new
    collector or replacement clock is constructed after uncertainty.
    """
    retire = m._retire
    retired = []

    def change(channels, pins):
        retire(channels, pins)
        retired.append(channels)
        if len(retired) != 4:  # Last outer cleanup, AFTER the final full read.
            return
        if fault == "deadline":
            monkeypatch.setattr(m, "time", SimpleNamespace(monotonic=lambda: joined.listener_end))
        elif fault == "input":
            (input_source.owner.root / m.peer_inputs.NAME).write_bytes(b"changed at retirement")
        elif fault == "listener":
            (joined.listeners["observer"].root / "foreign").touch()
        elif fault == "peer-exit":
            joined.pair.children["observer"].kill()
            joined.pair.children["observer"].wait(timeout=2)
        else:
            signal.pidfd_send_signal(joined.watch.fd, signal.SIGKILL)
            assert lifetime.select.select([joined.watch.fd], [], [], 2)[0]

    monkeypatch.setattr(m, "_retire", change)
    original.start(joined)
    original.refused(lambda: call(joined, input_source))
    assert len(retired) == 4 and joined.watch.closed
    assert joined.pair.obj.channel_delivery_attempted
    assert all(joined.pair.counts[role]["container"] == 8 for role in ("writer", "observer"))
    assert all(
        sock.fileno() == -1
        for channels in retired
        for sock in (channels.incoming, channels.outgoing)
    )
    lifetime.termination.exited(joined.pair)
    # Final cleanup must not turn delivery uncertainty into replay permission.
    original.refused(lambda: call(joined, input_source))
    assert len(retired) == 4
