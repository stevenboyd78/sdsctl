from __future__ import annotations

from dataclasses import replace

import pytest

from sds200.scanner_display_adapter import DisplayObservationStatus as Status
from sds200.scanner_display_frame import project_scanner_display_frame
from sds200.scanner_display_live import project_live_values
from sds200.scanner_display_values import ScannerDisplayValueStatus as ValueStatus
from sds200.scanner_display_values import scanner_display_values
from sds200.state import RadioStateSnapshot

from .test_scanner_display_adapter import ENDPOINT, info, live_adapter, store_for
from .test_scanner_display_values import MODE, screen_for


def token_value(token, content, *, group=2, current=True):
    sample = info("trunk_scan", content)
    screen = screen_for(token, group)
    values = scanner_display_values(
        screen,
        RadioStateSnapshot(),
        source_mode=MODE,
        current=current,
        live_values=project_live_values(sample),
    )
    identifier = next(slot.region.id for slot in screen.regions if slot.token == token)
    return next(value for value in values if value.region_id == identifier)


@pytest.mark.parametrize(
    "token,content,text",
    [
        ("FL_Name", '<MonitorList Name="Demo Favorites"/>', "Demo Favorites"),
        ("SystemType", '<System SystemType="P25 Trunk"/>', "P25 Trunk"),
        ("UnitId", '<UnitID U_Id="UID:123456" Name="Demo Unit"/>', "UID:123456"),
        ("UnitIdName", '<UnitID U_Id="UID:123456" Name="Demo Unit"/>', "Demo Unit"),
        ("UnitId", '<ConvFrequency U_Id="UID 456"/>', "UID 456"),
        (
            "NumberTag",
            '<MonitorList N_Tag="None"/><System N_Tag="None"/><TGID N_Tag="None"/>',
            "Tag:--.--.---",
        ),
        (
            "NumberTag",
            '<MonitorList N_Tag="01"/><System N_Tag="2"/><TGID N_Tag="003"/>',
            "Tag:01.2.003",
        ),
    ],
)
def test_qualified_fields_use_exact_sources(token, content, text):
    value = token_value(token, content)
    assert value.status is ValueStatus.RAW_SOURCE and value.text == text


@pytest.mark.parametrize("content", ["<UnitID/>", '<UnitID U_Id="None"/>', '<UnitID U_Id=""/>'])
def test_explicit_empty_unit_record_clears_even_an_old_channel_uid(content):
    value = token_value("UnitId", '<TGID U_Id="UID:old"/>' + content)
    assert value.status is ValueStatus.BLANK and value.text is None


@pytest.mark.parametrize(
    "tag,value",
    [
        ("MonitorList", "100"),
        ("System", "-1"),
        ("TGID", "1000"),
        ("TGID", "１"),
        ("TGID", "<script>"),
    ],
)
def test_number_tags_are_bounded_and_not_inferred(tag, value):
    from xml.sax.saxutils import quoteattr

    content = "".join(
        f"<{name} N_Tag={quoteattr(value if name == tag else 'None')}/>"
        for name in ("MonitorList", "System", "TGID")
    )
    assert token_value("NumberTag", content).status is ValueStatus.INVALID_SOURCE


@pytest.mark.parametrize(
    "content", ["", '<MonitorList N_Tag="None"/>', '<System N_Tag="None"/><TGID N_Tag="None"/>']
)
def test_absent_tag_source_is_not_unassigned(content):
    assert token_value("NumberTag", content).status is ValueStatus.DATA_UNAVAILABLE


@pytest.mark.parametrize(
    "token,content,text",
    [
        ("PRI", '<DualWatch PRI="Priority"/>', "PRI"),
        ("PRI", '<DualWatch PRI="DND"/>', "PRI"),
        ("CC", '<DualWatch CC="Priority"/>', "CC"),
        ("CC", '<DualWatch CC="DND"/>', "CC"),
        ("WxPRI", '<DualWatch WX="Priority"/>', "WX"),
        ("REC", '<Property Rec="On"/>', "REC"),
        ("IFX", '<SiteFrequency IFX="On"/>', "IFX"),
        ("P_Ch", '<TGID P_Ch="On"/>', "P"),
        ("Modulation", '<Site Mod="NFM"/>', "NFM"),
        ("LVL", '<TGID LVL="-2"/>', "V-2"),
        ("LVL", '<TGID LVL="1"/>', "V+1"),
    ],
)
def test_active_supported_icon_labels(token, content, text):
    value = token_value(token, content, group=4)
    assert value.status is ValueStatus.RAW_SOURCE and value.text == text
    assert token_value(token, content, group=4, current=False).status is ValueStatus.NOT_CURRENT


@pytest.mark.parametrize(
    "token,content",
    [
        ("PRI", '<DualWatch PRI="Off"/>'),
        ("CC", '<DualWatch CC="Off"/>'),
        ("WxPRI", '<DualWatch WX="Off"/>'),
        ("REC", '<Property Rec="Off"/>'),
        ("IFX", '<SiteFrequency IFX="Off"/>'),
        ("P_Ch", '<TGID P_Ch="Off"/>'),
        ("LVL", '<TGID LVL="0"/>'),
    ],
)
def test_confirmed_inactive_icons_are_blank_not_unavailable(token, content):
    value = token_value(token, content, group=4)
    assert value.status is ValueStatus.BLANK and value.text is None
    assert token_value(token, "", group=4).status is ValueStatus.DATA_UNAVAILABLE


@pytest.mark.parametrize(
    "token,content",
    [
        ("PRI", '<DualWatch PRI="Maybe"/>'),
        ("CC", '<DualWatch CC="On"/>'),
        ("WxPRI", '<DualWatch WX="DND"/>'),
        ("REC", '<Property Rec="true"/>'),
        ("IFX", '<SiteFrequency IFX="false"/>'),
        ("P_Ch", '<TGID P_Ch="yes"/>'),
        ("LVL", '<TGID LVL="99"/>'),
        ("Modulation", '<Site Mod="P25"/>'),
    ],
)
def test_invalid_icon_states_do_not_become_active(token, content):
    assert token_value(token, content, group=4).status is ValueStatus.INVALID_SOURCE


@pytest.mark.parametrize("token", ["GPS", "SCR", "REP"])
def test_unqualified_icons_stay_explicit(token):
    assert (
        token_value(token, '<Property GPS="On" SCR="On" REP="On"/>', group=4).status
        is ValueStatus.UNQUALIFIED
    )


def test_digital_data_label_and_none_are_not_stale_last_transmission():
    assert token_value("P25Status", '<Property P25Status="Data"/>', group=3).text == "DATA"
    assert token_value("P25Status", '<Property P25Status="P25"/>', group=3).text == "P25"
    assert (
        token_value("P25Status", '<Property P25Status="None"/>', group=3).status
        is ValueStatus.BLANK
    )


def test_normal_scan_overwrite_updates_only_channel_area_and_remains_current():
    adapter, session = live_adapter(
        info(
            "trunk_scan",
            '<System Name="Demo"/><TGID Name="Old"/><Site Hold="On"/>'
            '<ViewDescription><InfoArea1 Text="SITE HOLD"/>'
            '<OverWrite Text="ID Scanning..."/></ViewDescription>',
        )
    )
    profile = store_for().snapshot(ENDPOINT)
    frame = adapter.frame(profile, now=11)
    assert frame.status is Status.CURRENT
    values = {v.region_id: v for v in frame.values}
    assert values["channel"].text == "ID Scanning..."
    assert values["system"].text == "Demo"
    assert values["information_1"].text == "SITE HOLD"
    assert frame.indicators.site_hold is True
    assert project_scanner_display_frame(frame)["indicators"]["site_hold"] is True
    adapter.observe(
        session,
        info("trunk_scan", '<TGID Name="New"/><Site Hold="Off"/>'),
        sequence=2,
        received_at=12,
        now=12,
    )
    frame = adapter.frame(profile, now=12)
    assert {v.region_id: v for v in frame.values}["channel"].text == "New"
    assert {v.region_id: v for v in frame.values}["information_1"].status is ValueStatus.BLANK
    assert frame.indicators.site_hold is False
    assert (
        project_scanner_display_frame(adapter.frame(profile, now=18))["indicators"]["site_hold"]
        is None
    )


@pytest.mark.parametrize(
    "tag", ["PopupScreen", "PlainText", "Button", "ReplayDescription", "ReplayMode"]
)
def test_real_overlays_still_clear_all_fields(tag):
    adapter, _ = live_adapter(
        info("trunk_scan", f'<TGID Name="Demo"/><OverWrite Text="ID Scanning..."/><{tag}/>')
    )
    frame = adapter.frame(store_for().snapshot(ENDPOINT), now=11)
    assert frame.status is Status.OVERRIDE and not frame.values


@pytest.mark.parametrize(
    "tag", ["UnitID", "MonitorList", "InfoArea1", "InfoArea2", "DualWatch", "OverWrite"]
)
def test_duplicate_display_sources_fail_closed(tag):
    adapter, _ = live_adapter(info("trunk_scan", f"<{tag}/><{tag}/>"))
    assert adapter.frame(store_for().snapshot(ENDPOINT), now=11).status is Status.AMBIGUOUS_RECORDS


@pytest.mark.parametrize("text", ["x" * 257, "hidden\u200btext", "\x1bcontrol"])
def test_invalid_live_text_is_bounded_without_retaining_payload(text):
    from sds200.models import ScannerNode

    sample = info("trunk_scan", '<UnitID U_Id="ok"/>')
    sample = replace(sample, nodes={"UnitID": ScannerNode("UnitID", {"U_Id": text})})
    projected = project_live_values(sample)
    assert dict(projected.tokens)["UnitId"] == "\x00"
    assert text not in repr(projected)


def test_foreign_screen_records_cannot_supply_site_uid_or_monitor_values():
    adapter, _ = live_adapter(
        info(
            "wx_alert",
            '<WxChannel Freq="162.5500MHz"/><MonitorList Name="Foreign"/>'
            '<Site Hold="On"/><UnitID U_Id="UID:foreign"/>',
        )
    )
    frame = adapter.frame(store_for().snapshot(ENDPOINT), now=11)
    assert frame.indicators.site_hold is None
    assert "Foreign" not in repr(frame) and "UID:foreign" not in repr(frame)
