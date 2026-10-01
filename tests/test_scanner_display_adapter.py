from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from sds200.exceptions import ProtocolError
from sds200.scanner_display_adapter import (
    DisplayConflictReason,
    DisplayLayoutBasis,
    ScannerAlertLed,
    ScannerDisplayAdapter,
    ScannerDisplayAdapterError,
    ScannerDisplayIndicators,
    ScannerDisplayStyle,
)
from sds200.scanner_display_adapter import DisplayObservationStatus as Status
from sds200.scanner_display_profile import ScannerDisplayMode
from sds200.scanner_display_profile_state import (
    DisplayProfileBinding,
    DisplayProfileRefreshFailure,
    DisplayProfileSourceKind,
    DisplayProfileStatus,
    ScannerDisplayProfileStore,
)
from sds200.scanner_display_values import ScannerDisplayValueStatus as ValueStatus
from sds200.xml_protocol import ScannerInfoParser

ENDPOINT = UUID(int=11)
BINDING = DisplayProfileBinding(ENDPOINT, UUID(int=12), DisplayProfileSourceKind.MANUAL_IMPORT)
NOW = datetime(2026, 9, 15, tzinfo=UTC)


@pytest.mark.parametrize("led", list(ScannerAlertLed))
@pytest.mark.parametrize("command", ["PSI", "GSI"])
def test_alert_led_is_exact_allowlisted_complete_observation_data(led, command):
    adapter, _ = live_adapter(info(content=f'<Property A_Led="{led.value}"/>', command=command))
    assert adapter.frame(store_for().snapshot(ENDPOINT), now=11).indicators.alert_led is led


@pytest.mark.parametrize("led", [None, "", "Orange", "yellow", "Yellow ", "url(x)", "x" * 500])
def test_missing_or_unrecognized_alert_led_is_not_off_or_css(led):
    content = "<Property/>" if led is None else f'<Property A_Led="{led}"/>'
    adapter, _ = live_adapter(info(content=content))
    assert adapter.frame(store_for().snapshot(ENDPOINT), now=11).indicators.alert_led is None


@pytest.mark.parametrize("trunk", [False, True])
def test_name_holds_are_independent_and_only_current_reported_values(trunk):
    screen, node = ("trunk_scan", "TGID") if trunk else ("conventional_scan", "ConvFrequency")
    sample = info(
        screen,
        '<System Hold="On"/><Department Hold="Off"/>'
        f'<{node} Hold="Maybe"/><Property A_Led="Yellow"/>',
    )
    adapter, session = live_adapter(sample)
    profile = store_for().snapshot(ENDPOINT)
    assert adapter.frame(profile, now=11).indicators == ScannerDisplayIndicators(
        ScannerAlertLed.YELLOW, True, False, None
    )
    adapter.observe(session, info(screen, ""), sequence=2, received_at=12, now=12)
    assert adapter.frame(profile, now=12).indicators == ScannerDisplayIndicators()


@pytest.mark.parametrize(
    "transition", ["stale", "disconnect", "reconnect", "popup", "unknown", "duplicate"]
)
def test_led_and_holds_do_not_survive_loss_of_qualification(transition):
    sample = info("trunk_scan", '<System Hold="On"/><TGID Hold="On"/><Property A_Led="Red"/>')
    adapter, session = live_adapter(sample)
    profile = store_for().snapshot(ENDPOINT)
    now = 12
    if transition == "stale":
        now = 16
    elif transition in {"disconnect", "reconnect"}:
        adapter.disconnect(session)
        if transition == "reconnect":
            adapter.begin_session(now=12)
    else:
        next_sample = {
            "popup": info("trunk_scan", '<Property A_Led="Red"/><PopupScreen/>'),
            "unknown": info("waterfall", '<Property A_Led="Red"/>'),
            "duplicate": info("trunk_scan", '<Property A_Led="Red"/><Property A_Led="Green"/>'),
        }[transition]
        adapter.observe(session, next_sample, sequence=2, received_at=12, now=12)
    assert adapter.frame(profile, now=now).indicators == ScannerDisplayIndicators()


def test_special_family_can_show_led_but_not_old_scanning_holds():
    adapter, _ = live_adapter(
        info(
            content='<System Hold="On"/><Department Hold="On"/><WxChannel/><Property A_Led="Cyan"/>'
        )
    )
    assert adapter.frame(store_for().snapshot(ENDPOINT), now=11).indicators == (
        ScannerDisplayIndicators(ScannerAlertLed.CYAN)
    )


def profile_bytes(simple=False):
    records = [f"DisplayOption\t\t\t\t\t\tDEC\t\t\t\t\t{'On' if simple else 'Off'}\tAFS\tCOLOR"]
    for mode in ScannerDisplayMode:
        layout_id, color_id = mode.layout_ids
        count = 2 if layout_id in (1, 2) else 12 if layout_id in (3, 4) else 8
        tokens = ["Frequency", "SiteName", *["Empty"] * (count - 2)]
        records.append(f"DispOptItems\tDispOptId=2\tDispLayoutId={layout_id}\t" + "\t".join(tokens))
        small_count = 8 if layout_id in (1, 2) else 6
        small = ["Volume", "Squelch", "REC", *["Empty"] * (small_count - 3)]
        records.append(f"DispOptItems\tDispOptId=3\tDispLayoutId={layout_id}\t" + "\t".join(small))
        records.append(f"DispColors\tDispColorId=2\tColorLayoutId={color_id}\tffffff\t000000")
    return "\r\n".join(records).encode()


def store_for(simple=False):
    store = ScannerDisplayProfileStore(ENDPOINT)
    ticket = store.begin_refresh(BINDING, started_at=NOW)
    preview = store.prepare(ticket, profile_bytes(simple), acquired_at=NOW)
    store.commit(preview, imported_at=NOW)
    return store


def info(screen="wx_alert", content='<WxChannel Name="Demo WX" Freq="01625500"/>', command="PSI"):
    return ScannerInfoParser().parse(
        command,
        f'<ScannerInfo Mode="Demo" V_Screen="{screen}">{content}</ScannerInfo>',
    )


def live_adapter(sample=None):
    adapter = ScannerDisplayAdapter(ENDPOINT, stale_after=5)
    session = adapter.begin_session(now=10)
    adapter.observe(
        session, info() if sample is None else sample, sequence=1, received_at=11, now=11
    )
    return adapter, session


def texts(frame):
    return {value.text for value in frame.values if value.text is not None}


def test_documented_weather_projection_joins_bound_import_without_claiming_sync():
    sample = info(
        content='<WxChannel Name="Demo" Freq="01625500"/><Property VOL="0" SQL="2" Rec="Off"/>'
    )
    adapter, _ = live_adapter(sample)
    profile = store_for().snapshot(ENDPOINT)
    frame = adapter.frame(profile, now=12)
    assert frame.status is Status.CURRENT
    assert frame.layout_basis is DisplayLayoutBasis.DOCUMENTED_SCREEN
    assert frame.profile_status is DisplayProfileStatus.LAST_IMPORTED
    assert frame.age_seconds == 1 and frame.sequence == 1
    assert frame.provenance == profile.last_good.provenance
    assert frame.profile_revision == profile.last_good.profile.revision
    assert frame.screen.layout.requested_mode is ScannerDisplayMode.WEATHER
    assert {"01625500", "0", "2", "Off"} <= texts(frame)
    with pytest.raises(FrozenInstanceError):
        frame.sequence = 9


@pytest.mark.parametrize("command", ["PSI", "GSI"])
@pytest.mark.parametrize(
    ("screen", "node", "mode"),
    [
        ("custom_search", "SrchFrequency", ScannerDisplayMode.SEARCH_CLOSE_CALL),
        ("quick_search", "SrchFrequency", ScannerDisplayMode.SEARCH_CLOSE_CALL),
        ("close_call", "SrchFrequency", ScannerDisplayMode.SEARCH_CLOSE_CALL),
        ("wx_alert", "WxChannel", ScannerDisplayMode.WEATHER),
        ("tone_out", "ToneOutChannel", ScannerDisplayMode.TONE_OUT),
    ],
)
def test_exact_documented_modes_use_real_parser_and_shared_snapshot(command, screen, node, mode):
    adapter, _ = live_adapter(info(screen, f'<{node} Freq="00949000"/>', command))
    frame = adapter.frame(store_for().snapshot(ENDPOINT), now=11)
    assert frame.status is Status.CURRENT
    assert frame.screen.layout.requested_mode is mode
    assert "00949000" in texts(frame)


def test_close_call_searching_does_not_invent_a_detected_frequency():
    adapter, _ = live_adapter(info("cc_searching", '<CC_Bands Name="Demo"/>'))
    frame = adapter.frame(store_for().snapshot(ENDPOINT), now=11)
    assert frame.status is Status.CURRENT
    assert not texts(frame)


@pytest.mark.parametrize("simple", [False, True])
@pytest.mark.parametrize("trunk", [False, True])
def test_profile_layout_is_unconfirmed_but_operating_data_remains_available(simple, trunk):
    screen = "trunk_scan" if trunk else "conventional_scan"
    node = "TGID" if trunk else "ConvFrequency"
    adapter, _ = live_adapter(info(screen, f'<System Name="Demo"/><{node} Name="Sample"/>'))
    frame = adapter.frame(store_for(simple).snapshot(ENDPOINT), now=11)
    assert frame.status is Status.CURRENT
    assert frame.layout_basis is DisplayLayoutBasis.PROFILE_PREFERENCE_UNCONFIRMED
    assert frame.screen.layout.requested_mode.value == (
        f"{'simple' if simple else 'detail'}_{'trunk' if trunk else 'conventional'}"
    )
    assert {"Demo", "Sample"} <= texts(frame)


@pytest.mark.parametrize("style", list(ScannerDisplayStyle))
@pytest.mark.parametrize("trunk", [False, True])
def test_explicit_style_is_per_consumer_presentation_not_a_persisted_scanner_toggle(style, trunk):
    adapter, _ = live_adapter(info("trunk_scan" if trunk else "conventional_scan", ""))
    store = store_for(simple=True)
    profile = store.snapshot(ENDPOINT)
    chosen = adapter.frame(profile, now=11, style=style)
    other = adapter.frame(profile, now=11)
    assert chosen.status is Status.CURRENT
    assert chosen.layout_basis is DisplayLayoutBasis.EXPLICIT_PRESENTATION_CHOICE
    assert chosen.screen.layout.requested_mode.value.startswith(style.value)
    assert other.screen.layout.requested_mode.value.startswith("simple")
    assert other.layout_basis is DisplayLayoutBasis.PROFILE_PREFERENCE_UNCONFIRMED
    assert chosen.profile_revision == other.profile_revision
    assert store.snapshot(ENDPOINT) == profile


@pytest.mark.parametrize("style", list(ScannerDisplayStyle))
def test_style_does_not_change_a_special_operating_screen(style):
    adapter, _ = live_adapter()
    frame = adapter.frame(store_for().snapshot(ENDPOINT), now=11, style=style)
    assert frame.layout_basis is DisplayLayoutBasis.DOCUMENTED_SCREEN
    assert frame.screen.layout.requested_mode is ScannerDisplayMode.WEATHER


@pytest.mark.parametrize("style", [True, "simple", 1])
def test_invalid_style_cannot_be_interpreted_as_a_toggle(style):
    adapter, _ = live_adapter()
    with pytest.raises(ScannerDisplayAdapterError, match="presentation style"):
        adapter.frame(store_for().snapshot(ENDPOINT), now=11, style=style)


@pytest.mark.parametrize(
    ("screen", "mode", "expected"),
    [
        ("conventional_scan", "Scan Mode", Status.CURRENT),
        ("conventional_scan", "Scan Hold", Status.CURRENT),
        ("trunk_scan", "Trunk Scan Hold", Status.CURRENT),
        ("quick_search", "Quick Search Hold", Status.CURRENT),
        ("tone_out", "Tone-Out", Status.CURRENT),
        ("tone_out", "Trunk Scan", Status.AMBIGUOUS_RECORDS),
        ("trunk_scan", "Quick Search", Status.AMBIGUOUS_RECORDS),
        ("conventional_scan", "Tone-Out", Status.AMBIGUOUS_RECORDS),
    ],
)
def test_known_operating_mode_and_visual_screen_must_not_conflict(screen, mode, expected):
    adapter, _ = live_adapter(replace(info(screen, ""), mode=mode))
    frame = adapter.frame(store_for().snapshot(ENDPOINT), now=11)
    assert frame.status is expected


@pytest.mark.parametrize(
    "screen,mode,channel,foreign",
    [
        ("conventional_scan", "Trunk Scan", "ConvFrequency", "TGID"),
        ("trunk_scan", "Scan Mode", "TGID", "ConvFrequency"),
    ],
)
def test_observed_scan_mode_lag_uses_matching_visual_screen_records(screen, mode, channel, foreign):
    adapter, session = live_adapter()
    profile = store_for().snapshot(ENDPOINT)
    sample = replace(info(screen, f'<{channel} Name="Current transition"/>'), mode=mode)
    assert adapter.observe(session, sample, sequence=2, received_at=12, now=12) is None
    frame = adapter.frame(profile, now=12)
    assert frame.status is Status.CURRENT and "Current transition" in texts(frame)
    expected_family = "trunk" if screen == "trunk_scan" else "conventional"
    assert frame.screen.layout.requested_mode.value.endswith(expected_family)
    assert "Current transition" not in texts(adapter.frame(profile, now=17))
    # No expected channel record is not qualified, nor is a mixed/duplicate one.
    for seq, body in enumerate(("", f"<{channel}/><{foreign}/>", f"<{channel}/><{channel}/>"), 3):
        rejected = replace(info(screen, body), mode=mode)
        assert (
            adapter.observe(session, rejected, sequence=seq, received_at=17 + seq, now=17 + seq)
            is not None
        )
        bad = adapter.frame(profile, now=17 + seq)
        assert bad.status is Status.AMBIGUOUS_RECORDS and not bad.values
    # A held-mode discrepancy has not been qualified by this observation.
    held = "Trunk Scan Hold" if mode == "Trunk Scan" else "Scan Hold"
    assert (
        adapter.observe(session, replace(sample, mode=held), sequence=6, received_at=23, now=23)
        is not None
    )


def test_frame_and_foreign_adapter_tickets_cannot_substitute_for_current_session():
    first, first_session = live_adapter()
    second, second_session = live_adapter()
    with pytest.raises(ScannerDisplayAdapterError, match="session"):
        first.observe(second_session, info(), sequence=2, received_at=12, now=12)
    with pytest.raises(ScannerDisplayAdapterError, match="session"):
        second.disconnect(first_session)
    assert second.frame(store_for().snapshot(ENDPOINT), now=12).status is Status.CURRENT


@pytest.mark.parametrize("cc", ["Off", "DND", "Priority"])
@pytest.mark.parametrize(
    "screen,channel,mode",
    [("trunk_scan", "TGID", "Trunk Scan"), ("conventional_scan", "ConvFrequency", "Scan Mode")],
)
def test_unqualified_close_call_scan_conflict_clears_values_and_recovers(cc, screen, channel, mode):
    # A matching channel node and a CC flag do not establish a safe exception.
    # These synthetic records test refusal, not live firmware qualification.
    sample = replace(
        info(
            screen,
            f'<System Name="Before" Hold="On"/><{channel} Name="Before channel"/>'
            f'<Property A_Led="Red"/><DualWatch CC="{cc}"/>',
        ),
        mode=mode,
    )
    adapter, session = live_adapter(sample)
    profile = store_for().snapshot(ENDPOINT)
    assert "Before" in texts(adapter.frame(profile, now=11))
    conflict = adapter.observe(
        session, replace(sample, mode="Close Call"), sequence=2, received_at=12, now=12
    )
    assert conflict is not None
    rejected = adapter.frame(profile, now=12)
    assert rejected.status is Status.AMBIGUOUS_RECORDS
    assert rejected.screen is None and not rejected.values
    assert rejected.indicators == ScannerDisplayIndicators()
    recovered = replace(info(screen, f'<System Name="After"/><{channel}/>'), mode=mode)
    assert adapter.observe(session, recovered, sequence=3, received_at=13, now=13) is None
    current = adapter.frame(profile, now=13)
    assert current.status is Status.CURRENT
    assert "After" in texts(current) and "Before" not in texts(current)
    assert current.indicators == ScannerDisplayIndicators()


CC_DND_TRUNK_RECORDS = {
    "System": '<System Name="New system" Hold="Off"/>',
    "Department": '<Department Name="New department" Hold="Off"/>',
    "Site": '<Site Name="New site" Mod="NFM"/>',
    "SiteFrequency": '<SiteFrequency Freq="08511250"/>',
    "TGID": '<TGID Name="New channel" Hold="Off"/>',
    "Property": '<Property A_Led="Off" VOL="3" SQL="2"/>',
    "DualWatch": '<DualWatch CC="DND" PRI="Off" WX="Off"/>',
    "OverWrite": '<OverWrite Text="Scanning..."/>',
}


def cc_dnd_trunk_info(*, omit=None, extra="", dual_watch=None):
    records = dict(CC_DND_TRUNK_RECORDS)
    if omit is not None:
        del records[omit]
    if dual_watch is not None:
        records["DualWatch"] = dual_watch
    return replace(info("trunk_scan", "".join(records.values()) + extra), mode="Close Call")


@pytest.mark.parametrize("omit", [None, "OverWrite"])
def test_observed_cc_dnd_trunk_reply_uses_current_records_and_keeps_freshness(omit):
    before = info("trunk_scan", '<System Name="Old system" Hold="On"/><Property A_Led="Red"/>')
    adapter, session = live_adapter(before)
    profile = store_for().snapshot(ENDPOINT)
    assert "Old system" in texts(adapter.frame(profile, now=11))
    assert (
        adapter.observe(session, cc_dnd_trunk_info(omit=omit), sequence=2, received_at=12, now=12)
        is None
    )
    current = adapter.frame(profile, now=12)
    assert current.status is Status.CURRENT
    assert current.screen.layout.requested_mode is ScannerDisplayMode.DETAIL_TRUNK
    assert {"New system", "New department", "New site", "08511250"} <= texts(current)
    shown, absent = ("New channel", "Scanning...") if omit else ("Scanning...", "New channel")
    assert shown in texts(current) and absent not in texts(current)
    assert "Old system" not in texts(current)
    assert current.indicators == ScannerDisplayIndicators(ScannerAlertLed.OFF, False, False, False)
    assert adapter.frame(profile, now=17).status is Status.STALE
    assert not texts(adapter.frame(profile, now=17))
    # A genuine detected-frequency view must replace the scan view immediately.
    hit = replace(info("close_call", '<SrchFrequency Freq="00949000"/>'), mode="Close Call")
    assert adapter.observe(session, hit, sequence=3, received_at=18, now=18) is None
    actual_hit = adapter.frame(profile, now=18)
    assert actual_hit.status is Status.CURRENT
    assert actual_hit.screen.layout.requested_mode is ScannerDisplayMode.SEARCH_CLOSE_CALL
    assert "New system" not in texts(actual_hit) and "00949000" in texts(actual_hit)


def test_cc_dnd_trunk_channel_message_disappears_without_retaining_old_values():
    adapter, session = live_adapter(cc_dnd_trunk_info())
    profile = store_for().snapshot(ENDPOINT)
    assert "Scanning..." in texts(adapter.frame(profile, now=11))
    assert (
        adapter.observe(
            session, cc_dnd_trunk_info(omit="OverWrite"), sequence=2, received_at=12, now=12
        )
        is None
    )
    current = adapter.frame(profile, now=12)
    assert current.status is Status.CURRENT
    assert "New channel" in texts(current) and "Scanning..." not in texts(current)
    assert adapter.observe(session, cc_dnd_trunk_info(), sequence=3, received_at=13, now=13) is None
    message = adapter.frame(profile, now=13)
    assert message.status is Status.CURRENT
    assert "Scanning..." in texts(message) and "New channel" not in texts(message)


@pytest.mark.parametrize("tag", [tag for tag in CC_DND_TRUNK_RECORDS if tag != "OverWrite"])
@pytest.mark.parametrize("mutation", ["missing", "duplicate"])
def test_cc_dnd_trunk_exception_requires_one_of_every_observed_record(tag, mutation):
    sample = (
        cc_dnd_trunk_info(omit=tag)
        if mutation == "missing"
        else cc_dnd_trunk_info(extra=f"<{tag}/>")
    )
    adapter, _ = live_adapter(sample)
    frame = adapter.frame(store_for().snapshot(ENDPOINT), now=11)
    assert frame.status is Status.AMBIGUOUS_RECORDS
    assert frame.screen is None and not frame.values
    assert frame.indicators == ScannerDisplayIndicators()


@pytest.mark.parametrize("cc", [None, "", "Off", "Priority", "dnd", "DND "])
@pytest.mark.parametrize("omit", [None, "OverWrite"])
def test_cc_dnd_trunk_exception_never_assumes_the_close_call_policy(cc, omit):
    dual_watch = "<DualWatch/>" if cc is None else f'<DualWatch CC="{cc}"/>'
    adapter, _ = live_adapter(cc_dnd_trunk_info(dual_watch=dual_watch, omit=omit))
    assert adapter.frame(store_for().snapshot(ENDPOINT), now=11).status is Status.AMBIGUOUS_RECORDS


@pytest.mark.parametrize(
    "field,value",
    [
        ("mode", "Close Call Only"),
        ("mode", "Quick Search"),
        ("command", "GSI"),
        ("screen", "conventional_scan"),
    ],
)
@pytest.mark.parametrize("omit", [None, "OverWrite"])
def test_cc_dnd_exception_does_not_widen_other_mode_screen_command_pairs(field, value, omit):
    adapter, _ = live_adapter(replace(cc_dnd_trunk_info(omit=omit), **{field: value}))
    frame = adapter.frame(store_for().snapshot(ENDPOINT), now=11)
    assert frame.status is Status.AMBIGUOUS_RECORDS and not frame.values


@pytest.mark.parametrize(
    "tag", ["ConvFrequency", "SrchFrequency", "WxChannel", "CcHitsChannel", "ToneOutChannel"]
)
@pytest.mark.parametrize("omit", [None, "OverWrite"])
def test_cc_dnd_trunk_exception_never_bypasses_foreign_channel_rejection(tag, omit):
    adapter, _ = live_adapter(cc_dnd_trunk_info(extra=f'<{tag} Name="Foreign"/>', omit=omit))
    frame = adapter.frame(store_for().snapshot(ENDPOINT), now=11)
    assert frame.status is Status.AMBIGUOUS_RECORDS and not frame.values


@pytest.mark.parametrize("tag", ["MonitorList", "UnitID", "InfoArea1", "InfoArea2", "OverWrite"])
def test_cc_dnd_trunk_exception_preserves_other_duplicate_guards(tag):
    adapter, _ = live_adapter(
        cc_dnd_trunk_info(
            extra=f"<{tag}/><{tag}/>", omit="OverWrite" if tag == "OverWrite" else None
        )
    )
    frame = adapter.frame(store_for().snapshot(ENDPOINT), now=11)
    assert frame.status is Status.AMBIGUOUS_RECORDS and not frame.values


@pytest.mark.parametrize(
    "tag", ["PopupScreen", "PlainText", "ReplayDescription", "ReplayMode", "Button"]
)
@pytest.mark.parametrize("omit", [None, "OverWrite"])
def test_cc_dnd_trunk_exception_preserves_full_screen_overrides(tag, omit):
    adapter, _ = live_adapter(
        cc_dnd_trunk_info(extra=f'<{tag} Text="private overlay"/>', omit=omit)
    )
    frame = adapter.frame(store_for().snapshot(ENDPOINT), now=11)
    assert frame.status is Status.OVERRIDE and not frame.values


@pytest.mark.parametrize(
    "screen",
    [
        "menu_selection",
        "menu_input",
        "plain_text",
        "direct_entry",
        "waterfall",
        "custom_with_scan",
        "cchits_with_scan",
        "reverse_frequency",
        "repeater_find",
        "analyze",
        "future_weather",
        "WEATHER",
        "wx_alert ",
        "weather_alert",
    ],
)
def test_unsupported_modes_do_not_fall_back_from_node_or_text(screen):
    adapter, _ = live_adapter(info(screen))
    frame = adapter.frame(store_for().snapshot(ENDPOINT), now=11)
    assert frame.status is Status.UNSUPPORTED_SCREEN
    assert frame.screen is None and not frame.values


@pytest.mark.parametrize(
    "tag", ["PopupScreen", "OverWrite", "PlainText", "Button", "ReplayDescription", "ReplayMode"]
)
def test_overlays_and_replay_do_not_leave_a_normal_scanner_view_or_leak_text(tag):
    sample = info(
        content='<WxChannel Freq="01625500"/><ViewDescription>'
        f'<{tag} Text="private prompt"/></ViewDescription>'
    )
    adapter, _ = live_adapter(sample)
    frame = adapter.frame(store_for().snapshot(ENDPOINT), now=11)
    assert frame.status is Status.OVERRIDE
    assert frame.screen is None and not frame.values
    assert "private prompt" not in repr(adapter._observation) + repr(frame)


@pytest.mark.parametrize("mode", ["Menu tree", "REPLAY"])
def test_menu_or_replay_mode_blocks_even_an_underlying_normal_screen(mode):
    adapter, _ = live_adapter(replace(info(), mode=mode))
    assert adapter.frame(store_for().snapshot(ENDPOINT), now=11).status is Status.OVERRIDE


def test_info_areas_alone_do_not_mean_an_overlay():
    adapter, _ = live_adapter(
        info(
            content='<WxChannel Freq="01625500"/><ViewDescription>'
            '<InfoArea1 Text="Demo"/></ViewDescription>'
        )
    )
    frame = adapter.frame(store_for().snapshot(ENDPOINT), now=11)
    assert frame.status is Status.CURRENT and "01625500" in texts(frame)
    assert "Demo" not in repr(frame)


@pytest.mark.parametrize(
    "nodes",
    [
        '<WxChannel/><ConvFrequency Freq="111"/>',
        '<WxChannel/><TGID Name="Wrong"/>',
        "<WxChannel/><WxChannel/>",
        '<WxChannel/><Property VOL="1"/><Property VOL="2"/>',
        '<WxChannel Freq="111"/><SrchFrequency Freq="222"/>',
    ],
)
def test_ambiguous_channel_and_duplicate_records_are_not_last_wins(nodes):
    adapter, _ = live_adapter(info(content=nodes))
    frame = adapter.frame(store_for().snapshot(ENDPOINT), now=11)
    assert frame.status is Status.AMBIGUOUS_RECORDS and not frame.values


@pytest.mark.parametrize(
    ("screen", "mode", "content", "reasons", "duplicates", "foreign"),
    [
        (
            "trunk_scan",
            "Trunk Scan",
            "<TGID/><ConvFrequency/>",
            ("foreign_channel",),
            (),
            ("ConvFrequency",),
        ),
        ("trunk_scan", "Quick Search", "<TGID/>", ("mode_screen_mismatch",), (), ()),
        (
            "trunk_scan",
            "Trunk Scan",
            "<Property/><Property/><TGID/><TGID/>",
            ("duplicate_records",),
            ("Property", "TGID"),
            (),
        ),
        (
            "wx_alert",
            "unknown private mode",
            "<WxChannel/><SrchFrequency/>",
            ("weather_frequency_sources",),
            (),
            (),
        ),
        (
            "wx_alert",
            "Trunk Scan",
            "<TGID/><Property/><Property/><WxChannel/><SrchFrequency/>",
            (
                "foreign_channel",
                "mode_screen_mismatch",
                "duplicate_records",
                "weather_frequency_sources",
            ),
            ("Property",),
            ("TGID",),
        ),
    ],
)
def test_conflict_diagnostic_preserves_all_reasons_but_no_values(
    screen, mode, content, reasons, duplicates, foreign
):
    adapter, session = live_adapter()
    sample = replace(info(screen, content), mode=mode, raw_xml="PRIVATE_XML_SENTINEL")
    diagnostic = adapter.observe(session, sample, sequence=2, received_at=12, now=12)
    assert diagnostic is not None
    assert diagnostic.screen == screen
    assert diagnostic.operating_mode == (None if mode.startswith("unknown") else mode)
    assert diagnostic.reasons == tuple(DisplayConflictReason(reason) for reason in reasons)
    assert diagnostic.duplicate_tags == duplicates
    assert diagnostic.foreign_channel_tags == foreign
    assert "private" not in repr(diagnostic).lower()
    with pytest.raises(FrozenInstanceError):
        diagnostic.screen = "changed"
    frame = adapter.frame(store_for().snapshot(ENDPOINT), now=12)
    assert frame.status is Status.AMBIGUOUS_RECORDS and not frame.values
    assert frame.screen is None and frame.indicators == ScannerDisplayIndicators()
    assert adapter.quick_key_selection(session, now=12) is None
    assert adapter.observe(session, info(), sequence=3, received_at=13, now=13) is None
    assert adapter._observation.conflict is None


def test_unrecognized_structure_is_never_copied_into_conflict_diagnostic():
    adapter, session = live_adapter()
    sample = replace(
        info(
            "trunk_scan",
            '<PRIVATE_TAG Text="PRIVATE_VALUE"/><PRIVATE_TAG/>'
            '<TGID Name="PRIVATE_NAME"/><TGID/><Property A_Led="PRIVATE_LED"/>',
        ),
        mode="PRIVATE_MODE",
    )
    diagnostic = adapter.observe(session, sample, sequence=2, received_at=12, now=12)
    assert diagnostic.duplicate_tags == ("TGID",)
    assert diagnostic.operating_mode is None
    assert "PRIVATE" not in repr(diagnostic) + repr(adapter._observation)


@pytest.mark.parametrize(
    "screen,content",
    [("waterfall", "<TGID/><TGID/>"), ("trunk_scan", "<PopupScreen/><TGID/><TGID/>")],
)
def test_unsupported_and_overlay_classification_still_precedes_conflict(screen, content):
    adapter, session = live_adapter()
    assert (
        adapter.observe(session, info(screen, content), sequence=2, received_at=12, now=12) is None
    )
    frame = adapter.frame(store_for().snapshot(ENDPOINT), now=12)
    assert frame.status in {Status.UNSUPPORTED_SCREEN, Status.OVERRIDE}
    assert not frame.values


def test_mode_specific_filter_drops_old_hierarchy_and_profile_does_not_supply_data():
    adapter, _ = live_adapter(
        info(content='<WxChannel Freq="01625500"/><System Name="OLD"/><Site Name="OLD SITE"/>')
    )
    frame = adapter.frame(store_for().snapshot(ENDPOINT), now=11)
    assert frame.status is Status.CURRENT
    assert "OLD" not in repr(frame) and "OLD SITE" not in repr(adapter._observation)
    assert "01625500" in texts(frame)


def test_missing_fields_replace_old_snapshot_and_unchanged_psi_refreshes_receipt_time():
    adapter, session = live_adapter()
    profile = store_for().snapshot(ENDPOINT)
    assert "01625500" in texts(adapter.frame(profile, now=11))
    for sequence, received in ((2, 14), (3, 18)):
        adapter.observe(session, info(), sequence=sequence, received_at=received, now=received)
        assert adapter.frame(profile, now=received + 1).status is Status.CURRENT
    adapter.observe(session, info(content=""), sequence=4, received_at=20, now=20)
    assert not texts(adapter.frame(profile, now=20))


def test_age_expires_exactly_at_threshold_without_preserving_old_source_values():
    adapter, _ = live_adapter()
    profile = store_for().snapshot(ENDPOINT)
    assert adapter.frame(profile, now=15.999).status is Status.CURRENT
    expired = adapter.frame(profile, now=16)
    assert expired.status is Status.STALE and not texts(expired)
    assert any(value.status is ValueStatus.NOT_CURRENT for value in expired.values)


def test_delayed_delivery_is_already_stale_and_wall_clock_is_not_used():
    adapter = ScannerDisplayAdapter(ENDPOINT, stale_after=5)
    session = adapter.begin_session(now=10)
    sample = replace(info(), received_at=NOW + timedelta(days=999))
    adapter.observe(session, sample, sequence=1, received_at=11, now=20)
    frame = adapter.frame(store_for().snapshot(ENDPOINT), now=20)
    assert frame.age_seconds == 9 and frame.status is Status.STALE


def test_disconnect_reconnect_and_old_or_copied_session_cannot_repopulate_data():
    adapter, old = live_adapter()
    profile = store_for().snapshot(ENDPOINT)
    adapter.disconnect(old)
    assert adapter.frame(profile, now=12).status is Status.DISCONNECTED
    current = adapter.begin_session(now=13)
    waiting = adapter.frame(profile, now=13)
    assert waiting.status is Status.WAITING and waiting.sequence is None and not waiting.values
    for foreign in (old, replace(current)):
        with pytest.raises(ScannerDisplayAdapterError, match="session"):
            adapter.observe(foreign, info(), sequence=2, received_at=14, now=14)
        with pytest.raises(ScannerDisplayAdapterError, match="session"):
            adapter.disconnect(foreign)
    adapter.observe(current, info(), sequence=0, received_at=14, now=14)
    assert adapter.frame(profile, now=14).status is Status.CURRENT


@pytest.mark.parametrize(
    ("sequence", "received", "now"),
    [(1, 12, 12), (0, 12, 12), (2, 10, 12), (2, 13, 12), (2, 9, 12)],
)
def test_duplicate_out_of_order_future_and_pre_session_updates_are_rejected(
    sequence, received, now
):
    adapter, session = live_adapter()
    with pytest.raises(ScannerDisplayAdapterError):
        adapter.observe(session, info(content=""), sequence=sequence, received_at=received, now=now)
    frame = adapter.frame(store_for().snapshot(ENDPOINT), now=12)
    assert frame.sequence == 1 and "01625500" in texts(frame)


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), True, "2", 10**1000])
def test_invalid_time_and_timeout_inputs_are_sanitized(value):
    with pytest.raises(ScannerDisplayAdapterError):
        ScannerDisplayAdapter(ENDPOINT, stale_after=value)
    adapter = ScannerDisplayAdapter(ENDPOINT, stale_after=5)
    with pytest.raises(ScannerDisplayAdapterError):
        adapter.begin_session(now=value)


@pytest.mark.parametrize("timeout", [0, 301])
def test_stale_timeout_is_explicit_positive_and_bounded(timeout):
    with pytest.raises(ScannerDisplayAdapterError):
        ScannerDisplayAdapter(ENDPOINT, stale_after=timeout)


@pytest.mark.parametrize("sequence", [-1, True, 1.5, "1", 2**63])
def test_sequence_is_a_bounded_integer(sequence):
    adapter, session = live_adapter()
    with pytest.raises(ScannerDisplayAdapterError):
        adapter.observe(session, info(), sequence=sequence, received_at=12, now=12)


def test_clock_regression_cannot_make_stale_data_fresh_again():
    adapter, session = live_adapter()
    profile = store_for().snapshot(ENDPOINT)
    assert adapter.frame(profile, now=20).status is Status.STALE
    with pytest.raises(ScannerDisplayAdapterError, match="backwards"):
        adapter.frame(profile, now=11)
    with pytest.raises(ScannerDisplayAdapterError, match="backwards"):
        adapter.observe(session, info(), sequence=2, received_at=12, now=12)
    assert adapter.frame(profile, now=21).status is Status.STALE


def test_wrong_endpoint_and_foreign_import_never_resolve_a_screen():
    adapter, _ = live_adapter()
    profile = store_for().snapshot(ENDPOINT)
    foreign = replace(profile, endpoint_id=UUID(int=90))
    with pytest.raises(ScannerDisplayAdapterError, match="different endpoint"):
        adapter.frame(foreign, now=11)
    bad_provenance = replace(
        profile.last_good.provenance, binding=replace(BINDING, endpoint_id=UUID(int=90))
    )
    foreign_import = replace(profile.last_good, provenance=bad_provenance)
    with pytest.raises(ScannerDisplayAdapterError, match="different endpoint"):
        adapter.frame(replace(profile, last_good=foreign_import), now=11)


def test_missing_profile_failed_refresh_and_pending_refresh_have_separate_status():
    adapter, _ = live_adapter()
    empty = ScannerDisplayProfileStore(ENDPOINT)
    frame = adapter.frame(empty.snapshot(ENDPOINT), now=11)
    assert (
        frame.status is Status.CURRENT and frame.profile_status is DisplayProfileStatus.UNAVAILABLE
    )
    assert frame.screen is None and not frame.values
    store = store_for()
    ticket = store.begin_refresh(BINDING, started_at=NOW)
    pending = adapter.frame(store.snapshot(ENDPOINT), now=11)
    assert pending.profile_refresh_pending and "01625500" in texts(pending)
    store.fail(ticket, DisplayProfileRefreshFailure.UNAVAILABLE)
    failed = adapter.frame(store.snapshot(ENDPOINT), now=11)
    assert failed.profile_status is DisplayProfileStatus.REFRESH_FAILED
    assert (
        not failed.profile_refresh_pending and failed.profile_revision == pending.profile_revision
    )
    assert "01625500" in texts(failed)


def test_oversized_and_control_text_not_kept_or_displayed_raw():
    payload = "private" * 1000
    adapter, _ = live_adapter(info(content=f'<WxChannel Freq="{payload}"/>'))
    frame = adapter.frame(store_for().snapshot(ENDPOINT), now=11)
    assert payload not in repr(adapter._observation) + repr(frame)
    assert any(value.status is ValueStatus.INVALID_SOURCE for value in frame.values)
    assert not texts(frame)


@pytest.mark.parametrize("value", ["a&#x9;b", "a&#x202e;b"])
def test_terminal_and_unicode_controls_are_invalid_not_rendered(value):
    adapter, _ = live_adapter(info(content=f'<WxChannel Freq="{value}"/>'))
    frame = adapter.frame(store_for().snapshot(ENDPOINT), now=11)
    assert any(item.status is ValueStatus.INVALID_SOURCE for item in frame.values)
    assert not texts(frame)


def test_xml_forbidden_escape_is_rejected_before_it_can_refresh_the_adapter():
    adapter, _ = live_adapter()
    with pytest.raises(ProtocolError):
        info(content='<WxChannel Freq="a&#x1b;b"/>')
    frame = adapter.frame(store_for().snapshot(ENDPOINT), now=16)
    assert frame.status is Status.STALE and not texts(frame)


def test_numeric_cache_is_bounded_and_does_not_keep_nonfinite_values():
    sample = info(content=f'<Property VOL="{10**1000}" SQL="-99999999999" Rssi="nan"/>')
    adapter, _ = live_adapter(sample)
    cached = adapter._observation.snapshot
    assert cached.volume == cached.squelch == 2**31
    assert cached.rssi == 1e10
    frame = adapter.frame(store_for().snapshot(ENDPOINT), now=11)
    assert sum(value.status is ValueStatus.INVALID_SOURCE for value in frame.values) == 2


def test_endpoint_binding_requires_uuid_not_host_or_path():
    with pytest.raises(ScannerDisplayAdapterError, match="UUID"):
        ScannerDisplayAdapter("private-host.invalid", stale_after=5)


def test_adapter_keeps_no_raw_xml_unrelated_fields_or_addresses():
    sample = info(
        content='<WxChannel Freq="01625500"/>'
        '<Owner Password="private-key" Address="private-place"/>'
    )
    adapter, _ = live_adapter(sample)
    assert "private-key" not in repr(adapter._observation)
    assert "private-place" not in repr(adapter._observation)
    assert "ScannerInfo" not in repr(adapter._observation)
    assert "raw_xml" not in repr(adapter._observation)


@pytest.mark.parametrize("command", ["GLT", "PSI,OK", "VOL", "psi"])
def test_only_complete_expected_observation_commands_are_accepted(command):
    adapter, session = live_adapter()
    with pytest.raises(ScannerDisplayAdapterError, match="PSI or GSI"):
        adapter.observe(session, info(command=command), sequence=2, received_at=12, now=12)
