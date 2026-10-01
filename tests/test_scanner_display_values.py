from __future__ import annotations

from dataclasses import replace

import pytest

from sds200.scanner_display_layout import resolve_scanner_display_screen
from sds200.scanner_display_profile import (
    ScannerDisplayDataFamily,
    ScannerDisplayMode,
    parse_scanner_display_profile,
)
from sds200.scanner_display_values import (
    MAX_DISPLAY_VALUE_LENGTH,
    scanner_display_values,
)
from sds200.scanner_display_values import ScannerDisplayValueStatus as Status
from sds200.state import RadioStateSnapshot

MODE = ScannerDisplayMode.SIMPLE_CONVENTIONAL


@pytest.mark.parametrize("family", list(ScannerDisplayDataFamily))
@pytest.mark.parametrize("mode", list(ScannerDisplayMode))
def test_data_family_must_match_but_does_not_assert_manual_simple_detail_layout(family, mode):
    original = screen_for("Frequency")
    screen = replace(original, layout=replace(original.layout, requested_mode=mode))
    values = scanner_display_values(
        screen, RadioStateSnapshot(frequency="00949000"), source_family=family, current=True
    )
    region_id = next(slot.region.id for slot in screen.regions if slot.token == "Frequency")
    frequency = next(value for value in values if value.region_id == region_id)
    if family is mode.data_family:
        assert frequency.status is Status.RAW_SOURCE and frequency.text == "00949000"
    else:
        assert frequency.status is Status.MODE_MISMATCH and frequency.text is None


def test_family_qualification_still_clears_stale_values():
    values = scanner_display_values(
        screen_for("Frequency"),
        RadioStateSnapshot(frequency="00949000"),
        source_family=ScannerDisplayDataFamily.CONVENTIONAL,
    )
    assert all(value.text is None for value in values)
    assert any(value.status is Status.NOT_CURRENT for value in values)


@pytest.mark.parametrize("family", [True, "conventional", 1])
def test_family_qualification_requires_enum_not_user_text(family):
    with pytest.raises(ValueError, match="source family"):
        scanner_display_values(screen_for("Frequency"), RadioStateSnapshot(), source_family=family)


def test_exact_mode_and_family_cannot_be_combined_to_bypass_mismatch():
    with pytest.raises(ValueError, match="not both"):
        scanner_display_values(
            screen_for("Frequency"),
            RadioStateSnapshot(),
            source_mode=MODE,
            source_family=ScannerDisplayDataFamily.TRUNK,
            current=True,
        )


def screen_for(token, group=2):
    settings = "DisplayOption\t\t\t\t\t\tHEX\t\t\t\t\tOn\tAFS\tCOLOR"
    count = {1: 3, 2: 2, 3: 8, 4: 10}[group]
    options = "\t".join(
        ["DispOptItems", f"DispOptId={group}", "DispLayoutId=1", token, *["Empty"] * (count - 1)]
    )
    colors = "DispColors\tDispColorId=2\tColorLayoutId=1\tffffff\t000000"
    return resolve_scanner_display_screen(
        parse_scanner_display_profile("\r\n".join((settings, options, colors)).encode()), MODE
    )


def value_for(token, snapshot, *, group=2, current=True, source_mode=MODE):
    screen = screen_for(token, group)
    region_id = next(
        slot.region.id
        for slot in screen.regions
        if slot.region.option is not None and slot.region.option.group == group
    )
    return next(
        value
        for value in scanner_display_values(
            screen, snapshot, current=current, source_mode=source_mode
        )
        if value.region_id == region_id
    )


@pytest.mark.parametrize(
    "token", ["PRI", "CC", "WxPRI", "REC", "IFX", "GPS", "SCR", "REP", "LVL", "Modulation", "P_Ch"]
)
def test_documented_icon_membership_does_not_claim_live_glyph_support(token):
    value = value_for(token, RadioStateSnapshot(recording="On"), group=4)
    assert value.status is Status.UNQUALIFIED and value.text is None
    assert value.source_fields == ()


@pytest.mark.parametrize(
    "token", ["Volume", "Squelch", "Time", "Day", "ATT", "P25Status", "Frequency"]
)
def test_non_icon_tokens_cannot_populate_icon_area(token):
    value = value_for(token, RadioStateSnapshot(volume=5, p25_status="P25"), group=4)
    assert value.status is Status.INVALID_REGION and value.text is None


def test_owner_manual_sample_ref_is_not_a_saved_token_alias():
    value = value_for("REF", RadioStateSnapshot(), group=4)
    assert value.status is Status.UNKNOWN_TOKEN and value.text is None


def test_icon_membership_does_not_bypass_freshness_gate():
    value = value_for("REC", RadioStateSnapshot(recording="On"), group=4, current=False)
    assert value.status is Status.NOT_CURRENT and value.text is None


@pytest.mark.parametrize(
    ("token", "group", "field", "raw"),
    [
        ("SiteName", 1, "site", "Demo North"),
        ("SiteName", 2, "site", "Demo South"),
        ("Frequency", 1, "frequency", "00949000"),
        ("Frequency", 2, "frequency", "94.900000"),
        ("CTCSS/DCS", 1, "sub_audio_detected", "NAC:012"),
        ("ServiceType", 2, "service_type", "002"),
        ("TGID", 1, "talkgroup_id", "00101"),
        ("UnitId", 2, "unit_id", "0x001F"),
        ("P25Status", 3, "p25_status", "Data"),
        ("REC", 3, "recording", "Off"),
        ("REC", 3, "recording", "On"),
    ],
)
def test_source_text_is_preserved_without_invented_lcd_format(token, group, field, raw):
    value = value_for(token, RadioStateSnapshot(**{field: raw}), group=group)
    assert value.status is Status.RAW_SOURCE and value.text == raw
    assert value.source_fields == (field,)


@pytest.mark.parametrize(
    ("token", "group", "kwargs", "expected", "fields"),
    [
        ("Volume", 3, {"volume": 0}, "0", ("volume",)),
        ("Squelch", 3, {"squelch": 0}, "0", ("squelch",)),
        ("Volume&Squelch", 1, {"volume": 0, "squelch": 5}, "0 / 5", ("volume", "squelch")),
        ("Volume&Squelch", 2, {"volume": 7, "squelch": 0}, "7 / 0", ("volume", "squelch")),
        ("Rssi", 2, {"rssi": -67.5}, "-67.5", ("rssi",)),
        ("Rssi", 2, {"rssi": -75.0}, "-75", ("rssi",)),
        ("Rssi", 2, {"rssi": 0.0}, "0", ("rssi",)),
        ("Rssi", 2, {"rssi": 0}, "0", ("rssi",)),
    ],
)
def test_raw_scalars_preserve_zero_and_do_not_invent_units(token, group, kwargs, expected, fields):
    value = value_for(token, RadioStateSnapshot(**kwargs), group=group)
    assert value.status is Status.RAW_SOURCE and value.text == expected
    assert value.source_fields == fields


@pytest.mark.parametrize(
    ("token", "group"),
    [("SiteName", 1), ("Rssi", 2), ("Volume", 3), ("REC", 3), ("Volume&Squelch", 1)],
)
def test_missing_values_are_unavailable_not_zero(token, group):
    value = value_for(token, RadioStateSnapshot(), group=group)
    assert value.status is Status.DATA_UNAVAILABLE and value.text is None


@pytest.mark.parametrize("missing", ["volume", "squelch"])
def test_combined_value_does_not_keep_a_previous_half(missing):
    first = RadioStateSnapshot(volume=8, squelch=2)
    assert value_for("Volume&Squelch", first).text == "8 / 2"
    second = replace(first, **{missing: None})
    value = value_for("Volume&Squelch", second)
    assert value.status is Status.DATA_UNAVAILABLE and value.text is None


@pytest.mark.parametrize(
    ("token", "group"),
    [
        ("BattVoltage", 2),
        ("Modulation", 3),
        ("Rssi Bar", 2),
        ("Day", 3),
        ("Time", 3),
        ("SystemId", 1),
        ("USB1_vbus", 2),
        ("FL_Name", 1),
        ("ATT", 3),
        ("REC", 4),
    ],
)
def test_unqualified_fields_never_use_related_but_unproven_values(token, group):
    value = value_for(
        token,
        RadioStateSnapshot(battery=4.0, rssi=-65, modulation="P25", recording="On", system="Demo"),
        group=group,
    )
    assert value.status is Status.UNQUALIFIED and value.text is None
    assert value.source_fields == ()


@pytest.mark.parametrize(("token", "group"), [("REC", 1), ("Volume", 2), ("Frequency", 3)])
def test_known_token_in_wrong_region_is_not_presented(token, group):
    value = value_for(
        token, RadioStateSnapshot(volume=5, frequency="00949000", recording="On"), group=group
    )
    assert value.status is Status.INVALID_REGION and value.text is None


def test_unknown_token_is_not_an_arbitrary_snapshot_attribute_lookup():
    value = value_for("battery", RadioStateSnapshot(battery=4.2))
    assert value.status is Status.UNKNOWN_TOKEN and value.text is None


@pytest.mark.parametrize(
    ("current", "source_mode", "status"),
    [
        (False, MODE, Status.NOT_CURRENT),
        (True, None, Status.MODE_UNQUALIFIED),
        (True, ScannerDisplayMode.SIMPLE_TRUNK, Status.MODE_MISMATCH),
        (True, ScannerDisplayMode.DETAIL_CONVENTIONAL, Status.MODE_MISMATCH),
    ],
)
def test_freshness_and_qualified_matching_mode_are_required(current, source_mode, status):
    snapshot = RadioStateSnapshot(system="Never show stale name", frequency="00949000")
    screen = screen_for("Frequency")
    values = scanner_display_values(screen, snapshot, current=current, source_mode=source_mode)
    assert all(value.text is None for value in values)
    assert next(value for value in values if value.region_id == "system").status is status


def test_default_call_is_safe_and_reconnect_does_not_retain_old_fields():
    screen = screen_for("Frequency")
    first = RadioStateSnapshot(system="Demo", frequency="00949000")
    assert all(value.text is None for value in scanner_display_values(screen, first))
    live = scanner_display_values(screen, first, current=True, source_mode=MODE)
    assert next(value for value in live if value.region_id == "system").text == "Demo"
    cleared = scanner_display_values(screen, RadioStateSnapshot(), current=True, source_mode=MODE)
    assert all(value.text is None for value in cleared)
    assert (
        next(value for value in cleared if value.region_id == "system").status
        is Status.DATA_UNAVAILABLE
    )


@pytest.mark.parametrize("bad", [True, "3", 2**40, float("nan")])
def test_levels_reject_bad_source_types_and_sizes(bad):
    value = value_for("Volume", RadioStateSnapshot(volume=bad), group=3)
    assert value.status is Status.INVALID_SOURCE and value.text is None


@pytest.mark.parametrize("bad", [True, "-65", float("nan"), float("inf"), -float("inf"), 1e20])
def test_rssi_rejects_nonfinite_or_invalid_source(bad):
    value = value_for("Rssi", RadioStateSnapshot(rssi=bad))
    assert value.status is Status.INVALID_SOURCE and value.text is None


def test_huge_integer_rssi_is_rejected_without_float_conversion_overflow():
    value = value_for("Rssi", RadioStateSnapshot(rssi=10**400))
    assert value.status is Status.INVALID_SOURCE and value.text is None


@pytest.mark.parametrize("sentinel", [-999, -999.0])
def test_scanner_rssi_unavailable_sentinel_is_not_a_signal_measurement(sentinel):
    value = value_for("Rssi", RadioStateSnapshot(rssi=sentinel))
    assert value.status is Status.DATA_UNAVAILABLE and value.text is None
    assert value.source_fields == ("rssi",)


@pytest.mark.parametrize(
    "bad",
    [
        123,
        "\x1b[31msecret",
        "line\nnext",
        "bidi\u202e",
        "\x7f",
        "\ud800",
        "A" * (MAX_DISPLAY_VALUE_LENGTH + 1),
    ],
)
def test_text_rejects_control_sequences_and_oversize_without_echo(bad):
    value = value_for("SiteName", RadioStateSnapshot(site=bad))
    assert value.status is Status.INVALID_SOURCE and value.text is None


def test_text_remains_text_not_interpreted_markup():
    raw = "<b>Demo & Test</b>"
    value = value_for("SiteName", RadioStateSnapshot(site=raw))
    assert value.status is Status.RAW_SOURCE and value.text == raw


@pytest.mark.parametrize(("token", "status"), [("", Status.BLANK), ("Empty", Status.EMPTY)])
def test_intentional_blank_and_empty_remain_distinct_even_offline(token, status):
    value = value_for(token, RadioStateSnapshot(), current=False)
    assert value.status is status and value.text is None


def test_invalid_mode_or_nonboolean_freshness_is_rejected():
    for kwargs in ({"current": 1}, {"source_mode": "simple_conventional"}):
        with pytest.raises(ValueError):
            scanner_display_values(screen_for("Frequency"), RadioStateSnapshot(), **kwargs)
