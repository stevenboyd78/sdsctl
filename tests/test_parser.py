import pytest

from sds200.exceptions import ProtocolError
from sds200.models import (
    ChargeStatus,
    FirmwareResponse,
    GstResponse,
    ModelResponse,
    Packet,
    StatusResponse,
    ValueResponse,
)
from sds200.parser import PacketParser


def test_model_response() -> None:
    parser = PacketParser()
    parsed = parser.parse_typed(parser.parse_packet("MDL,SDS200"))
    assert isinstance(parsed, ModelResponse)
    assert parsed.model == "SDS200"


def test_firmware_response() -> None:
    parser = PacketParser()
    parsed = parser.parse_typed(parser.parse_packet("VER,Version 1.23.00"))
    assert isinstance(parsed, FirmwareResponse)
    assert parsed.version == "Version 1.23.00"


def test_value_response() -> None:
    parser = PacketParser()
    parsed = parser.parse_typed(parser.parse_packet("VOL,12"))
    assert isinstance(parsed, ValueResponse)
    assert parsed.value == 12


@pytest.mark.parametrize("raw", ["VOL,OK", "SQL,OK", "VOL, ok "])
def test_level_set_acknowledgement_remains_packet(raw: str) -> None:
    parser = PacketParser()
    parsed = parser.parse_typed(parser.parse_packet(raw))

    assert isinstance(parsed, Packet)
    assert parsed.raw == raw


@pytest.mark.parametrize("raw", ["VOL,NOPE", "SQL,NG"])
def test_invalid_level_response_is_rejected(raw: str) -> None:
    parser = PacketParser()

    with pytest.raises(ProtocolError, match="expected an integer"):
        parser.parse_typed(parser.parse_packet(raw))


def test_status_preserves_display_lines() -> None:
    parser = PacketParser()
    raw = ",".join(
        (
            "STS",
            "00000",
            "System Name",
            "************************",
            "Channel Name",
            "________________________",
            "Line 3",
            "",
            "Line 4",
            "",
            "Line 5",
            "",
            "0",
            "1",
            "0",
            "0",
            "",
            "",
            "",
            "0",
            "OFF",
        )
    )
    parsed = parser.parse_typed(parser.parse_packet(raw))
    assert isinstance(parsed, StatusResponse)
    assert parsed.display_form == "00000"
    assert parsed.lines[0].text == "System Name"
    assert parsed.lines[1].text == "Channel Name"
    assert len(parsed.lines) == 5
    assert len(parsed.reserved) == 9


def test_status_rejects_undocumented_seven_reserved_field_shape() -> None:
    parser = PacketParser()
    raw = ",".join(
        (
            "STS",
            "11111",
            "Line 1",
            "*",
            "Line 2",
            "_",
            "Line 3",
            "",
            "Line 4",
            "",
            "Line 5",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
        )
    )

    with pytest.raises(ProtocolError, match="invalid field shape") as caught:
        parser.parse_typed(parser.parse_packet(raw))

    assert raw not in str(caught.value)


def test_status_rejects_missing_reserved_fields_without_exposing_payload() -> None:
    parser = PacketParser()
    raw = "STS,00000,SENSITIVE DISPLAY,MODE,0,1"

    with pytest.raises(ProtocolError, match="invalid field shape") as caught:
        parser.parse_typed(parser.parse_packet(raw))

    assert "SENSITIVE" not in str(caught.value)
    assert raw not in str(caught.value)


def test_status_rejects_odd_display_fields_without_exposing_payload() -> None:
    parser = PacketParser()
    raw = (
        "STS,00000,SENSITIVE DISPLAY,MODE,UNMATCHED,"
        "0,1,0,0,,,,0,OFF"
    )

    with pytest.raises(
        ProtocolError,
        match="invalid field shape",
    ) as caught:
        parser.parse_typed(parser.parse_packet(raw))

    assert "SENSITIVE" not in str(caught.value)
    assert "UNMATCHED" not in str(caught.value)
    assert raw not in str(caught.value)


def test_waterfall_status_preserves_exact_specification_shape() -> None:
    parser = PacketParser()
    raw = (
        "GST,01010,Line 1,*,Line 2,_,Line 3,,Line 4,****,Line 5,____,"
        "0,2,0,1,1555500,NFM,120,1550000,1540000,1560000,1,2"
    )
    packet = parser.parse_packet(raw)

    parsed = parser.parse_typed(packet)

    assert isinstance(parsed, GstResponse)
    assert parsed.packet is packet
    assert parsed.display_form == "01010"
    assert tuple(line.text for line in parsed.lines) == (
        "Line 1",
        "Line 2",
        "Line 3",
        "Line 4",
        "Line 5",
    )
    assert tuple(line.mode for line in parsed.lines) == ("*", "_", "", "****", "____")
    assert parsed.mute == "0"
    assert parsed.alert_led == "2"
    assert parsed.charge_led == "0"
    assert parsed.waterfall_mode == "1"
    assert parsed.marker_frequency == "1555500"
    assert parsed.modulation == "NFM"
    assert parsed.marker_position == "120"
    assert parsed.center_frequency == "1550000"
    assert parsed.lower_frequency == "1540000"
    assert parsed.upper_frequency == "1560000"
    assert parsed.color_mode == "1"
    assert parsed.fft_area_size == "2"


@pytest.mark.parametrize(
    "raw",
    [
        "GST",
        "GST,0101,Line 1,*",
        "GST,0101X,Line 1,*",
        (
            "GST,01010,Line 1,*,Line 2,_,Line 3,,Line 4,****,"
            "0,2,0,1,1555500,NFM,120,1550000,1540000,1560000,1,2"
        ),
        (
            "GST,01010,Line 1,*,Line 2,_,Line 3,,Line 4,****,Line 5,____,"
            "0,2,0,1,1555500,NFM,120,1550000,1540000,1560000,1,2,FUTURE"
        ),
    ],
)
def test_unsupported_waterfall_status_shapes_remain_generic(raw: str) -> None:
    parser = PacketParser()
    packet = parser.parse_packet(raw)

    parsed = parser.parse_typed(packet)

    assert parsed is packet


def test_sds150_reported_model_is_normalized() -> None:
    parser = PacketParser()
    parsed = parser.parse_typed(parser.parse_packet("MDL,SDS150GBT"))

    assert isinstance(parsed, ModelResponse)
    assert parsed.model == "SDS150"
    assert parsed.reported_model == "SDS150GBT"


def test_charge_status_response() -> None:
    parser = PacketParser()
    parsed = parser.parse_typed(
        parser.parse_packet(
            "GCS,CST=6,VOLT=4012mV:82%,CURR=0123mA,TEMP= 27.65C"
        )
    )

    assert isinstance(parsed, ChargeStatus)
    assert parsed.status == "charging"
    assert parsed.charging is True
    assert parsed.voltage_mv == 4012
    assert parsed.capacity_percent == 82
    assert parsed.current_ma == 123
    assert parsed.temperature_c == 27.65


def test_malformed_charge_status_is_rejected() -> None:
    parser = PacketParser()

    with pytest.raises(ProtocolError, match="Invalid GCS response"):
        parser.parse_typed(parser.parse_packet("GCS,CST=6,VOLT=bad"))



def test_pwf_typed_response_preserves_variable_fields_and_raw_packet() -> None:
    from sds200.models import PwfResponse

    parser = PacketParser()
    raw = "PWF,FFT-A,17,,23,FUTURE"
    packet = parser.parse_packet(raw)
    parsed = parser.parse_typed(packet)

    assert isinstance(parsed, PwfResponse)
    assert parsed.values == ("FFT-A", "17", "", "23", "FUTURE")
    assert parsed.packet is packet
    assert parsed.packet.raw == raw


def test_gwf_typed_response_preserves_exact_240_values_and_raw_packet() -> None:
    from sds200.models import GwfResponse

    parser = PacketParser()
    values = tuple(str(index) for index in range(240))
    raw = "GWF," + ",".join(values)
    packet = parser.parse_packet(raw)
    parsed = parser.parse_typed(packet)

    assert isinstance(parsed, GwfResponse)
    assert parsed.values == values
    assert parsed.packet is packet
    assert parsed.packet.raw == raw


def test_gwf_typed_response_accepts_specification_terminal_separator() -> None:
    from sds200.models import GwfResponse

    parser = PacketParser()
    values = tuple(str(index) for index in range(240))
    raw = "GWF," + ",".join(values) + ","
    packet = parser.parse_packet(raw)
    parsed = parser.parse_typed(packet)

    assert isinstance(parsed, GwfResponse)
    assert parsed.values == values
    assert parsed.packet is packet
    assert parsed.packet.fields == values + ("",)
    assert parsed.packet.raw == raw


@pytest.mark.parametrize("count", [0, 1, 239, 241])
def test_non_240_gwf_shapes_remain_lossless_generic_packets(
    count: int,
) -> None:
    parser = PacketParser()
    values = tuple(str(index) for index in range(count))
    raw = "GWF" if not values else "GWF," + ",".join(values)
    packet = parser.parse_packet(raw)

    parsed = parser.parse_typed(packet)

    assert parsed is packet
    assert parsed.command == "GWF"
    assert parsed.fields == values
    assert parsed.raw == raw
