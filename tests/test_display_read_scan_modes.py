"""Exact documented scan labels; synthetic PSI, never a scanner connection.

The September 18 mode trial retained conventional_scan + Scan Mode and the
known record presence below. This is an anonymized reconstruction, not raw
captured XML: it does not invent the eighth unclassified record or its values.
Scan Hold comes from Uniden Remote Command Specification V1.02 p18, not that
physical observation. Policy attributes here are fixtures, not causal claims.
"""

import pytest

from sds200.daemon_display_read_research import DisplayReadKind, _selection
from sds200.xml_protocol import ScannerInfoParser

from .test_supplemental_research_launcher import launcher
from .test_supplemental_research_launcher import trial as trial

KINDS = list(DisplayReadKind)
EXPECTED = {
    DisplayReadKind.CLOCK: (),
    DisplayReadKind.FAVORITES: (),
    DisplayReadKind.SYSTEM: (1,),
    DisplayReadKind.DEPARTMENT: (1, 23),
}
MODES = {
    "conventional_scan": ("Scan Mode", "Scan Hold"),
    "trunk_scan": ("Trunk Scan", "Trunk Scan Hold"),
}
EXCLUDED = (
    "PopupScreen",
    "PlainText",
    "ReplayDescription",
    "ReplayMode",
    "Button",
    "SrchFrequency",
    "CcHitsChannel",
    "WxChannel",
    "ToneOutChannel",
    "SystemStatus",
    "Analyze",
    "RfPowerPlot",
    "TGID",
)


def scan(
    *,
    mode="Scan Mode",
    screen="conventional_scan",
    command="PSI",
    favorites="01",
    system="23",
    system_count=1,
    extra="",
    channel=None,
):
    attribute = "" if mode is None else f'Mode="{mode}"'
    if channel is None:
        channel = "TGID" if screen == "trunk_scan" else "ConvFrequency"
    return ScannerInfoParser().parse(
        command,
        f'<ScannerInfo {attribute} V_Screen="{screen}">'
        f'<MonitorList Q_Key="{favorites}"/>'
        + f'<System Index="700" Q_Key="{system}"/>'
        * system_count
        + f"<Department/><{channel}/><Property/>"
        '<DualWatch PRI="Off" CC="Off" WX="Priority"/><OverWrite/>' + extra + "</ScannerInfo>",
    )


@pytest.mark.parametrize("mode", ["Scan Mode", "Scan Hold"])
@pytest.mark.parametrize("kind", KINDS)
def test_documented_conventional_labels_preserve_exact_read_scope(mode, kind):
    info = scan(mode=mode)
    assert info.mode == mode and info.screen == "conventional_scan"
    assert _selection(info, kind) == EXPECTED[kind]
    shape = launcher.scan_context_shape(info)
    assert shape["violations"] == [] and shape["mode_class"] == "documented"
    assert shape["present_known_tags"] == [
        "ConvFrequency",
        "Department",
        "DualWatch",
        "MonitorList",
        "OverWrite",
        "Property",
        "System",
    ]
    assert shape["dual_watch"] == {
        "record_status": "single",
        "PRI": "Off",
        "CC": "Off",
        "WX": "Priority",
    }


@pytest.mark.parametrize("screen", MODES)
@pytest.mark.parametrize("mode", ["Scan Mode", "Scan Hold", "Trunk Scan", "Trunk Scan Hold"])
@pytest.mark.parametrize("kind", KINDS)
def test_only_matching_screen_family_is_admitted(screen, mode, kind):
    expected = EXPECTED[kind] if mode in MODES[screen] else None
    assert _selection(scan(screen=screen, mode=mode), kind) == expected


@pytest.mark.parametrize(
    "mode",
    [
        None,
        "",
        " ",
        "Scan Mode ",
        " Scan Mode",
        "scan mode",
        "SCAN MODE",
        "Scan Hold ",
        "scan hold",
        "Scan  Mode",
        "Conventional Scan",
        "Conventional Scan Hold",
        "Close Call",
        "Close Call Only",
        "Menu tree",
        "Custom Search",
        "Service Scan",
        "Tone-Out",
        "PRIVATE_MODE",
    ],
)
@pytest.mark.parametrize("kind", KINDS)
def test_aliases_other_families_and_nonexact_labels_stay_refused(mode, kind):
    info = scan(mode=mode)
    assert _selection(info, kind) is None
    assert launcher.scan_context_shape(info)["violations"] == ["mode_mismatch"]


@pytest.mark.parametrize("mode", ["Scan Mode", "Scan Hold"])
@pytest.mark.parametrize(
    "screen",
    [
        "waterfall",
        "analyze_system_status",
        "wx_alert",
        "close_call",
        "cc_searching",
        "custom_search",
        "quick_search",
        "tone_out",
        "custom_with_scan",
        "cchits_with_scan",
        "menu_selection",
        "plain_text",
        "PRIVATE_SCREEN",
    ],
)
def test_documented_label_cannot_authorize_a_non_scan_screen(mode, screen):
    info = scan(mode=mode, screen=screen)
    assert all(_selection(info, kind) is None for kind in KINDS)


@pytest.mark.parametrize("mode", ["Scan Mode", "Scan Hold"])
@pytest.mark.parametrize("tag", EXCLUDED)
@pytest.mark.parametrize("kind", KINDS)
def test_every_existing_excluded_record_still_refuses_conventional_reads(mode, tag, kind):
    info = scan(mode=mode, extra=f"<{tag}/>")
    assert _selection(info, kind) is None
    assert "excluded_record_tag" in launcher.scan_context_shape(info)["violations"]


@pytest.mark.parametrize("mode", ["Scan Mode", "Scan Hold"])
@pytest.mark.parametrize(
    "tag",
    [
        "System",
        "MonitorList",
        "Department",
        "ConvFrequency",
        "Property",
        "DualWatch",
        "OverWrite",
        "PrivateTag",
    ],
)
@pytest.mark.parametrize("kind", KINDS)
def test_duplicate_record_guard_including_unknown_tags_still_applies(mode, tag, kind):
    extra = f"<{tag}/>" * (2 if tag == "PrivateTag" else 1)
    info = scan(mode=mode, extra=extra)
    assert _selection(info, kind) is None
    assert "duplicate_record_tag" in launcher.scan_context_shape(info)["violations"]


@pytest.mark.parametrize("mode", ["Scan Mode", "Scan Hold"])
@pytest.mark.parametrize("system_count", [0, 2])
@pytest.mark.parametrize("kind", KINDS)
def test_exactly_one_system_is_still_required(mode, system_count, kind):
    assert _selection(scan(mode=mode, system_count=system_count), kind) is None


@pytest.mark.parametrize("mode", ["Scan Mode", "Scan Hold"])
@pytest.mark.parametrize("kind", KINDS)
def test_gsi_is_still_not_read_authority(mode, kind):
    assert _selection(scan(mode=mode, command="GSI"), kind) is None


@pytest.mark.parametrize("mode", ["Scan Mode", "Scan Hold"])
@pytest.mark.parametrize(
    "favorites,system,scoped",
    [
        ("None", "None", (None, None)),
        ("1", "None", ((1,), None)),
        ("None", "23", (None, None)),
        ("100", "23", (None, None)),
        ("1", "100", (None, None)),
        ("", "23", (None, None)),
    ],
)
def test_scoped_reads_do_not_gain_index_unassigned_or_malformed_fallback(
    mode, favorites, system, scoped
):
    info = scan(mode=mode, favorites=favorites, system=system)
    assert _selection(info, DisplayReadKind.CLOCK) == ()
    assert _selection(info, DisplayReadKind.FAVORITES) == ()
    assert (
        tuple(
            _selection(info, kind) for kind in (DisplayReadKind.SYSTEM, DisplayReadKind.DEPARTMENT)
        )
        == scoped
    )


@pytest.mark.parametrize("trial", [(True, True)], indirect=True)
def test_mixed_exact_scan_families_complete_bounded_synthetic_window(trial):
    window, scanner, cache, clock, psi = trial
    conventional = scan(favorites="None", system="None")
    psi(conventional)
    assert not window.closed and not window.allow_poll() and not scanner.reads
    assert window.arm()
    pairs = [(screen, mode) for screen, modes in MODES.items() for mode in modes]
    for i in range(60):
        clock.now = 10 + (i // 2) * 2 + (i % 2) * 0.5
        screen, mode = pairs[i % len(pairs)]
        psi(scan(screen=screen, mode=mode, favorites="None", system="None"))
        assert window.allow_poll() and cache.poll_once()
    psi(conventional)
    psi(conventional)
    assert window.report()["status"] == "replies_and_psi_observed"
    assert window.report()["scan_rejection"] is None
    assert window.replies == {"DTM": 30, "FQK": 30}
    assert len(scanner.reads) == 60 and not window.allow_poll()
    with pytest.raises(ValueError, match="rearmed"):
        window.arm()


@pytest.mark.parametrize("trial", [(True, True)], indirect=True)
@pytest.mark.parametrize(
    "bad",
    [
        scan(mode="Conventional Scan"),
        scan(mode="Scan Mode", screen="trunk_scan"),
        scan(mode="Close Call", extra="<Site/><SiteFrequency/>"),
        scan(extra="<PopupScreen/>"),
        scan(extra='<DualWatch CC="DND"/>'),
    ],
)
def test_refusal_after_conventional_read_remains_terminal_and_immutable(trial, bad):
    window, scanner, cache, clock, psi = trial
    assert window.arm()
    psi(scan())
    assert cache.poll_once() and len(scanner.reads) == 1
    clock.now = 10.5
    psi(bad)
    first = window.report()["scan_rejection"]
    assert first["violations"] and window.closed and not window.allow_poll()
    psi(scan())
    psi(scan(mode="PRIVATE_MODE"))
    cache.poll_once()
    assert len(scanner.reads) == 1 and window.report()["scan_rejection"] == first
    assert window.timing_report()["scan_rejection"] == first
    with pytest.raises(ValueError, match="rearmed"):
        window.arm()
