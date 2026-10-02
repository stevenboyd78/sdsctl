from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from sds200.daemon_api import (
    DAEMON_API_PROTOCOL,
    DAEMON_API_VERSION,
    DaemonApiErrorCode,
    DaemonApiOperation,
    DaemonReadOnlyApi,
)
from sds200.daemon_front_panel_control import QualifiedMenuControlPolicy
from sds200.daemon_runtime import DaemonControlOperation, DaemonRuntimeState
from sds200.exceptions import (
    DaemonControlUnavailableError,
    UnsupportedScannerFeatureError,
)
from sds200.front_panel_keys import FrontPanelKey

from .test_daemon_front_panel_research import Scanner, frame, runtime_for


class MenuScanner(Scanner):
    @contextmanager
    def _front_panel_control_scope(self, *, timeout: float) -> Iterator[None]:
        self._stage("control-scope", timeout)
        yield


@pytest.fixture
def scanner() -> Iterator[MenuScanner]:
    value = MenuScanner()
    try:
        yield value
    finally:
        value.close_research()


def request(operation: DaemonApiOperation, params: object = None) -> dict[str, object]:
    payload: dict[str, object] = {
        "protocol": DAEMON_API_PROTOCOL,
        "version": DAEMON_API_VERSION,
        "request_id": "qualified-menu",
        "operation": operation.value,
    }
    if params is not None:
        payload["params"] = params
    return payload


def test_policy_cannot_expand_beyond_physical_evidence() -> None:
    assert QualifiedMenuControlPolicy().key is FrontPanelKey.MENU
    with pytest.raises(ValueError, match="physically qualified"):
        QualifiedMenuControlPolicy(key=FrontPanelKey.ENTER_YES)
    with pytest.raises(ValueError, match="physically qualified"):
        QualifiedMenuControlPolicy(firmware="Version 1.27.00")


def test_runtime_is_disabled_by_default_and_exact_opt_in_is_advertised(
    scanner: MenuScanner,
) -> None:
    disabled = runtime_for(scanner)
    assert disabled.front_panel_control_available is False
    with pytest.raises(UnsupportedScannerFeatureError, match="not enabled"):
        disabled.press_front_panel(FrontPanelKey.MENU)

    enabled = runtime_for(scanner, front_panel_control=QualifiedMenuControlPolicy())
    assert enabled.front_panel_control_available is True
    enabled._state = DaemonRuntimeState.RUNNING
    result = enabled.press_front_panel(FrontPanelKey.MENU, timeout=0.2)

    assert result.operation is DaemonControlOperation.FRONT_PANEL_PRESS
    assert scanner.commands == ["KEY,M,P"]
    assert scanner.stages == [
        "control-scope",
        "MDL",
        "VER",
        "GSI",
        "GSI",
        "KEY",
    ]
    assert scanner.timeouts == sorted(scanner.timeouts, reverse=True)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("model", "SDS100"),
        ("firmware", "Version 1.27.00"),
    ],
)
def test_identity_mismatch_refuses_without_key(
    scanner: MenuScanner,
    field: str,
    value: str,
) -> None:
    setattr(scanner, field, value)
    runtime = runtime_for(scanner, front_panel_control=QualifiedMenuControlPolicy())
    runtime._state = DaemonRuntimeState.RUNNING

    with pytest.raises(DaemonControlUnavailableError):
        runtime.press_front_panel(FrontPanelKey.MENU, timeout=0.2)
    assert scanner.commands == []


def test_context_mismatch_refuses_without_key(scanner: MenuScanner) -> None:
    scanner.frames[1] = frame(mode="Scan Hold")
    runtime = runtime_for(scanner, front_panel_control=QualifiedMenuControlPolicy())
    runtime._state = DaemonRuntimeState.RUNNING

    with pytest.raises(DaemonControlUnavailableError):
        runtime.press_front_panel(FrontPanelKey.MENU, timeout=0.2)
    assert scanner.commands == []


@pytest.mark.parametrize("key", [FrontPanelKey.ENTER_YES, "M", None])
def test_runtime_refuses_every_unqualified_or_untyped_key(
    scanner: MenuScanner,
    key: object,
) -> None:
    runtime = runtime_for(scanner, front_panel_control=QualifiedMenuControlPolicy())
    runtime._state = DaemonRuntimeState.RUNNING
    with pytest.raises(ValueError, match="Only the qualified Menu"):
        runtime.press_front_panel(key)
    assert scanner.stages == [] and scanner.commands == []


def test_api_advertises_and_dispatches_only_explicit_qualified_menu(
    scanner: MenuScanner,
) -> None:
    disabled_api = DaemonReadOnlyApi(runtime_for(scanner))
    disabled_hello = disabled_api.handle_payload(request(DaemonApiOperation.HELLO))
    assert disabled_hello.result is not None
    assert (
        DaemonApiOperation.SCANNER_FRONT_PANEL_PRESS.value
        not in disabled_hello.result["control_operations"]
    )

    runtime = runtime_for(scanner, front_panel_control=QualifiedMenuControlPolicy())
    runtime._state = DaemonRuntimeState.RUNNING
    api = DaemonReadOnlyApi(runtime)
    hello = api.handle_payload(request(DaemonApiOperation.HELLO))
    assert hello.result is not None
    assert (
        DaemonApiOperation.SCANNER_FRONT_PANEL_PRESS.value
        in hello.result["control_operations"]
    )

    response = api.handle_control_payload(
        request(
            DaemonApiOperation.SCANNER_FRONT_PANEL_PRESS,
            {"key": "M", "timeout": 0.2},
        )
    )
    assert response.error is None
    assert response.result is not None
    assert response.result["operation"] == "scanner.front_panel.press"
    assert scanner.commands == ["KEY,M,P"]


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"key": "E"},
        {"key": "M", "unexpected": True},
        {"key": "M", "timeout": 2.1},
    ],
)
def test_api_rejects_invalid_or_unqualified_requests_before_io(
    scanner: MenuScanner,
    params: dict[str, object],
) -> None:
    runtime = runtime_for(scanner, front_panel_control=QualifiedMenuControlPolicy())
    runtime._state = DaemonRuntimeState.RUNNING
    response = DaemonReadOnlyApi(runtime).handle_control_payload(
        request(DaemonApiOperation.SCANNER_FRONT_PANEL_PRESS, params)
    )
    assert response.error is not None
    assert response.error.code is DaemonApiErrorCode.INVALID_PARAMETERS
    assert scanner.stages == [] and scanner.commands == []
