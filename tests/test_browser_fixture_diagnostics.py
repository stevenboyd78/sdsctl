"""The fictional namespace helper reports setup failures, never a success/retry."""
from types import SimpleNamespace

import pytest

from tests import test_browser_device_launch as fixture


def test_fixture_diagnostic_returns_exact_success_without_extra_launch(monkeypatch, tmp_path):
    handoff = SimpleNamespace(_root=tmp_path)
    expected, calls = object(), []

    def launch(value, **kwargs):
        calls.append((value, kwargs))
        return expected

    monkeypatch.setattr(fixture.launch, "run_browser_recovery", launch)
    assert fixture.fixture_recovery(handoff, browser="fixture", bwrap="existing") is expected
    assert calls == [(handoff, {"browser": "fixture", "bwrap": "existing"})]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("child_diagnostic", [False, True])
def test_fixture_diagnostic_preserves_failure_context_and_evidence(
        monkeypatch, tmp_path, child_diagnostic):
    handoff = SimpleNamespace(_root=tmp_path)
    path = tmp_path / "fixture-error.txt"
    if child_diagnostic:
        path.write_text("fictional child: ready response assertion failed")
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.iterdir()}
    calls = []

    def launch(*args, **kwargs):
        calls.append(1)
        try:
            raise ValueError("fictional inner phase")
        except ValueError:
            raise fixture.launch.BrowserRecoveryLaunchError() from None

    monkeypatch.setattr(fixture.launch, "run_browser_recovery", launch)
    with pytest.raises(pytest.fail.Exception) as raised:
        fixture.fixture_recovery(handoff, browser="fixture", bwrap="existing")
    assert "Fictional recovery setup failed after" in str(raised.value)
    assert "ValueError: fictional inner phase" in str(raised.value)
    assert ("ready response assertion failed" if child_diagnostic else
            "no child diagnostic") in str(raised.value)
    assert calls == [1]
    assert {p.name: (p.read_bytes(), p.stat().st_mtime_ns)
            for p in tmp_path.iterdir()} == before
