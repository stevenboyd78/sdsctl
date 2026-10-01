"""Explicit, default-off TUI consumer of the existing owner's cached delivery.

The source must use finite I/O on a dedicated authenticated client. The worker
alone owns that client; the UI never closes or waits on it. All guard mutations
and projections share one Condition, with no transport operation under its lock.
Demand renewal is a separate optional source capability; never arms acquisition.
No scanner ownership or ordinary-frame fallback.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import asdict, dataclass
from threading import Condition, Thread
from time import monotonic
from types import MappingProxyType
from typing import Any, Protocol
from uuid import uuid4

from .daemon_remote_client import DaemonRemoteClientError, DaemonRemoteClientErrorReason
from .exceptions import DaemonRequestError
from .scanner_display_reader import decode_display_packet
from .scanner_display_supplemental import SupplementalDisplayValues
from .scanner_display_supplemental_client import SupplementalConsumer, SupplementalRequest
from .scanner_display_supplemental_transport import (
    decode_context_response,
    decode_demand_response,
    validate_bundle,
)
from .scanner_display_supplemental_wire import LIFETIME, SupplementalContext, bounded_seconds
from .scanner_display_web import scanner_display_browser_contract

_CLOCK_REGIONS: Any = scanner_display_browser_contract()["supplemental_clock_regions"]
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_OPERATIONS = {"display.supplemental.context", "display.supplemental.frame"}


class SupplementalReaderStopped(ValueError):
    """Terminal admission/history failure; never display remote error text."""


class _DemandUnconfirmed(SupplementalReaderStopped):
    """Stop this instance even if it was hidden while the request was in flight."""


@dataclass(frozen=True)
class SupplementalFrameSource:
    negotiate: Callable[[], object]
    read: Callable[[object], object]
    close: Callable[[], None]
    renew: Callable[[object, str], object] | None = None


class _SupplementalClient(Protocol):
    def hello(self) -> dict[str, object]: ...
    def display_supplemental_context(self) -> dict[str, object]: ...
    def display_supplemental_frame(self, context: object) -> dict[str, object]: ...
    def display_supplemental_demand(
        self, context: object, renewal_id: str
    ) -> dict[str, object]: ...
    def close(self) -> None: ...


def daemon_supplemental_source(
    client: _SupplementalClient, *, demand: bool = False
) -> SupplementalFrameSource:
    """Opt-in factory; reuse admission/capabilities on a dedicated finite client."""
    if type(demand) is not bool:
        raise TypeError("Supplemental demand opt-in must be boolean.")
    required = _OPERATIONS | ({"display.supplemental.demand"} if demand else set())

    def negotiate() -> object:
        operations = client.hello().get("operations")
        if (
            type(operations) is not list
            or not all(type(op) is str for op in operations)
            or not required.issubset(operations)
        ):
            raise SupplementalReaderStopped("Supplemental delivery is not supported.")
        return client.display_supplemental_context()

    return SupplementalFrameSource(
        negotiate,
        client.display_supplemental_frame,
        client.close,
        client.display_supplemental_demand if demand else None,
    )


def _terminal(error: Exception) -> bool:
    return (
        isinstance(error, SupplementalReaderStopped)
        or isinstance(error, DaemonRequestError)
        and error.code in {"authorization_denied", "authentication_expired"}
        or isinstance(error, DaemonRemoteClientError)
        and error.reason is not DaemonRemoteClientErrorReason.CONNECT_FAILED
    )


def _project(packet: Mapping[str, Any], values: SupplementalDisplayValues) -> Mapping[str, Any]:
    """Immutable structural copy; only configured small Day/Time slots change."""
    frames = {}
    local = values.clock.local_time
    for style, original in packet["frames"].items():
        screen = original["screen"]
        regions = []
        for region in screen["regions"]:
            if (
                region["id"] in _CLOCK_REGIONS[screen["mode"]]
                and region["selection"] == "configured"
                and region["token"] in {"Day", "Time"}
                and region["value_status"] in {"unqualified", "data_unavailable", "raw_source"}
            ):
                text = None
                if local is not None:
                    text = (
                        f"{_MONTHS[local.month - 1]}{local.day:02}"
                        if region["token"] == "Day"
                        else f"{local.hour:02}:{local.minute:02}"
                    )
                region = MappingProxyType(
                    {
                        **region,
                        "text": text,
                        "value_status": "raw_source" if text else "data_unavailable",
                    }
                )
            regions.append(region)
        frames[style] = MappingProxyType(
            {**original, "screen": MappingProxyType({**screen, "regions": tuple(regions)})}
        )
    return MappingProxyType({**packet, "frames": MappingProxyType(frames)})


class SupplementalFrameReader:
    """Lazy worker with serialized source leases and independently expiring views."""

    def __init__(self, source: SupplementalFrameSource, *, clock: Callable[[], float] = monotonic):
        self._source, self._clock = source, clock
        self._condition = Condition()
        self._active = self._closed = self._terminal = self._negotiated = False
        self._generation = 0
        self._thread: Thread | None = None
        self._guard: SupplementalConsumer | None = None
        self._retired_connections: set[tuple[str, str]] = set()
        self._retired_profiles: set[str] = set()
        self._packet: Mapping[str, Any] | None = None
        self._projected: Mapping[str, Any] | None = None
        self._projection_key: object = None
        self._sequence: int | None = None
        self._deadline = 0.0
        self._message = "Mimic-SDS inactive — values cleared"

    def _clear(self, message: str) -> None:
        self._packet = self._projected = None
        self._projection_key = None
        self._message = message

    def _suspend(self, message: str) -> None:
        self._negotiated = False
        if self._guard is not None:
            self._guard.suspend()
        self._clear(message)

    def set_active(self, active: bool) -> None:
        with self._condition:
            if self._closed or self._terminal or self._active == active:
                return
            self._active = active
            self._generation += 1
            self._suspend("Waiting for current scanner data" if active else "Mimic-SDS inactive")
            if active and self._thread is None:
                self._thread = Thread(
                    target=self._run, name="sdsctl-mimic-supplemental", daemon=True
                )
                self._thread.start()
            self._condition.notify_all()

    def _values(self) -> SupplementalDisplayValues | None:
        if self._guard is None or self._packet is None:
            return None
        now = bounded_seconds(self._clock())
        values = self._guard.snapshot(now=now)
        if now >= self._deadline:
            self._clear("Stale scanner data — values cleared")
            return None
        return values

    def view(self) -> tuple[Mapping[str, Any] | None, str]:
        with self._condition:
            self._refresh_view()
            return self._projected, self._message

    def _refresh_view(self) -> SupplementalDisplayValues | None:
        """Caller holds the condition; one time/capture for frame and details."""
        try:
            values = self._values()
            if values is not None and self._packet is not None:
                # Age changes alone need no repaint; source expiry does, even
                # during a blocked read with unchanged PSI/terminal geometry.
                key = (id(self._packet), values.clock.status, values.clock.local_time)
                if key != self._projection_key:
                    self._projected = _project(self._packet, values)
                    self._projection_key = key
            return values
        except ValueError:
            self._stop()
            return None

    def details_snapshot(self) -> str:
        return self.view_and_details()[2]

    def view_and_details(self) -> tuple[Mapping[str, Any] | None, str, str]:
        with self._condition:
            values = self._refresh_view()
            return self._projected, self._message, self._details(values)

    @staticmethod
    def _details(values: SupplementalDisplayValues | None) -> str:
        heading = (
            "Supplemental snapshot at drawer open — not live while polling is paused\n"
            "Scanner-local RTC; no timezone conversion or clock extrapolation.\n"
            "Global Favorites quick keys 00–99 (not the LCD F0 / S0 / D0 banks).\n"
        )
        if values is None:
            return heading + "No current supplemental values.\n"
        output = (
            heading
            + f"Clock: {values.clock.status.value}\n"
            + f"Favorites: {values.favorites.status.value}\n"
        )
        if values.favorites.states is not None:
            labels = {0: "Absent", 1: "Off", 2: "On"}
            output += (
                "\n".join(
                    "  ".join(
                        f"{key:02}:{labels[values.favorites.states[key]]}"
                        for key in range(start, start + 10)
                    )
                    for start in range(0, 100, 10)
                )
                + "\n"
            )
        return output

    def _stop(self) -> None:
        self._terminal, self._active = True, False
        self._generation += 1
        self._suspend("Display access or context ended — administrator review required")
        if self._guard is not None:
            self._guard.close()
        self._condition.notify_all()

    def close(self, *, wait: bool = False) -> None:
        with self._condition:
            self._closed, self._active = True, False
            self._generation += 1
            self._suspend("Mimic-SDS stopped")
            if self._guard is not None:
                self._guard.close()
            self._condition.notify_all()
        if wait and self._thread is not None:
            self._thread.join(timeout=5)

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _bind(self, context: SupplementalContext) -> None:
        """Called under the condition after generation-checked negotiation."""
        old = self._guard.context if self._guard else None
        if old == context:
            self._negotiated = True
            return
        identity = context.stream_id, context.session_id
        changed = old is not None and identity != (old.stream_id, old.session_id)
        epoch = old is not None and (
            context.context_revision > old.context_revision
            or context.profile_invalidation > old.profile_invalidation
        )
        if (
            identity in self._retired_connections
            or old is not None
            and (
                context.endpoint_id != old.endpoint_id
                or not changed
                and (
                    context.context_revision < old.context_revision
                    or context.profile_invalidation < old.profile_invalidation
                    or not epoch
                    and context.profile_revision in self._retired_profiles
                )
            )
        ):
            raise SupplementalReaderStopped("Display context was retired.")
        if old is not None:
            if changed:
                if len(self._retired_connections) >= 64:
                    raise SupplementalReaderStopped("Display history limit reached.")
                self._retired_connections.add((old.stream_id, old.session_id))
            elif not epoch:
                if len(self._retired_profiles) >= 64:
                    raise SupplementalReaderStopped("Display history limit reached.")
                self._retired_profiles.add(old.profile_revision)
        if changed or epoch:
            self._retired_profiles.clear()
        if self._guard is not None:
            self._guard.close()
        self._guard = SupplementalConsumer(context)
        self._sequence, self._deadline = None, 0.0
        self._clear("New display context verified — waiting for current scanner data")
        self._negotiated = True

    def _accept(self, ticket: SupplementalRequest, bundle: dict[str, object], now: float) -> None:
        assert self._guard is not None
        if not self._guard.accept(ticket, bundle["supplemental"], now=now):
            self._clear("Stale or invalid scanner data — values cleared")
            return
        packet = decode_display_packet(bundle["display"])
        frame = packet["frames"]["preferred"]
        deadline = ticket.started_at + LIFETIME - frame["age_seconds"]
        if frame["sequence"] == self._sequence:
            deadline = min(self._deadline, deadline)
        self._sequence, self._deadline = frame["sequence"], deadline
        self._clear("Current scanner data")
        self._packet = packet

    def _current(self, generation: int) -> bool:
        return (
            self._active
            and not self._closed
            and not self._terminal
            and generation == self._generation
        )

    def _run(self) -> None:
        try:
            while True:
                with self._condition:
                    active = self._active
                if not active:
                    with suppress(Exception):
                        self._source.close()
                with self._condition:
                    self._condition.wait_for(lambda: self._active or self._closed or self._terminal)
                    if self._closed or self._terminal:
                        return
                    generation, negotiate = self._generation, not self._negotiated
                delay = 0.25
                try:
                    started = bounded_seconds(self._clock())
                    if negotiate:
                        context = decode_context_response(self._source.negotiate())
                        with self._condition:
                            if not self._current(generation):
                                continue
                            if bounded_seconds(self._clock()) - started >= LIFETIME:
                                raise TimeoutError
                            self._bind(context)
                    with self._condition:
                        if not self._current(generation):
                            continue
                        assert self._guard is not None
                        context = self._guard.context
                    if self._source.renew is not None:
                        nonce = str(uuid4())
                        try:
                            response = self._source.renew(asdict(context), nonce)
                            renewed = decode_demand_response(response, context, nonce)
                            if bounded_seconds(self._clock()) - started >= LIFETIME:
                                raise TimeoutError
                        except Exception as error:
                            if (
                                isinstance(error, DaemonRequestError)
                                and error.code == "supplemental_context_changed"
                            ):
                                raise  # Explicit pre-mutation context rejection only.
                            raise _DemandUnconfirmed(
                                "Supplemental demand was not confirmed."
                            ) from None
                        with self._condition:
                            if not self._current(generation):
                                continue
                            self._bind(renewed)
                            context = renewed
                    with self._condition:
                        if not self._current(generation):
                            continue
                        assert self._guard is not None
                        ticket = self._guard.begin(now=self._clock())
                    # Validation and I/O do not hold the UI/guard lock.
                    bundle = validate_bundle(self._source.read(asdict(context)), context)
                    with self._condition:
                        if not self._current(generation):
                            continue
                        now = bounded_seconds(self._clock())
                        if now - started >= LIFETIME:
                            raise TimeoutError
                        self._accept(ticket, bundle, now)
                except Exception as error:
                    delay = 2.0
                    with self._condition:
                        if isinstance(error, _DemandUnconfirmed) and not self._closed:
                            self._stop()
                            self._message = "Supplemental demand unconfirmed — renewal stopped"
                        elif self._current(generation):
                            if _terminal(error):
                                self._stop()
                            else:
                                self._suspend("Display temporarily unavailable — retrying")
                    with suppress(Exception):
                        self._source.close()
                with self._condition:

                    def finished(ticket: int = generation) -> bool:
                        return not self._current(ticket)

                    self._condition.wait_for(finished, timeout=delay)
        finally:
            with suppress(Exception):
                self._source.close()
