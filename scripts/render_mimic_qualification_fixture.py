#!/usr/bin/env python3
"""Build a scanner-free Favorites and Mimic-SDS qualification artifact."""

from __future__ import annotations

import argparse
import json
import runpy
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sds200.scanner_display_adapter import (
    ScannerDisplayAdapter,
    ScannerDisplayFrame,
    ScannerDisplayStyle,
)
from sds200.scanner_display_frame_preview import render_scanner_display_gallery
from sds200.scanner_display_profile_state import (
    DisplayProfileBinding,
    DisplayProfileSourceKind,
    ScannerDisplayProfileStore,
)
from sds200.xml_protocol import ScannerInfoParser

_LIST_NAME = "OFFLINE Mimic Qualification"
_FILENAME = "f_000001.hpd"
_FILE_SPEC_URL = (
    "https://info.uniden.com/twiki/pub/UnidenMan4/"
    "SDS200FirmwareUpdate/SDSx00_File_Specification_V1_08.pdf"
)


@dataclass(frozen=True, slots=True)
class FavoritesChannel:
    """One deterministic conventional-channel configuration case."""

    name: str
    frequency: str
    modulation: str
    alert_tone: str
    alert_volume: str
    alert_color: str
    alert_pattern: str
    number_tag: str
    priority: str


_CHANNELS = (
    FavoritesChannel(
        "01 AUTO / no alert",
        "155000000",
        "AUTO",
        "Off",
        "Auto",
        "Off",
        "On",
        "101",
        "Off",
    ),
    FavoritesChannel(
        "02 AM / blue steady",
        "155012500",
        "AM",
        "1",
        "5",
        "Blue",
        "On",
        "102",
        "On",
    ),
    FavoritesChannel(
        "03 NFM / red slow",
        "155025000",
        "NFM",
        "2",
        "6",
        "Red",
        "Slow Blink",
        "103",
        "Off",
    ),
    FavoritesChannel(
        "04 FM / green fast",
        "155037500",
        "FM",
        "3",
        "7",
        "Green",
        "Fast Blink",
        "104",
        "On",
    ),
    FavoritesChannel(
        "05 NFM / magenta steady",
        "155050000",
        "NFM",
        "4",
        "8",
        "Magenta",
        "On",
        "105",
        "Off",
    ),
    FavoritesChannel(
        "06 NFM / cyan slow",
        "155062500",
        "NFM",
        "5",
        "9",
        "Cyan",
        "Slow Blink",
        "106",
        "On",
    ),
    FavoritesChannel(
        "07 NFM / yellow fast",
        "155075000",
        "NFM",
        "6",
        "10",
        "Yellow",
        "Fast Blink",
        "107",
        "Off",
    ),
    FavoritesChannel(
        "08 NFM / white steady",
        "155087500",
        "NFM",
        "7",
        "11",
        "White",
        "On",
        "108",
        "On",
    ),
)

_TRUNK_SYSTEM_TYPES = (
    ("motorola", "Motorola"),
    ("edacs", "EDACS"),
    ("scat", "SCAT"),
    ("ltr", "LTR"),
    ("p25_standard", "P25 Standard"),
    ("p25_one_frequency", "P25 One Frequency"),
    ("p25_x2_tdma", "P25 X2-TDMA"),
    ("mototrbo", "MotoTRBO"),
    ("dmr_one_frequency", "DMR One Frequency"),
    ("nxdn", "NXDN"),
    ("nxdn_one_frequency", "NXDN One Frequency"),
)


@dataclass(frozen=True, slots=True)
class DisplayCase:
    """One invented scanner-info observation for offline presentation review."""

    identifier: str
    label: str
    kind: str
    mode: str
    screen: str
    content: str
    boundary: str


def _record(*fields: str) -> bytes:
    if any(
        not field.isascii() or "\t" in field or "\r" in field or "\n" in field
        for field in fields
    ):
        raise ValueError("Favorites fixture fields must be single-line ASCII values.")
    return "\t".join(fields).encode("ascii") + b"\r\n"


def build_favorites_catalog_bytes() -> bytes:
    """Return a fail-safe one-entry catalog with monitoring and all keys off."""

    entry = (
        "F-List",
        _LIST_NAME,
        _FILENAME,
        "Off",  # Location control.
        "Off",  # Monitor: an accidental copy must not begin scanning it.
        "Off",  # Favorites quick key.
        "Off",  # Number tag.
        *(["Off"] * 10),  # Startup keys 0-9.
        *(["Off"] * 100),  # System quick keys 00-99.
    )
    return b"".join(
        (
            _record("TargetModel", "BCDx36HP"),
            _record("FormatVersion", "1.00"),
            _record(*entry),
        )
    )


def _channel_record(channel: FavoritesChannel) -> bytes:
    return _record(
        "C-Freq",
        "",
        "",
        channel.name,
        "Off",
        channel.frequency,
        channel.modulation,
        "",
        "2",
        "Off",
        "2",
        "0",
        channel.alert_tone,
        channel.alert_volume,
        channel.alert_color,
        channel.alert_pattern,
        channel.number_tag,
        channel.priority,
    )


def build_favorites_hpd_bytes() -> bytes:
    """Return a CRLF, tab-delimited conventional Favorites fixture."""

    records = [
        _record("TargetModel", "BCDx36HP"),
        _record("FormatVersion", "1.00"),
        _record(
            "Conventional",
            "",
            "",
            "Offline Mimic Field Matrix",
            "Off",
            "",
            "Conventional",
            "Off",
            "Off",
            "90",
            "Off",
            "Off",
            "400",
            "Auto",
            "8",
        ),
        _record("DQKs_Status", "", *(["Off"] * 100)),
        _record(
            "C-Group",
            "",
            "",
            "Modulation and Alert Rails",
            "Off",
            "0.000000",
            "0.000000",
            "0.0",
            "Circle",
            "Off",
            "Global",
        ),
    ]
    records.extend(_channel_record(channel) for channel in _CHANNELS)
    return b"".join(records)


def _mhz(frequency: str) -> str:
    value = int(frequency)
    return f"{value // 1_000_000}.{value % 1_000_000:06d}MHz"


def _conventional_content(
    channel: FavoritesChannel,
    *,
    live_alert: str = "Off",
) -> str:
    modulation = "Auto" if channel.modulation == "AUTO" else channel.modulation
    return (
        f'<MonitorList Name="{_LIST_NAME}" N_Tag="90"/>'
        '<System Name="Offline Conventional" SystemType="Conventional" '
        'N_Tag="1" Avoid="Off" Hold="Off"/>'
        '<Department Name="Modulation and Alert Rails" Avoid="Off" Hold="Off"/>'
        f'<ConvFrequency Name="{channel.name}" Freq="{_mhz(channel.frequency)}" '
        f'Mod="{modulation}" SvcType="002" SAD="CTCSS:123.0" '
        f'N_Tag="{channel.number_tag}" P_Ch="{channel.priority}" IFX="Off" '
        'LVL="0" Avoid="Off" Hold="Off"/>'
        '<DualWatch PRI="Off" CC="Off" WX="Off"/>'
        f'<Property VOL="4" SQL="2" Rec="Off" Rssi="-75.0" A_Led="{live_alert}"/>'
    )


def _trunk_content(label: str, index: int) -> str:
    return (
        f'<MonitorList Name="{_LIST_NAME}" N_Tag="90"/>'
        f'<System Name="Offline {label}" SystemType="{label}" '
        f'N_Tag="{index}" Avoid="Off" Hold="Off"/>'
        '<Department Name="Synthetic Dispatch" Avoid="Off" Hold="Off"/>'
        '<Site Name="Synthetic Site" Mod="NFM" Hold="Off"/>'
        '<SiteFrequency Freq="851.012500MHz" IFX="Off"/>'
        f'<TGID Name="Synthetic {label} Call" TGID="{1000 + index}" '
        'U_Id="UID:42" SvcType="002" N_Tag="201" P_Ch="Off" LVL="0" '
        'Avoid="Off" Hold="Off"/>'
        '<DualWatch PRI="Off" CC="Off" WX="Off"/>'
        '<Property VOL="4" SQL="2" Rec="Off" Rssi="-72.0" '
        'P25Status="None" A_Led="Off"/>'
    )


def _search_content(modulation: str) -> str:
    return (
        f'<SrchFrequency Freq="099.500000MHz" Mod="{modulation}" '
        'SAD="None" IFX="Off" P_Ch="Off" LVL="0"/>'
        '<DualWatch PRI="Off" CC="Off" WX="Off"/>'
        '<Property VOL="4" SQL="2" Rec="Off" Rssi="-80.0" A_Led="Off"/>'
    )


def build_display_cases() -> tuple[DisplayCase, ...]:
    """Return all bounded, invented observations represented in the gallery."""

    cases = [
        DisplayCase(
            identifier=f"modulation_{channel.modulation.lower()}",
            label=f"Favorites modulation: {channel.modulation}",
            kind="favorites_modulation",
            mode="Scan Mode",
            screen="conventional_scan",
            content=_conventional_content(channel),
            boundary=(
                "Valid ordinary C-Freq modulation; live alert is inactive even if "
                "the channel has a configured alert."
            ),
        )
        for channel in _CHANNELS[:4]
    ]
    red = next(channel for channel in _CHANNELS if channel.alert_color == "Red")
    cases.append(
        DisplayCase(
            identifier="alert_red_inactive",
            label="Configured red / slow alert, live alert inactive",
            kind="alert_transition",
            mode="Scan Mode",
            screen="conventional_scan",
            content=_conventional_content(red),
            boundary="Property.A_Led is Off; neutral inactive rails must remain visible.",
        )
    )
    cases.extend(
        DisplayCase(
            identifier=f"alert_{channel.alert_color.lower()}_active",
            label=(
                f"Configured {channel.alert_color} / {channel.alert_pattern}, "
                "live alert active"
            ),
            kind="alert_transition",
            mode="Scan Mode",
            screen="conventional_scan",
            content=_conventional_content(channel, live_alert=channel.alert_color),
            boundary=(
                "Synthetic live A_Led color only; the gallery does not invent blink "
                "timing from the saved pattern."
            ),
        )
        for channel in _CHANNELS
        if channel.alert_color != "Off"
    )
    cases.extend(
        DisplayCase(
            identifier=f"system_{identifier}",
            label=f"System type presentation: {label}",
            kind="system_type",
            mode="Trunk Scan",
            screen="trunk_scan",
            content=_trunk_content(label, index),
            boundary=(
                "Invented ScannerInfo presentation case, not a claim that an importable "
                "system definition or local RF traffic exists."
            ),
        )
        for index, (identifier, label) in enumerate(_TRUNK_SYSTEM_TYPES, start=2)
    )
    cases.extend(
        DisplayCase(
            identifier=f"search_{modulation.lower()}",
            label=f"Quick Search modulation: {modulation}",
            kind="non_favorites_modulation",
            mode="Quick Search Hold",
            screen="quick_search",
            content=_search_content(modulation),
            boundary=(
                "WFM and FMB are display/search cases; they are intentionally absent "
                "from ordinary C-Freq Favorites records."
            ),
        )
        for modulation in ("WFM", "FMB")
    )
    return tuple(cases)


def qualification_profile_bytes() -> bytes:
    """Select review-oriented fields in an otherwise invented display profile."""

    fixture = runpy.run_path(
        str(Path(__file__).with_name("render_scanner_display_preview.py"))
    )
    lines = fixture["synthetic_profile_bytes"]().decode("ascii").split("\r\n")
    rewritten: list[str] = []
    for line in lines:
        parts = line.split("\t")
        if parts[0] != "DispOptItems":
            rewritten.append(line)
            continue
        group = int(parts[1].split("=", maxsplit=1)[1])
        layout = int(parts[2].split("=", maxsplit=1)[1])
        simple = layout <= 2
        scan = layout <= 4
        if group == 2:
            if simple:
                tokens = ["SystemType", "FL_Name"]
            elif scan:
                tokens = [
                    "SystemType",
                    "FL_Name",
                    "SiteName",
                    "Frequency",
                    "CTCSS/DCS",
                    "ServiceType",
                    "TGID",
                    "UnitId",
                    "UnitIdName",
                    "Rssi",
                    "Volume&Squelch",
                    "Lcn",
                ]
            else:
                tokens = [
                    "SystemType",
                    "FL_Name",
                    "Frequency",
                    "CTCSS/DCS",
                    "ServiceType",
                    "Rssi",
                    "Volume&Squelch",
                    "Lcn",
                ]
            parts = [*parts[:3], *tokens]
        elif group == 3:
            tokens = (
                ["Modulation", "P25Status", "Volume", "Squelch", "REC", "IFX", "P_Ch", "LVL"]
                if simple
                else ["Modulation", "P25Status", "REC", "IFX", "P_Ch", "LVL"]
            )
            parts = [*parts[:3], *tokens]
        rewritten.append("\t".join(parts))
    return "\r\n".join(rewritten).encode("ascii")


def _build_qualification_scenarios() -> dict[str, dict[str, ScannerDisplayFrame]]:
    """Project the qualification observations through the real adapter."""

    endpoint = UUID(int=200)
    binding = DisplayProfileBinding(
        endpoint,
        UUID(int=201),
        DisplayProfileSourceKind.MANUAL_IMPORT,
    )
    stamp = datetime(2026, 10, 10, tzinfo=UTC)
    store = ScannerDisplayProfileStore(endpoint)
    refresh = store.begin_refresh(binding, started_at=stamp)
    preview = store.prepare(refresh, qualification_profile_bytes(), acquired_at=stamp)
    store.commit(preview, imported_at=stamp)
    adapter = ScannerDisplayAdapter(endpoint, stale_after=5)
    session = adapter.begin_session(now=0)
    scenarios: dict[str, dict[str, ScannerDisplayFrame]] = {}
    parser = ScannerInfoParser()
    for sequence, case in enumerate(build_display_cases(), start=1):
        now = float(sequence)
        info = parser.parse(
            "PSI",
            (
                f'<ScannerInfo Mode="{case.mode}" V_Screen="{case.screen}">'
                f"{case.content}</ScannerInfo>"
            ),
        )
        adapter.observe(session, info, sequence=sequence, received_at=now, now=now)
        profile = store.snapshot(endpoint)
        scenarios[case.identifier] = {
            "profile": adapter.frame(profile, now=now),
            "simple": adapter.frame(profile, now=now, style=ScannerDisplayStyle.SIMPLE),
            "detail": adapter.frame(profile, now=now, style=ScannerDisplayStyle.DETAIL),
        }
    return scenarios


def build_scenarios() -> dict[str, dict[str, ScannerDisplayFrame]]:
    """Combine the established transition gallery with qualification cases."""

    baseline = runpy.run_path(
        str(Path(__file__).with_name("render_scanner_display_frames.py"))
    )["build_scenarios"]()
    qualification = _build_qualification_scenarios()
    overlap = set(baseline) & set(qualification)
    if overlap:
        raise ValueError(f"Qualification scenario identifiers overlap: {sorted(overlap)!r}")
    return {**baseline, **qualification}


def build_manifest() -> dict[str, object]:
    """Return the human- and machine-reviewable qualification inventory."""

    return {
        "schema": 1,
        "status": "OFFLINE_ONLY_NOT_IMPORT_APPROVED",
        "favorites_file_specification": _FILE_SPEC_URL,
        "safety": {
            "scanner_access": False,
            "network_access": False,
            "favorites_monitor": "Off",
            "location_control": "Off",
            "favorites_quick_key": "Off",
            "startup_keys": "Off",
            "system_quick_keys": "Off",
        },
        "favorites": {
            "catalog": "favorites/f_list.cfg",
            "document": f"favorites/{_FILENAME}",
            "ordinary_c_freq_modulations": ["AUTO", "AM", "NFM", "FM"],
            "intentionally_excluded_c_freq_modulations": ["WFM", "FMB"],
            "channels": [
                {
                    "name": channel.name,
                    "frequency_hz": channel.frequency,
                    "modulation": channel.modulation,
                    "alert_tone": channel.alert_tone,
                    "alert_volume": channel.alert_volume,
                    "alert_color": channel.alert_color,
                    "alert_pattern": channel.alert_pattern,
                    "number_tag": channel.number_tag,
                    "priority": channel.priority,
                }
                for channel in _CHANNELS
            ],
        },
        "baseline_transition_cases": sorted(
            runpy.run_path(
                str(Path(__file__).with_name("render_scanner_display_frames.py"))
            )["build_scenarios"]()
        ),
        "display_cases": [
            {
                "id": case.identifier,
                "label": case.label,
                "kind": case.kind,
                "mode": case.mode,
                "screen": case.screen,
                "boundary": case.boundary,
            }
            for case in build_display_cases()
        ],
    }


def build_readme() -> str:
    """Return the artifact's offline review and safety instructions."""

    return f"""# Offline Mimic-SDS qualification fixture

Status: **OFFLINE ONLY — NOT IMPORT APPROVED**

This directory is generated entirely from invented data. The generator does not
open a scanner, network connection, credential, installed profile, Pi service,
or Home Assistant instance.

## Contents

- `index.html` is a self-contained gallery produced by the real Mimic-SDS
  adapter and HTML renderer. It includes the established transition cases plus
  the field matrix in Profile, Simple, and Detail variants.
- `manifest.json` records every case and the boundary on what it proves.
- `favorites/f_list.cfg` and `favorites/{_FILENAME}` are exact CRLF,
  tab-delimited Favorites files for offline parser and field review.

The Favorites file covers the four ordinary `C-Freq` modulation values
`AUTO`, `AM`, `NFM`, and `FM`, all documented alert colors, all three alert
patterns, priority on/off, number tags, and a neutral no-alert case. `WFM` and
`FMB` are deliberately represented only as synthetic Quick Search display
cases because they are not ordinary `C-Freq` Favorites modulation values.

The system-type pages are invented `ScannerInfo` observations. They let the
presentation be reviewed for systems that may not exist locally, but they are
not importable trunk-system definitions and do not claim scanner LCD parity or
reception of those systems.

## Review order

1. Open `index.html` with networking disabled or directly from disk.
2. Compare `alert_red_inactive` with `alert_red_active` to review the neutral
   inactive rails and active color.
3. Review the four Favorites modulation cases, then the WFM/FMB search cases.
4. Review every `system_*` case for clipping and field placement.
5. Inspect `manifest.json` and the two Favorites files before proposing any
   later physical staging.

Do not copy this directory into a scanner. A later import requires separate
authorization, a protected backup, filename collision checks, source pinning,
rollback steps, and a deliberately selected subset of cases.

Reference: [Uniden SDS100/SDS200 File Specification V1.08]({_FILE_SPEC_URL})
"""


def write_artifact(output_dir: Path) -> None:
    """Write one new deterministic artifact directory without overwriting."""

    catalog = build_favorites_catalog_bytes()
    hpd = build_favorites_hpd_bytes()
    manifest = json.dumps(build_manifest(), indent=2, sort_keys=True) + "\n"
    readme = build_readme()
    gallery = render_scanner_display_gallery(build_scenarios())

    output_dir.mkdir(parents=True, exist_ok=False)
    favorites_dir = output_dir / "favorites"
    favorites_dir.mkdir()
    with (favorites_dir / "f_list.cfg").open("xb") as stream:
        stream.write(catalog)
    with (favorites_dir / _FILENAME).open("xb") as stream:
        stream.write(hpd)
    with (output_dir / "manifest.json").open("x", encoding="utf-8") as stream:
        stream.write(manifest)
    with (output_dir / "README.md").open("x", encoding="utf-8") as stream:
        stream.write(readme)
    with (output_dir / "index.html").open("x", encoding="utf-8") as stream:
        stream.write(gallery)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    write_artifact(args.output_dir)
    print(
        f"Wrote {len(build_scenarios())} offline scenarios "
        f"({len(build_display_cases())} qualification cases); "
        "no scanner or network access."
    )


if __name__ == "__main__":
    main()
