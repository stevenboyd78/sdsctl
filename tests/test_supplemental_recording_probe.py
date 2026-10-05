"""Real disposable process-tree binding around explicitly fake cache responses.

The reused cached collector has separate real IPC tests. These tests qualify
only the additional process/guardian/watcher boundary, not scanner or host auth.
"""

import importlib.util
import json
import os
import select
import signal
import subprocess
import sys
import time
from contextlib import suppress
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from . import test_supplemental_handoff_cached as cache_tests

NAME = "supplemental_recording_probe"
SPEC = importlib.util.spec_from_file_location(
    NAME, Path(cache_tests.c.__file__).with_name(NAME + ".py")
)
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)

TREE = """import json,os
read,write=os.pipe()
kids=[]
try:
    for _ in range(2):
        pid=os.fork()
        if pid==0:
            os.close(write)
            for fd in (0,1,2): os.close(fd)
            os.read(read,1)
            os._exit(0)
        kids.append(pid)
    os.close(read)
    facts=[]
    for pid in [os.getpid(),*kids]:
        with open(f'/proc/{pid}/stat','rb') as f:
            ticks=int(f.read().rpartition(b') ')[2].split()[19])
        facts.append(dict(pid=pid,start_ticks=ticks,uid=os.geteuid(),gid=os.getegid()))
    print(json.dumps(facts),flush=True)
    os.read(0,1)
finally:
    os.close(write)
    for pid in kids: os.waitpid(pid,0)
"""


@pytest.fixture
def family():
    process = subprocess.Popen(
        [sys.executable, "-I", "-B", "-c", TREE],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    handles = []
    try:
        assert select.select([process.stdout], [], [], 3)[0]
        facts = json.loads(process.stdout.readline())
        assert facts[0]["pid"] == process.pid
        for item in facts:
            handles.append(os.pidfd_open(item["pid"]))
        expected = m.Expected(
            *(m.Process(**item) for item in facts), "a" * 64, time.monotonic() + 20
        )
        yield SimpleNamespace(process=process, handles=handles, expected=expected)
    finally:
        # Only retained, live-bound fixture identities are signaled. Resuming a
        # deliberate freeze lets the actual owned parent reap both children.
        for fd in handles:
            with suppress(ProcessLookupError):
                signal.pidfd_send_signal(fd, signal.SIGCONT)
        process.stdin.close()
        process.stdin = None
        out, err = process.communicate(timeout=3)
        assert process.returncode == 0 and out == err == b""
        for fd in handles:
            assert select.select([fd], [], [], 0)[0]
            os.close(fd)


@pytest.fixture
def fake_cache(family, monkeypatch, tmp_path):
    result = m.cached.CachedEvidence(
        family.expected.profile_sha256,
        True,
        True,
        True,
        family.expected.native.pid,
        str(family.expected.native.start_ticks),
    )
    fixture = SimpleNamespace(result=result, calls=[], hook=None)

    def collect(*args, **kwargs):
        fixture.calls.append((args, kwargs))
        if fixture.hook is not None:
            fixture.hook()
        return fixture.result

    monkeypatch.setattr(m.cached, "collect_cached", collect)
    fixture.paths = tuple(
        tmp_path / name for name in ("deployment.toml", "recordings", "daemon.sock")
    )
    return fixture


def probe(family, fixture, *, expected=None):
    return m.collect(
        family.expected if expected is None else expected,
        *fixture.paths,
        firmware="Version 1.26.01",
    )


def denied(callback):
    before = len(os.listdir("/proc/self/fd"))
    with pytest.raises(m.UnconfirmedProbe) as caught:
        callback()
    assert str(caught.value) == m.MESSAGE and caught.value.__suppress_context__
    assert len(os.listdir("/proc/self/fd")) == before


@pytest.mark.parametrize(
    "healthy,recording", [(True, True), (True, False), (False, True), (False, False)]
)
def test_actual_tree_keeps_truthful_cache_flags_and_exact_read_only_request(
    family, fake_cache, healthy, recording
):
    fake_cache.result = replace(fake_cache.result, healthy=healthy, recording=recording)
    before = len(os.listdir("/proc/self/fd"))
    result = probe(family, fake_cache)
    assert result is fake_cache.result
    assert fake_cache.calls == [
        (fake_cache.paths, {"firmware": "Version 1.26.01", "supplemental": True})
    ]
    assert len(os.listdir("/proc/self/fd")) == before
    assert not select.select(family.handles, [], [], 0)[0]


@pytest.mark.parametrize("role", ["guardian", "native", "watchdog"])
@pytest.mark.parametrize("field", ["start_ticks", "uid", "gid"])
def test_wrong_bound_identity_refuses_before_cache(family, fake_cache, role, field):
    process = getattr(family.expected, role)
    expected = replace(
        family.expected, **{role: replace(process, **{field: getattr(process, field) + 1})}
    )
    denied(lambda: probe(family, fake_cache, expected=expected))
    assert not fake_cache.calls


@pytest.mark.parametrize("role", ["guardian", "native", "watchdog"])
@pytest.mark.parametrize("moment", ["before", "during"])
def test_frozen_process_is_not_healthy_even_if_cached_flags_are_good(
    family, fake_cache, role, moment
):
    fd = family.handles[("guardian", "native", "watchdog").index(role)]

    def freeze():
        signal.pidfd_send_signal(fd, signal.SIGSTOP)
        # Wait for the kernel state, not an arbitrary sleep or PID reselection.
        deadline = time.monotonic() + 1
        pid = getattr(family.expected, role).pid
        while True:
            raw = Path(f"/proc/{pid}/stat").read_bytes().rpartition(b") ")[2].split()[0]
            if raw in (b"T", b"t"):
                break
            assert time.monotonic() < deadline

    if moment == "before":
        freeze()
    else:
        fake_cache.hook = freeze
    denied(lambda: probe(family, fake_cache))
    assert len(fake_cache.calls) == (moment == "during")


@pytest.mark.parametrize("role", ["native", "watchdog"])
def test_child_exit_during_cache_is_never_a_successful_return(family, fake_cache, role):
    fd = family.handles[("guardian", "native", "watchdog").index(role)]

    def exit_child():
        signal.pidfd_send_signal(fd, signal.SIGKILL)
        assert select.select([fd], [], [], 1)[0]

    fake_cache.hook = exit_child
    denied(lambda: probe(family, fake_cache))
    assert len(fake_cache.calls) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("profile_sha256", "b" * 64),
        ("peer_pid", 1),
        ("peer_start_ticks", "0"),
        ("supplemental_advertised", False),
        ("healthy", 1),
        ("recording", 0),
    ],
)
def test_wrong_cached_peer_profile_or_flag_types_refuse(family, fake_cache, field, value):
    fake_cache.result = replace(fake_cache.result, **{field: value})
    denied(lambda: probe(family, fake_cache))


@pytest.mark.parametrize("deadline", [0, -1, True, float("nan"), float("inf"), 10**20])
def test_original_deadline_is_finite_unexpired_and_bounded(family, fake_cache, deadline):
    denied(lambda: probe(family, fake_cache, expected=replace(family.expected, deadline=deadline)))
    assert not fake_cache.calls


def test_late_cache_return_is_discarded_without_second_read(family, fake_cache, monkeypatch):
    began = time.monotonic()
    fake_cache.hook = lambda: monkeypatch.setattr(
        m.time, "monotonic", lambda: began + m.MAX_SECONDS + 1
    )
    denied(lambda: probe(family, fake_cache))
    assert len(fake_cache.calls) == 1


def test_misparented_tree_cannot_pass_on_individual_live_pids(family, fake_cache):
    expected = replace(
        family.expected, guardian=family.expected.native, native=family.expected.guardian
    )
    denied(lambda: probe(family, fake_cache, expected=expected))
    assert not fake_cache.calls


@pytest.mark.parametrize("fault", ["duplicate", "self", "bool_pid", "dict", "profile", "paths"])
def test_unqualified_inputs_do_not_reach_cached_ipc(family, fake_cache, fault):
    expected = family.expected
    if fault == "duplicate":
        expected = replace(expected, watchdog=expected.native)
    elif fault == "self":
        expected = replace(expected, guardian=replace(expected.guardian, pid=os.getpid()))
    elif fault == "bool_pid":
        expected = replace(expected, native=replace(expected.native, pid=True))
    elif fault == "dict":
        expected = {"guardian": expected.guardian}
    elif fault == "profile":
        expected = replace(expected, profile_sha256="not an authenticated profile")
    else:
        fake_cache.paths = (fake_cache.paths[1] / "deployment.toml", *fake_cache.paths[1:])
    denied(lambda: probe(family, fake_cache, expected=expected))
    assert not fake_cache.calls


def test_cache_failure_never_returns_a_stale_success_or_retries(family, fake_cache):
    assert probe(family, fake_cache) is fake_cache.result

    def fail():
        raise RuntimeError("PRIVATE cache failure")

    fake_cache.hook = fail
    denied(lambda: probe(family, fake_cache))
    assert len(fake_cache.calls) == 2
