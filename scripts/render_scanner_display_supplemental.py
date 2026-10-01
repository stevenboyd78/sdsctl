#!/usr/bin/env python3
"""Generate synthetic clock/Favorites presentation states; no live acquisition."""

from __future__ import annotations

import argparse
import runpy
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from uuid import UUID

from sds200.daemon_display_frames import SupplementalDisplayFrameSet
from sds200.models import FavoritesQuickKeyState as Key
from sds200.scanner_display_frame_preview import render_scanner_display_gallery
from sds200.scanner_display_supplemental import (
    DisplayClockValue,
    DisplayFavoritesValue,
    SupplementalDisplayValues,
    SupplementalValueStatus,
)
from sds200.scanner_display_supplemental_presentation import (
    SupplementalPresentation,
    present_supplemental_capture,
)


def build_presentations() -> dict[str, SupplementalPresentation]:
    """Reuse the canonical invented profile, with no user inputs or I/O targets."""
    fixture = runpy.run_path(str(Path(__file__).with_name("render_scanner_display_frames.py")))
    frames = fixture["build_scenarios"]()
    values = SupplementalDisplayValues(
        DisplayClockValue(SupplementalValueStatus.CURRENT, datetime(2026, 9, 21, 4, 12, 59), 0.25),
        DisplayFavoritesValue(
            SupplementalValueStatus.CURRENT,
            tuple([Key.ENABLED, Key.DISABLED, Key.NONEXISTENT] * 33 + [Key.ENABLED]),
            0.5,
        ),
    )

    def capture(name: str) -> SupplementalDisplayFrameSet:
        variants = frames[name]
        return SupplementalDisplayFrameSet(
            endpoint_id=str(UUID(int=100)),
            stream_id=str(UUID(int=102)),
            session_id=str(UUID(int=103)),
            sequence=variants["profile"].sequence,
            profile_invalidation=0,
            captured_at=10.0,
            preferred=variants["profile"],
            simple=variants["simple"],
            detail=variants["detail"],
            supplemental=values,
        )

    trunk = capture("trunk")
    cases = {
        "current_trunk": (trunk, 10.0),
        "current_conventional": (capture("conventional"), 10.0),
        "clock_expired": (
            replace(
                trunk, supplemental=replace(values, clock=replace(values.clock, age_seconds=5))
            ),
            10.0,
        ),
        "favorites_expired": (
            replace(
                trunk,
                supplemental=replace(values, favorites=replace(values.favorites, age_seconds=5)),
            ),
            10.0,
        ),
        "psi_expired": (trunk, 15.0),
        "clock_invalid": (
            replace(
                trunk,
                supplemental=replace(
                    values, clock=DisplayClockValue(SupplementalValueStatus.INVALID_RTC)
                ),
            ),
            10.0,
        ),
        "supplemental_disabled": (
            replace(
                trunk,
                supplemental=SupplementalDisplayValues(
                    DisplayClockValue(SupplementalValueStatus.DISABLED),
                    DisplayFavoritesValue(SupplementalValueStatus.DISABLED),
                ),
            ),
            10.0,
        ),
    }
    return {
        name: present_supplemental_capture(sample, now=now) for name, (sample, now) in cases.items()
    }


def render_preview() -> str:
    presentations = build_presentations()
    scenarios = {
        name: {"profile": view.preferred, "simple": view.simple, "detail": view.detail}
        for name, view in presentations.items()
    }
    notes = {
        name: (
            "SYNTHETIC POINT-IN-TIME PREVIEW — not a live polling or freshness contract.\n"
            f"Scanner clock: {view.clock.status.value}. Scanner-local RTC; 24-hour HH:MM.\n"
            "Only the profile's valid Day/Time slots receive the clock. "
            "No host-time or time-zone conversion.\n"
            f"Global Favorites quick keys: {view.favorites.status.value}.\n"
            "Numbered states below are diagnostics, NOT the scanner's F0/S0/D0 display.\n"
            + ("\n".join(view.favorites_rows) or "No current Favorites states to display.")
        )
        for name, view in presentations.items()
    }
    return render_scanner_display_gallery(scenarios, notes=notes)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    document = render_preview()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with (args.output_dir / "index.html").open("x", encoding="utf-8") as output:
        output.write(document)
    print("Wrote 7 synthetic supplemental scenarios; no scanner or network access.")


if __name__ == "__main__":
    main()
