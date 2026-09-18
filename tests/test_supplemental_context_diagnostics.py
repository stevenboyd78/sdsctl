"""Private first-refusal evidence; no admission changes and no real scanner I/O."""

import json
from dataclasses import replace

import pytest

from sds200.daemon_display_read_research import DisplayReadKind, _selection
from sds200.xml_protocol import ScannerInfoParser

from .test_supplemental_research_launcher import launcher
from .test_supplemental_research_launcher import trial as trial


def frame(*, command="PSI", screen="trunk_scan", mode="Trunk Scan", records="<System/>"):
    parsed = ScannerInfoParser().parse(
        "PSI",
        '<ScannerInfo Mode="Trunk Scan" V_Screen="trunk_scan">' + records + "</ScannerInfo>",
    )
    return replace(parsed, command=command, screen=screen, mode=mode)


@pytest.mark.parametrize(
    "info,violations",
    [
        (None, ["not_scanner_info"]),
        (object(), ["not_scanner_info"]),
        (frame(command="GSI"), ["not_psi"]),
        (frame(screen="private"), ["unsupported_screen"]),
        (frame(mode="private"), ["mode_mismatch"]),
        (frame(records=""), ["system_record_count"]),
        (frame(records="<System/><System/>"), ["system_record_count", "duplicate_record_tag"]),
        (frame(records="<System/><Site/><Site/>"), ["duplicate_record_tag"]),
        (frame(records="<System/><PrivateSentinel/><PrivateSentinel/>"), ["duplicate_record_tag"]),
        (frame(records="<System/><PopupScreen/>"), ["excluded_record_tag"]),
        (frame(records="<System/><ConvFrequency/>"), ["excluded_record_tag"]),
        (
            frame(screen="conventional_scan", mode="Conventional Scan", records="<System/><TGID/>"),
            ["excluded_record_tag"],
        ),
        (
            frame(command="GSI", mode="private", records="<System/><System/><PlainText/>"),
            [
                "not_psi",
                "mode_mismatch",
                "system_record_count",
                "duplicate_record_tag",
                "excluded_record_tag",
            ],
        ),
    ],
)
def test_specific_refusal_facts_match_existing_guard(info, violations):
    assert _selection(info, DisplayReadKind.CLOCK) is None
    assert launcher.scan_context_shape(info)["violations"] == violations


@pytest.mark.parametrize("tag", sorted(launcher.EXCLUDED_SCAN_TAGS))
@pytest.mark.parametrize(
    "screen,mode", [("trunk_scan", "Trunk Scan"), ("conventional_scan", "Conventional Scan")]
)
def test_every_excluded_tag_is_named_without_values(tag, screen, mode):
    info = frame(
        screen=screen,
        mode=mode,
        records=f'<System/><{tag} Name="PRIVATE_VALUE">PRIVATE_TEXT</{tag}>',
    )
    summary = launcher.scan_context_shape(info)
    assert summary["excluded_record_tags"] == [tag]
    assert summary["violations"] == ["excluded_record_tag"]
    assert _selection(info, DisplayReadKind.CLOCK) is None
    assert "PRIVATE" not in json.dumps(summary)


@pytest.mark.parametrize("command", ["PSI", "GSI", "PRIVATE_COMMAND"])
@pytest.mark.parametrize("screen", ["trunk_scan", "conventional_scan", None, "PRIVATE_SCREEN"])
@pytest.mark.parametrize(
    "mode", ["Trunk Scan", "Trunk Scan Hold", "Conventional Scan", "Conventional Scan Hold", None]
)
@pytest.mark.parametrize(
    "records",
    [
        "<System/>",
        "",
        "<System/><System/>",
        "<System/><TGID/>",
        "<System/><ConvFrequency/>",
        "<System/><PrivateSentinel/>",
        "<System/><PrivateSentinel/><PrivateSentinel/>",
    ],
)
def test_diagnostic_explanation_matches_clock_and_favorites_selector(
    command, screen, mode, records
):
    """Mirrored labels cannot silently drift from the unchanged admission code."""
    info = frame(command=command, screen=screen, mode=mode, records=records)
    rejected = bool(launcher.scan_context_shape(info)["violations"])
    for kind in (DisplayReadKind.CLOCK, DisplayReadKind.FAVORITES):
        assert rejected == (_selection(info, kind) is None)


def test_unknown_strings_attributes_text_and_huge_counts_are_never_retained():
    records = '<System Name="PRIVATE_SYSTEM">PRIVATE_BODY</System>' * 300
    records += "".join(f"<PrivateSentinel{i}/><PrivateSentinel{i}/>" for i in range(400))
    info = frame(
        command="PRIVATE_COMMAND", screen="PRIVATE_SCREEN", mode="PRIVATE_MODE", records=records
    )
    summary = launcher.scan_context_shape(info)
    assert summary["command"] == summary["screen"] == summary["mode"] == "other"
    assert summary["system_record_count"] == summary["system_record_count_cap"] == 2
    assert summary["record_count"] == summary["record_count_cap"] == 256
    assert summary["other_duplicate_tag_count"] == summary["other_duplicate_tag_count_cap"] == 2
    assert summary["duplicate_known_tags"] == ["System"]
    encoded = json.dumps(summary)
    assert len(encoded) < 2048
    assert "PRIVATE" not in encoded and "PrivateSentinel" not in encoded


@pytest.mark.parametrize("trial", [(True, True)], indirect=True)
def test_first_rejection_is_private_immutable_and_permanently_stops_reads(trial):
    window, scanner, cache, clock, psi = trial
    assert window.arm()
    psi()
    assert cache.poll_once() and len(scanner.reads) == 1
    clock.now = 10.5
    window.observe_psi(frame(records="<System/><Site/><Site/>"))
    report = window.report()
    rejection = report["scan_rejection"]
    assert report["failure"] == "scan_context_changed"
    assert rejection["violations"] == ["duplicate_record_tag"]
    assert rejection["duplicate_known_tags"] == ["Site"]
    assert rejection["monotonic_seconds"] == 10.5 and not rejection["before_arm"]
    assert window.timing_report()["scan_rejection"] == rejection
    assert [e["event"] for e in window.timing_events][-2:] == ["context_rejected", "closed"]
    # Neither a new normal update nor a different rejected update replaces evidence.
    clock.now = 11
    psi()
    window.observe_psi(None)
    cache.poll_once()
    assert len(scanner.reads) == 1 and not window.allow_poll()
    assert window.report()["scan_rejection"] == rejection
    report["scan_rejection"]["violations"].clear()
    copied = window.timing_report()
    copied["scan_rejection"]["duplicate_known_tags"].clear()
    assert window.report()["scan_rejection"]["violations"] == ["duplicate_record_tag"]
    assert window.timing_report()["scan_rejection"]["duplicate_known_tags"] == ["Site"]


@pytest.mark.parametrize("trial", [(True, True)], indirect=True)
def test_prearm_rejection_records_structure_but_never_arms_or_reads(trial):
    window, scanner, cache, _, _ = trial
    window.observe_psi(frame(records=""))
    assert window.report()["scan_rejection"]["before_arm"]
    assert window.report()["scan_rejection"]["violations"] == ["system_record_count"]
    assert not window.timing_events
    assert not cache.poll_once() and not scanner.reads
    with pytest.raises(ValueError, match="rearmed"):
        window.arm()


@pytest.mark.parametrize("trial", [False, True], indirect=True)
def test_non_timing_cases_do_not_collect_rejection_structure(trial):
    window, scanner, _, _, _ = trial
    window.observe_psi(None)
    assert window.failure == "scan_context_changed" and not scanner.reads
    assert window.report()["scan_rejection"] is None
    assert window.timing_report()["scan_rejection"] is None


@pytest.mark.parametrize("trial", [(True, True)], indirect=True)
def test_successful_scan_and_other_stop_reasons_do_not_fabricate_rejection(trial):
    window, _, _, _, psi = trial
    assert window.arm()
    psi()
    assert window.report()["scan_rejection"] is None
    window.stop("cancelled")
    window.observe_psi(None)
    assert window.failure == "cancelled" and window.report()["scan_rejection"] is None


@pytest.mark.parametrize("trial", [(True, True)], indirect=True)
def test_selector_remains_authority_if_diagnostic_labels_drift(trial, monkeypatch):
    window, _, _, _, _ = trial
    monkeypatch.setattr(launcher, "_selection", lambda *_: None)
    window.observe_psi(frame())
    assert window.report()["scan_rejection"]["violations"] == ["unclassified_rejection"]
    assert window.failure == "scan_context_changed" and not window.allow_poll()


def test_maximum_allowlisted_report_stays_small_and_does_not_retain_mutable_input():
    info = frame(records="".join(f"<{tag}/><{tag}/>" for tag in sorted(launcher.DIAGNOSTIC_TAGS)))
    summary = launcher.scan_context_shape(info)
    assert summary["duplicate_known_tags"] == sorted(launcher.DIAGNOSTIC_TAGS)
    assert len(json.dumps(summary)) < 2048
    assert "raw_xml" not in summary and "nodes" not in summary


@pytest.mark.parametrize("trial", [(True, True)], indirect=True)
def test_trigger_serializes_same_rejection_and_restores_trace_without_reading(trial, tmp_path):
    window, scanner, _, _, _ = trial
    original = scanner.trace
    removed = []
    scanner.on_connection = lambda cb: lambda: removed.append("connection")
    scanner.on_packet = lambda cb: lambda: removed.append("packet")
    scanner.on_psi = lambda cb: (
        cb(frame(records='<System/><PopupScreen Name="PRIVATE"/>')),
        lambda: removed.append("psi"),
    )[1]
    trigger = launcher.SupplementalTrigger(
        tmp_path / "case", "Version 1.26.01", continuity=True, timing=True
    )
    trigger.window = window
    report = trigger.invoke(window.runtime)
    trigger.write("result.json", report)
    saved = json.loads((trigger.directory / "result.json").read_text())
    timing = json.loads((trigger.directory / "timing.json").read_text())
    assert saved["scan_rejection"] == timing["scan_rejection"]
    assert saved["scan_rejection"]["excluded_record_tags"] == ["PopupScreen"]
    assert saved["scan_rejection"]["before_arm"]
    assert not saved["research_started"] and not scanner.reads
    assert scanner.trace is original and removed == ["packet", "psi", "connection"]
    assert not timing["events"]
    assert "PRIVATE" not in json.dumps(saved) + json.dumps(timing)
