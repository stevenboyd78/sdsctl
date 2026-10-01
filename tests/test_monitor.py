from datetime import UTC, datetime
from io import StringIO

import pytest

from sds200.monitor import TerminalMonitor, format_snapshot
from sds200.state import RadioStateSnapshot


def test_format_snapshot_includes_live_fields() -> None:
    snapshot = RadioStateSnapshot(
        mode="Trunk Scan",
        system="Utah Communications Authority (P25)",
        channel="Patch 65132",
        frequency="769.431250MHz",
        talkgroup_id="TGID:000123",
        unit_id="UID:000045",
        p25_status="unrecognized status",
        battery=0,
        signal=5,
        volume=10,
        squelch=2,
    )

    rendered = format_snapshot(
        snapshot,
        "serial:///dev/ttyACM0",
        observed_at=datetime(2026, 7, 23, 12, 0, tzinfo=UTC),
    )

    assert "SDS-series Live Monitor" in rendered
    assert "Patch 65132" in rendered
    assert "769.431250MHz" in rendered
    assert "Talkgroup   : TGID:000123" in rendered
    assert "Unit ID     : UID:000045" in rendered
    assert "P25 status  : unrecognized status" in rendered
    assert "Battery raw : 0" in rendered
    assert "█████ (5)" in rendered


def test_format_snapshot_bounds_literal_values_and_clears_missing_telemetry() -> None:
    raw = "TGID:000123\x1b\n\r\t\u202e\u2028" + "界" * 500

    rendered = format_snapshot(
        RadioStateSnapshot(talkgroup_id=raw, unit_id="0"),
        "fake://scanner",
        observed_at=datetime(2026, 7, 23, 12, 0, tzinfo=UTC),
    )

    talkgroup = next(line for line in rendered.splitlines() if line.startswith("Talkgroup"))
    assert "TGID:000123??????" in talkgroup and talkgroup.endswith("…")
    assert "Unit ID     : 0" in rendered
    assert "P25 status  : —" in rendered
    assert "Battery raw : —" in rendered
    assert "\x1b" not in rendered and "\u202e" not in rendered


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), True, "1"])
def test_format_snapshot_rejects_nonfinite_or_non_numeric_battery(value: object) -> None:
    rendered = format_snapshot(
        RadioStateSnapshot(battery=value),
        "fake://scanner",
        observed_at=datetime(2026, 7, 23, 12, 0, tzinfo=UTC),
    )

    assert "Battery raw : —" in rendered


def test_terminal_monitor_can_append_without_ansi_clear() -> None:
    stream = StringIO()
    monitor = TerminalMonitor(stream=stream, clear=False)

    monitor.render(RadioStateSnapshot(channel="Dispatch"), "fake://scanner")

    assert not stream.getvalue().startswith("\x1b")
    assert "Dispatch" in stream.getvalue()
