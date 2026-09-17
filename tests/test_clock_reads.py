"""Spec and captured DTM GET fixtures; offline replay is not physical acceptance."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from itertools import product
from threading import Event

import pytest

from sds200 import GetDateTime, ScannerDateTime
from sds200.exceptions import CommandRejectedError, CommandTimeoutError, ProtocolError
from sds200.models import Packet
from sds200.radio import SDS200

from .fakes import FakeTransport

FIELDS = ("0", "2026", "09", "17", "21", "26", "59", "1")
# One receive-only capture during the explicit SDS200 1.26.01 GET test.
CAPTURED_FIELDS = ("1", "2026", "9", "17", "3", "38", "10", "1")


def packet(fields=FIELDS, *, command="DTM"):
    return Packet(command, fields, raw="PRIVATE_RAW_CLOCK_RESPONSE")


def test_captured_sds200_reply_accepts_unpadded_month_and_hour():
    result = GetDateTime().parse_response(packet(CAPTURED_FIELDS))
    assert result.local_time == datetime(2026, 9, 17, 3, 38, 10)
    assert result.local_time.tzinfo is None
    assert result.rtc_valid is True
    assert result.daylight_saving == "1"  # Opaque; do not infer a UTC offset.


@pytest.mark.parametrize(
    "components", tuple(product(("9", "09"), ("7", "07"), ("3", "03"), ("8", "08"), ("5", "05")))
)
def test_each_decimal_clock_component_allows_optional_leading_zero(components):
    result = GetDateTime().parse_response(packet(("0", "2026", *components, "1")))
    assert result.local_time == datetime(2026, 9, 7, 3, 8, 5)


@pytest.mark.parametrize("index", range(2, 7))
@pytest.mark.parametrize("invalid", ["", "001", " 1", "1 ", "+1", "-1", "１", "1.0"])
def test_unpadded_component_support_does_not_accept_extra_width_or_coercion(index, invalid):
    fields = list(CAPTURED_FIELDS)
    fields[index] = invalid
    with pytest.raises(ProtocolError, match="invalid clock fields"):
        GetDateTime().parse_response(packet(tuple(fields)))


@pytest.mark.parametrize("daylight", ["0", "1", "Off", "On", "DST_UNKNOWN"])
def test_get_clock_preserves_scanner_local_time_and_opaque_daylight_token(daylight):
    response = packet((daylight, *FIELDS[1:]))
    command = GetDateTime()
    result = command.parse_response(response)
    assert command.wire == command.response_command == "DTM"
    assert isinstance(result, ScannerDateTime)
    assert result.local_time == datetime(2026, 9, 17, 21, 26, 59)
    assert result.local_time.tzinfo is None
    assert result.rtc_valid is True
    assert result.daylight_saving == daylight
    assert result.packet is response


@pytest.mark.parametrize("year,valid", [(2000, True), (2024, True), (2026, False), (2100, False)])
def test_clock_calendar_validation_includes_leap_year_rules(year, valid):
    response = packet(("0", str(year), "02", "29", "00", "00", "00", "1"))
    if valid:
        assert GetDateTime().parse_response(response).local_time == datetime(year, 2, 29)
    else:
        with pytest.raises(ProtocolError, match="calendar"):
            GetDateTime().parse_response(response)


@pytest.mark.parametrize(
    "components",
    [FIELDS[1:7], ("0000", "00", "00", "00", "00", "00"), ("0000", "0", "0", "0", "0", "0")],
)
def test_invalid_rtc_never_exposes_a_usable_time(components):
    result = GetDateTime().parse_response(packet(("0", *components, "0")))
    assert result.rtc_valid is False
    assert result.local_time is None


@pytest.mark.parametrize(
    "fields",
    [
        (),
        ("OK",),  # SET acknowledgement is not a clock reading.
        FIELDS[:-1],
        (*FIELDS, "extra"),
        ("", *FIELDS[1:]),
        ("private secret", *FIELDS[1:]),
        ("x" * 17, *FIELDS[1:]),
        ("１", *FIELDS[1:]),
        ("0", "26", *FIELDS[2:]),
        ("0", "0000", *FIELDS[2:]),
        (*FIELDS[:2], "13", *FIELDS[3:]),
        (*FIELDS[:2], "00", *FIELDS[3:]),
        (*FIELDS[:3], "31", *FIELDS[4:]),  # September has 30 days.
        (*FIELDS[:3], "00", *FIELDS[4:]),
        (*FIELDS[:4], "24", *FIELDS[5:]),
        (*FIELDS[:5], "60", *FIELDS[6:]),
        (*FIELDS[:6], "60", "1"),
        (*FIELDS[:6], "-1", "1"),
        (*FIELDS[:6], "５９", "1"),
        (*FIELDS[:6], " 1", "1"),
        (*FIELDS[:6], "001", "1"),
        (*FIELDS[:7], "2"),
        (*FIELDS[:7], "true"),
        (*FIELDS[:6], "PRIVATE_CLOCK_VALUE", "0"),  # RTC NG still validates shape.
    ],
)
def test_clock_refuses_malformed_fields_without_echoing_reply(fields):
    with pytest.raises(ProtocolError) as exc:
        GetDateTime().parse_response(packet(fields))
    assert "PRIVATE" not in str(exc.value)
    assert "private secret" not in str(exc.value)


@pytest.mark.parametrize("fields", [("NG",), ("ERR",), ("ERROR",)])
def test_clock_definite_rejection_has_its_own_failure_class(fields):
    with pytest.raises(CommandRejectedError, match="rejected"):
        GetDateTime().parse_response(packet(fields))


@pytest.mark.parametrize("response", [object(), packet(command="VER")])
def test_clock_rejects_other_response_types(response):
    with pytest.raises(ProtocolError, match="unexpected"):
        GetDateTime().parse_response(response)


@pytest.mark.parametrize("idle", [False, True])
@pytest.mark.parametrize("fields", [FIELDS, CAPTURED_FIELDS])
def test_clock_uses_existing_transport_get_only_and_does_not_update_scanner_state(idle, fields):
    class ReplyingTransport(FakeTransport):
        def write_command(self, command):
            super().write_command(command)
            assert command == "DTM"
            self.feed_line("DTM," + ",".join(fields))

    transport = ReplyingTransport()
    radio = SDS200.from_transport(transport)
    assert radio.read_clock_if_idle() is None
    assert transport.writes == []  # No implicit connect or worker.
    with radio:
        before = radio.state.snapshot
        result = radio.read_clock_if_idle() if idle else radio.get_date_time(timeout=1.0)
        assert result.local_time == datetime(*(int(v) for v in fields[1:7]))
        assert radio.state.snapshot == before
    assert transport.writes == ["DTM"]


def test_idle_clock_yields_to_existing_control_without_queuing():
    entered, release = Event(), Event()

    class BusyTransport(FakeTransport):
        def write_command(self, command):
            super().write_command(command)
            assert command == "VOL"
            entered.set()
            assert release.wait(2)
            self.feed_line("VOL,3")

    transport = BusyTransport()
    radio = SDS200.from_transport(transport)
    with radio, ThreadPoolExecutor(max_workers=2) as pool:
        control = pool.submit(radio.get_volume, timeout=1.0)
        try:
            assert entered.wait(1)
            assert pool.submit(radio.read_clock_if_idle).result(timeout=0.25) is None
            assert transport.writes == ["VOL"]
        finally:
            release.set()
        assert control.result(timeout=1) == 3


def test_idle_clock_timeout_releases_command_lane():
    transport = FakeTransport()
    radio = SDS200.from_transport(transport)
    with radio:
        with pytest.raises(CommandTimeoutError):
            radio.read_clock_if_idle(timeout=0.01)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(radio.send, "VER").result(timeout=0.5)
    assert transport.writes == ["DTM", "VER"]


@pytest.mark.parametrize(
    "reply,error", [("DTM,NG", CommandRejectedError), ("DTM,OK", ProtocolError)]
)
def test_idle_clock_rejection_or_bad_response_releases_command_lane(reply, error):
    class ReplyingTransport(FakeTransport):
        def write_command(self, command):
            super().write_command(command)
            if command == "DTM":
                self.feed_line(reply)
            else:
                assert command == "VOL"
                self.feed_line("VOL,3")

    transport = ReplyingTransport()
    radio = SDS200.from_transport(transport)
    with radio:
        with pytest.raises(error):
            radio.read_clock_if_idle()
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(radio.get_volume, timeout=0.5).result(timeout=1) == 3
    assert transport.writes == ["DTM", "VOL"]


@pytest.mark.parametrize("timeout", [0, -1, 0.51, 2, True, float("nan"), float("inf")])
def test_idle_clock_rejects_invalid_or_expanded_budget(timeout):
    transport = FakeTransport()
    radio = SDS200.from_transport(transport)
    with pytest.raises((TypeError, ValueError)):
        radio.read_clock_if_idle(timeout=timeout)
    assert transport.writes == []
