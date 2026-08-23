from sds200.state import RadioState, snapshot_from_scanner_info
from sds200.xml_protocol import ScannerInfoParser

XML = """<?xml version="1.0" encoding="utf-8"?>
<ScannerInfo Mode="Trunk Scan" V_Screen="trunk_scan">
<System Name="Utah Communications Authority (P25)" Index="100" Hold="On" />
<Department Name="Harris Dynamic Patch - Northern Utah" Index="200" Hold="Off" />
<Site Name="Utah County Simulcast" Index="300" Hold="Off" Mod="NFM" />
<TGID Name="Patch 65132" Index="400" Hold="On" TGID="TGID:65132"
  SvcType="Interop" U_Id="UID:9190014" />
<SiteFrequency Freq=" 769.431250MHz" />
<Property VOL="10" SQL="2" Sig="5" Rssi="-42" Battery="2.7"
  P25Status="P25" Mute="Unmute" Rec="Off" />
</ScannerInfo>"""


def test_scanner_info_converts_to_shared_snapshot() -> None:
    snapshot = snapshot_from_scanner_info(ScannerInfoParser().parse("PSI", XML))

    assert snapshot.channel == "Patch 65132"
    assert snapshot.system_index == 100
    assert snapshot.department_index == 200
    assert snapshot.site_index == 300
    assert snapshot.channel_index == 400
    assert snapshot.channel_kind == "TGID"
    assert snapshot.system_hold == "On"
    assert snapshot.department_hold == "Off"
    assert snapshot.site_hold == "Off"
    assert snapshot.channel_hold == "On"
    assert snapshot.frequency == "769.431250MHz"
    assert snapshot.signal == 5
    assert snapshot.battery == 2.7
    assert snapshot.recording == "Off"


def test_state_change_contains_rich_scanner_information() -> None:
    info = ScannerInfoParser().parse("PSI", XML)
    state = RadioState()

    change = state.update(info)

    assert change is not None
    assert change.current.site == "Utah County Simulcast"
    assert change.current.frequency == "769.431250MHz"
    assert change.current.modulation == "NFM"
    assert change.current.service_type == "Interop"
    assert change.current.volume == 10
    assert change.current.signal == 5
    assert change.changed("channel")


def test_identical_state_does_not_emit_a_change() -> None:
    info = ScannerInfoParser().parse("PSI", XML)
    state = RadioState()

    assert state.update(info) is not None
    assert state.update(info) is None


def test_battery_zero_is_distinct_from_absence_and_absence_clears_state() -> None:
    state = RadioState()
    with_battery = ScannerInfoParser().parse(
        "PSI",
        '<ScannerInfo><Property Battery="2.7" /></ScannerInfo>',
    )
    with_zero = ScannerInfoParser().parse(
        "PSI",
        '<ScannerInfo><Property Battery="0" /></ScannerInfo>',
    )
    without_battery = ScannerInfoParser().parse(
        "PSI",
        "<ScannerInfo><Property /></ScannerInfo>",
    )

    initial_change = state.update(with_battery)
    assert initial_change is not None
    assert initial_change.current.battery == 2.7
    zero_change = state.update(with_zero)
    assert zero_change is not None
    assert zero_change.current.battery == 0.0
    assert zero_change.changed("battery")
    absent_change = state.update(without_battery)
    assert absent_change is not None
    assert absent_change.current.battery is None
    assert absent_change.changed("battery")
