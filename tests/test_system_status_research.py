from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from sds200.commands import StartSystemStatusAnalysis
from sds200.exceptions import ProtocolError
from sds200.models import Packet, ScannerInfo
from sds200.system_status_research import (
    SystemStatusProbeRefused,
    SystemStatusStartGuard,
    SystemStatusTarget,
    system_status_target,
)
from sds200.xml_protocol import ScannerInfoParser


def info(
    *,
    system: str = "120",
    site: str = "240",
    extra: str = "",
    mode: str = "Trunk Scan",
    screen: str = "trunk_scan",
    command: str = "PSI",
) -> ScannerInfo:
    return ScannerInfoParser().parse(
        command,
        f'''
        <ScannerInfo Mode="{mode}" V_Screen="{screen}">
          <System Index="{system}" SystemID="0001Eh" Q_Key="2" />
          <Site Index="{site}" SiteID="0016" Q_Key="3" />
          {extra}
        </ScannerInfo>
    ''',
    )


def claim(
    guard: SystemStatusStartGuard, session: object, **overrides: object
) -> StartSystemStatusAnalysis:
    params = dict(
        owner_session=session,
        observed_at=10.2,
        now=10.3,
        operator_ready=True,
        scanner_connected=True,
        waterfall_idle=True,
    )
    params.update(overrides)
    return guard.claim(info(), **params)  # type: ignore[arg-type]


def test_indices_not_broadcast_identifiers_or_quick_keys() -> None:
    assert system_status_target(info()) == SystemStatusTarget(120, 240)
    assert system_status_target(info(system="00120", site="00240")) == SystemStatusTarget(120, 240)


@pytest.mark.parametrize("command", ["PSI", "GSI"])
@pytest.mark.parametrize("mode", ["Trunk Scan", "Trunk Scan Hold"])
def test_ordinary_trunk_observations(command: str, mode: str) -> None:
    assert system_status_target(info(command=command, mode=mode)).site_index == 240


@pytest.mark.parametrize(
    "index",
    [
        "",
        "None",
        "-1",
        "+1",
        " 1",
        "1 ",
        "1.0",
        "0x10",
        "16h",
        "１２",
        "4294967295",
        "4294967296",
        "0" * 11,
    ],
)
@pytest.mark.parametrize("field", ["system", "site"])
def test_bad_indices(index: str, field: str) -> None:
    with pytest.raises(SystemStatusProbeRefused):
        system_status_target(info(**{field: index}))


def test_valid_index_boundaries() -> None:
    assert system_status_target(info(system="0", site="4294967294")) == SystemStatusTarget(
        0, 4294967294
    )


@pytest.mark.parametrize("value", [True, False, -1, 2**32 - 1, 1.0, "1", None])
def test_target_rejects_nonindices(value: object) -> None:
    with pytest.raises(SystemStatusProbeRefused):
        SystemStatusTarget(120, value)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "mode,screen",
    [
        ("Trunk Scan", "analyze_system_status"),
        ("Scan Mode", "trunk_scan"),
        ("Close Call", "trunk_scan"),
        ("Trunk Scan", "conventional_scan"),
        ("unknown", "trunk_scan"),
    ],
)
def test_mode_label_cannot_hide_analysis_or_other_screen(mode: str, screen: str) -> None:
    with pytest.raises(SystemStatusProbeRefused):
        system_status_target(info(mode=mode, screen=screen))


@pytest.mark.parametrize(
    "extra",
    [
        '<System Index="999"/>',
        '<Site Index="999"/>',
        '<PopupScreen Text="private"/>',
        '<PlainText Text="private"/>',
        "<ReplayDescription/>",
        "<ReplayMode/>",
        "<Button/>",
        "<ConvFrequency/>",
        "<SrchFrequency/>",
        "<CcHitsChannel/>",
        "<WxChannel/>",
        "<ToneOutChannel/>",
        '<SystemStatus SiteID="0016"/>',
        "<Analyze/>",
        "<RfPowerPlot/>",
        "<Unknown/><Unknown/>",
    ],
)
def test_duplicate_foreign_and_overlay_records_refused(extra: str) -> None:
    with pytest.raises(SystemStatusProbeRefused) as error:
        system_status_target(info(extra=extra))
    assert "private" not in str(error.value)


def test_missing_index_does_not_fall_back_to_site_id() -> None:
    frame = ScannerInfoParser().parse(
        "PSI",
        """<ScannerInfo Mode="Trunk Scan" V_Screen="trunk_scan">
      <System Index="120"/><Site SiteID="0016" Q_Key="3"/>
    </ScannerInfo>""",
    )
    with pytest.raises(SystemStatusProbeRefused):
        system_status_target(frame)


@pytest.mark.parametrize("records", ["", '<System Index="120"/>', '<Site Index="240"/>'])
def test_missing_selection_records_are_refused(records: str) -> None:
    frame = ScannerInfoParser().parse(
        "PSI",
        f'<ScannerInfo Mode="Trunk Scan" V_Screen="trunk_scan">{records}</ScannerInfo>',
    )
    with pytest.raises(SystemStatusProbeRefused, match="ambiguous or obscured"):
        system_status_target(frame)


def test_unqualified_command_is_refused() -> None:
    with pytest.raises(SystemStatusProbeRefused, match="normal trunk screen"):
        system_status_target(replace(info(), command="AST"))


@pytest.mark.parametrize(
    "observed,now",
    [
        (8, 10.1),
        (11, 10),
        (-1, 0),
        (True, 1),
        (0, False),
        (float("nan"), 10),
        (10, float("inf")),
        (10**1000, 10),
    ],
)
def test_preparation_requires_finite_monotonic_freshness(observed: float, now: float) -> None:
    with pytest.raises(SystemStatusProbeRefused):
        SystemStatusStartGuard(info(), owner_session=object(), observed_at=observed, now=now)


def test_claim_is_one_shot_even_without_sending() -> None:
    session = object()
    guard = SystemStatusStartGuard(info(), owner_session=session, observed_at=9.9, now=10)
    command = claim(guard, session)
    assert command.wire == "AST,SYSTEM_STATUS,240"
    with pytest.raises(SystemStatusProbeRefused, match="already reserved"):
        claim(guard, session)


def test_target_cannot_be_replaced_after_preparation() -> None:
    guard = SystemStatusStartGuard(info(), owner_session=object(), observed_at=10, now=10)
    with pytest.raises(AttributeError):
        guard.target = SystemStatusTarget(120, 16)  # type: ignore[misc]


def test_missing_owner_session_is_refused() -> None:
    with pytest.raises(SystemStatusProbeRefused, match="session is unavailable"):
        SystemStatusStartGuard(info(), owner_session=None, observed_at=10, now=10)


def test_nonexact_acknowledgement_does_not_rearm_guard() -> None:
    session = object()
    guard = SystemStatusStartGuard(info(), owner_session=session, observed_at=10, now=10)
    command = claim(guard, session)
    with pytest.raises(ProtocolError):
        command.parse_response(Packet(command="AST", fields=("NG",), raw="AST,NG"))
    with pytest.raises(SystemStatusProbeRefused, match="already reserved"):
        claim(guard, session)


def test_exact_acknowledgement_does_not_supply_analysis_data_or_rearm() -> None:
    session = object()
    guard = SystemStatusStartGuard(info(), owner_session=session, observed_at=10, now=10)
    command = claim(guard, session)
    assert command.parse_response(Packet(command="AST", fields=("OK",), raw="AST,OK")) is None
    with pytest.raises(SystemStatusProbeRefused, match="already reserved"):
        claim(guard, session)


@pytest.mark.parametrize("key", ["operator_ready", "scanner_connected", "waterfall_idle"])
@pytest.mark.parametrize("value", [False, None, 1, "true"])
def test_readiness_requires_explicit_true(key: str, value: object) -> None:
    session = object()
    guard = SystemStatusStartGuard(info(), owner_session=session, observed_at=10, now=10)
    with pytest.raises(SystemStatusProbeRefused, match="not ready"):
        claim(guard, session, **{key: value})


def test_owner_session_compared_by_identity_not_equality() -> None:
    session = [1]
    guard = SystemStatusStartGuard(info(), owner_session=session, observed_at=10, now=10)
    with pytest.raises(SystemStatusProbeRefused, match="session has changed"):
        claim(guard, [1])


@pytest.mark.parametrize("system,site", [("121", "240"), ("120", "241")])
def test_selection_must_be_rechecked(system: str, site: str) -> None:
    session = object()
    guard = SystemStatusStartGuard(info(), owner_session=session, observed_at=10, now=10)
    with pytest.raises(SystemStatusProbeRefused, match="selection changed"):
        guard.claim(
            info(system=system, site=site),
            owner_session=session,
            observed_at=10.2,
            now=10.3,
            operator_ready=True,
            scanner_connected=True,
            waterfall_idle=True,
        )


@pytest.mark.parametrize("observed,now", [(9.9, 10.3), (12.5, 12.6), (10.2, 12.3), (11, 10.3)])
def test_old_future_or_expired_claim(observed: float, now: float) -> None:
    session = object()
    guard = SystemStatusStartGuard(info(), owner_session=session, observed_at=10, now=10)
    with pytest.raises(SystemStatusProbeRefused):
        claim(guard, session, observed_at=observed, now=now)


def test_concurrent_claims_only_issue_one_command() -> None:
    session = object()
    guard = SystemStatusStartGuard(info(), owner_session=session, observed_at=10, now=10)

    def attempt(_: int) -> str:
        try:
            return claim(guard, session).wire
        except SystemStatusProbeRefused:
            return "refused"

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(16)))
    assert results.count("AST,SYSTEM_STATUS,240") == 1
    assert results.count("refused") == 15
