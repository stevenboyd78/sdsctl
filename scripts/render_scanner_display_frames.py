#!/usr/bin/env python3
"""Generate a local-only Mimic-SDS adapter transition gallery from invented data."""

from __future__ import annotations

import argparse
import runpy
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
    DisplayProfileRefreshFailure,
    DisplayProfileSourceKind,
    ScannerDisplayProfileStore,
)
from sds200.xml_protocol import ScannerInfoParser


def build_scenarios() -> dict[str, dict[str, ScannerDisplayFrame]]:
    # Reuse the existing synthetic profile; never read a real scanner profile.
    fixture = runpy.run_path(str(Path(__file__).with_name("render_scanner_display_preview.py")))
    profile_bytes = fixture["synthetic_profile_bytes"]()
    endpoint = UUID(int=100)
    binding = DisplayProfileBinding(endpoint, UUID(int=101), DisplayProfileSourceKind.MANUAL_IMPORT)
    stamp = datetime(2026, 9, 15, tzinfo=UTC)
    store = ScannerDisplayProfileStore(endpoint)
    adapter = ScannerDisplayAdapter(endpoint, stale_after=5)
    scenarios: dict[str, dict[str, ScannerDisplayFrame]] = {}

    def capture(name: str, now: float) -> None:
        profile = store.snapshot(endpoint)
        scenarios[name] = {
            "profile": adapter.frame(profile, now=now),
            "simple": adapter.frame(profile, now=now, style=ScannerDisplayStyle.SIMPLE),
            "detail": adapter.frame(profile, now=now, style=ScannerDisplayStyle.DETAIL),
        }

    capture("disconnected", 0)
    session = adapter.begin_session(now=1)
    capture("waiting", 1)
    sequence = 0

    def observe(screen: str, content: str, now: float, mode: str = "Demo") -> None:
        nonlocal sequence
        sequence += 1
        info = ScannerInfoParser().parse(
            "PSI", f'<ScannerInfo Mode="{mode}" V_Screen="{screen}">{content}</ScannerInfo>'
        )
        adapter.observe(session, info, sequence=sequence, received_at=now, now=now)

    conventional = (
        '<System Name="Demo County Public Safety"/><Department Name="Central Dispatch"/>'
        '<ConvFrequency Name="Dispatch 1 (Demo)" Freq="01541500" SvcType="002" SAD="CTCSS:123.0"/>'
        '<Property VOL="5" SQL="2" Rec="Off" Rssi="-67.5"/>'
    )
    observe("conventional_scan", conventional, 2, "Scan Mode")
    capture("profile_missing", 2)
    ticket = store.begin_refresh(binding, started_at=stamp)
    preview = store.prepare(ticket, profile_bytes, acquired_at=stamp)
    store.commit(preview, imported_at=stamp)
    capture("conventional", 2)
    observe(
        "trunk_scan",
        (
            '<System Name="Demo Regional Radio"/><Department Name="North Dispatch"/>'
            '<Site Name="Metro Simulcast"/><SiteFrequency Freq="07694312"/>'
            '<TGID Name="Dispatch 2 (Demo)" TGID="00101" U_Id="00042" SvcType="002" SAD="NAC:012"/>'
            '<Property VOL="5" SQL="2" Rec="On" Rssi="-71.0" P25Status="P25"/>'
        ),
        3,
        "Trunk Scan Hold",
    )
    capture("trunk", 3)
    capture("stale", 8)
    adapter.disconnect(session)
    capture("connection_lost", 9)
    session = adapter.begin_session(now=10)
    capture("reconnecting", 10)
    observe("conventional_scan", conventional, 11, "Scan Mode")
    capture("recovered", 11)
    refresh = store.begin_refresh(binding, started_at=stamp)
    capture("profile_refresh_pending", 11)
    store.fail(refresh, DisplayProfileRefreshFailure.INVALID_PROFILE)
    capture("profile_refresh_failed", 11)
    refresh = store.begin_refresh(binding, started_at=stamp)
    preview = store.prepare(refresh, profile_bytes, acquired_at=stamp)
    store.commit(preview, imported_at=stamp)
    for screen, node, name, freq, moment in (
        ("quick_search", "SrchFrequency", "search", "00949000", 12),
        ("wx_alert", "WxChannel", "weather", "01625500", 13),
        ("tone_out", "ToneOutChannel", "tone_out", "00461500", 14),
    ):
        observe(screen, f'<{node} Freq="{freq}"/><Property VOL="0" SQL="2" Rec="Off"/>', moment)
        capture(name, moment)
    observe(
        "wx_alert",
        '<WxChannel Freq="01625500"/><ViewDescription>'
        '<PopupScreen Text="Demo dialog"/></ViewDescription>',
        15,
    )
    capture("popup", 15)
    observe("future_screen", '<WxChannel Freq="01625500"/>', 16)
    capture("unsupported", 16)
    observe("wx_alert", '<WxChannel Freq="01625500"/><TGID Name="Wrong context"/>', 17)
    capture("conflicting_records", 17)
    observe(
        "conventional_scan",
        '<ConvFrequency Name="&lt;img src=x onerror=alert(1)&gt; &amp; demo"/>',
        18,
    )
    capture("safe_text", 18)
    observe("conventional_scan", "", 19)
    capture("fields_cleared", 19)
    return scenarios


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    scenarios = build_scenarios()
    with (args.output_dir / "index.html").open("x", encoding="utf-8") as output:
        output.write(render_scanner_display_gallery(scenarios))
    print(f"Wrote {len(scenarios)} synthetic transition scenarios; no scanner or network access.")


if __name__ == "__main__":
    main()
