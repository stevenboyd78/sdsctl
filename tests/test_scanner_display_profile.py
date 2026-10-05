"""Synthetic scanner-display profiles only; never copy a user's profile.cfg."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError

import pytest

from sds200 import scanner_display_profile as display
from sds200.scanner_display_profile import (
    ScannerDisplayMode,
    ScannerDisplayProfileError,
    parse_scanner_display_profile,
)

_SETTINGS = "\t".join(
    ["DisplayOption", "", "", "", "", "", "DEC", "", "", "", "", "Off", "AFS", "COLOR"]
)
_ITEMS = "DispOptItems\tDispOptId=1\tDispLayoutId=1\tFrequency\tEmpty\t"
_COLORS = "DispColors\tDispColorId=2\tColorLayoutId=1\tFFD600\t000000"
_MINIMAL = "\r\n".join([_SETTINGS, _ITEMS, _COLORS]).encode("ascii")
_EXPECTED_IDS = [(1, 1), (2, 6), (3, 2), (4, 7), (5, 3), (6, 4), (7, 5)]


def _all_modes() -> bytes:
    # Counts follow the documented screen families. Slot choices/colors are
    # invented, not a dump of the user's supplied profile.
    lines = ["TargetModel\tBCDx36HP", "ProductName\tSDS200", "FormatVersion\t1.00", _SETTINGS]
    for layout_id, color_layout_id in _EXPECTED_IDS:
        if layout_id <= 2:
            option_counts = {1: 3, 2: 2, 3: 8, 4: 10}
            color_counts = {1: 6, 2: 3, 3: 8, 4: 2, 5: 10, 6: 6, 7: 5}
        elif layout_id <= 4:
            option_counts = {1: 3, 2: 12, 3: 6, 4: 10}
            color_counts = {1: 9, 2: 3, 3: 6, 4: 12, 5: 10, 6: 5, 7: 5}
        else:
            option_counts = {2: 8, 3: 6, 4: 10}
            color_counts = {1: 11, 3: 6, 4: 8, 5: 10, 6: 5, 7: 5}
        for group_id, count in option_counts.items():
            lines.append(
                "\t".join(
                    [
                        "DispOptItems",
                        f"DispOptId={group_id}",
                        f"DispLayoutId={layout_id}",
                        *[f"FutureOption_{layout_id}_{group_id}_{index}" for index in range(count)],
                    ]
                )
            )
        for group_id, count in color_counts.items():
            lines.append(
                "\t".join(
                    [
                        "DispColors",
                        f"DispColorId={group_id}",
                        f"ColorLayoutId={color_layout_id}",
                        *[
                            value
                            for index in range(count)
                            for value in (
                                f"{color_layout_id:02x}{group_id:02x}{index:02x}",
                                "102030",
                            )
                        ],
                    ]
                )
            )
    return ("\r\n".join(lines) + "\r\n").encode("ascii")


def test_all_seven_modes_have_distinct_screen_and_color_mapping() -> None:
    profile = parse_scanner_display_profile(_all_modes())
    assert len(profile.option_groups) == 25
    assert len(profile.color_groups) == 46
    assert dict(profile.metadata) == {
        "TargetModel": "BCDx36HP",
        "ProductName": "SDS200",
        "FormatVersion": "1.00",
    }
    assert [mode.layout_ids for mode in ScannerDisplayMode] == _EXPECTED_IDS
    for mode, (layout_id, color_layout_id) in zip(ScannerDisplayMode, _EXPECTED_IDS, strict=True):
        groups = profile.options_for(mode)
        colors = profile.colors_for(mode)
        assert groups and colors
        assert all(group.layout_id == layout_id for group in groups)
        assert all(group.color_layout_id == color_layout_id for group in colors)
        assert groups[0].items[0].startswith(f"FutureOption_{layout_id}_")
        assert colors[0].colors[0].text.startswith(f"{color_layout_id:02x}")


@pytest.mark.parametrize("line_ending", [b"\r\n", b"\n", b"\r"])
@pytest.mark.parametrize("final_ending", [False, True])
def test_line_endings_empty_slots_and_color_normalization(
    line_ending: bytes, final_ending: bool
) -> None:
    source = _MINIMAL.replace(b"\r\n", line_ending) + (line_ending if final_ending else b"")
    profile = parse_scanner_display_profile(source)
    assert profile.option_groups[0].items == ("Frequency", "Empty", "")
    assert profile.color_groups[0].colors[0].text == "ffd600"
    assert profile.color_groups[0].colors[0].background == "000000"
    assert source.startswith(_SETTINGS.encode("ascii"))  # Input bytes unchanged.


@pytest.mark.parametrize("motorola", ["DEC", "HEX"])
@pytest.mark.parametrize("simple", ["Off", "On"])
@pytest.mark.parametrize("edacs", ["AFS", "DEC"])
@pytest.mark.parametrize("color", ["COLOR", "BLACK", "WHITE"])
def test_positional_display_options(motorola: str, simple: str, edacs: str, color: str) -> None:
    fields = _SETTINGS.split("\t")
    for index in [1, 2, 3, 4, 5, 7, 8, 9, 10]:
        fields[index] = "RESERVED_PRIVATE_SENTINEL"
    fields[6], fields[11], fields[12], fields[13] = motorola, simple, edacs, color
    fields.append("APPENDED_RESERVED_PRIVATE_SENTINEL")
    profile = parse_scanner_display_profile(
        "\r\n".join(["\t".join(fields), _ITEMS, _COLORS]).encode()
    )
    assert profile.options == display.ScannerDisplayOptions(motorola, simple == "On", edacs, color)
    assert "SENTINEL" not in repr(profile)
    assert "SENTINEL" not in json.dumps(profile.as_dict())


def test_unrelated_records_never_enter_projection_or_revision() -> None:
    baseline = parse_scanner_display_profile(_MINIMAL)
    source = (
        b"Owner\tPRIVATE_OWNER_SENTINEL\r\nLocation\tPRIVATE_LOCATION_SENTINEL\r\n"
        b"UnknownExtension\tPRIVATE_EXTENSION_SENTINEL\r\n" + _MINIMAL
    )
    profile = parse_scanner_display_profile(source)
    assert profile == baseline
    assert profile.revision == baseline.revision
    assert "PRIVATE" not in json.dumps(profile.as_dict())
    assert "PRIVATE" not in repr(profile)
    assert not hasattr(profile, "raw_bytes")


def test_revision_normalizes_records_but_tracks_slot_order_and_colors() -> None:
    source = _all_modes()
    baseline = parse_scanner_display_profile(source)
    reversed_records = b"\n".join(reversed(source.splitlines()))
    assert parse_scanner_display_profile(reversed_records).revision == baseline.revision
    assert len(baseline.revision) == 64
    changed = source.replace(b"FutureOption_1_1_0", b"FutureOption_1_1_changed", 1)
    assert parse_scanner_display_profile(changed).revision != baseline.revision
    swapped = _MINIMAL.replace(b"Frequency\tEmpty\t", b"Empty\tFrequency\t")
    assert (
        parse_scanner_display_profile(swapped).revision
        != parse_scanner_display_profile(_MINIMAL).revision
    )
    changed_color = _MINIMAL.replace(b"FFD600", b"FF0000")
    assert (
        parse_scanner_display_profile(changed_color).revision
        != parse_scanner_display_profile(_MINIMAL).revision
    )
    assert (
        parse_scanner_display_profile(_MINIMAL.replace(b"FFD600", b"ffd600")).revision
        == parse_scanner_display_profile(_MINIMAL).revision
    )


def test_immutable_profile_with_detached_descriptor_and_no_filled_missing_modes() -> None:
    profile = parse_scanner_display_profile(_MINIMAL)
    assert profile.options_for(ScannerDisplayMode.TONE_OUT) == ()
    assert profile.colors_for(ScannerDisplayMode.TONE_OUT) == ()
    for obj, name in [
        (profile, "metadata"),
        (profile.options, "color_mode"),
        (profile.option_groups[0], "items"),
        (profile.color_groups[0], "colors"),
        (profile.color_groups[0].colors[0], "text"),
    ]:
        with pytest.raises(FrozenInstanceError):
            setattr(obj, name, "changed")
    first = profile.as_dict()
    # Mutating a nested export must not change the immutable source descriptor.
    assert isinstance(first["option_groups"], list)
    first["option_groups"][0]["items"][0] = "changed"
    assert isinstance(first["color_groups"], list)
    first["color_groups"][0]["colors"][0]["text"] = "ff0000"
    assert isinstance(first["options"], dict)
    first["options"]["color_mode"] = "BLACK"
    first.clear()
    assert profile.as_dict()["schema_version"] == 1
    assert profile == parse_scanner_display_profile(_MINIMAL)


def test_color_pair_order_is_preserved_without_semantic_inversion() -> None:
    source = _MINIMAL.replace(b"FFD600\t000000", b"112233\t445566\tAABBCC\tDDEEFF")
    colors = parse_scanner_display_profile(source).color_groups[0].colors
    assert colors == (
        display.ScannerDisplayColor("112233", "445566"),
        display.ScannerDisplayColor("aabbcc", "ddeeff"),
    )


@pytest.mark.parametrize("extra", [_SETTINGS, _ITEMS, _COLORS, "ProductName\tSDS100"])
def test_duplicates_rejected_even_when_identical(extra: str) -> None:
    source = b"ProductName\tSDS100\r\n" + _MINIMAL + b"\r\n" + extra.encode()
    with pytest.raises(ScannerDisplayProfileError, match="duplicate") as raised:
        parse_scanner_display_profile(source)
    assert raised.value.line_number == 5


@pytest.mark.parametrize(
    "record",
    [
        "DisplayOption",
        "DisplayOption\tDEC\tOff\tAFS\tCOLOR",
        _SETTINGS.replace("DEC", "decimal"),
        _SETTINGS.replace("Off", "false"),
        _SETTINGS.replace("AFS", "HEX"),
        _SETTINGS.replace("COLOR", "color"),
        "DispOptItems",
        "DispOptItems\tDispOptId=1\tDispLayoutId=1",
        _ITEMS.replace("DispOptId=1", "DispColorId=1"),
        _ITEMS.replace("DispOptId=1", "DispOptId=0"),
        _ITEMS.replace("DispOptId=1", "DispOptId=5"),
        _ITEMS.replace("DispLayoutId=1", "DispLayoutId=8"),
        _ITEMS.replace("DispLayoutId=1", "DispLayoutId=01"),
        _ITEMS.replace("DispLayoutId=1", "DispLayoutId=-1"),
        _ITEMS.replace("Frequency", "<script>PRIVATE_SENTINEL</script>"),
        _ITEMS.replace("Frequency", 'PRIVATE_SENTINEL"'),
        "DispColors",
        "DispColors\tDispColorId=1\tColorLayoutId=1",
        _COLORS.rsplit("\t", 1)[0],
        _COLORS + "\t",
        _COLORS.replace("DispColorId=2", "DispOptId=2"),
        _COLORS.replace("DispColorId=2", "DispColorId=8"),
        _COLORS.replace("ColorLayoutId=1", "DispLayoutId=1"),
        _COLORS.replace("ColorLayoutId=1", "ColorLayoutId=0"),
        _COLORS.replace("FFD600", "#ffd600"),
        _COLORS.replace("FFD600", "gold"),
        _COLORS.replace("FFD600", "gggggg"),
        _COLORS.replace("FFD600", "ffffff00"),
        _COLORS.replace("FFD600", ""),
        _COLORS.replace("FFD600", " ffd600"),
        "ProductName",
        "ProductName\t",
        "ProductName\tSDS200\textra",
        "ProductName\tPRIVATE_SENTINEL<script>",
        "FormatVersion\t" + "X" * 65,
    ],
)
def test_known_malformed_records_fail_with_sanitized_error(record: str) -> None:
    with pytest.raises(ScannerDisplayProfileError) as raised:
        parse_scanner_display_profile(record.encode() + b"\n" + _MINIMAL)
    assert raised.value.line_number in {1, 2}
    assert "PRIVATE_SENTINEL" not in str(raised.value)
    assert raised.value.__context__ is None


@pytest.mark.parametrize("invalid", [b"\x00", b"\x1b", b"\x7f", b"\x0b", b"\xff", b"\xc3\xa9"])
def test_invalid_encoding_and_controls_rejected_without_decode_exception(invalid: bytes) -> None:
    with pytest.raises(ScannerDisplayProfileError, match="ASCII") as raised:
        parse_scanner_display_profile(b"Owner\tPRIVATE_SENTINEL" + invalid + b"\n" + _MINIMAL)
    assert "PRIVATE_SENTINEL" not in str(raised.value)
    assert raised.value.__context__ is None


@pytest.mark.parametrize(
    "source",
    [
        b"",
        b"\r\n",
        b"Owner\tPrivate",
        _SETTINGS.encode(),
        (_SETTINGS + "\n" + _ITEMS).encode(),
        (_SETTINGS + "\n" + _COLORS).encode(),
    ],
)
def test_missing_display_records_refused(source: bytes) -> None:
    with pytest.raises(ScannerDisplayProfileError, match="missing"):
        parse_scanner_display_profile(source)


@pytest.mark.parametrize("source", ["not bytes", bytearray(_MINIMAL), memoryview(_MINIMAL), None])
def test_only_immutable_bytes_input(source: object) -> None:
    with pytest.raises(TypeError, match="must be bytes"):
        parse_scanner_display_profile(source)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "limit,accepted,rejected",
    [
        ("MAX_PROFILE_BYTES", _MINIMAL, _MINIMAL + b"\n"),
        ("MAX_RECORDS", _MINIMAL, _MINIMAL + b"\nOwner\tPrivate"),
    ],
)
def test_total_limits_at_boundary(
    monkeypatch: pytest.MonkeyPatch, limit: str, accepted: bytes, rejected: bytes
) -> None:
    monkeypatch.setattr(display, limit, len(_MINIMAL) if limit == "MAX_PROFILE_BYTES" else 3)
    parse_scanner_display_profile(accepted)
    with pytest.raises(ScannerDisplayProfileError, match="limit exceeded"):
        parse_scanner_display_profile(rejected)


def test_record_limit_counts_blank_lines_and_not_final_terminator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(display, "MAX_RECORDS", 3)
    parse_scanner_display_profile(_MINIMAL + b"\r\n")
    with pytest.raises(ScannerDisplayProfileError, match="record limit"):
        parse_scanner_display_profile(_MINIMAL + b"\r\n\r\n")


@pytest.mark.parametrize(
    "limit,boundary_record,oversize_record",
    [
        ("MAX_RECORD_BYTES", b"Unknown\t" + b"x\t" * 8188, b"Unknown\t" + b"x\t" * 8189),
        ("MAX_FIELDS", b"Unknown" + b"\t" * 255, b"Unknown" + b"\t" * 256),
        ("MAX_FIELD_BYTES", b"Unknown\t" + b"x" * 1024, b"Unknown\t" + b"x" * 1025),
    ],
)
def test_per_record_limits_cover_ignored_private_records(
    monkeypatch: pytest.MonkeyPatch, limit: str, boundary_record: bytes, oversize_record: bytes
) -> None:
    if limit == "MAX_RECORD_BYTES":
        monkeypatch.setattr(display, "MAX_FIELDS", 20_000)
    parse_scanner_display_profile(boundary_record + b"\n" + _MINIMAL)
    with pytest.raises(ScannerDisplayProfileError, match="limit exceeded"):
        parse_scanner_display_profile(oversize_record + b"\n" + _MINIMAL)


@pytest.mark.parametrize(
    "tag,id1,id2,values",
    [
        ("DispOptItems", "DispOptId=4", "DispLayoutId=7", ["Empty"]),
        ("DispColors", "DispColorId=7", "ColorLayoutId=7", ["123abc", "000000"]),
    ],
)
def test_group_item_limit(tag: str, id1: str, id2: str, values: list[str]) -> None:
    boundary = "\t".join([tag, id1, id2, *(values * 64)]).encode()
    parse_scanner_display_profile(_MINIMAL + b"\n" + boundary)
    oversize = boundary + b"\t" + "\t".join(values).encode()
    with pytest.raises(ScannerDisplayProfileError, match="group length"):
        parse_scanner_display_profile(_MINIMAL + b"\n" + oversize)


def test_safe_unknown_tokens_and_token_length_boundary() -> None:
    tokens = ["CTCSS/DCS", "Volume&Squelch", "Rssi Bar", "", "Empty", "Future_+.-", "X" * 64]
    source = _MINIMAL.replace(b"Frequency\tEmpty\t", "\t".join(tokens).encode())
    assert parse_scanner_display_profile(source).option_groups[0].items == tuple(tokens)
    with pytest.raises(ScannerDisplayProfileError, match="option token"):
        parse_scanner_display_profile(source.replace(b"X" * 64, b"X" * 65))


def test_real_byte_limit_checked_before_record_parsing() -> None:
    with pytest.raises(ScannerDisplayProfileError, match="byte limit"):
        parse_scanner_display_profile(b"x" * (display.MAX_PROFILE_BYTES + 1))
