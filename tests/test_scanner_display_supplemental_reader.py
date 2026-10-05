"""Offline reader/UI acceptance; fixtures never contact a real scanner."""

import asyncio
from copy import deepcopy
from dataclasses import asdict, replace
from threading import Event
from uuid import UUID

import pytest
from textual.widgets import Static

from sds200.daemon_remote_client import DaemonRemoteClientError, DaemonRemoteClientErrorReason
from sds200.exceptions import DaemonRequestError
from sds200.scanner_display_reader import DisplayFrameSource
from sds200.scanner_display_supplemental_reader import (
    SupplementalFrameReader,
    SupplementalFrameSource,
    SupplementalReaderStopped,
    daemon_supplemental_source,
)
from sds200.scanner_display_supplemental_transport import validate_bundle
from sds200.scanner_display_supplemental_wire import decode_supplemental_context
from sds200.scanner_display_tui import MimicRuntimeScreen, render_mimic_terminal
from sds200.tui import ScannerIdentity, ScannerTuiApp

from .test_daemon_display_frames import configured as configured
from .test_daemon_quick_key_worker import scan
from .test_daemon_supplemental_reads import engine as engine
from .test_daemon_supplemental_reads import wires
from .test_scanner_display_supplemental_presentation import capture as capture
from .test_scanner_display_supplemental_transport import bundle as bundle
from .test_scanner_display_supplemental_transport import context as context
from .test_scanner_display_supplemental_transport import service as service
from .test_scanner_display_supplemental_transport import unix_api
from .test_scanner_display_tui import _until
from .test_tui import _app, _plain


def response(context):
    return {"protocol": "sdsctl.supplemental-context", "version": 1, "context": context}


def source(context, bundle, *, closed=lambda: None):
    return SupplementalFrameSource(lambda: response(context), lambda _: bundle, closed)


def make_reader(context, *, now):
    reader = SupplementalFrameReader(source(context, None), clock=lambda: now[0])
    with reader._condition:
        reader._bind(decode_supplemental_context(context))
    return reader


def submit(reader, bundle, *, started, now):
    with reader._condition:
        guard = reader._guard
        ticket = guard.begin(now=started)
        reader._accept(ticket, validate_bundle(bundle, guard.context), now)


def sequence(bundle, number):
    result = deepcopy(bundle)
    result["supplemental"]["psi"]["sequence"] = number
    for frame in result["display"]["frames"].values():
        frame["sequence"] = number
    return result


def text(packet):
    return render_mimic_terminal(packet["frames"]["detail"], width=160, height=41).plain


def test_immutable_projection_expires_independently_without_new_packet(bundle, context):
    now = [10.0]
    bundle["supplemental"]["clock"]["age_seconds"] = 4.0
    bundle["supplemental"]["favorites"]["age_seconds"] = 1.0
    original = deepcopy(bundle)
    reader = make_reader(context, now=now)
    submit(reader, bundle, started=10, now=10)
    first = reader.view()[0]
    assert "21:26" in text(first) and "Sep17" in text(first)
    assert bundle == original and "21:26" not in str(bundle["display"])
    with pytest.raises(TypeError):
        first["frames"]["detail"]["screen"]["regions"][0]["text"] = "changed"
    now[0] = 10.9
    assert reader.view()[0] is first  # no repaint from fractional age alone
    now[0] = 11.0
    expired = reader.view()[0]
    assert expired is not first and "21:26" not in text(expired)
    assert "Sep17" not in text(expired) and "Clock: stale" in reader.details_snapshot()
    assert "Favorites: current" in reader.details_snapshot()
    assert reader.view()[0] is expired
    now[0] = 14.0
    assert "Favorites: stale" in reader.details_snapshot()
    assert "00:" not in reader.details_snapshot()
    now[0] = 15.0
    assert reader.view()[0] is None
    assert "No current supplemental" in reader.details_snapshot()
    reader.close()


def test_runtime_packet_and_supplemental_details_share_one_clock_cut(bundle, context):
    reads = []

    def clock():
        reads.append(1)
        return 10 if len(reads) == 1 else 15

    reader = SupplementalFrameReader(source(context, bundle), clock=clock)
    with reader._condition:
        reader._bind(decode_supplemental_context(context))
    submit(reader, bundle, started=10, now=10)
    packet, _, details = reader.view_and_details()
    assert reads == [1]
    assert "21:26" in text(packet) and "Clock: current" in details and "00:" in details
    packet, _, details = reader.view_and_details()
    assert packet is None and "00:" not in details


def test_slow_reply_charged_before_io_and_repeated_samples_do_not_renew(bundle, context):
    now = [14.0]
    reader = make_reader(context, now=now)
    submit(reader, bundle, started=10, now=14)
    assert "21:26" in text(reader.view()[0])
    now[0] = 14.9
    submit(reader, sequence(bundle, 1000), started=14.9, now=14.9)
    now[0] = 15
    assert "21:26" not in text(reader.view()[0])  # fresh PSI, old clock
    submit(reader, sequence(bundle, 1001), started=15, now=15)
    assert "21:26" not in text(reader.view()[0])
    fresh = sequence(bundle, 1002)
    for name in ("clock", "favorites"):
        fresh["supplemental"][name]["sample_sequence"] += 1
    submit(reader, fresh, started=15, now=15)
    assert "21:26" in text(reader.view()[0])


def test_same_context_retains_guard_and_cannot_revive_suspended_psi(bundle, context):
    now = [10.0]
    reader = make_reader(context, now=now)
    submit(reader, bundle, started=10, now=10)
    guard = reader._guard
    with reader._condition:
        reader._suspend("hidden")
        reader._bind(decode_supplemental_context(context))
    assert reader._guard is guard
    submit(reader, bundle, started=10, now=10)
    assert reader.view()[0] is None
    submit(reader, sequence(bundle, 1000), started=10, now=10)
    assert reader.view()[0] is not None
    assert "21:26" not in text(reader.view()[0])  # retired clock does not revive


@pytest.mark.parametrize("field", ["clock", "favorites"])
@pytest.mark.parametrize("status", ["disabled", "blocked", "invalid_rtc", "invalid_source"])
def test_source_status_clears_only_that_source(bundle, context, field, status):
    reader = make_reader(context, now=[10.0])
    bundle["supplemental"][field] = dict(
        status=status, sample_sequence=None, age_seconds=None, value=None
    )
    submit(reader, bundle, started=10, now=10)
    packet = reader.view()[0]
    assert ("21:26" in text(packet)) is (field != "clock")
    assert ("00:" in reader.details_snapshot()) is (field != "favorites")
    assert f"{field.title()}: {status}" in reader.details_snapshot()
    for style, frame in packet["frames"].items():
        before = bundle["display"]["frames"][style]["screen"]["regions"]
        for raw, projected in zip(before, frame["screen"]["regions"], strict=True):
            if raw["token"] not in {"Day", "Time"}:
                assert dict(projected) == raw


def test_remote_raw_clock_never_falls_back_when_auxiliary_value_absent(bundle, context):
    for frame in bundle["display"]["frames"].values():
        for region in frame["screen"]["regions"]:
            if region["token"] in {"Day", "Time"}:
                region.update(value_status="raw_source", text="PRIVATE")
    bundle["supplemental"]["clock"] = dict(
        status="unavailable", sample_sequence=None, age_seconds=None, value=None
    )
    reader = make_reader(context, now=[10.0])
    submit(reader, bundle, started=10, now=10)
    assert "PRIVATE" not in text(reader.view()[0])


@pytest.mark.parametrize("selection,status", [("empty", "empty"), ("configured", "blank")])
def test_empty_or_blank_clock_cells_are_not_resurrected(bundle, context, selection, status):
    for frame in bundle["display"]["frames"].values():
        for region in frame["screen"]["regions"]:
            if region["token"] in {"Day", "Time"}:
                region.update(selection=selection, value_status=status, text=None)
    reader = make_reader(context, now=[10.0])
    submit(reader, bundle, started=10, now=10)
    assert "21:26" not in text(reader.view()[0])


@pytest.mark.parametrize("phase", ["negotiation", "frame"])
def test_whole_cycle_budget_rejects_late_response(bundle, context, phase):
    now, reads = [10.0], []

    def negotiate():
        if phase == "negotiation":
            now[0] += 5
        return response(context)

    def read(_):
        reads.append(1)
        now[0] += 5
        return bundle

    reader = SupplementalFrameReader(
        SupplementalFrameSource(negotiate, read, lambda: None), clock=lambda: now[0]
    )
    try:
        reader.set_active(True)
        _until(lambda: "retrying" in reader.view()[1])
        assert reader.view()[0] is None
        assert reads == ([] if phase == "negotiation" else [1])
    finally:
        reader.close(wait=True)


@pytest.mark.parametrize("failure", ["malformed", "changed", "unavailable"])
def test_frame_failure_clears_and_renegotiates_before_any_next_frame(bundle, context, failure):
    calls, allow_failure, failed = [], Event(), Event()

    def negotiate():
        calls.append("context")
        return response(context)

    def read(_):
        calls.append("frame")
        if calls.count("frame") == 1:
            return bundle
        if calls.count("frame") == 2:
            assert allow_failure.wait(3)
            failed.set()
            if failure == "malformed":
                return {"secret": "PRIVATE"}
            raise DaemonRequestError(
                f"supplemental_{'context_changed' if failure == 'changed' else failure}",
                "PRIVATE",
                request_id="x",
            )
        return sequence(bundle, 1000)

    reader = SupplementalFrameReader(
        SupplementalFrameSource(negotiate, read, lambda: None), clock=lambda: 10
    )
    try:
        reader.set_active(True)
        _until(lambda: reader.view()[0] is not None)
        allow_failure.set()
        assert failed.wait(2)
        _until(lambda: reader.view()[0] is None)
        assert "PRIVATE" not in reader.view()[1]
        _until(lambda: reader.view()[0] is not None)
        assert calls[:5] == ["context", "frame", "frame", "context", "frame"]
        assert "21:26" not in text(reader.view()[0])  # no retired sample revival
    finally:
        allow_failure.set()
        reader.close(wait=True)


@pytest.mark.parametrize("bad_time", [9, float("nan"), float("inf"), -1, True])
def test_invalid_or_backward_ui_clock_stops_without_showing_values(bundle, context, bad_time):
    now = [10.0]
    reader = make_reader(context, now=now)
    submit(reader, bundle, started=10, now=10)
    assert reader.view()[0] is not None
    now[0] = bad_time
    assert reader.view()[0] is None and reader._terminal
    now[0] = 11
    reader.set_active(True)
    assert reader.view()[0] is None and not reader.alive


@pytest.mark.parametrize("field", ["endpoint_id", "context_revision", "profile_invalidation"])
def test_context_endpoint_and_epoch_rollback_rejected(context, field):
    binding = decode_supplemental_context(context)
    binding = replace(binding, context_revision=10, profile_invalidation=10)
    reader = make_reader(asdict(binding), now=[0.0])
    changed = replace(binding, **{field: str(UUID(int=91)) if field == "endpoint_id" else 9})
    with reader._condition, pytest.raises(SupplementalReaderStopped):
        reader._bind(changed)
    assert reader._guard.context == binding


@pytest.mark.parametrize("kind", ["profile", "connection"])
def test_retired_incomparable_contexts_cannot_replay_and_history_is_bounded(context, kind):
    reader = make_reader(context, now=[0.0])
    first = reader._guard.context
    for number in range(1, 65):
        binding = replace(
            first,
            **(
                {"profile_revision": f"{number:064x}"}
                if kind == "profile"
                else {"session_id": str(UUID(int=number + 100))}
            ),
        )
        with reader._condition:
            reader._bind(binding)
    with reader._condition, pytest.raises(SupplementalReaderStopped):
        reader._bind(first)
    next_binding = replace(
        binding,
        **(
            {"profile_revision": "f" * 64}
            if kind == "profile"
            else {"session_id": str(UUID(int=999))}
        ),
    )
    with reader._condition, pytest.raises(SupplementalReaderStopped):
        reader._bind(next_binding)
    assert len(reader._retired_profiles if kind == "profile" else reader._retired_connections) == 64


def test_routine_epochs_compact_profile_history_instead_of_exhausting_it(context):
    reader = make_reader(context, now=[0.0])
    first = reader._guard.context
    for number in range(1, 151):
        with reader._condition:
            reader._bind(replace(first, context_revision=first.context_revision + number))
    assert not reader._retired_profiles and not reader._retired_connections
    with reader._condition, pytest.raises(SupplementalReaderStopped):
        reader._bind(first)


@pytest.mark.parametrize("phase", ["negotiation", "frame"])
@pytest.mark.parametrize("action", ["hide", "close"])
def test_lazy_worker_late_reply_cannot_revive_or_block_ui(bundle, context, phase, action):
    entered, release, closed = Event(), Event(), Event()

    def block(value):
        entered.set()
        assert release.wait(3)
        return value

    reader = SupplementalFrameReader(
        SupplementalFrameSource(
            lambda: block(response(context)) if phase == "negotiation" else response(context),
            lambda _: block(bundle) if phase == "frame" else bundle,
            closed.set,
        )
    )
    try:
        assert not reader.alive and not entered.is_set()
        reader.set_active(True)
        assert entered.wait(2)
        if action == "hide":
            reader.set_active(False)
        else:
            reader.close()
        assert reader.view()[0] is None
        release.set()
        _until(closed.is_set)
        assert reader.view()[0] is None
    finally:
        release.set()
        reader.close(wait=True)
    reader.set_active(True)
    assert not reader.alive


@pytest.mark.parametrize(
    "error",
    [
        DaemonRequestError("authorization_denied", "PRIVATE", request_id="x"),
        DaemonRequestError("authentication_expired", "PRIVATE", request_id="x"),
        DaemonRemoteClientError(DaemonRemoteClientErrorReason.AUTHENTICATION_FAILED),
        DaemonRemoteClientError(DaemonRemoteClientErrorReason.TLS_HANDSHAKE_FAILED),
        SupplementalReaderStopped("PRIVATE"),
    ],
)
def test_auth_and_trust_failure_terminal_and_sanitized(context, error):
    calls = []

    def read(_):
        calls.append(1)
        raise error

    reader = SupplementalFrameReader(
        SupplementalFrameSource(lambda: response(context), read, lambda: None)
    )
    try:
        reader.set_active(True)
        _until(lambda: not reader.alive)
        assert reader.view()[0] is None
        assert "PRIVATE" not in reader.view()[1]
        reader.set_active(False)
        reader.set_active(True)
        assert calls == [1] and reader.view()[0] is None
    finally:
        reader.close(wait=True)


def test_adapter_requires_both_capabilities_no_ordinary_fallback(context):
    calls = []

    class Client:
        def hello(self):
            calls.append("hello")
            return {"operations": ["display.frame", "display.supplemental.context"]}

        def display_supplemental_context(self):
            pytest.fail("missing capability")

        def display_supplemental_frame(self, context):
            pytest.fail("missing capability")

        def close(self):
            calls.append("close")

    reader = SupplementalFrameReader(daemon_supplemental_source(Client()))
    try:
        reader.set_active(True)
        _until(lambda: not reader.alive)
        assert calls.count("hello") == 1 and "close" in calls
        assert reader.view()[0] is None
    finally:
        reader.close(wait=True)


def test_real_unix_reader_renegotiates_after_owner_reconnect_without_new_reads(
    service, engine, tmp_path
):
    _, _, _, scanner, clock = engine
    with unix_api(tmp_path, service) as factory:
        reader = SupplementalFrameReader(
            daemon_supplemental_source(factory()), clock=lambda: clock.now
        )
        try:
            reader.set_active(True)
            _until(lambda: reader.view()[0] is not None)
            first = reader._guard.context
            scanner.connect_event(False)
            _until(lambda: reader.view()[0] is None)
            scanner.connect_event(True)
            scanner.sample(scan("None", "None"))
            _until(lambda: reader.view()[0] is not None)
            assert reader._guard.context != first
            assert "21:26" not in text(reader.view()[0])
            assert wires(scanner) == ["FQK", "DTM"]
        finally:
            reader.close(wait=True)
        assert not reader.alive


@pytest.mark.parametrize("size", [(80, 30), (160, 45)])
def test_textual_expiry_overlay_preferences_and_shutdown(bundle, context, size):
    async def exercise():
        now = [10.0]
        entered, release = Event(), Event()
        selected = deepcopy(bundle)
        selected["supplemental"]["clock"]["age_seconds"] = 4
        calls = []

        def read(_):
            calls.append(1)
            if len(calls) == 2:
                entered.set()
                assert release.wait(3)
            return selected

        app = ScannerTuiApp(
            ScannerIdentity("sdsctl-remote-daemon", "SDS200", "fixture"),
            _app()._snapshot,
            supplemental_display_source=SupplementalFrameSource(
                lambda: response(context), read, lambda: None
            ),
            clock=lambda: now[0],
        )
        try:
            async with app.run_test(size=size) as pilot:
                assert not calls
                await pilot.press("m")
                await pilot.pause(0.3)
                screen = app.screen
                assert "21:26" in _plain(screen.query_one("#mimic-grid", Static))
                now[0] = 11
                await pilot.pause(0.2)
                assert entered.is_set() and not release.is_set()
                assert "21:26" not in _plain(screen.query_one("#mimic-grid", Static))
                release.set()
                await pilot.press("v", "b", "x")
                await pilot.pause(0.2)
                assert isinstance(app.screen, MimicRuntimeScreen)
                details = _plain(app.screen.query_one("#mimic-runtime", Static))
                assert "snapshot at drawer open" in details
                assert "not the LCD F0 / S0 / D0" in details
                assert "00:" in details and "99:" in details
                count = len(calls)
                await pilot.pause(0.3)
                assert len(calls) == count and app._mimic_reader.view()[0] is None
                await pilot.press("escape")
                selected.update(sequence(selected, 1000))
                await pilot.pause(0.3)
                assert (
                    app.screen is screen
                    and screen.style == "simple"
                    and screen.treatment == "border"
                )
                assert "21:26" not in _plain(screen.query_one("#mimic-grid", Static))
                await pilot.press("q")
        finally:
            release.set()
            app._mimic_reader.close(wait=True)
        assert not app._mimic_reader.alive

    asyncio.run(exercise())


def test_tui_default_off_and_mutually_exclusive_source_selection(context, bundle):
    assert _app()._mimic_reader is None
    with pytest.raises(ValueError, match="Choose one"):
        ScannerTuiApp(
            ScannerIdentity("sdsctl-remote-daemon", "SDS200", "fixture"),
            _app()._snapshot,
            display_source=DisplayFrameSource(lambda: None, lambda: None),
            supplemental_display_source=source(context, bundle),
        )
