from __future__ import annotations

import logging
import math
import threading
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from .exceptions import ProtocolError
from .models import (
    AnalysisRecord,
    AnalysisResponse,
    GltRecord,
    GltResponse,
    MsiRecord,
    MsiResponse,
    ScannerInfo,
    ScannerNode,
)

XML_COMMAND_ROOTS: Mapping[str, str] = MappingProxyType(
    {
        "GSI": "ScannerInfo",
        "PSI": "ScannerInfo",
        "GLT": "GLT",
        "AST": "AST",
        "MSI": "MSI",
    }
)
XML_RESPONSE_DEFAULT_MAX_LINES = 10_000
XML_RESPONSE_DEFAULT_MAX_BYTES = 4 * 1024 * 1024
XML_RESPONSE_DEFAULT_MAX_ELEMENTS = 10_000
XML_RESPONSE_DEFAULT_MAX_DEPTH = 64
XML_RESPONSE_DEFAULT_MAX_LIFETIME = 10.0

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class XmlResponseAssemblyFeed:
    """Atomic XML assembly outcome used by the radio receive path."""

    response: tuple[str, str] | None = None
    consumed: bool = False
    expired: bool = False
    report_expiration: bool = False


class _XmlStructureTarget:
    """Validate XML structure synchronously without retaining a parsed tree."""

    def __init__(
        self,
        expected_root: str,
        *,
        max_elements: int,
        max_depth: int,
    ) -> None:
        self.expected_root = expected_root
        self.max_elements = max_elements
        self.max_depth = max_depth
        self.element_count = 0
        self.depth = 0
        self.seen_root = False
        self.complete = False

    def start(self, tag: str, _attributes: Mapping[str, str]) -> None:
        if not self.seen_root:
            if tag != self.expected_root:
                raise ProtocolError(
                    "XML response has an unexpected root element."
                )
            self.seen_root = True
        self.element_count += 1
        self.depth += 1
        if self.element_count > self.max_elements or self.depth > self.max_depth:
            raise ProtocolError(
                "XML response assembly exceeded its configured limit."
            )

    def end(self, _tag: str) -> None:
        self.depth -= 1
        if self.seen_root and self.depth == 0:
            self.complete = True

    def data(self, _data: str) -> None:
        return

    def close(self) -> None:
        return


class XmlResponseAssembler:
    """Collect supported CR-delimited XML responses into bounded documents."""

    def __init__(
        self,
        command_roots: Mapping[str, str] | None = None,
        *,
        max_lines: int = XML_RESPONSE_DEFAULT_MAX_LINES,
        max_bytes: int = XML_RESPONSE_DEFAULT_MAX_BYTES,
        max_elements: int = XML_RESPONSE_DEFAULT_MAX_ELEMENTS,
        max_depth: int = XML_RESPONSE_DEFAULT_MAX_DEPTH,
        max_lifetime: float = XML_RESPONSE_DEFAULT_MAX_LIFETIME,
        monotonic: Callable[[], float] = time.monotonic,
        expiration_handler: Callable[[ProtocolError], None] | None = None,
    ) -> None:
        for name, value in (
            ("lines", max_lines),
            ("bytes", max_bytes),
            ("elements", max_elements),
            ("depth", max_depth),
        ):
            if type(value) is not int:
                raise TypeError(f"Maximum XML response {name} must be an integer.")
            if value <= 0:
                raise ValueError(f"Maximum XML response {name} must be positive.")
        if isinstance(max_lifetime, bool) or not isinstance(
            max_lifetime,
            (int, float),
        ):
            raise TypeError("Maximum XML response lifetime must be numeric.")
        try:
            normalized_lifetime = float(max_lifetime)
        except OverflowError as exc:
            raise ValueError(
                "Maximum XML response lifetime must be finite and positive."
            ) from exc
        if not math.isfinite(normalized_lifetime) or normalized_lifetime <= 0:
            raise ValueError(
                "Maximum XML response lifetime must be finite and positive."
            )
        configured = XML_COMMAND_ROOTS if command_roots is None else command_roots
        self._command_roots = MappingProxyType(
            {command.upper(): root for command, root in configured.items()}
        )
        self._command: str | None = None
        self._root: str | None = None
        self._lines: list[str] = []
        self._max_lines = max_lines
        self._max_bytes = max_bytes
        self._max_elements = max_elements
        self._max_depth = max_depth
        self._max_lifetime = normalized_lifetime
        self._monotonic = monotonic
        self._expiration_handler = expiration_handler
        self._lock = threading.RLock()
        self._started_at: float | None = None
        self._deadline: float | None = None
        self._byte_count = 0
        self._parser: ET.XMLParser | None = None
        self._structure: _XmlStructureTarget | None = None
        self._expiry_timer: threading.Timer | None = None
        self._expired_pending = False
        self._pending_expiration_reported = False

    def _header_command(self, line: str) -> str | None:
        upper = line.upper()
        for command in self._command_roots:
            if upper.startswith(f"{command},<XML>"):
                return command
        return None

    def recognizes_header(self, line: str) -> bool:
        return self._header_command(line) is not None

    def feed(self, line: str) -> tuple[str, str] | None:
        with self._lock:
            header_command = self._header_command(line)
            if header_command is not None:
                self._begin_locked(header_command)
                return None
            if self._consume_pending_expiration_locked():
                raise ProtocolError(
                    "XML response assembly exceeded its lifetime limit."
                )
            if self._deadline_reached_locked():
                self._clear_document_locked()
                raise ProtocolError(
                    "XML response assembly exceeded its lifetime limit."
                )
            return self._feed_document_line_locked(line, recover_expiry=False).response

    def feed_with_status(self, line: str) -> XmlResponseAssemblyFeed:
        """Feed one line while preserving expiry recovery for radio dispatch."""

        with self._lock:
            expired = self._expired_pending
            report_expiration = expired and not self._pending_expiration_reported
            self._expired_pending = False
            self._pending_expiration_reported = False

            if self._deadline_reached_locked():
                self._clear_document_locked()
                expired = True
                report_expiration = True

            header_command = self._header_command(line)
            if header_command is not None:
                self._begin_locked(header_command)
                return XmlResponseAssemblyFeed(
                    consumed=True,
                    expired=expired,
                    report_expiration=report_expiration,
                )

            if self._command is None:
                return XmlResponseAssemblyFeed(
                    consumed=(
                        expired and self._looks_like_xml_continuation(line)
                    ),
                    expired=expired,
                    report_expiration=report_expiration,
                )

            result = self._feed_document_line_locked(line, recover_expiry=True)
            if not expired:
                return result
            return XmlResponseAssemblyFeed(
                response=result.response,
                consumed=result.consumed,
                expired=True,
                report_expiration=(
                    report_expiration or result.report_expiration
                ),
            )

    def _begin_locked(self, command: str) -> None:
        # A new XML header is also a resynchronization point if an earlier
        # document was truncated by a disconnect or dropped packet.
        self._clear_document_locked()
        self._expired_pending = False
        self._pending_expiration_reported = False
        self._command = command
        self._root = self._command_roots[command]
        self._started_at = self._monotonic()
        self._deadline = self._started_at + self._max_lifetime
        self._structure = _XmlStructureTarget(
            self._root,
            max_elements=self._max_elements,
            max_depth=self._max_depth,
        )
        self._parser = ET.XMLParser(target=self._structure)
        if self._expiry_timer is None:
            self._start_expiry_timer_locked(self._max_lifetime)

    def _feed_document_line_locked(
        self,
        line: str,
        *,
        recover_expiry: bool,
    ) -> XmlResponseAssemblyFeed:
        if self._command is None:
            return XmlResponseAssemblyFeed()

        encoded_length = len(line.encode("utf-8"))
        byte_count = self._byte_count + encoded_length + int(bool(self._lines))
        if len(self._lines) + 1 > self._max_lines or byte_count > self._max_bytes:
            self._clear_document_locked()
            raise ProtocolError("XML response assembly exceeded its configured limit.")

        self._lines.append(line)
        self._byte_count = byte_count
        parser = self._parser
        structure = self._structure
        assert parser is not None
        assert structure is not None
        complete = False
        try:
            if self._deadline_reached_locked():
                self._clear_document_locked()
                if recover_expiry:
                    return XmlResponseAssemblyFeed(
                        consumed=True,
                        expired=True,
                        report_expiration=True,
                    )
                raise ProtocolError(
                    "XML response assembly exceeded its lifetime limit."
                )
            parser.feed(f"{line}\n")
            complete = structure.complete
            if self._deadline_reached_locked():
                self._clear_document_locked()
                if recover_expiry:
                    return XmlResponseAssemblyFeed(
                        consumed=True,
                        expired=True,
                        report_expiration=True,
                    )
                raise ProtocolError(
                    "XML response assembly exceeded its lifetime limit."
                )
        except ET.ParseError as exc:
            command = self._command
            self._clear_document_locked()
            assert command is not None
            raise ProtocolError(f"Invalid {command} XML response") from exc
        except ProtocolError:
            self._clear_document_locked()
            raise

        if not complete:
            return XmlResponseAssemblyFeed(consumed=True)

        try:
            parser.close()
        except ET.ParseError as exc:
            command = self._command
            self._clear_document_locked()
            assert command is not None
            raise ProtocolError(f"Invalid {command} XML response") from exc

        command = self._command
        xml = "\n".join(self._lines)
        self._clear_document_locked()
        assert command is not None
        return XmlResponseAssemblyFeed(
            response=(command, xml),
            consumed=True,
        )

    def _deadline_reached_locked(self) -> bool:
        return (
            self._command is not None
            and self._deadline is not None
            and self._monotonic() >= self._deadline
        )

    def _start_expiry_timer_locked(self, delay: float) -> None:
        timer: threading.Timer

        def expire() -> None:
            self._expiry_timer_fired(timer)

        timer = threading.Timer(min(delay, threading.TIMEOUT_MAX), expire)
        timer.daemon = True
        self._expiry_timer = timer
        timer.start()

    def _expiry_timer_fired(self, timer: threading.Timer) -> None:
        handler: Callable[[ProtocolError], None] | None = None
        error: ProtocolError | None = None
        with self._lock:
            if self._expiry_timer is not timer:
                return
            self._expiry_timer = None
            if self._command is None or self._deadline is None:
                return
            remaining = self._deadline - self._monotonic()
            if remaining > 0:
                self._start_expiry_timer_locked(remaining)
                return
            self._clear_document_locked()
            self._expired_pending = True
            handler = self._expiration_handler
            self._pending_expiration_reported = handler is not None
            error = ProtocolError(
                "XML response assembly exceeded its lifetime limit."
            )

        if handler is not None:
            assert error is not None
            try:
                handler(error)
            except Exception:
                logger.exception("Unhandled exception in XML expiration callback")

    def _consume_pending_expiration_locked(self) -> bool:
        expired = self._expired_pending
        self._expired_pending = False
        self._pending_expiration_reported = False
        return expired

    @staticmethod
    def _looks_like_xml_continuation(line: str) -> bool:
        return line.lstrip().startswith("<")

    def reset(self) -> None:
        with self._lock:
            timer, self._expiry_timer = self._expiry_timer, None
            if timer is not None:
                timer.cancel()
            self._clear_document_locked()
            self._expired_pending = False
            self._pending_expiration_reported = False

    def _clear_document_locked(self) -> None:
        self._command = None
        self._root = None
        self._lines.clear()
        self._started_at = None
        self._deadline = None
        self._byte_count = 0
        self._parser = None
        self._structure = None

    @property
    def collecting(self) -> bool:
        with self._lock:
            return self._command is not None


class ScannerInfoParser:
    def parse(self, command: str, xml: str) -> ScannerInfo:
        try:
            root = ET.fromstring(xml)
        except ET.ParseError as exc:
            raise ProtocolError(f"Invalid {command} XML response: {exc}") from exc

        if root.tag != "ScannerInfo":
            raise ProtocolError(f"Expected ScannerInfo root, received {root.tag!r}")

        records = tuple(
            ScannerNode.create(element.tag, element.attrib)
            for element in root.iter()
            if element is not root
        )
        nodes = {record.tag: record for record in records}

        return ScannerInfo(
            command=command,
            mode=root.attrib.get("Mode"),
            screen=root.attrib.get("V_Screen"),
            nodes=MappingProxyType(nodes),
            raw_xml=xml,
            records=records,
        )


class GltParser:
    def parse(self, command: str, xml: str) -> GltResponse:
        try:
            root = ET.fromstring(xml)
        except ET.ParseError as exc:
            raise ProtocolError("Invalid GLT XML response") from exc

        if root.tag != "GLT":
            raise ProtocolError(f"Expected GLT root, received {root.tag!r}")

        return GltResponse.create(
            command=command,
            root_attributes=root.attrib,
            records=tuple(
                GltRecord.create(element.tag, element.attrib) for element in root
            ),
            raw_xml=xml,
        )


class MsiParser:
    """Parse bounded MSI XML without assigning menu-field semantics."""

    def parse(self, command: str, xml: str) -> MsiResponse:
        try:
            root = ET.fromstring(xml)
        except ET.ParseError as exc:
            raise ProtocolError("Invalid MSI XML response") from exc

        if root.tag != "MSI":
            raise ProtocolError(f"Expected MSI root, received {root.tag!r}")

        return MsiResponse.create(
            command=command,
            root_attributes=root.attrib,
            records=tuple(
                MsiRecord.create(element.tag, element.attrib)
                for element in root.iter()
                if element is not root
            ),
            raw_xml=xml,
        )


class AnalysisParser:
    def parse(self, command: str, xml: str) -> AnalysisResponse:
        try:
            root = ET.fromstring(xml)
        except ET.ParseError as exc:
            raise ProtocolError("Invalid AST XML response") from exc

        if root.tag != "AST":
            raise ProtocolError(f"Expected AST root, received {root.tag!r}")

        return AnalysisResponse.create(
            command=command,
            root_attributes=root.attrib,
            records=tuple(
                AnalysisRecord.create(element.tag, element.attrib)
                for element in root.iter()
                if element is not root
            ),
            raw_xml=xml,
        )
