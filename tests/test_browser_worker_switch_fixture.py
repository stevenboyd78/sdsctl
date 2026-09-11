"""Probe readback tests, not real-browser or production acceptance."""

import json
import runpy
import sys
from pathlib import Path

import pytest


@pytest.fixture
def probe(monkeypatch):
    monkeypatch.setattr(sys, "path", list(sys.path))
    path = (Path(__file__).resolve().parents[1]
            / "scripts/experimental/qualify_browser_worker_switch.py")
    return runpy.run_path(str(path), run_name="worker_switch_test")


@pytest.fixture
def proof():
    return {"role": "B", "manifestRole": "B", "run": "a" * 32,
            "pageRun": "a" * 32, "started": 1000}


def test_fixture_accepts_fresh_worker(probe, proof):
    assert probe["fresh_readback"](json.dumps(proof), role="B", started_ms=999, seen=set()) == proof


@pytest.mark.parametrize("field,value", [
    ("role", "A"), ("manifestRole", "A"), ("role", None), ("role", ["B"]),
    ("run", "a" * 31), ("run", "A" * 32), ("run", 10),
    ("pageRun", "b" * 32), ("started", True), ("started", "1000"),
    ("started", 998), ("started", 10**20),
])
def test_fixture_rejects_wrong_worker(probe, proof, field, value):
    proof[field] = value
    with pytest.raises(ValueError):
        probe["fresh_readback"](json.dumps(proof), role="B", started_ms=999, seen=set())


@pytest.mark.parametrize("body", ["[]", "null", "{}", "x", "x" * 2049, b"{}"])
def test_fixture_rejects_invalid_readback(probe, body):
    with pytest.raises(ValueError):
        probe["fresh_readback"](body, role="B", started_ms=999, seen=set())


def test_fixture_rejects_duplicate_fields(probe, proof):
    body = json.dumps(proof)[:-1] + ',"role":"B"}'
    with pytest.raises(ValueError, match="Duplicate"):
        probe["fresh_readback"](body, role="B", started_ms=999, seen=set())


def test_fixture_rejects_extra_fields(probe, proof):
    proof["extra"] = True
    with pytest.raises(ValueError):
        probe["fresh_readback"](json.dumps(proof), role="B", started_ms=999, seen=set())


def test_fixture_rejects_prior_worker_nonce(probe, proof):
    with pytest.raises(ValueError):
        probe["fresh_readback"](json.dumps(proof), role="B", started_ms=999, seen={"a" * 32})


@pytest.mark.parametrize("started", [float("nan"), float("inf"), True, "999"])
def test_fixture_rejects_invalid_launch_time(probe, proof, started):
    with pytest.raises(ValueError):
        probe["fresh_readback"](json.dumps(proof), role="B", started_ms=started, seen=set())


def test_stable_prototype_really_has_identical_worker_bytes(probe):
    worker = probe["fixture_worker"]
    assert worker("A", stable=True) == worker("B", stable=True)
    assert worker("A", stable=False) != worker("B", stable=False)
    assert "getManifest()" in worker("A", stable=True)
