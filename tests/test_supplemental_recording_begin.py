"""Durable original intent -> actual private write; no real scanner or Docker.

Reuses explicit synthetic Engine/namespace/clock metadata. Actual files, fsync,
Unix framing, retained owned processes and deadlines exercise the write boundary.
"""

import importlib.util
import json
import os
import select
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest

from . import test_supplemental_recording_channel as channel
from . import test_supplemental_recording_ready as ready_tests

NAME = "supplemental_recording_begin"
SPEC = importlib.util.spec_from_file_location(
    NAME, Path(ready_tests.m.__file__).with_name(NAME + ".py")
)
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)
layout, tree, routing, projection, binding, directory, prepared, family, actors, calibration = (
    ready_tests.layout,
    ready_tests.tree,
    ready_tests.routing,
    ready_tests.projection,
    ready_tests.binding,
    ready_tests.directory,
    ready_tests.prepared,
    ready_tests.family,
    ready_tests.actors,
    ready_tests.calibration,
)


@pytest.fixture
def ledger(prepared, tmp_path):
    path = tmp_path / "PRIVATE_recording_ledger"
    path.mkdir(mode=0o700)
    return m.host.Ledger(path, prepared.pins.host, now=time.monotonic())


def intent(ledger, prepared, **changes):
    now = time.monotonic()
    return ledger.start_intent(
        **(
            {
                "now": now,
                "generation": prepared.pins.generation,
                "authorization_sha256": "1" * 64,
                "start_by": now + 9,
                "finish_by": now + 25,
            }
            | changes
        )
    )


def refused(result, ledger):
    with pytest.raises(m.UnconfirmedBegin) as error:
        m.send_once(result, ledger)
    assert str(error.value) == m.MESSAGE and "PRIVATE" not in str(error.value)
    assert result.failed and result.client.closed
    assert not result.processes.closed


def test_exact_original_intent_precedes_one_actual_frame_and_does_not_claim_started(
    prepared, actors, calibration, ledger, monkeypatch
):
    assert m.native is channel.m
    peers = []
    with ready_tests.attached(prepared, actors, monkeypatch, tap=peers) as (client, requests):
        result = ready_tests.capture(client, calibration)
        try:
            original = intent(ledger, prepared)
            bound = m.send_once(result, ledger)
            message = ready_tests.joined.engine.attached.begun(peers[0])
            assert message == {
                "schema": 1,
                "kind": "finite-recording-operator",
                "phase": "begin",
                "context": json.loads(result.context_raw),
                "body": {
                    "binding": bound.payload(),
                    "intent_at": original.now,
                    "intent_sha256": original.sha256,
                },
            }
            assert ledger.state == original and ledger.state.expected is None
            assert m.host.load(ledger.directory, ledger.binding) == original
            assert len(requests) == 5 and client.attachment.begun
            assert len(list(ledger.directory.iterdir())) == 2
            refused(result, ledger)
            assert peers[0].recv(1) == b""  # Closed, never another frame.
        finally:
            result.close()


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "closed",
        "poisoned",
        "stale_ready",
        "generation",
        "binding",
        "memory",
        "replacement",
        "corrupt",
        "extra",
        "late",
    ],
)
def test_invalid_or_replaced_original_intent_never_writes_begin(
    prepared, actors, calibration, ledger, monkeypatch, fault
):
    if fault == "stale_ready":
        intent(ledger, prepared)
    peers = []
    with ready_tests.attached(prepared, actors, monkeypatch, tap=peers) as (client, _requests):
        result = ready_tests.capture(client, calibration)
        try:
            if fault not in ("missing", "stale_ready"):
                intent(
                    ledger, prepared, **({"generation": "e" * 64} if fault == "generation" else {})
                )
            if fault == "closed":
                ledger.abandon(now=time.monotonic())
            elif fault == "poisoned":
                ledger._poisoned = True
            elif fault == "binding":
                ledger.binding = replace(ledger.binding, source_sha256="e" * 64)
            elif fault == "memory":
                ledger.state = replace(ledger.state, sha256="e" * 64)
            elif fault == "replacement":
                old = ledger.directory.with_name("PRIVATE_original_ledger")
                ledger.directory.rename(old)
                ledger.directory.mkdir(mode=0o700)
                for entry in old.iterdir():
                    new = ledger.directory / entry.name
                    new.write_bytes(entry.read_bytes())
                    new.chmod(0o600)
            elif fault == "corrupt":
                (ledger.directory / "0001.json").write_bytes(b"PRIVATE")
            elif fault == "extra":
                (ledger.directory / "PRIVATE_extra").touch()
            elif fault == "late":
                # Retain a valid but insufficient original margin, never refresh.
                result.ready_by = time.monotonic() - 1
            refused(result, ledger)
            assert not client.attachment.begun and peers[0].recv(1) == b""
            assert not result.processes.exited("init")
        finally:
            result.close()


@pytest.mark.parametrize("at", [1, 2])
def test_lost_fsync_acknowledgment_is_not_an_actionable_intent(
    prepared, actors, calibration, ledger, monkeypatch, at
):
    peers = []
    with ready_tests.attached(prepared, actors, monkeypatch, tap=peers) as (client, _requests):
        result = ready_tests.capture(client, calibration)
        calls, original = [], os.fsync

        def lost(fd):
            original(fd)
            calls.append(fd)
            if len(calls) == at:
                raise OSError("PRIVATE lost fsync acknowledgment")

        try:
            with monkeypatch.context() as patch:
                patch.setattr(os, "fsync", lost)
                with pytest.raises(m.host.UnconfirmedBinding):
                    intent(ledger, prepared)
            assert m.host.load(ledger.directory, ledger.binding).count == 2
            refused(result, ledger)
            assert not client.attachment.begun and peers[0].recv(1) == b""
        finally:
            result.close()


@pytest.mark.parametrize(
    "fault", ["write_return_lost", "history_after_write", "clock_after_write", "peer_closed"]
)
def test_uncertain_write_preserves_intent_without_started_or_retry(
    prepared, actors, calibration, ledger, monkeypatch, fault
):
    peers = []
    with ready_tests.attached(prepared, actors, monkeypatch, tap=peers) as (client, _requests):
        result = ready_tests.capture(client, calibration)
        try:
            original_state = intent(ledger, prepared)
            send = client.attachment.send_begin

            def sending(value, *, deadline):
                send(value, deadline=deadline)
                if fault == "write_return_lost":
                    raise OSError("PRIVATE lost successful write return")
                if fault == "history_after_write":
                    (ledger.directory / "0001.json").write_bytes(b"PRIVATE")
                if fault == "clock_after_write":
                    calibration.offset += 5

            if fault == "peer_closed":
                peers[0].close()
            else:
                monkeypatch.setattr(client.attachment, "send_begin", sending)
            refused(result, ledger)
            if fault != "peer_closed":
                frame = ready_tests.joined.engine.attached.begun(peers[0])
                assert frame["body"]["intent_sha256"] == original_state.sha256
                assert peers[0].recv(1) == b""
            assert ledger.state == original_state and ledger.state.expected is None
            refused(result, ledger)
        finally:
            result.close()


def test_insufficient_preparation_margin_is_not_shifted(
    prepared, actors, calibration, ledger, monkeypatch
):
    peers = []
    with ready_tests.attached(prepared, actors, monkeypatch, tap=peers) as (client, _requests):
        result = ready_tests.capture(client, calibration)
        try:
            state = intent(ledger, prepared, start_by=time.monotonic() + 1)
            refused(result, ledger)
            assert not client.attachment.begun and ledger.state == state
            assert select.select([peers[0]], [], [], 0)[0] and peers[0].recv(1) == b""
        finally:
            result.close()
