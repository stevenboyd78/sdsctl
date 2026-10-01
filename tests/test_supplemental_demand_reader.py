"""Explicit TUI lease selection, exact acknowledgement and no uncertain replay."""

from copy import deepcopy
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import pytest

from sds200.exceptions import DaemonRequestError
from sds200.scanner_display_supplemental_reader import (
    SupplementalFrameReader,
    SupplementalFrameSource,
    SupplementalReaderStopped,
    daemon_supplemental_source,
)
from sds200.scanner_display_supplemental_transport import DEMAND_PROTOCOL

from .test_daemon_display_frames import configured as configured
from .test_daemon_quick_key_worker import wait_for
from .test_daemon_supplemental_reads import engine as engine
from .test_scanner_display_supplemental_presentation import capture as capture
from .test_scanner_display_supplemental_reader import response
from .test_scanner_display_supplemental_transport import bundle as bundle
from .test_scanner_display_supplemental_transport import context as context
from .test_scanner_display_supplemental_transport import service as service


def ack(context, nonce):
    return {
        "protocol": DEMAND_PROTOCOL,
        "version": 1,
        "context": context,
        "renewal_id": nonce,
        "lease_seconds": 5,
    }


@pytest.mark.parametrize("enabled", [False, True])
def test_adapter_requires_separate_capability(context, enabled):
    operations = ["display.supplemental.context", "display.supplemental.frame"]
    calls = []
    client = SimpleNamespace(
        hello=lambda: {"operations": operations},
        display_supplemental_context=lambda: response(context),
        display_supplemental_frame=lambda _: None,
        display_supplemental_demand=lambda binding, nonce: calls.append(binding),
        close=lambda: None,
    )
    source = daemon_supplemental_source(client, demand=enabled)
    assert (source.renew is not None) is enabled
    if enabled:
        with pytest.raises(SupplementalReaderStopped):
            source.negotiate()
        operations.append("display.supplemental.demand")
    assert source.negotiate() == response(context)
    assert calls == []  # Negotiation never causes acquisition.


@pytest.mark.parametrize("value", [None, 0, 1, "true"])
def test_adapter_flag_is_exact_bool(value):
    with pytest.raises(TypeError):
        daemon_supplemental_source(object(), demand=value)


def test_first_ack_advances_epoch_before_frame_not_renegotiation_loop(context, bundle):
    context, bundle = deepcopy(context), deepcopy(bundle)
    renewals, reads, negotiations = [], [], []

    def negotiate():
        negotiations.append(1)
        return response(dict(context))

    def renew(binding, nonce):
        renewals.append(nonce)
        assert binding == context
        if len(renewals) == 1:
            context["context_revision"] += 1
            bundle["supplemental"]["context"] = dict(context)
        return ack(dict(context), nonce)

    def read(binding):
        reads.append(binding)
        assert binding == context
        return bundle

    reader = SupplementalFrameReader(
        SupplementalFrameSource(negotiate, read, lambda: None, renew), clock=lambda: 10
    )
    try:
        reader.set_active(True)
        wait_for(lambda: reader.view()[0] is not None)
        reader.set_active(False)
        assert negotiations == [1] and reads and renewals
        assert reader.view()[0] is None
    finally:
        reader.close(wait=True)


@pytest.mark.parametrize("failure", ["lost", "bad_nonce", "late", "unconfirmed"])
@pytest.mark.parametrize("hidden", [False, True])
def test_uncertain_demand_is_terminal_even_after_visibility_change(context, failure, hidden):
    entered, released = Event(), Event()
    now, calls, reads = [10.0], [], []

    def renew(binding, nonce):
        calls.append(nonce)
        entered.set()
        assert released.wait(3)
        if failure == "lost":
            raise TimeoutError("PRIVATE")
        if failure == "unconfirmed":
            raise DaemonRequestError("supplemental_demand_unconfirmed", "PRIVATE", request_id="x")
        if failure == "late":
            now[0] = 15
        return ack(binding, str(uuid4()) if failure == "bad_nonce" else nonce)

    reader = SupplementalFrameReader(
        SupplementalFrameSource(
            lambda: response(context), lambda binding: reads.append(binding), lambda: None, renew
        ),
        clock=lambda: now[0],
    )
    try:
        reader.set_active(True)
        assert entered.wait(3)
        if hidden:
            reader.set_active(False)
            reader.set_active(True)
        released.set()
        wait_for(lambda: not reader.alive)
        assert reader._terminal
        reader.set_active(True)
        assert not reader.alive and reads == [] and len(calls) == 1
        assert reader.view()[0] is None
        assert "PRIVATE" not in str(reader.view())
    finally:
        released.set()
        reader.close(wait=True)


@pytest.mark.parametrize("close", [False, True])
def test_valid_late_ack_does_not_revive_hidden_or_closed_consumer(context, close):
    entered, released = Event(), Event()
    reads, calls = [], []

    def renew(binding, nonce):
        calls.append(nonce)
        entered.set()
        assert released.wait(3)
        return ack(binding, nonce)

    reader = SupplementalFrameReader(
        SupplementalFrameSource(
            lambda: response(context), lambda binding: reads.append(binding), lambda: None, renew
        ),
        clock=lambda: 10,
    )
    try:
        reader.set_active(True)
        assert entered.wait(3)
        if close:
            reader.close()
        else:
            reader.set_active(False)
        released.set()
        Event().wait(0.05)
        assert reads == [] and len(calls) == 1
        assert reader.view()[0] is None
    finally:
        released.set()
        reader.close(wait=True)


def test_explicit_premutation_context_rejection_can_renegotiate(context, bundle):
    calls = []

    def renew(binding, nonce):
        calls.append(nonce)
        if len(calls) == 1:
            raise DaemonRequestError("supplemental_context_changed", "PRIVATE", request_id="x")
        return ack(binding, nonce)

    reader = SupplementalFrameReader(
        SupplementalFrameSource(lambda: response(context), lambda _: bundle, lambda: None, renew),
        clock=lambda: 10,
    )
    try:
        reader.set_active(True)
        wait_for(lambda: len(calls) == 1)
        # Wake the bounded retry through an explicit visibility transition.
        reader.set_active(False)
        reader.set_active(True)
        wait_for(lambda: reader.view()[0] is not None)
        assert not reader._terminal
    finally:
        reader.close(wait=True)
