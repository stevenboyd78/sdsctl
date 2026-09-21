"""Actual opt-in browser coordinator, synthetic HTTP/DOM only; no live hardware."""

import json
import shutil
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sds200.web_dashboard import create_web_dashboard_app

from .test_scanner_display_supplemental_web import bundle as bundle
from .test_scanner_display_supplemental_web import capture as capture
from .test_scanner_display_supplemental_web import configured as configured
from .test_scanner_display_supplemental_web import engine as engine

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def scripts():
    with TestClient(create_web_dashboard_app(lambda: None)) as client:
        script = client.get("/assets/mimic-sds.js").text
        assert client.get("/assets/mimic-supplemental.js").status_code == 404
        assert "supplementalRoot" not in client.get("/assets/dashboard.js").text
    return {
        "script": script,
        "auxiliary": (ROOT / "src/sds200/web_assets/mimic-supplemental.js").read_text(),
    }


def run(scripts, bundle, scenario, **options):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node required for synthetic browser coordinator regression")
    initial = deepcopy(bundle)
    initial["supplemental"]["psi"]["age_seconds"] = 0
    for frame in initial["display"]["frames"].values():
        frame["age_seconds"] = 0
    result = subprocess.run(
        [node, str(Path(__file__).with_name("scanner_display_supplemental_negotiation.cjs"))],
        input=json.dumps({**scripts, "bundle": initial, "scenario": scenario, **options}),
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "negotiation scenario passed" in result.stdout


@pytest.mark.parametrize(
    "scenario",
    [
        "happy",
        "ip-origin",
        "mutually-exclusive",
        "same-context-retirement",
        "frame-cannot-rebind",
        "replace:session",
        "replace:stream",
        "replace:profile",
        "replace:repair",
        "rollback:retired",
        "rollback:revision",
        "rollback:invalidation",
        "rollback:endpoint",
        "history-cap",
        "profile-history-cap",
        "epoch-compaction",
        "same-epoch-profile-replay",
        "old-connection-new-revision",
        "unavailable:negotiation",
        "unavailable:frame",
        "outer-auth-stop",
        "admission:context:401",
        "admission:context:403",
        "admission:frame:401",
        "admission:frame:403",
        "late:context:stop",
        "late:context:hide",
        "late:body:stop",
        "late:body:hide",
        "late:frame:stop",
        "late:frame:hide",
        "timeout:headers",
        "timeout:body",
        "total-request-budget",
        "malformed:oversized",
        "malformed:utf8",
        "malformed:json",
        "same-context-expiry",
        "late-conflict",
        "old-finally-retains-new-deadline",
        "visibility-retains-guard",
        "independent-expiry",
        "independent-instances",
    ],
)
def test_negotiated_browser_lifecycle(scripts, bundle, scenario):
    run(scripts, bundle, scenario)


@pytest.mark.parametrize(
    "value",
    [
        "/relative/",
        "https://other.example.test/",
        "http://scanner.example.test/",
        "https://user:password@scanner.example.test/",
        "https://scanner.example.test/?query=1",
        "https://scanner.example.test/#fragment",
        "https://scanner.example.test/no-slash",
        123,
    ],
)
def test_fixed_same_origin_root(scripts, bundle, value):
    run(scripts, bundle, "root:invalid", value=value)


@pytest.mark.parametrize(
    "mutation",
    [
        {"extra": "PRIVATE"},
        {"protocol": "PRIVATE"},
        {"version": True},
        {"version": 2},
        {"context": None},
        {"field": "endpoint_id", "value": "PRIVATE"},
        {"field": "stream_id", "value": None},
        {"field": "session_id", "value": ""},
        {"field": "profile_revision", "value": "A" * 64},
        {"field": "profile_invalidation", "value": -1},
        {"field": "context_revision", "value": True},
        {"field": "context_revision", "value": 9007199254740992},
    ],
)
def test_strict_negotiation_payload(scripts, bundle, mutation):
    run(scripts, bundle, "bad-context:invalid", mutation=mutation)


def test_browser_accepts_real_authenticated_unix_transport_responses(
    scripts, capture, engine, tmp_path
):
    from sds200.scanner_display_supplemental_transport import SupplementalDeliveryService
    from sds200.web_supplemental import CONTEXT_PATH, FRAME_PATH

    from .test_scanner_display_supplemental_transport import (
        ORIGIN,
        VERSION,
        headers,
        login,
        unix_api,
        web,
    )

    feed, _, _, scanner, clock = engine
    service = SupplementalDeliveryService(feed, clock=lambda: clock.now)
    before = list(scanner.reads)
    with (
        unix_api(tmp_path, service) as factory,
        TestClient(web(factory, supplemental_delivery=True), base_url=ORIGIN) as client,
    ):
        assert client.get(CONTEXT_PATH, headers=VERSION).status_code == 401
        login(client)
        negotiation = client.get(CONTEXT_PATH, headers=VERSION)
        assert negotiation.status_code == 200
        response = client.get(FRAME_PATH, headers=headers(negotiation.json()["context"]))
        assert response.status_code == 200
        run(scripts, response.json(), "happy", negotiation=negotiation.json())
    assert scanner.reads == before
