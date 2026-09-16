"""Bounded, per-observation Mimic fields outside the shared radio snapshot.

Only the adapter's already-qualified, single-screen records enter here. No
scanner queries, retained raw XML, database lookup, or host-clock substitutes.
PSI/GSI V1.02 pp.18-23 and SDS200 manual pp.39-41 define the ordinary sources.
The separate UnitID.U_Id/Name node is also observed on SDS200 firmware.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from .models import ScannerInfo


@dataclass(frozen=True, slots=True)
class ScannerDisplayLiveValues:
    # None = source unavailable; empty string = confirmed blank/off.
    tokens: tuple[tuple[str, str | None], ...] = ()
    regions: tuple[tuple[str, str | None], ...] = ()


def _bounded(value: str | None) -> str | None:
    if value is not None and (
        len(value) > 256 or any(unicodedata.category(c).startswith("C") for c in value)
    ):
        return "\x00"  # Fixed invalid-source sentinel; never rendered.
    return value


def project_live_values(info: ScannerInfo) -> ScannerDisplayLiveValues:
    def attr(tag: str, key: str) -> str | None:
        node = info.node(tag)
        return None if node is None else _bounded(node.get(key))

    def first(tags: tuple[str, ...], key: str) -> str | None:
        for tag in tags:
            value = attr(tag, key)
            if value is not None:
                return value
        return None

    def icon(raw: str | None, label: str, active: tuple[str, ...] = ("On",)) -> str | None:
        if raw == "Off":
            return ""
        if raw in active:
            return label
        return None if raw is None else "\x00"

    channel = ("ConvFrequency", "TGID", "SrchFrequency", "WxChannel", "ToneOutChannel")
    frequency = ("ConvFrequency", "SiteFrequency", "SrchFrequency", "WxChannel", "ToneOutChannel")
    tokens: dict[str, str | None] = {
        "FL_Name": attr("MonitorList", "Name"),
        "SystemType": attr("System", "SystemType"),
        "Modulation": first(
            ("ConvFrequency", "Site", "SrchFrequency", "WxChannel", "ToneOutChannel"), "Mod"
        ),
        "PRI": icon(attr("DualWatch", "PRI"), "PRI", ("DND", "Priority")),
        "CC": icon(attr("DualWatch", "CC"), "CC", ("DND", "Priority")),
        "WxPRI": icon(attr("DualWatch", "WX"), "WX", ("Priority",)),
        "REC": icon(attr("Property", "Rec"), "REC"),
        "IFX": icon(first(frequency, "IFX"), "IFX"),
        "P_Ch": icon(first(channel, "P_Ch"), "P"),
    }
    offset = first(channel, "LVL")
    tokens["LVL"] = (
        ""
        if offset == "0"
        else f"V{int(offset):+d}"
        if offset in ("-3", "-2", "-1", "1", "2", "3")
        else None
        if offset is None
        else "\x00"
    )
    # Keep valid, scanner-reported modulation literal; do not infer it from P25.
    if tokens["Modulation"] not in (None, "Auto", "AM", "NFM", "FM", "WFM", "FMB"):
        tokens["Modulation"] = "\x00"
    tags = (attr("MonitorList", "N_Tag"), attr("System", "N_Tag"), first(channel, "N_Tag"))
    if all(value is not None for value in tags):
        parts = []
        for value, width, maximum in zip(tags, (2, 2, 3), (99, 99, 999), strict=True):
            if value == "None":
                parts.append("-" * width)
            elif (
                value is not None
                and value.isascii()
                and value.isdecimal()
                and len(value) <= width
                and int(value) <= maximum
            ):
                parts.append(value)
            else:
                parts = []
                break
        tokens["NumberTag"] = "Tag:" + ".".join(parts) if parts else "\x00"
    else:
        tokens["NumberTag"] = None
    uid = attr("UnitID", "U_Id") if info.node("UnitID") is not None else first(channel, "U_Id")
    if info.node("UnitID") is not None and uid is None:
        uid = ""  # Empty UnitID record explicitly clears the previous caller.
    tokens["UnitId"] = "" if uid == "None" else uid
    tokens["UnitIdName"] = attr("UnitID", "Name")
    digital = attr("Property", "P25Status")
    tokens["P25Status"] = "" if digital == "None" else "DATA" if digital == "Data" else digital
    regions: dict[str, str | None] = {}
    for index in (1, 2):
        tag = f"InfoArea{index}"
        # The SDS200 may omit these records while showing F/S/D quick-key
        # rows on its LCD. Missing telemetry is not a confirmed blank, and
        # current Q_Key selections cannot reconstruct a bank's full status.
        regions[f"information_{index}"] = attr(tag, "Text")
    if info.screen in ("conventional_scan", "trunk_scan"):
        regions.update({"soft_key_1": "SYSTEM", "soft_key_2": "DEPT", "soft_key_3": "CHANNEL"})
        if info.node("OverWrite") is not None:
            regions["channel"] = attr("OverWrite", "Text")
    return ScannerDisplayLiveValues(tuple(tokens.items()), tuple(regions.items()))
