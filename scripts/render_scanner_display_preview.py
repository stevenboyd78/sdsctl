#!/usr/bin/env python3
"""Write a scanner-free SVG gallery from invented profiles and sample values."""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

from sds200.scanner_display_layout import resolve_scanner_display_screen
from sds200.scanner_display_preview import render_scanner_display_preview
from sds200.scanner_display_profile import ScannerDisplayMode, parse_scanner_display_profile
from sds200.scanner_display_values import scanner_display_values
from sds200.state import RadioStateSnapshot


def synthetic_profile_bytes() -> bytes:
    records = ["DisplayOption\t\t\t\t\t\tDEC\t\t\t\t\tOff\tDEC\tCOLOR"]
    for mode in ScannerDisplayMode:
        layout, color = mode.layout_ids
        simple, scan = layout <= 2, layout <= 4
        choices = {
            2: ["ServiceType", "CTCSS/DCS"]
            if simple
            else [
                "SiteName",
                "Rssi",
                "TGID",
                "UnitId",
                "Frequency",
                "Volume&Squelch",
                *(["BattVoltage", "Filter", "Lcn", "Rssi Bar"] if scan else []),
                "USB1_vbus",
                "Noise",
            ],
            3: ["P25Status", "Volume", "Squelch", "REC", "Day", "Time"]
            + (["Modulation", "ATT"] if simple else []),
            4: ["Empty"] * 10,
        }
        if scan:
            choices[1] = ["SiteName", "Empty", "Frequency"]
        for group, tokens in choices.items():
            records.append(
                "\t".join(["DispOptItems", f"DispOptId={group}", f"DispLayoutId={layout}", *tokens])
            )
        counts = {
            1: 6 if simple else (9 if scan else 11),
            3: 8 if simple else 6,
            4: len(choices[2]),
            5: 10,
            6: 6 if simple else 5,
            7: 5,
        }
        if scan:
            counts[2] = 3
        for group, count in counts.items():
            text = {1: "ffd600", 2: "ff8800", 4: "a5d8ff"}.get(group, "ffffff")
            # Invented demo colors echo the red/green/blue name bands in the
            # user's reference photos; no private profile content is read.
            name_colors = {0: "ff3030", 2: "40f040", 4: "4477ff"} if scan and group == 1 else {}
            records.append(
                "\t".join(
                    [
                        "DispColors",
                        f"DispColorId={group}",
                        f"ColorLayoutId={color}",
                        *[
                            part
                            for i in range(count)
                            for part in (name_colors.get(i, text), "000000")
                        ],
                    ]
                )
            )
    return "\r\n".join(records).encode("ascii")


def synthetic_profile():
    return parse_scanner_display_profile(synthetic_profile_bytes())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    profile = synthetic_profile()
    sample = RadioStateSnapshot(
        system="Demo County Public Safety",
        department="Central Dispatch",
        channel="Dispatch 1 (Demo)",
        site="Metro Simulcast",
        frequency="00949000",
        sub_audio_detected="NAC:012",
        service_type="002",
        talkgroup_id="00101",
        unit_id="00042",
        volume=5,
        squelch=2,
        rssi=-67.5,
        recording="Off",
        p25_status="P25",
    )
    for mode in ScannerDisplayMode:
        screen = resolve_scanner_display_screen(profile, mode)
        mode_sample = sample
        if mode.layout_ids[0] >= 5:
            # Do not populate a special-screen example from scanning hierarchy
            # or trunk-ID values merely because the demo has them available.
            mode_sample = replace(
                sample,
                system=None,
                department=None,
                channel=None,
                site=None,
                talkgroup_id=None,
                unit_id=None,
                service_type=None,
                sub_audio_detected=None,
                p25_status=None,
                frequency={
                    ScannerDisplayMode.WEATHER: "01625500",
                    ScannerDisplayMode.TONE_OUT: "00461500",
                }.get(mode, "00949000"),
            )
        values = scanner_display_values(screen, mode_sample, source_mode=mode, current=True)
        path = args.output_dir / f"{mode.value}.svg"
        # Developer-selected output only; no reader for real/private profiles.
        with path.open("x", encoding="utf-8") as stream:
            stream.write(render_scanner_display_preview(screen, values))
    print("Wrote seven offline synthetic SVG previews; no scanner or network access.")


if __name__ == "__main__":
    main()
