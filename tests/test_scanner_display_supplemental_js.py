"""Python/JavaScript parity with synthetic clocks and replies, not browser UI."""

from __future__ import annotations

import json
import random
import shutil
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sds200.scanner_display_supplemental import SupplementalValueStatus as Status
from sds200.scanner_display_supplemental_wire import decode_supplemental_delivery
from sds200.web_dashboard import create_web_dashboard_app

from .test_scanner_display_supplemental_delivery import configured as configured
from .test_scanner_display_supplemental_delivery import consumer, newer
from .test_scanner_display_supplemental_delivery import engine as engine
from .test_scanner_display_supplemental_delivery import payload as payload

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "src/sds200/web_assets/mimic-supplemental.js"
RUNNER = r"""
const vm = require('node:vm');
const input = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const window = {};
vm.runInNewContext(input.script, {window});
const api = window.sdsctlSupplemental;
if (input.decode) {
  console.log(JSON.stringify(input.decode.map(payload => {
    try {api.decode(payload); return true;} catch (_) {return false;}
  })));
} else {
  const output = [];
  for (const trace of input.traces) {
    const guard = api.create(input.context), tickets = {}, steps = [];
    for (const op of trace) {
      try {
        if(op.kind === 'begin') {tickets[op.id] = guard.begin(op.now); steps.push(null);}
        if(op.kind === 'accept') steps.push(guard.accept(tickets[op.id], op.payload, op.now));
        if(op.kind === 'snapshot') {
          const view = guard.snapshot(op.now);
          for(const source of Object.values(view))
            if(source.age_seconds !== null)
              source.age_seconds = Number(source.age_seconds.toFixed(6));
          steps.push(view);
        }
        if(op.kind === 'suspend') {guard.suspend(); steps.push(null);}
        if(op.kind === 'close') {guard.close(); steps.push(null);}
      } catch (_) {steps.push('error');}
    }
    output.push(steps);
  }
  console.log(JSON.stringify(output));
}
"""


def javascript(**values):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for supplemental consumer parity")
    result = subprocess.run(
        [node, "-e", RUNNER],
        input=json.dumps({"script": SCRIPT.read_text(), **values}, allow_nan=False),
        text=True,
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def python_trace(payload, trace):
    guard = consumer(payload)
    tickets, output = {}, []
    for op in trace:
        try:
            if op["kind"] == "begin":
                tickets[op["id"]] = guard.begin(now=op["now"])
                output.append(None)
            elif op["kind"] == "accept":
                output.append(guard.accept(tickets[op["id"]], op["payload"], now=op["now"]))
            elif op["kind"] == "snapshot":
                view = guard.snapshot(now=op["now"])
                output.append(
                    {
                        name: {
                            "status": item.status.value,
                            "sample_sequence": item.sample_sequence,
                            "age_seconds": None
                            if item.age_seconds is None
                            else round(item.age_seconds, 6),
                            "value": (item.local_time.isoformat() if item.local_time else None)
                            if name == "clock"
                            else (
                                "".join(str(int(key)) for key in item.states)
                                if item.states
                                else None
                            ),
                        }
                        for name, item in (("clock", view.clock), ("favorites", view.favorites))
                    }
                )
            elif op["kind"] == "suspend":
                guard.suspend()
                output.append(None)
            elif op["kind"] == "close":
                guard.close()
                output.append(None)
        except ValueError:
            output.append("error")
    return output


def roundtrip(payload, now, *, finish=None, identifier="request"):
    return [
        {"kind": "begin", "id": identifier, "now": now},
        {
            "kind": "accept",
            "id": identifier,
            "now": now if finish is None else finish,
            "payload": payload,
        },
        {"kind": "snapshot", "now": now if finish is None else finish},
    ]


def test_actual_javascript_matches_python_state_machine(payload):
    traces = [
        roundtrip(payload, 100)
        + roundtrip(newer(payload), 104)
        + [{"kind": "snapshot", "now": 104.5}, {"kind": "snapshot", "now": 105}],
        roundtrip(payload, 100, finish=106)
        + roundtrip(newer(payload), 106.1)
        + roundtrip(newer(payload, psi=2, clock=1, favorites=1), 106.2),
        roundtrip(payload, 100)
        + [{"kind": "suspend"}]
        + roundtrip(payload, 100.1)
        + roundtrip(newer(payload), 100.2)
        + roundtrip(newer(payload, psi=2, clock=1, favorites=1), 100.3),
        [
            {"kind": "begin", "id": "a", "now": 100},
            {"kind": "begin", "id": "b", "now": 101},
            {"kind": "accept", "id": "a", "payload": payload, "now": 101.1},
            {"kind": "accept", "id": "b", "payload": payload, "now": 101.2},
            {"kind": "snapshot", "now": 101.2},
            {"kind": "accept", "id": "a", "payload": payload, "now": 101.3},
            {"kind": "snapshot", "now": 101.3},
        ],
        roundtrip(payload, 100)
        + [
            {"kind": "begin", "id": "a", "now": 101},
            {"kind": "close"},
            {"kind": "accept", "id": "a", "payload": payload, "now": 101.1},
            {"kind": "snapshot", "now": 101.1},
            {"kind": "begin", "id": "a", "now": 102},
        ],
        roundtrip(payload, 100)
        + [{"kind": "snapshot", "now": 99}, {"kind": "begin", "id": "a", "now": 102}],
    ]
    for field in ("clock", "favorites"):
        trace = roundtrip(payload, 100)
        for now in (101, 102, 103, 104, 105):
            trace += roundtrip(newer(payload, psi=now), now)
        trace += roundtrip(newer(payload, psi=106, **{field: 1}), 106)
        traces.append(trace)
        for state in Status:
            if state is Status.CURRENT:
                continue
            bad = newer(payload)
            bad[field] = {
                "status": state.value,
                "sample_sequence": None,
                "age_seconds": None,
                "value": None,
            }
            traces.append(
                roundtrip(payload, 100)
                + roundtrip(bad, 101)
                + roundtrip(newer(payload, psi=2), 102)
                + roundtrip(newer(payload, psi=3, **{field: 1}), 103)
            )
        conflict = newer(payload)
        conflict[field]["value"] = "2026-09-18T12:00:00" if field == "clock" else "1" * 100
        traces.append(
            roundtrip(payload, 100)
            + roundtrip(conflict, 101)
            + roundtrip(newer(payload, psi=2), 102)
        )
    expected = [python_trace(payload, trace) for trace in traces]
    assert expected[0][-2]["clock"]["status"] == "current"
    assert expected[0][-2]["favorites"]["status"] == "stale"
    assert expected[0][-1]["clock"]["status"] == "stale"
    assert expected[1][1] is False and expected[1][5]["clock"]["value"] is None
    assert expected[1][-1]["clock"]["status"] == "current"
    assert javascript(context=payload["context"], traces=traces) == expected


def test_deterministic_adversarial_reply_sequences_match(payload):
    rng = random.Random(9172026)
    traces = []
    for _ in range(40):
        now, seq, c, f = 100.0, 0, 0, 0
        trace = []
        for _ in range(30):
            now += rng.choice((0.1, 0.5, 1, 6))
            seq += rng.choice((0, 1, 1, 3))
            c += rng.choice((0, 1))
            f += rng.choice((0, 1))
            value = newer(payload, psi=seq, clock=c, favorites=f)
            for source in ("clock", "favorites"):
                value[source]["age_seconds"] = rng.choice((0, 0.5, 3, 4.999))
            if rng.randrange(9) == 0:
                trace.append({"kind": "suspend"})
            if rng.randrange(9) == 0:
                value["context"]["context_revision"] += 1
            finish = now + rng.choice((0, 0.1, 5))
            trace += roundtrip(value, now, finish=finish)
            now = finish
        traces.append(trace)
    expected = [python_trace(payload, trace) for trace in traces]
    assert javascript(context=payload["context"], traces=traces) == expected


@pytest.mark.parametrize("source", ["clock", "favorites"])
def test_javascript_strict_source_validation(payload, source):
    samples, expected = [payload], [True]
    for field, value in [
        ("sample_sequence", True),
        ("sample_sequence", 0),
        ("sample_sequence", 2**53),
        ("age_seconds", True),
        ("age_seconds", -1),
        ("age_seconds", 5),
        ("value", None),
        ("value", []),
        ("value", "<script>"),
        ("status", "stale"),
        ("extra", "PRIVATE"),
    ]:
        changed = deepcopy(payload)
        changed[source][field] = value
        samples.append(changed)
        expected.append(False)
    assert javascript(decode=samples) == expected


@pytest.mark.parametrize(
    "date,accepted",
    [
        ("0001-01-01T00:00:00", True),
        ("2000-02-29T23:59:59", True),
        ("1900-02-29T00:00:00", False),
        ("2026-02-29T00:00:00", False),
        ("2026-04-31T00:00:00", False),
        ("2026-01-01T24:00:00", False),
        ("0000-01-01T00:00:00", False),
        ("2026-01-01T12:60:00", False),
        ("2026-01-01T12:00:60", False),
        ("2026-01-01T00:00:00Z", False),
    ],
)
def test_calendar_is_not_javascript_date_rollover_or_host_timezone(payload, date, accepted):
    payload["clock"]["value"] = date
    assert javascript(decode=[payload]) == [accepted]


def test_candidate_guard_is_not_loaded_served_or_enabled_by_ordinary_dashboard():
    with TestClient(create_web_dashboard_app(lambda: None)) as client:
        assert "mimic-supplemental" not in client.get("/").text
        assert client.get("/assets/mimic-supplemental.js").status_code == 404
        assert "supplementalContext = null" in client.get("/assets/mimic-sds.js").text
        assert "supplementalContext" not in client.get("/assets/dashboard.js").text
    generated = ROOT / "src/sds200/themes/home-assistant/mimic-sds/sds200-mimic-card.js"
    assert "sdsctlSupplemental" not in generated.read_text()
    text = SCRIPT.read_text()
    for forbidden in (
        "fetch(",
        "setTimeout(",
        "setInterval(",
        "localStorage",
        "sessionStorage",
        "Date.now(",
        "new Date(",
        "document.",
    ):
        assert forbidden not in text


@pytest.mark.parametrize("number,accepted", [(1.0, True), (1.25, False), (True, False)])
def test_json_integral_number_semantics_match_both_decoders(payload, number, accepted):
    for container, field in (
        (payload, "version"),
        (payload["psi"], "sequence"),
        (payload["context"], "context_revision"),
        (payload["clock"], "sample_sequence"),
    ):
        container[field] = number
    assert javascript(decode=[payload]) == [accepted]
    if accepted:
        assert decode_supplemental_delivery(payload).clock.sample_sequence == 1
    else:
        with pytest.raises(ValueError):
            decode_supplemental_delivery(payload)
