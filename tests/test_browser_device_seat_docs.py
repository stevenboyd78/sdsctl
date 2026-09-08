"""Execute the documented stop helper, never a real service manager or browser.

Only the reviewed account/profile/runtime paths are substituted. A closed PATH
contains strict id/systemctl fakes; the shell and Unix socket/marker checks are
real. This qualifies example logic, not compositor ordering or physical startup.
"""
from __future__ import annotations

import configparser
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

DOC = Path(__file__).resolve().parents[1] / "docs/browser-device-seat.md"
pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux shell/Unix socket example")

ID = """
import json, os, sys
from pathlib import Path
c = json.loads(Path(os.environ['SEAT_TEST_ROOT'], 'config.json').read_text())
if sys.argv[1:] == ['-un']: print(c.get('user', 'display'))
elif sys.argv[1:] == ['-u']: print(997)
else: raise SystemExit(91)
"""
SYSTEMCTL = r"""
import json, os, sys, time
from pathlib import Path
r = Path(os.environ['SEAT_TEST_ROOT'])
c = json.loads((r / 'config.json').read_text())
a = sys.argv[1:]
assert os.environ['XDG_RUNTIME_DIR'] == c['runtime']
assert os.environ['DBUS_SESSION_BUS_ADDRESS'] == 'unix:path=' + c['runtime'] + '/bus'
with (r / 'events.jsonl').open('a') as stream:
    stream.write(json.dumps(a) + chr(10))
if a == ['--user', 'stop', 'sdsctl-browser-seat-session.target', 'graphical-session.target']:
    time.sleep(c.get('delay', 0))
    if c.get('stop_failed'): raise SystemExit(1)
    (r / 'stop-returned').touch()
elif len(a) == 6 and a[:2] == ['--user', 'show'] and a[3] == '-p' and a[5] == '--value':
    assert (r / 'stop-returned').exists(), 'Inspection before synchronous stop completed'
    key = a[2] + ':' + a[4]
    if c.get('property_failed') == key: raise SystemExit(2)
    defaults = {'LoadState': 'loaded', 'ActiveState': 'inactive', 'MainPID': '0',
                'Result': 'success', 'ExecMainStatus': '0'}
    print(c.get(key, defaults[a[4]]))
else: raise SystemExit(92)
"""


def example(kind):
    matches = re.findall(rf"^```{kind}\n(.*?)^```", DOC.read_text(), re.M | re.S)
    assert len(matches) == 1
    return matches[0]


def run_case(tmp_path, configuration=None, *, marker=None, kind="file", bus="socket",
             directory="real"):
    commands = tmp_path / "bin"
    commands.mkdir()
    for name, body in (("id", ID), ("systemctl", SYSTEMCTL)):
        compile(body, name, "exec")
        executable = commands / name
        executable.write_text(f"#!{sys.executable}\n{body}")
        executable.chmod(0o700)
    profile = tmp_path / "chromium"
    profile.mkdir()
    sentinel = profile / "retained-state"
    sentinel.write_bytes(b"fictional browser state must not change")
    selected = profile / marker if marker else None
    if selected:
        if kind == "file":
            selected.write_bytes(b"retained marker")
        elif kind == "directory":
            selected.mkdir()
        else:
            selected.symlink_to(sentinel if kind == "live-link" else profile / "absent")
    checked_profile = profile
    if directory == "missing":
        checked_profile = tmp_path / "absent-profile"
    elif directory == "link":
        checked_profile = tmp_path / "linked-profile"
        checked_profile.symlink_to(profile)
    # A short socket path avoids the Unix socket pathname length limit in CI.
    with tempfile.TemporaryDirectory(prefix="sdsctl-seat-") as runtime_root:
        runtime = Path(runtime_root) / "997"
        runtime.mkdir()
        config = {**(configuration or {}), "runtime": str(runtime)}
        (tmp_path / "config.json").write_text(json.dumps(config))
        helper = example("sh")
        assert helper.count("/run/user/") == 1
        assert helper.count("/home/display/sdsctl-lab/chromium-data") == 1
        helper = helper.replace("/run/user/", runtime_root + "/").replace(
            "browser_directory=/home/display/sdsctl-lab/chromium-data",
            f"browser_directory='{checked_profile}'",
        )
        script = tmp_path / "stop.sh"
        script.write_text(helper)
        with socket.socket(socket.AF_UNIX) as listener:
            if bus == "socket":
                listener.bind(str(runtime / "bus"))
            elif bus == "file":
                (runtime / "bus").touch()
            result = subprocess.run(
                ["/bin/sh", str(script)], capture_output=True, text=True, timeout=5,
                env={"PATH": str(commands), "SEAT_TEST_ROOT": str(tmp_path)},
            )
    assert sentinel.read_bytes() == b"fictional browser state must not change"
    if selected:
        assert os.path.lexists(selected)
        if kind == "file":
            assert selected.read_bytes() == b"retained marker"
        elif kind == "directory":
            assert selected.is_dir()
        else:
            assert selected.is_symlink()
            expected = sentinel if kind == "live-link" else profile / "absent"
            assert os.readlink(selected) == str(expected)
    events_path = tmp_path / "events.jsonl"
    events = [json.loads(line) for line in events_path.read_text().splitlines()] \
        if events_path.exists() else []
    return result, events


def test_documented_helper_waits_for_stop_before_checking(tmp_path):
    result, events = run_case(tmp_path, {"delay": 0.05})
    assert result.returncode == 0, result.stderr
    assert "compositor may now terminate" in result.stdout
    assert events[0] == ["--user", "stop", "sdsctl-browser-seat-session.target",
                         "graphical-session.target"]
    assert len(events) == 13
    assert all(event[:2] == ["--user", "show"] for event in events[1:])


@pytest.mark.parametrize("configuration", [
    {"stop_failed": True},
    {"sdsctl-browser-seat-session.target:ActiveState": "active"},
    {"graphical-session.target:ActiveState": "active"},
    *[{f"{unit}:{prop}": value}
      for unit in ("sdsctl-browser-device.service", "sdsctl-browser-setup.service")
      for prop, value in (("LoadState", "not-found"), ("ActiveState", "deactivating"),
                          ("MainPID", "123"), ("Result", "timeout"), ("ExecMainStatus", "78"))],
    {"sdsctl-browser-device.service:Result": ""},
    {"property_failed": "sdsctl-browser-device.service:MainPID"},
])
def test_stop_or_inspection_failure_is_not_success(tmp_path, configuration):
    result, events = run_case(tmp_path, configuration)
    assert result.returncode != 0
    assert "compositor may now terminate" not in result.stdout
    assert events and all(event[1] in ("stop", "show") for event in events)
    if configuration.get("stop_failed"):
        assert len(events) == 1


@pytest.mark.parametrize("marker", ["SingletonLock", "SingletonSocket", "SingletonCookie"])
@pytest.mark.parametrize("kind", ["file", "directory", "live-link", "dangling-link"])
def test_every_marker_is_preserved_and_refused(tmp_path, marker, kind):
    result, events = run_case(tmp_path, marker=marker, kind=kind)
    assert result.returncode != 0
    assert "compositor may now terminate" not in result.stdout
    assert len(events) == 13


@pytest.mark.parametrize("options", [
    {"configuration": {"user": "root"}}, {"configuration": {"user": "personal-user"}},
    {"bus": "missing"}, {"bus": "file"}, {"directory": "missing"}, {"directory": "link"},
])
def test_bad_prerequisites_do_not_contact_manager(tmp_path, options):
    result, events = run_case(tmp_path, **options)
    assert result.returncode != 0
    assert events == []


def test_seat_dropin_is_synchronous_non_privileged_and_bounded():
    parsed = configparser.ConfigParser()
    parsed.read_string(example("ini"))
    assert parsed.sections() == ["Service"]
    assert dict(parsed["Service"]) == {
        "execstop": "/usr/local/libexec/sdsctl-browser-seat-stop", "timeoutstopsec": "45s",
    }
    text = DOC.read_text()
    assert "does not veto compositor termination" in text
    assert "full user-manager restart, host reboot and power cuts were not tested" in text
    assert "blank-password keyring" in text


def test_documented_helper_shell_syntax(tmp_path):
    script = tmp_path / "stop.sh"
    script.write_text(example("sh"))
    for shell in filter(None, (shutil.which("sh"), shutil.which("bash"))):
        result = subprocess.run([shell, "-n", str(script)], capture_output=True, timeout=5)
        assert result.returncode == 0, result.stderr


def test_seat_dropin_with_real_systemd_parser(tmp_path):
    analyze = shutil.which("systemd-analyze")
    if analyze is None:
        pytest.skip("systemd parser unavailable")
    helper = tmp_path / "stop.sh"
    helper.write_text(example("sh"))
    helper.chmod(0o700)
    unit = tmp_path / "sdsctl-doc-seat.service"
    unit.write_text("[Unit]\nDescription=Offline documentation parser fixture\n"
                    "[Service]\nType=simple\nExecStart=/bin/true\n")
    dropin = tmp_path / "sdsctl-doc-seat.service.d"
    dropin.mkdir()
    dropin_file = dropin / "30-browser-first.conf"
    dropin_file.write_text(example("ini").replace(
        "/usr/local/libexec/sdsctl-browser-seat-stop", str(helper),
    ))
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    result = subprocess.run(
        [analyze, "--user", "verify", str(unit)], capture_output=True, timeout=10,
        env={**os.environ, "XDG_RUNTIME_DIR": str(runtime)},
    )
    assert result.returncode == 0, result.stderr.decode()
    # Prove the parser read the drop-in rather than validating only the base unit.
    dropin_file.write_text(example("ini").replace(
        "/usr/local/libexec/sdsctl-browser-seat-stop", str(tmp_path / "missing-helper"),
    ))
    invalid = subprocess.run(
        [analyze, "--user", "verify", str(unit)], capture_output=True, timeout=10,
        env={**os.environ, "XDG_RUNTIME_DIR": str(runtime)},
    )
    assert invalid.returncode != 0
    assert b"missing-helper" in invalid.stderr
