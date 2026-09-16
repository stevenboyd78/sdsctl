"""Spec-derived GET fixtures only; not physical quick-key/LCD acceptance."""

import pytest

from sds200 import (
    DepartmentQuickKeys,
    GetDepartmentQuickKeys,
    GetSystemQuickKeys,
    SystemQuickKeys,
)
from sds200.exceptions import ProtocolError
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
