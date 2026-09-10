"""Owned SQL/private-file producer, with portable simulated history/ancestry.

The actual retained namespace chains require separate qualification. This module
does not enable the producer in ordinary native dispatch or authorize sign-in.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import replace

import pytest

from sds200 import browser_device_continuation_context as context
from sds200 import browser_device_continuation_current as current
from sds200 import browser_device_continuation_ownership as ownership
from sds200 import browser_device_native as native
from sds200.browser_device_recovery import RecoveryMode
from sds200.browser_device_startup import _launch_lock
from sds200.browser_device_worker import BrowserWorkerSelection, worker_graph
from tests.test_browser_device_bundle import profile as profile
from tests.test_browser_device_bundle import public_key as public_key
from tests.test_browser_device_continuation_current import (
    activation_candidate as activation_candidate,
)
from tests.test_browser_device_continuation_current import active_native
from tests.test_browser_device_continuation_current import candidate as candidate
from tests.test_browser_device_continuation_ownership import handoff as handoff
from tests.test_browser_device_guard_release import blocked
from tests.test_browser_device_native import certificates as certificates
from tests.test_browser_device_profile import CREDENTIAL
from tests.test_browser_device_registration import source as source
from tests.test_browser_device_resume_workflow import lab as lab
from tests.test_browser_device_resume_workflow import snap
from tests.test_browser_device_startup import inputs as inputs

ERROR = context.BrowserContinuationContextError
pytestmark = pytest.mark.skipif(sys.platform != "linux" or os.geteuid() == 0,
                               reason="Non-root Linux owned current-state fixtures")


@pytest.fixture
def selected(lab, candidate, monkeypatch):
    monkeypatch.setattr(current, "_browser_directory", lambda *_: candidate.root)
    monkeypatch.setattr(ownership, "_browser_directory", lambda *_: candidate.root)
    monkeypatch.setattr(native, "_post_browser_device", lambda *a, **k: pytest.fail("Network"))
    return BrowserWorkerSelection(lab.args["bundle"], lab.args["public_key"])


@pytest.mark.parametrize("active", [False, True])
def test_exact_owned_context_matches_browser_parser_without_enabling_dispatch(
        lab, candidate, selected, active):
    if active:
        active_native(lab, candidate)
    before = snap(lab)
    with _launch_lock(candidate.root, create=False):
        document = context._continuation_worker_context(lab.configuration, selected)
    assert snap(lab) == before
    assert set(document) == {"version", "ok", "build", "role", "config", "extensionId",
                             "acknowledge", "launch", "continuation"}
    assert document["build"] == worker_graph()[0]
    assert document["role"] == "continuation"
    assert document["acknowledge"] is False and document["launch"] is None
    encoded = json.dumps(document)
    assert CREDENTIAL not in encoded and str(candidate.root) not in encoded
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")
    script = """
import fs from 'node:fs';
import assert from 'node:assert/strict';
import {validateContinuationContext}
  from './src/sds200/browser_assets/browser_device_continuation_context.mjs';
import {validateWorkerContext} from './src/sds200/browser_assets/browser_device_worker.mjs';
const v=JSON.parse(fs.readFileSync(0,'utf8'));
const result=validateContinuationContext(v,v.build,v.extensionId);
assert.throws(()=>validateWorkerContext(v,v.build,v.extensionId));
console.log(JSON.stringify(result));
"""
    run = subprocess.run([node, "--input-type=module", "-e", script], input=encoded,
                         capture_output=True, text=True, timeout=10)
    assert run.returncode == 0 and run.stderr == "", run.stdout + run.stderr
    result = json.loads(run.stdout)
    assert result["observed"]["mode"] == ("active" if active else "paused")
    assert result["observed"]["binding"]["generation"] == (7 if active else None)
    assert set(result) == {"settings", "observed"}
    blocked(lab)


@pytest.mark.parametrize("failure", ["exit", "graph", "second-read", "credential"])
def test_changed_context_cannot_escape_as_success(lab, candidate, selected, monkeypatch, failure):
    before = snap(lab)
    if failure == "exit":
        original = current._worker_current_scope

        @contextmanager
        def changed(*args):
            with original(*args) as reader:
                yield reader
                candidate.paths["manifest"].chmod(0o644)

        monkeypatch.setattr(current, "_worker_current_scope", changed)
    elif failure == "graph":
        graph = worker_graph()
        replies = iter([graph, ("f" * 64, graph[1])])
        monkeypatch.setattr(context, "worker_graph", lambda: next(replies))
    else:
        original_read = current._CurrentRead.observe
        calls = []

        def changed_read(reader):
            result = original_read(reader)
            calls.append(True)
            if len(calls) == 1 and failure == "credential":
                candidate.paths["credential"].write_bytes(b"changed fictional credential")
            if len(calls) == 2 and failure == "second-read":
                return replace(result, state=replace(result.state, native_revision=999))
            return result

        monkeypatch.setattr(current._CurrentRead, "observe", changed_read)
    with _launch_lock(candidate.root, create=False), pytest.raises(ERROR) as error:
        context._continuation_worker_context(lab.configuration, selected)
    assert CREDENTIAL not in str(error.value) and str(candidate.root) not in str(error.value)
    if failure in {"graph", "second-read"}:
        assert snap(lab) == before
    elif failure == "exit":
        assert candidate.paths["manifest"].stat().st_mode & 0o777 == 0o644
    else:
        assert candidate.paths["credential"].read_bytes() == b"changed fictional credential"


@pytest.mark.parametrize("change", ["identity", "origin", "device", "epoch", "fingerprint",
    "revision-bool", "revision-large", "mode", "mode-string", "paused-generation"])
def test_malformed_read_is_not_a_browser_context(lab, candidate, selected, monkeypatch, change):
    original = current._CurrentRead.observe
    changes = {"identity": ("identity", "f" * 64),
        "origin": ("origin", "https://different.example"), "device": ("device_id", "other"),
        "epoch": ("epoch", "bad"), "fingerprint": ("state_fingerprint", "bad"),
        "revision-bool": ("native_revision", True), "revision-large": ("native_revision", 2**53),
        "mode": ("mode", RecoveryMode.REJECTED), "mode-string": ("mode", "paused")}
    changed = []

    def observe(reader):
        result = original(reader)
        changed.append(True)
        if change == "paused-generation":
            return replace(result, generation=7)
        key, value = changes[change]
        return replace(result, state=replace(result.state, **{key: value}))

    monkeypatch.setattr(current._CurrentRead, "observe", observe)
    before = snap(lab)
    with _launch_lock(candidate.root, create=False), pytest.raises(ERROR):
        context._continuation_worker_context(lab.configuration, selected)
    assert changed == [True]
    assert snap(lab) == before


@pytest.mark.parametrize("generation", [None, 0, -1, True, "7", 1.5, 2**53 - 1])
def test_active_context_refuses_invalid_generation(
        lab, candidate, selected, monkeypatch, generation):
    active_native(lab, candidate)
    original = current._CurrentRead.observe
    changed = []

    def observe(reader):
        result = original(reader)
        assert result.state.mode is RecoveryMode.ACTIVE and result.generation == 7
        changed.append(True)
        return replace(result, generation=generation)

    monkeypatch.setattr(current._CurrentRead, "observe", observe)
    before = snap(lab)
    with _launch_lock(candidate.root, create=False), pytest.raises(ERROR):
        context._continuation_worker_context(lab.configuration, selected)
    assert changed == [True] and snap(lab) == before


@pytest.mark.parametrize("change", ["configuration-object", "selection-object", "root",
    "directory", "normal-bundle", "intent"])
def test_browser_values_cannot_select_a_native_context(lab, candidate, selected, change):
    config = lab.configuration
    if change == "configuration-object":
        config = dict(identity=config.identity)
    elif change == "selection-object":
        selected = dict(bundle=selected.bundle, public_key=selected.public_key)
    elif change == "root":
        config = replace(config, root=candidate.root)
    else:
        field = {"directory": "directory", "normal-bundle": "normal_bundle",
                 "intent": "intent"}[change]
        selected = replace(selected, **{field: "browser-value" if field == "intent"
                                       else candidate.root})
    before = snap(lab)
    with _launch_lock(candidate.root, create=False), pytest.raises(ERROR):
        context._continuation_worker_context(config, selected)
    assert snap(lab) == before
