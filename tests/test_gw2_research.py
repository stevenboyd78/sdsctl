from __future__ import annotations

import base64
import json
import os
import socket
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from sds200.gw2_research import (
    GW2_RESEARCH_CAPTURE_SCHEMA,
    Gw2CandidateForm,
    Gw2DatagramClassification,
    Gw2DatagramObservation,
    Gw2ProbePlan,
    Gw2ProbeResult,
    Gw2ResearchLimits,
    capture_gw2_udp,
    gw2_candidate,
    write_private_gw2_capture,
)

FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "advanced_protocol"
    / "synthetic-gw2-datagrams.json"
)
PROTOCOL_RESEARCH = (
    Path(__file__).parents[1] / "docs" / "advanced-protocol-research.md"
)


class _Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class _FakeSocket:
    def __init__(
        self,
        clock: _Clock,
        receives: list[tuple[bytes, int] | BaseException],
        *,
        cleanup_error: bool = False,
    ) -> None:
        self.clock = clock
        self.receives = list(receives)
        self.cleanup_error = cleanup_error
        self.timeout = 0.0
        self.connected: tuple[str, int] | None = None
        self.sent: list[bytes] = []
        self.closed = False

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout

    def connect(self, address: tuple[str, int]) -> None:
        self.connected = address

    def send(self, data: bytes) -> int:
        self.sent.append(data)
        if self.cleanup_error and len(self.sent) > 1:
            raise OSError("synthetic cleanup failure")
        return len(data)

    def recvmsg(self, buffer_size: int) -> tuple[bytes, list[object], int, object]:
        self.clock.advance(0.1)
        if not self.receives:
            self.clock.advance(self.timeout)
            raise TimeoutError
        item = self.receives.pop(0)
        if isinstance(item, BaseException):
            raise item
        data, flags = item
        return data[:buffer_size], [], flags, None

    def close(self) -> None:
        self.closed = True


def _plan(tmp_path: Path) -> Gw2ProbePlan:
    return Gw2ProbePlan(
        target_host="192.0.2.20",
        target_port=50536,
        candidate_form=Gw2CandidateForm.COMMAND_TABLE_GW2_TYPE_1,
        output_path=tmp_path / "capture.json",
        scanner_model="SDS200",
        scanner_firmware="Version 1.26.01",
        limits=Gw2ResearchLimits(
            max_datagram_bytes=512,
            max_datagrams=4,
            max_elapsed_seconds=2.0,
            inactivity_seconds=0.5,
        ),
    )


def test_candidates_preserve_both_contradictory_exact_forms() -> None:
    command_table = gw2_candidate(Gw2CandidateForm.COMMAND_TABLE_GW2_TYPE_1)
    detail_row = gw2_candidate(Gw2CandidateForm.DETAIL_ROW_GWF_TYPE_1)

    assert command_table.start_wire == b"GW2,1,ON\r"
    assert command_table.cleanup_wire == b"GW2,1,OFF\r"
    assert detail_row.start_wire == b"GWF,1,ON\r"
    assert detail_row.cleanup_wire == b"GWF,1,OFF\r"
    assert command_table.form is not detail_row.form


@pytest.mark.parametrize("target", ["scanner.local", "::1", "", "192.0.2.999"])
def test_probe_plan_requires_ipv4_literal(tmp_path: Path, target: str) -> None:
    with pytest.raises(ValueError, match="IPv4 address literal"):
        Gw2ProbePlan(
            target_host=target,
            target_port=50536,
            candidate_form=Gw2CandidateForm.COMMAND_TABLE_GW2_TYPE_1,
            output_path=tmp_path / "capture.json",
            scanner_model="SDS200",
            scanner_firmware="Version 1.26.01",
        )


def test_confirmation_token_binds_every_exact_plan_field(tmp_path: Path) -> None:
    first = _plan(tmp_path)
    changed_candidate = Gw2ProbePlan(
        target_host=first.target_host,
        target_port=first.target_port,
        candidate_form=Gw2CandidateForm.DETAIL_ROW_GWF_TYPE_1,
        output_path=first.output_path,
        scanner_model=first.scanner_model,
        scanner_firmware=first.scanner_firmware,
        limits=first.limits,
    )
    changed_output = Gw2ProbePlan(
        target_host=first.target_host,
        target_port=first.target_port,
        candidate_form=first.candidate_form,
        output_path=tmp_path / "other.json",
        scanner_model=first.scanner_model,
        scanner_firmware=first.scanner_firmware,
        limits=first.limits,
    )

    assert len(first.confirmation_token) == 64
    assert first.confirmation_token != changed_candidate.confirmation_token
    assert first.confirmation_token != changed_output.confirmation_token

    changed_firmware = Gw2ProbePlan(
        target_host=first.target_host,
        target_port=first.target_port,
        candidate_form=first.candidate_form,
        output_path=first.output_path,
        scanner_model=first.scanner_model,
        scanner_firmware="Version 1.27.00",
        limits=first.limits,
    )
    assert first.confirmation_token != changed_firmware.confirmation_token


@pytest.mark.parametrize(
    ("model", "firmware"),
    [
        ("SDS100", "Version 1.26.01"),
        ("SDS200", ""),
        ("SDS200", " Version 1.26.01"),
        ("SDS200", "Version 1.26.01\n"),
    ],
)
def test_probe_plan_rejects_unqualified_physical_provenance(
    tmp_path: Path,
    model: str,
    firmware: str,
) -> None:
    with pytest.raises(ValueError):
        Gw2ProbePlan(
            target_host="192.0.2.20",
            target_port=50536,
            candidate_form=Gw2CandidateForm.COMMAND_TABLE_GW2_TYPE_1,
            output_path=tmp_path / "capture.json",
            scanner_model=model,
            scanner_firmware=firmware,
        )


def test_synthetic_fixture_preserves_exact_bytes_and_structural_only_scope() -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))

    assert fixture["synthetic"] is True
    assert fixture["semantic_claims"] == []
    records = fixture["records"]
    observations = [
        Gw2DatagramObservation(
            sequence=index,
            elapsed_seconds=index / 10,
            source_bytes=base64.b64decode(record["source_base64"], validate=True),
            transport_truncated=record["transport_truncated"],
        )
        for index, record in enumerate(records, start=1)
    ]

    assert len(observations[0].source_bytes) == 240
    assert observations[0].classification is Gw2DatagramClassification.OPAQUE_BINARY
    assert observations[1].source_bytes.startswith(b"GWF,")
    assert observations[1].classification is Gw2DatagramClassification.OPAQUE_BINARY
    assert observations[2].classification is Gw2DatagramClassification.UNEXPECTED_TEXT
    assert observations[3].as_dict()["possible_concatenated_text_records"] is True
    assert (
        observations[4].classification
        is Gw2DatagramClassification.TRANSPORT_TRUNCATED
    )
    for observation in observations:
        document = observation.as_dict()
        assert base64.b64decode(document["source_base64"], validate=True) == (
            observation.source_bytes
        )
        assert document["interpretation"] == "structural_only"


def test_capture_requires_matching_confirmation_and_stopped_owner(
    tmp_path: Path,
) -> None:
    plan = _plan(tmp_path)
    clock = _Clock()

    with pytest.raises(ValueError, match="verified stopped"):
        capture_gw2_udp(
            plan,
            confirmation_token=plan.confirmation_token,
            scanner_owner_stopped=False,
            monotonic=clock,
        )
    with pytest.raises(ValueError, match="does not match"):
        capture_gw2_udp(
            plan,
            confirmation_token="0" * 64,
            scanner_owner_stopped=True,
            monotonic=clock,
        )


def test_bounded_capture_sends_one_candidate_and_paired_cleanup(
    tmp_path: Path,
) -> None:
    plan = _plan(tmp_path)
    clock = _Clock()
    fake = _FakeSocket(
        clock,
        [
            (b"GW2," + bytes(range(16)), 0),
            (b"GW2," + bytes(range(32)), socket.MSG_TRUNC),
        ],
    )

    result = capture_gw2_udp(
        plan,
        confirmation_token=plan.confirmation_token,
        scanner_owner_stopped=True,
        socket_factory=lambda _family, _kind: fake,
        monotonic=clock,
        now=lambda: datetime(2026, 8, 31, tzinfo=UTC),
    )

    assert fake.connected == (plan.target_host, plan.target_port)
    assert fake.sent == [b"GW2,1,ON\r", b"GW2,1,OFF\r"]
    assert fake.closed is True
    assert result.safe_completion is True
    assert result.end_reason == "inactivity"
    assert len(result.observations) == 2
    assert result.observations[1].transport_truncated is True


def test_capture_attempts_cleanup_after_receive_failure(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    clock = _Clock()
    fake = _FakeSocket(clock, [OSError("synthetic receive failure")])

    result = capture_gw2_udp(
        plan,
        confirmation_token=plan.confirmation_token,
        scanner_owner_stopped=True,
        socket_factory=lambda _family, _kind: fake,
        monotonic=clock,
    )

    assert fake.sent == [b"GW2,1,ON\r", b"GW2,1,OFF\r"]
    assert result.cleanup_sent is True
    assert result.safe_completion is False
    assert result.capture_error == "OSError"


def test_cleanup_failure_is_not_safe_completion(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    clock = _Clock()
    fake = _FakeSocket(clock, [], cleanup_error=True)

    result = capture_gw2_udp(
        plan,
        confirmation_token=plan.confirmation_token,
        scanner_owner_stopped=True,
        socket_factory=lambda _family, _kind: fake,
        monotonic=clock,
    )

    assert result.start_sent is True
    assert result.cleanup_sent is False
    assert result.cleanup_error == "OSError"
    assert result.safe_completion is False


def test_private_capture_is_mode_0600_and_never_replaced(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    result = Gw2ProbeResult(
        plan=plan,
        captured_at=datetime(2026, 8, 31, tzinfo=UTC),
        observations=(),
        end_reason="inactivity",
        start_sent=True,
        cleanup_sent=True,
    )

    write_private_gw2_capture(plan.output_path, result)

    assert os.stat(plan.output_path).st_mode & 0o777 == 0o600
    document: dict[str, Any] = json.loads(plan.output_path.read_text(encoding="utf-8"))
    assert document["schema"] == GW2_RESEARCH_CAPTURE_SCHEMA
    assert document["semantic_claims"] == []
    assert document["safe_completion"] is True
    with pytest.raises(FileExistsError):
        write_private_gw2_capture(plan.output_path, result)


def test_protocol_research_records_sanitized_physical_gw2_conclusion() -> None:
    document = PROTOCOL_RESEARCH.read_text(encoding="utf-8")
    section = document.split(
        "### Milestone 29.6 physical observation and conclusion",
        1,
    )[1].split("## Milestone 24.1 boundary", 1)[0]
    normalized = " ".join(section.split())

    for required in (
        "physical SDS200 running firmware 1.26.01 through LAN UDP control",
        "exact `GW2,1,ON\\r`",
        "complete four-byte `ERR\\r` UDP datagram after 31.844 ms",
        "exact paired cleanup `GW2,1,OFF\\r`",
        "No other syntax was guessed or sent",
        "all three Waterfall cards `running` at 3.1 fps",
        "phase-stable text-GWF path remains authoritative",
    ):
        assert required in normalized
    assert "192.168." not in section
    assert "sdsctl-local-validation" not in section
