from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import math
import os
import socket
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol

GW2_RESEARCH_CAPTURE_SCHEMA = "sdsctl.gw2-research-capture"
GW2_RESEARCH_CAPTURE_VERSION = 1


class Gw2CandidateForm(StrEnum):
    """Exact, contradictory V2.00 forms available for separate research."""

    COMMAND_TABLE_GW2_TYPE_1 = "command-table-gw2-type-1"
    DETAIL_ROW_GWF_TYPE_1 = "detail-row-gwf-type-1"


@dataclass(frozen=True, slots=True)
class Gw2Candidate:
    """One exact start/cleanup pair without a claim that it is correct."""

    form: Gw2CandidateForm
    start_wire: bytes
    cleanup_wire: bytes
    evidence: str


_GW2_CANDIDATES = MappingProxyType(
    {
        Gw2CandidateForm.COMMAND_TABLE_GW2_TYPE_1: Gw2Candidate(
            form=Gw2CandidateForm.COMMAND_TABLE_GW2_TYPE_1,
            start_wire=b"GW2,1,ON\r",
            cleanup_wire=b"GW2,1,OFF\r",
            evidence=(
                "V2.00 command-table name combined with the qualified text-GWF "
                "type-1 control shape"
            ),
        ),
        Gw2CandidateForm.DETAIL_ROW_GWF_TYPE_1: Gw2Candidate(
            form=Gw2CandidateForm.DETAIL_ROW_GWF_TYPE_1,
            start_wire=b"GWF,1,ON\r",
            cleanup_wire=b"GWF,1,OFF\r",
            evidence=(
                "V2.00 GW2 detail-row spelling, which contradicts the command table"
            ),
        ),
    }
)


def gw2_candidate(form: Gw2CandidateForm | str) -> Gw2Candidate:
    """Return one immutable candidate without selecting it automatically."""

    return _GW2_CANDIDATES[Gw2CandidateForm(form)]


@dataclass(frozen=True, slots=True)
class Gw2ResearchLimits:
    """Independent hard limits for one physical research capture."""

    max_datagram_bytes: int = 4096
    max_datagrams: int = 32
    max_elapsed_seconds: float = 3.0
    inactivity_seconds: float = 0.75

    def __post_init__(self) -> None:
        for integer_name, integer_value in (
            ("max_datagram_bytes", self.max_datagram_bytes),
            ("max_datagrams", self.max_datagrams),
        ):
            if type(integer_value) is not int:
                raise TypeError(f"{integer_name} must be an integer.")
            if integer_value <= 0:
                raise ValueError(f"{integer_name} must be positive.")
        for numeric_name, numeric_value in (
            ("max_elapsed_seconds", self.max_elapsed_seconds),
            ("inactivity_seconds", self.inactivity_seconds),
        ):
            if isinstance(numeric_value, bool) or not isinstance(
                numeric_value, (int, float)
            ):
                raise TypeError(f"{numeric_name} must be numeric.")
            if not math.isfinite(numeric_value) or numeric_value <= 0:
                raise ValueError(f"{numeric_name} must be finite and positive.")
        if self.inactivity_seconds > self.max_elapsed_seconds:
            raise ValueError(
                "inactivity_seconds must not exceed max_elapsed_seconds."
            )

    def as_dict(self) -> dict[str, int | float]:
        return {
            "max_datagram_bytes": self.max_datagram_bytes,
            "max_datagrams": self.max_datagrams,
            "max_elapsed_seconds": float(self.max_elapsed_seconds),
            "inactivity_seconds": float(self.inactivity_seconds),
        }


@dataclass(frozen=True, slots=True)
class Gw2ProbePlan:
    """An exact, reviewable physical-probe plan."""

    target_host: str
    target_port: int
    candidate_form: Gw2CandidateForm
    output_path: Path
    scanner_model: str
    scanner_firmware: str
    limits: Gw2ResearchLimits = Gw2ResearchLimits()

    def __post_init__(self) -> None:
        try:
            address = ipaddress.ip_address(self.target_host)
        except ValueError as exc:
            raise ValueError("GW2 research target must be an IPv4 address literal.") from exc
        if address.version != 4:
            raise ValueError("GW2 research target must be an IPv4 address literal.")
        if type(self.target_port) is not int:
            raise TypeError("GW2 research target port must be an integer.")
        if not 1 <= self.target_port <= 65535:
            raise ValueError("GW2 research target port must be between 1 and 65535.")
        if not self.output_path.name:
            raise ValueError("GW2 research output path must name a file.")
        if self.scanner_model != "SDS200":
            raise ValueError("Milestone 29.6 GW2 research requires exact model SDS200.")
        if not _is_safe_provenance_text(self.scanner_firmware):
            raise ValueError(
                "GW2 research scanner firmware must be printable ASCII without "
                "leading or trailing whitespace."
            )

    @property
    def candidate(self) -> Gw2Candidate:
        return gw2_candidate(self.candidate_form)

    @property
    def confirmation_token(self) -> str:
        document = json.dumps(
            self.as_dict(),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(document).hexdigest()

    def as_dict(self) -> dict[str, object]:
        return {
            "candidate_form": self.candidate_form.value,
            "start_wire_ascii": self.candidate.start_wire.decode("ascii"),
            "cleanup_wire_ascii": self.candidate.cleanup_wire.decode("ascii"),
            "candidate_evidence": self.candidate.evidence,
            "target_host": str(ipaddress.ip_address(self.target_host)),
            "target_port": self.target_port,
            "output_path": str(self.output_path.resolve()),
            "scanner_model": self.scanner_model,
            "scanner_firmware": self.scanner_firmware,
            "transport_observation_scope": "physical SDS200 LAN UDP control only",
            "limits": self.limits.as_dict(),
            "scanner_owner_required_state": "stopped",
        }


class Gw2DatagramClassification(StrEnum):
    """Structural classifications that assign no FFT meaning."""

    EMPTY = "empty"
    TRANSPORT_TRUNCATED = "transport_truncated"
    UNEXPECTED_TEXT = "unexpected_text"
    OPAQUE_BINARY = "opaque_binary"


def _is_safe_provenance_text(value: object) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= 80
        and value == value.strip()
        and all(32 <= ord(character) <= 126 for character in value)
    )


@dataclass(frozen=True, slots=True)
class Gw2DatagramObservation:
    """One exact raw datagram plus renderer-neutral structural metadata."""

    sequence: int
    elapsed_seconds: float
    source_bytes: bytes
    transport_truncated: bool = False

    @property
    def classification(self) -> Gw2DatagramClassification:
        if self.transport_truncated:
            return Gw2DatagramClassification.TRANSPORT_TRUNCATED
        if not self.source_bytes:
            return Gw2DatagramClassification.EMPTY
        if _is_printable_protocol_text(self.source_bytes):
            return Gw2DatagramClassification.UNEXPECTED_TEXT
        return Gw2DatagramClassification.OPAQUE_BINARY

    def as_dict(self) -> dict[str, object]:
        text_segments = _text_segments(self.source_bytes)
        return {
            "sequence": self.sequence,
            "elapsed_seconds": self.elapsed_seconds,
            "source_length": len(self.source_bytes),
            "source_sha256": hashlib.sha256(self.source_bytes).hexdigest(),
            "source_base64": base64.b64encode(self.source_bytes).decode("ascii"),
            "transport_boundary": "udp_datagram",
            "transport_complete": not self.transport_truncated,
            "classification": self.classification.value,
            "carriage_return_count": self.source_bytes.count(b"\r"),
            "line_feed_count": self.source_bytes.count(b"\n"),
            "comma_count": self.source_bytes.count(b","),
            "nul_count": self.source_bytes.count(b"\x00"),
            "terminates_with_carriage_return": self.source_bytes.endswith(b"\r"),
            "possible_concatenated_text_records": (
                len(text_segments) > 1 if text_segments is not None else False
            ),
            "printable_text_segments": text_segments,
            "interpretation": "structural_only",
        }


def _is_printable_protocol_text(data: bytes) -> bool:
    return all(byte in {9, 10, 13} or 32 <= byte <= 126 for byte in data)


def _text_segments(data: bytes) -> list[str] | None:
    if not data or not _is_printable_protocol_text(data):
        return None
    normalized = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return [
        segment.decode("ascii")
        for segment in normalized.split(b"\n")
        if segment
    ]


class _DatagramSocket(Protocol):
    def settimeout(self, timeout: float) -> None: ...

    def connect(self, address: tuple[str, int]) -> None: ...

    def send(self, data: bytes) -> int: ...

    def recvmsg(
        self,
        buffer_size: int,
    ) -> tuple[bytes, list[tuple[int, int, bytes]], int, Any]: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class Gw2ProbeResult:
    """Complete bounded-probe evidence, including cleanup outcome."""

    plan: Gw2ProbePlan
    captured_at: datetime
    observations: tuple[Gw2DatagramObservation, ...]
    end_reason: str
    start_sent: bool
    cleanup_sent: bool
    capture_error: str | None = None
    cleanup_error: str | None = None

    @property
    def safe_completion(self) -> bool:
        return (
            self.start_sent
            and self.cleanup_sent
            and self.capture_error is None
            and self.cleanup_error is None
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "schema": GW2_RESEARCH_CAPTURE_SCHEMA,
            "version": GW2_RESEARCH_CAPTURE_VERSION,
            "captured_at": self.captured_at.isoformat(),
            "plan": self.plan.as_dict(),
            "confirmation_token": self.plan.confirmation_token,
            "start_sent": self.start_sent,
            "cleanup_sent": self.cleanup_sent,
            "safe_completion": self.safe_completion,
            "end_reason": self.end_reason,
            "capture_error": self.capture_error,
            "cleanup_error": self.cleanup_error,
            "datagram_count": len(self.observations),
            "observations": [item.as_dict() for item in self.observations],
            "semantic_claims": [],
        }


DatagramSocketFactory = Callable[[int, int], _DatagramSocket]


def _default_socket_factory(family: int, kind: int) -> _DatagramSocket:
    return socket.socket(family, kind)


def capture_gw2_udp(
    plan: Gw2ProbePlan,
    *,
    confirmation_token: str,
    scanner_owner_stopped: bool,
    socket_factory: DatagramSocketFactory = _default_socket_factory,
    monotonic: Callable[[], float],
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> Gw2ProbeResult:
    """Execute one confirmed and bounded candidate probe with cleanup."""

    if not scanner_owner_stopped:
        raise ValueError("Scanner owner must be verified stopped before a GW2 probe.")
    if not hmac.compare_digest(
        confirmation_token,
        plan.confirmation_token,
    ):
        raise ValueError("GW2 research confirmation token does not match the exact plan.")

    udp_socket: _DatagramSocket | None = None
    observations: list[Gw2DatagramObservation] = []
    start_sent = False
    cleanup_sent = False
    capture_error: str | None = None
    cleanup_error: str | None = None
    end_reason = "not_started"
    started_at = monotonic()
    last_receive_at = started_at

    try:
        udp_socket = socket_factory(socket.AF_INET, socket.SOCK_DGRAM)
        udp_socket.connect((plan.target_host, plan.target_port))
        start_sent = _send_exact(udp_socket, plan.candidate.start_wire)
        if not start_sent:
            raise OSError("incomplete_start_write")

        while True:
            current = monotonic()
            elapsed_remaining = plan.limits.max_elapsed_seconds - (
                current - started_at
            )
            inactivity_remaining = plan.limits.inactivity_seconds - (
                current - last_receive_at
            )
            if elapsed_remaining <= 0:
                end_reason = "max_elapsed"
                break
            if inactivity_remaining <= 0:
                end_reason = "inactivity"
                break
            if len(observations) >= plan.limits.max_datagrams:
                end_reason = "max_datagrams"
                break

            udp_socket.settimeout(min(elapsed_remaining, inactivity_remaining))
            try:
                data, _ancillary, flags, _address = udp_socket.recvmsg(
                    plan.limits.max_datagram_bytes
                )
            except TimeoutError:
                current = monotonic()
                if current - started_at >= plan.limits.max_elapsed_seconds:
                    end_reason = "max_elapsed"
                else:
                    end_reason = "inactivity"
                break

            observed_at = monotonic()
            last_receive_at = observed_at
            observations.append(
                Gw2DatagramObservation(
                    sequence=len(observations) + 1,
                    elapsed_seconds=max(0.0, observed_at - started_at),
                    source_bytes=data,
                    transport_truncated=bool(flags & socket.MSG_TRUNC),
                )
            )
    except (OSError, TimeoutError) as exc:
        end_reason = "capture_error"
        capture_error = type(exc).__name__
    finally:
        if udp_socket is not None and start_sent:
            try:
                cleanup_sent = _send_exact(
                    udp_socket,
                    plan.candidate.cleanup_wire,
                )
                if not cleanup_sent:
                    cleanup_error = "incomplete_cleanup_write"
            except OSError as exc:
                cleanup_error = type(exc).__name__
            try:
                udp_socket.close()
            except OSError:
                if cleanup_error is None:
                    cleanup_error = "close_error"
        elif udp_socket is not None:
            with suppress(OSError):
                udp_socket.close()

    return Gw2ProbeResult(
        plan=plan,
        captured_at=now(),
        observations=tuple(observations),
        end_reason=end_reason,
        start_sent=start_sent,
        cleanup_sent=cleanup_sent,
        capture_error=capture_error,
        cleanup_error=cleanup_error,
    )


def _send_exact(udp_socket: _DatagramSocket, data: bytes) -> bool:
    return udp_socket.send(data) == len(data)


def write_private_gw2_capture(path: Path, result: Gw2ProbeResult) -> None:
    """Create one mode-0600 JSON report without replacing existing evidence."""

    document = json.dumps(
        result.as_dict(),
        indent=2,
        sort_keys=True,
    ).encode("utf-8") + b"\n"
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(document)
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        with suppress(OSError):
            path.unlink()
        raise


def gw2_candidate_forms() -> Mapping[Gw2CandidateForm, Gw2Candidate]:
    """Expose immutable candidate evidence for review interfaces."""

    return _GW2_CANDIDATES


__all__ = [
    "GW2_RESEARCH_CAPTURE_SCHEMA",
    "GW2_RESEARCH_CAPTURE_VERSION",
    "Gw2Candidate",
    "Gw2CandidateForm",
    "Gw2DatagramClassification",
    "Gw2DatagramObservation",
    "Gw2ProbePlan",
    "Gw2ProbeResult",
    "Gw2ResearchLimits",
    "capture_gw2_udp",
    "gw2_candidate",
    "gw2_candidate_forms",
    "write_private_gw2_capture",
]
