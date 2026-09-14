"""Strict identity-bound native resume frames and real process supervision."""
from __future__ import annotations

import json
import struct
import subprocess
import sys
import time

import pytest

from sds200.browser_device_protocol import (
    BrowserDeviceProtocolError,
    BrowserResumeRequest,
    parse_browser_device_request,
)
from sds200.browser_device_recovery import RecoveryMode
from tests.test_browser_device_native import certificates as certificates
from tests.test_browser_device_resume_transport import INTENT
from tests.test_browser_device_resume_transport import lab as lab

RUNNER = """
import os,sys
from pathlib import Path
from sds200.browser_device_native import run_browser_native
raise SystemExit(run_browser_native(Path(sys.argv[1]),[sys.argv[2]],
    os.fdopen(os.dup(0),'rb',buffering=0),os.fdopen(os.dup(1),'wb',buffering=0),
    expected_identity=None if sys.argv[3]=='none' else sys.argv[3]))
"""


def invoke(lab, action, *, caller=None, identity=None, script=RUNNER, **fields):
    body=json.dumps({"version":1,"action":action,**fields}).encode()
    result=subprocess.run([sys.executable,"-c",script,str(lab.root),
        caller or lab.configuration.extension_origin,identity or lab.configuration.identity],
        input=struct.pack("=I",len(body))+body,capture_output=True,timeout=13)
    assert result.stderr == b"" and result.returncode == 0
    length=struct.unpack("=I",result.stdout[:4])[0]
    assert length <= 4096 and length == len(result.stdout)-4
    assert lab.issued.credential.encode() not in result.stdout
    return json.loads(result.stdout[4:])


def test_real_review_prepare_commit_and_replay(lab):
    before=lab.ledger.path.read_bytes()
    review=invoke(lab,"review-resume")
    assert review == {"version":1,"ok":True,"mode":"paused","revision":3,"generation":1}
    assert lab.ledger.path.read_bytes() == before and not lab.sessions._sessions
    prepared=invoke(lab,"prepare-resume",intent=INTENT,revision=3,generation=1)
    assert prepared["ok"] and lab.ledger.inspect().mode is RecoveryMode.PAUSED
    approval=prepared["approval"]
    result=invoke(lab,"commit-resume",intent=INTENT,**approval)
    assert result["ok"] and result["mode"] == "active" and result["revision"] == 6
    assert result["session"]["token"].startswith("sdsctl-browser-session-v1.")
    assert len(lab.observed) == 4
    assert invoke(lab,"commit-resume",intent=INTENT,**approval)["ok"] is False
    assert len(lab.observed) == 4


@pytest.mark.parametrize("change", [{"identity":"none"},{"identity":"f"*64},
    {"caller":"chrome-extension://"+"p"*32+"/"}])
def test_resume_requires_fixed_matching_wrapper_and_extension(lab,change):
    before=lab.ledger.path.read_bytes()
    assert invoke(lab,"review-resume",**change) == {"version":1,"ok":False,"mode":"setup_error"}
    assert not lab.observed and lab.ledger.path.read_bytes() == before


@pytest.mark.parametrize("action,fields", [
    ("review-resume",{}),
    ("prepare-resume",{"intent":INTENT,"revision":3,"generation":1}),
    ("commit-resume",{"intent":INTENT,"revision":4,"ticket":"a"*64,"expires_at":1000}),
])
def test_valid_resume_request_repr_never_contains_private_handoff(action,fields):
    parsed=parse_browser_device_request(json.dumps({"version":1,"action":action,**fields}).encode())
    assert isinstance(parsed,BrowserResumeRequest) and parsed.action == action
    assert INTENT not in repr(parsed) and "a"*64 not in repr(parsed)


@pytest.mark.parametrize("fields", [
    {},{"intent":INTENT,"revision":True,"generation":1},
    {"intent":"bad","revision":3,"generation":1},
    {"intent":INTENT,"revision":3,"generation":0},
    {"intent":INTENT,"revision":3,"generation":2**53},
    {"intent":INTENT,"revision":3,"generation":1,"origin":"https://private.invalid"},
])
def test_bad_prepare_schema_is_redacted(fields):
    with pytest.raises(BrowserDeviceProtocolError,match="^Invalid browser-device request.$"):
        parse_browser_device_request(json.dumps({"version":1,"action":"prepare-resume",**fields}).encode())


@pytest.mark.parametrize("expires", [True,None,"1000",float("inf"),float("nan"),-1,2**2000])
def test_bad_commit_timestamp_is_redacted(expires):
    with pytest.raises(BrowserDeviceProtocolError):
        parse_browser_device_request(json.dumps({"version":1,"action":"commit-resume",
            "intent":INTENT,"ticket":"a"*64,"revision":4,"expires_at":expires}).encode())


@pytest.mark.parametrize("action", ["review-resume","prepare-resume","commit-resume"])
def test_supervisor_kills_and_reaps_blocked_resume_child(lab, action):
    # Replace only the child dispatch with a never-returning local fixture. Use
    # the actual ten-second supervisor, not a shortened test deadline.
    script=RUNNER.replace("raise SystemExit(run_browser_native", """
import sds200.browser_device_native as native
import time
def blocked(*args,**kwargs):
    while True: time.sleep(1)
native._resume_request=blocked
raise SystemExit(run_browser_native""")
    fields={"review-resume":{},"prepare-resume":{"intent":INTENT,"revision":3,"generation":1},
        "commit-resume":{"intent":INTENT,"revision":4,"ticket":"a"*64,"expires_at":1000}}[action]
    body=json.dumps({"version":1,"action":action,**fields}).encode()
    start=time.monotonic()
    result=subprocess.run([sys.executable,"-c",script,str(lab.root),
        lab.configuration.extension_origin,lab.configuration.identity],
        input=struct.pack("=I",len(body))+body,capture_output=True,timeout=13)
    assert result.returncode == 2 and result.stdout == result.stderr == b""
    assert 9 <= time.monotonic()-start < 13
    assert lab.ledger.inspect().mode is RecoveryMode.PAUSED and not lab.observed
