"""Credential-free helper profile; real owned child/proc, synthetic cgroup.

No Docker, Home Assistant or scanner access; digest matching is not installed
qualification. Process identity still uses an original real pidfd in these tests.
"""

import copy
import os
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_environment as env

m, image = env.m, env.image
CID = "b" * 64


@pytest.fixture
def configured(image):
    return [*image, "TZ=" + env.TIMEZONE]


def pin(configured, image, **overrides):
    profile = dict(image_environment_sha256=m.environment(image), timezone=env.TIMEZONE)
    return m.helper_environment(configured, **(profile | overrides))


def profile(configured, image):
    return dict(
        configured=configured,
        configured_sha256=pin(configured, image),
        image_environment_sha256=m.environment(image),
        timezone=env.TIMEZONE,
        hostname=env.HOSTNAME,
    )


def raw(configured):
    return env.process_bytes(configured, fixed_exec=False)


def test_helper_profile_is_distinct_and_does_not_mutate_or_filter(configured, image, capsys):
    original = copy.deepcopy((configured, image))
    result = pin(configured, image)
    assert result == pin(list(reversed(configured)), image)
    assert result != m.environment(image)
    assert result == m.checksum(
        dict(
            schema=1,
            kind=m.KIND + "-helper-environment",
            values=dict(v.split("=", 1) for v in configured),
        )
    )
    env.runtime.denied(lambda: m.environment(configured))
    env.runtime.denied(lambda: env.config_pin(configured, image))
    env.runtime.denied(lambda: pin(image, image))
    assert (configured, image) == original and capsys.readouterr() == ("", "")


@pytest.mark.parametrize(
    "key",
    [
        "SUPERVISOR_TOKEN",
        "HASSIO_TOKEN",
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "PYTHONPATH",
        "GCONV_PATH",
        "HOME",
        "HOSTNAME",
        "TERM",
    ],
)
def test_helper_never_adopts_extra_credentials_loader_or_shell_environment(configured, image, key):
    env.runtime.denied(lambda: pin([*configured, key + "=" + env.TOKEN], image))


@pytest.mark.parametrize(
    "fault", ["missing", "duplicate", "replacement", "dict", "tuple", "none", "boolean"]
)
def test_helper_config_requires_original_closed_shape(configured, image, fault):
    values = list(configured)
    if fault == "missing":
        values.pop()
    elif fault == "duplicate":
        values[-1] = values[0]
    elif fault == "replacement":
        values[-1] = "SUPERVISOR_TOKEN=" + env.TOKEN
    elif fault == "dict":
        values = dict(v.split("=", 1) for v in values)
    elif fault == "tuple":
        values = tuple(values)
    elif fault == "none":
        values = None
    else:
        values[0] = True
    env.runtime.denied(lambda: pin(values, image))


@pytest.mark.parametrize(
    "key,value",
    [
        ("PATH", m.FIXED_EXEC_PATH),
        ("PYTHON_VERSION", "3.14.8"),
        ("PYTHON_SHA256", "c" * 64),
        ("PYTHONDONTWRITEBYTECODE", "0"),
        ("PYTHONUNBUFFERED", "0"),
        ("TZ", "UTC"),
    ],
)
def test_each_helper_value_must_match_original_pin(configured, image, key, value):
    env.runtime.denied(lambda: pin(env.replace_value(configured, key, value), image))


@pytest.mark.parametrize(
    "zone", ["", "/etc/localtime", ":UTC", "A/../B", "A//B", None, True, "A" * 65]
)
def test_helper_zone_is_an_explicit_bounded_name(configured, image, zone):
    env.runtime.denied(lambda: pin(configured, image, timezone=zone))


def test_helper_startup_additions_and_domain_separation(configured, image):
    args = profile(configured, image)
    result = m.helper_process_environment(raw(configured), **args)
    assert len(result) == 64 and result != args["configured_sha256"]
    env.runtime.denied(
        lambda: m.helper_process_environment(
            raw(configured), **(args | {"configured_sha256": m.environment(image)})
        )
    )
    env.runtime.denied(
        lambda: m.supervised_process_environment(raw(configured), **args, fixed_exec=False)
    )
    supervised = [*configured, "SUPERVISOR_TOKEN=" + env.TOKEN, "HASSIO_TOKEN=" + env.TOKEN]
    env.runtime.denied(lambda: m.helper_process_environment(raw(supervised), **args))


@pytest.mark.parametrize(
    "fault",
    [
        "none",
        "text",
        "unterminated",
        "oversize",
        "nonascii",
        "duplicate",
        "missing",
        "extra",
        "path",
        "home",
        "hostname",
        "timezone",
        "empty_entry",
    ],
)
def test_helper_startup_bytes_are_exact_and_closed(configured, image, fault):
    value = raw(configured)
    if fault == "none":
        value = None
    elif fault == "text":
        value = value.decode()
    elif fault == "unterminated":
        value = value[:-1]
    elif fault == "oversize":
        value = b"x" * 16384 + b"\0"
    elif fault == "nonascii":
        value = value.replace(b"America", b"\xffmerica")
    elif fault == "duplicate":
        value += b"HOME=/root\0"
    elif fault == "missing":
        value = value.replace(b"HOME=/root\0", b"")
    elif fault == "extra":
        value += b"LD_PRELOAD=PRIVATE\0"
    elif fault == "path":
        value = env.process_bytes(configured, fixed_exec=True)
    elif fault == "home":
        value = value.replace(b"HOME=/root", b"HOME=/other")
    elif fault == "hostname":
        value = value.replace(env.HOSTNAME.encode(), b"replacement-helper")
    elif fault == "timezone":
        value = value.replace(env.TIMEZONE.encode(), b"UTC")
    else:
        value += b"\0"
    env.runtime.denied(lambda: m.helper_process_environment(value, **profile(configured, image)))


@pytest.fixture
def bound(configured, image, monkeypatch):
    values = dict(value.split("=", 1) for value in configured)
    values.update(HOME="/root", HOSTNAME=env.HOSTNAME)
    child = subprocess.Popen(
        [sys.executable, "-I", "-B", "-c", "import sys; sys.stdin.read()"],
        stdin=subprocess.PIPE,
        env=values,
    )

    def identity(pid, cid):
        assert pid == child.pid and cid == CID
        return m.processes.process_identity(
            pid,
            cid,
            Path(f"/proc/{pid}/stat").read_text(),
            f"0::/system.slice/docker-{cid}.scope\n",
        )

    monkeypatch.setattr(m, "ROOT_UID", os.geteuid())
    monkeypatch.setattr(m.processes, "read_identity", identity)
    witness = None
    try:
        witness = m.processes.ProcessWitness(identity(child.pid, CID))
        yield SimpleNamespace(child=child, witness=witness, profile=profile(configured, image))
    finally:
        if witness is not None:
            witness.close()
        if not child.stdin.closed:
            child.stdin.close()
        try:
            child.wait(timeout=3)
        except subprocess.TimeoutExpired:
            child.kill()  # Only this fixture's owned harmless child.
            child.wait(timeout=3)


def collect(bound, **changes):
    return m.collect_helper_process_environment(
        bound.witness, deadline=time.monotonic() + 0.8, **(bound.profile | changes)
    )


def test_actual_original_helper_startup_reads_retain_owned_pidfd(bound, capsys):
    before = len(os.listdir("/proc/self/fd"))
    started = time.monotonic()
    result = collect(bound)
    assert result.process == bound.witness.identity
    assert started <= result.observed_at <= time.monotonic()
    with open(f"/proc/{bound.child.pid}/environ", "rb") as stream:
        value = stream.read(16385)
    assert result.sha256 == m.helper_process_environment(value, **bound.profile)
    assert len(os.listdir("/proc/self/fd")) == before
    assert not bound.witness.exited() and bound.child.poll() is None
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize(
    "fault",
    ["exit", "closed", "changed_identity", "changed_second_read", "wrong_pin", "exec_override"],
)
def test_helper_collector_never_rebinds_or_adopts_different_startup(bound, monkeypatch, fault):
    if fault == "exit":
        bound.child.stdin.close()
        bound.child.wait(timeout=3)
    elif fault == "closed":
        bound.witness.close()
    elif fault == "changed_identity":
        original = m.processes.read_identity
        monkeypatch.setattr(
            m.processes, "read_identity", lambda *args: replace(original(*args), start_ticks=1)
        )
    elif fault == "changed_second_read":
        original_read, calls = m.os.read, 0

        def changed(fd, size):
            nonlocal calls
            calls += 1
            value = original_read(fd, size)
            return value.replace(b"America", b"PRIVATE") if calls == 3 else value

        monkeypatch.setattr(m.os, "read", changed)
    elif fault == "wrong_pin":
        bound.profile["configured_sha256"] = "f" * 64
    else:
        bound.profile["fixed_exec"] = True
    before = len(os.listdir("/proc/self/fd"))
    env.runtime.denied(lambda: collect(bound))
    assert len(os.listdir("/proc/self/fd")) == before
