"""Spec-derived GET fixtures only; not physical quick-key/LCD acceptance."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from sds200 import (
    DepartmentQuickKeys,
    GetDepartmentQuickKeys,
    GetSystemQuickKeys,
    SystemQuickKeys,
)
from sds200.commands import GetFavoritesQuickKeys, SetFavoritesQuickKeys
from sds200.exceptions import CommandTimeoutError, ProtocolError
from sds200.models import FavoritesQuickKeyState, Packet
from sds200.radio import SDS200

from .fakes import FakeTransport

STATES = tuple(str(i % 3) for i in range(100))


def packet(command, fields):
    return Packet(command=command, fields=fields, raw=command + "," + ",".join(fields))


@pytest.mark.parametrize("favorites,system", [(0, 0), (1, 23), (99, 99)])
def test_scoped_get_commands_preserve_documented_selectors_and_all_states(favorites, system):
    for command, model in (
        (GetSystemQuickKeys(favorites), SystemQuickKeys),
        (GetDepartmentQuickKeys(favorites, system), DepartmentQuickKeys),
    ):
        name = command.response_command
        response = packet(name, (str(favorites), str(system), *STATES))
        result = command.parse_response(response)
        assert isinstance(result, model)
        assert result.favorites_quick_key == favorites
        assert result.packet is response
        assert len(result.states) == 100
        assert result.states[:3] == tuple(FavoritesQuickKeyState)
        assert result.states[-1] is FavoritesQuickKeyState.NONEXISTENT
        if name == "SQK":
            assert command.wire == f"SQK,{favorites}"
            assert result.reported_system_quick_key == system
        else:
            assert command.wire == f"DQK,{favorites},{system}"
            assert result.system_quick_key == system


@pytest.mark.parametrize("bad", [True, False, -1, 100, 10**100, 1.0, "1", None])
def test_get_selectors_cannot_be_unassigned_indices_or_arbitrary_wire_text(bad):
    for factory in (
        lambda: GetSystemQuickKeys(bad),
        lambda: GetDepartmentQuickKeys(bad, 0),
        lambda: GetDepartmentQuickKeys(0, bad),
    ):
        with pytest.raises(ValueError, match="quick key"):
            factory()


@pytest.mark.parametrize("command", [GetSystemQuickKeys(1), GetDepartmentQuickKeys(1, 23)])
@pytest.mark.parametrize(
    "fields",
    [
        (),
        ("OK",),
        ("ERR",),
        ("1", *STATES),  # Do not guess away the documented extra SQK selector.
        ("1", "23", *STATES[:-1]),
        ("1", "23", *STATES, "0"),
        ("1", "23", *STATES[:-1], "3"),
        ("1", "23", *STATES[:-1], " 2"),
        ("1", "23", *STATES[:-1], "２"),
        ("1", "23", *STATES[:-1], "PRIVATE_RESPONSE"),
        ("None", "23", *STATES),
        ("1", "None", *STATES),
        ("100", "23", *STATES),
        ("1", "100", *STATES),
        (" 1", "23", *STATES),
        ("１", "23", *STATES),
        ("1", "-1", *STATES),
        ("2", "23", *STATES),  # Wrong Favorites scope.
    ],
)
def test_scoped_reads_refuse_malformed_or_foreign_scope_without_echo(command, fields):
    with pytest.raises(ProtocolError) as exc:
        command.parse_response(packet(command.response_command, fields))
    assert "PRIVATE_RESPONSE" not in str(exc.value)


def test_department_scope_must_match_both_requested_keys():
    with pytest.raises(ProtocolError, match="different quick-key selectors"):
        GetDepartmentQuickKeys(1, 23).parse_response(packet("DQK", ("1", "24", *STATES)))


@pytest.mark.parametrize("command", [GetSystemQuickKeys(1), GetDepartmentQuickKeys(1, 23)])
@pytest.mark.parametrize("response", [object(), packet("FQK", STATES)])
def test_get_rejects_other_responses(command, response):
    with pytest.raises(ProtocolError, match="unexpected response"):
        command.parse_response(response)


@pytest.mark.parametrize("kind", ["system", "department"])
def test_get_uses_existing_radio_transport_and_leaves_state_unchanged(kind):
    name, expected = ("SQK", "SQK,1") if kind == "system" else ("DQK", "DQK,1,23")

    class RespondingTransport(FakeTransport):
        def write_command(self, command):
            super().write_command(command)
            assert command == expected
            self.feed_line(name + ",1,23," + ",".join(STATES))

    transport = RespondingTransport()
    radio = SDS200.from_transport(transport)
    with radio:
        initial = radio.state.snapshot
        if kind == "system":
            result = radio.get_system_quick_keys(1, timeout=1.0)
        else:
            result = radio.get_department_quick_keys(1, 23, timeout=1.0)
        assert radio.state.snapshot == initial
    assert transport.writes == [expected]  # GET form only, no status list/set.
    assert result.states == tuple(FavoritesQuickKeyState(int(s)) for s in STATES)


@pytest.mark.parametrize(
    "command", [GetFavoritesQuickKeys(), GetSystemQuickKeys(1), GetDepartmentQuickKeys(1, 23)]
)
def test_idle_read_dispatches_only_the_whitelisted_get_on_existing_transport(command):
    name = command.response_command
    fields = STATES if name == "FQK" else ("1", "23", *STATES)

    class ReplyingTransport(FakeTransport):
        def write_command(self, wire):
            super().write_command(wire)
            self.feed_line(name + "," + ",".join(fields))

    transport = ReplyingTransport()
    radio = SDS200.from_transport(transport)
    assert radio.read_quick_keys_if_idle(command) is None
    assert transport.writes == []  # Does not connect as a side effect.
    with radio:
        before = radio.state.snapshot
        result = radio.read_quick_keys_if_idle(command)
        assert result.states == tuple(FavoritesQuickKeyState(int(s)) for s in STATES)
        assert radio.state.snapshot == before
    assert transport.writes == [command.wire]


def test_background_read_yields_to_existing_control_without_queuing():
    entered, release = Event(), Event()

    class BusyTransport(FakeTransport):
        def write_command(self, command):
            super().write_command(command)
            assert command == "VOL"
            entered.set()
            assert release.wait(2)
            self.feed_line("VOL,3")

    transport = BusyTransport()
    radio = SDS200.from_transport(transport)
    with radio, ThreadPoolExecutor(max_workers=2) as pool:
        control = pool.submit(radio.get_volume, timeout=1.0)
        try:
            assert entered.wait(1)
            # Completes while the foreground read is still blocked.
            idle = pool.submit(radio.read_quick_keys_if_idle, GetFavoritesQuickKeys())
            assert idle.result(timeout=0.25) is None
            assert transport.writes == ["VOL"]
        finally:
            release.set()
        assert control.result(timeout=1) == 3


def test_background_read_times_out_and_releases_command_lane():
    transport = FakeTransport()
    radio = SDS200.from_transport(transport)
    with radio:
        with pytest.raises(CommandTimeoutError):
            radio.read_quick_keys_if_idle(GetFavoritesQuickKeys(), timeout=0.01)
        # Another thread can take the lane after timeout; this does not retry GET.
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(radio.send, "VER").result(timeout=0.5)
    assert transport.writes == ["FQK", "VER"]


@pytest.mark.parametrize("timeout", [0, -1, 0.51, 2, True, float("nan"), float("inf")])
def test_background_read_budget_cannot_be_expanded_or_invalid(timeout):
    transport = FakeTransport()
    radio = SDS200.from_transport(transport)
    with pytest.raises(TypeError if isinstance(timeout, bool) else ValueError):
        radio.read_quick_keys_if_idle(GetFavoritesQuickKeys(), timeout=timeout)
    assert transport.writes == []


@pytest.mark.parametrize("command", [SetFavoritesQuickKeys([2] * 100), "FQK", object()])
def test_background_read_rejects_sets_and_arbitrary_commands(command):
    transport = FakeTransport()
    radio = SDS200.from_transport(transport)
    with pytest.raises(ValueError):
        radio.read_quick_keys_if_idle(command)
    assert transport.writes == []
