from __future__ import annotations

import os
import runpy
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from xml.etree import ElementTree

import pytest

from sds200.scanner_display_layout import resolve_scanner_display_screen
from sds200.scanner_display_preview import render_scanner_display_preview
from sds200.scanner_display_profile import ScannerDisplayColor, ScannerDisplayMode
from sds200.scanner_display_values import scanner_display_values
from sds200.state import RadioStateSnapshot

SVG = "{http://www.w3.org/2000/svg}"


def synthetic_profile():
    root = Path(__file__).resolve().parents[1]
    namespace = runpy.run_path(str(root / "scripts/render_scanner_display_preview.py"))
    return namespace["synthetic_profile"]()


@pytest.mark.parametrize("mode", list(ScannerDisplayMode))
def test_svg_has_exact_regions_and_no_active_content_or_external_resources(mode):
    screen = resolve_scanner_display_screen(synthetic_profile(), mode)
    values = scanner_display_values(screen, RadioStateSnapshot(), current=True, source_mode=mode)
    source = render_scanner_display_preview(screen, values)
    root = ElementTree.fromstring(source)
    groups = [element for element in root.iter(f"{SVG}g") if "data-region" in element.attrib]
    assert {element.attrib["data-region"] for element in groups} == {
        slot.region.id for slot in screen.regions
    }
    assert len(groups) == len(screen.regions)
    assert root.attrib["viewBox"] == "0 0 960 740"
    assert all(
        element.tag not in {f"{SVG}script", f"{SVG}foreignObject", f"{SVG}image", f"{SVG}a"}
        for element in root.iter()
    )
    assert all(
        not attribute.lower().startswith("on") and "href" not in attribute.lower()
        for element in root.iter()
        for attribute in element.attrib
    )
    assert "OFFLINE PREVIEW" in source and "No scanner connection" in source
    assert len(root.findall(f".//{SVG}clipPath")) == len(screen.regions)
    assert len(source.encode()) < 100_000


def test_untrusted_source_text_and_metadata_are_escaped_not_markup():
    mode = ScannerDisplayMode.SIMPLE_CONVENTIONAL
    screen = resolve_scanner_display_screen(synthetic_profile(), mode)
    screen = replace(screen, color_mode="COLOR</text><script>alert(1)</script>")
    snapshot = RadioStateSnapshot(system="<script>x</script> & <img src=x onerror=x>")
    values = scanner_display_values(screen, snapshot, current=True, source_mode=mode)
    source = render_scanner_display_preview(screen, values)
    root = ElementTree.fromstring(source)
    assert root.find(f".//{SVG}script") is None
    assert root.find(f".//{SVG}img") is None
    assert "&lt;script&gt;" in source and "&amp;" in source


def test_malformed_color_does_not_become_svg_attribute_injection():
    mode = ScannerDisplayMode.SIMPLE_CONVENTIONAL
    screen = resolve_scanner_display_screen(synthetic_profile(), mode)
    screen = replace(
        screen,
        regions=(
            replace(
                screen.regions[0],
                stored_color=ScannerDisplayColor('ffffff" onload="injected', "000000"),
            ),
        )
        + screen.regions[1:],
    )
    values = scanner_display_values(screen, RadioStateSnapshot())
    source = render_scanner_display_preview(screen, values)
    assert "injected" not in source
    ElementTree.fromstring(source)


@pytest.mark.parametrize("case", ["missing", "duplicate", "foreign"])
def test_values_must_match_exactly_once(case):
    screen = resolve_scanner_display_screen(synthetic_profile(), ScannerDisplayMode.SIMPLE_TRUNK)
    values = scanner_display_values(screen, RadioStateSnapshot())
    if case == "missing":
        values = values[:-1]
    elif case == "duplicate":
        values = values + values[:1]
    else:
        values = (replace(values[0], region_id="foreign"),) + values[1:]
    with pytest.raises(ValueError, match="exactly once"):
        render_scanner_display_preview(screen, values)


def test_synthetic_gallery_cli_and_non_overwrite_behavior(tmp_path):
    root = Path(__file__).resolve().parents[1]
    output = tmp_path / "gallery"
    command = [
        sys.executable,
        str(root / "scripts/render_scanner_display_preview.py"),
        "--output-dir",
        str(output),
    ]
    environment = dict(os.environ, PYTHONPATH=str(root / "src"))
    completed = subprocess.run(
        command, env=environment, capture_output=True, text=True, timeout=10, check=False
    )
    assert completed.returncode == 0, completed.stderr
    assert {path.name for path in output.iterdir()} == {
        f"{mode.value}.svg" for mode in ScannerDisplayMode
    }
    before = {path.name: path.read_bytes() for path in output.iterdir()}
    for source in before.values():
        ElementTree.fromstring(source)
        assert b"No scanner connection" in source
    for mode in ("search_close_call", "weather", "tone_out"):
        assert b"Metro Simulcast" not in before[f"{mode}.svg"]
        assert b"00101" not in before[f"{mode}.svg"]
        assert b"Demo County" not in before[f"{mode}.svg"]
    repeated = subprocess.run(
        command, env=environment, capture_output=True, text=True, timeout=10, check=False
    )
    assert repeated.returncode != 0
    assert before == {path.name: path.read_bytes() for path in output.iterdir()}
