"""Actual generated HA card under synthetic HA/DOM; no real HA or scanner."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from .test_home_assistant_mimic_lovelace import module
from .test_scanner_display_supplemental_web import bundle as bundle
from .test_scanner_display_supplemental_web import capture as capture
from .test_scanner_display_supplemental_web import configured as configured
from .test_scanner_display_supplemental_web import engine as engine


@pytest.mark.parametrize(
    "case",
    [
        "aux_default_off",
        "aux_profile",
        "aux_expiry",
        "aux_duplicate",
        "aux_resume",
        "aux_context",
        "aux_replay",
        "aux_epochs",
        "aux_limits",
        "aux_auth",
        "aux_late_auth",
        "aux_late_body",
        "aux_stall",
        "aux_shared",
        "aux_limits_body",
        "aux_discovery",
        "aux_new_ingress",
        "aux_instance",
        "aux_clock_budget",
        "aux_unmount",
        "aux_old_deadline",
        "aux_total_budget",
        "aux_frame_cannot_rebind",
        "aux_statuses",
        "aux_guard_private",
        "aux_legacy_owner",
    ],
)
@pytest.mark.parametrize("origin", ["https://ha.example.test", "http://192.0.2.18:8123"])
def test_generated_supplemental_card(case, origin, bundle):
    run(case, origin, bundle)


def run(case, origin, bundle):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node needed for actual generated card lifecycle tests")
    result = subprocess.run(
        [node, str(Path(__file__).with_name("home_assistant_mimic_lifecycle.cjs"))],
        input=json.dumps(
            dict(
                script=module(),
                waterfall=module("waterfall"),
                supplemental=True,
                bundle=bundle,
                case=case,
                origin=origin,
            )
        ),
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"{case} passed"


def test_generated_card_uses_authenticated_http_unix_delivery(capture, engine, tmp_path):
    from fastapi.testclient import TestClient

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
        negotiated = client.get(CONTEXT_PATH, headers=VERSION)
        assert negotiated.status_code == 200
        frame = client.get(FRAME_PATH, headers=headers(negotiated.json()["context"]))
        assert frame.status_code == 200
        assert frame.headers["cache-control"] == "no-store"
        run("aux_profile", "https://ha.example.test", frame.json())
    assert scanner.reads == before
