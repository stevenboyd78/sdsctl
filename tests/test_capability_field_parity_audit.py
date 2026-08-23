from __future__ import annotations

import re
from dataclasses import fields
from pathlib import Path

from sds200.state import RadioStateSnapshot

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
AUDIT = REPOSITORY_ROOT / "docs" / "capability-field-parity-audit.md"


def _section(document: str, heading: str, next_heading: str) -> str:
    return document.split(heading, 1)[1].split(next_heading, 1)[0]


def _field_rows(inventory: str) -> dict[str, tuple[str, ...]]:
    rows: dict[str, tuple[str, ...]] = {}
    for line in inventory.splitlines():
        if not line.startswith("| `"):
            continue
        cells = tuple(cell.strip() for cell in line.strip("|").split("|"))
        rows[cells[0].strip("`")] = cells
    return rows


def test_audit_inventory_matches_every_shared_radio_state_field() -> None:
    document = AUDIT.read_text(encoding="utf-8")
    inventory = _section(
        document,
        "## Shared live-state field inventory",
        "## Modeled data outside shared live state",
    )
    documented_fields = tuple(re.findall(r"^\| `([a-z0-9_]+)` \|", inventory, flags=re.MULTILINE))
    expected_fields = tuple(field.name for field in fields(RadioStateSnapshot))

    assert documented_fields == expected_fields
    assert f"canonical {len(expected_fields)}-field" in inventory


def test_audit_matrix_preserves_reviewed_renderer_distinctions() -> None:
    document = AUDIT.read_text(encoding="utf-8")
    inventory = _section(
        document,
        "## Shared live-state field inventory",
        "## Modeled data outside shared live state",
    )
    rows = _field_rows(inventory)

    assert rows["mode"][4] == "U/R*"
    assert rows["screen"][4] == "U/R*"
    assert rows["screen"][5] == "R*"
    assert rows["system"][4] == "R*"
    assert rows["system_hold"][7] == "R+C* / —"
    assert rows["volume"][4] == "R+C†"

    outside_shared_state = _section(
        document,
        "## Modeled data outside shared live state",
        "## Semantic-control parity",
    )
    normalized_outside = outside_shared_state.casefold()
    assert "dedicated `sdsctl battery`" in normalized_outside
    assert "dedicated `sdsctl command sts`" in normalized_outside


def test_audit_preserves_evidence_and_finding_boundaries() -> None:
    document = AUDIT.read_text(encoding="utf-8")
    normalized = " ".join(document.split())

    for required in (
        "merge commit `eac8e8033527607789451dccf0112b240fa0222e`",
        "August 22, 2026",
        "does not infer undocumented protocol semantics",
        "does not claim that every modeled command works on every supported model",
        "A field delivered in daemon JSON or MQTT is also not counted as human-visible",
        "`R1`",
        "`R2`",
        "`R3`",
        "`R4`",
        "SDS150",
        "specification-only",
        "The daemon API, SSE payloads, and generic MQTT `state/radio` "
        "publication preserve the complete mapping",
        "GSI battery",
        "System Status",
        "Unknown or deferred is deliberately different from unsupported",
        "Only `A01` is scheduled by this audit",
        "## Milestone 26.3 handoff",
        "existing copied-tree or USB verified executor",
        "canonical private host-state directory",
        "Supported Name Tag replacement may target existing catalog",
        "Arbitrary positional-field editing",
    ):
        assert required in document or required in normalized
