"""Offline namespace path construction for local and hosted CI interpreters."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_app_native_preflight as native


@pytest.mark.parametrize(
    "prefix", ["/opt/hostedtoolcache/Python/3.12.12/x64", "/opt/hostedtoolcache/Python/3.14.0/x64"]
)
def test_hosted_interpreter_base_is_explicit_read_only_not_all_opt(monkeypatch, prefix):
    monkeypatch.setattr(native.sys, "base_prefix", prefix)
    staged = SimpleNamespace(
        python=Path("/tmp/fixture/python/bin/python"),
        layout=SimpleNamespace(native=Path("/tmp/fixture/native")),
    )
    mapped = SimpleNamespace(
        bwrap="bwrap", data=Path("/tmp/fixture/data"), media=Path("/tmp/fixture/media")
    )
    command = native.namespace_command(None, mapped, staged)
    binds = [command[i + 1 : i + 3] for i, value in enumerate(command) if value == "--ro-bind"]
    assert [prefix, prefix] in binds
    assert ["/opt", "/opt"] not in binds
    assert ["/tmp/fixture/python", "/usr/local"] in binds
    assert ["/tmp/fixture/native", "/opt/sdsctl-supplemental-recording"] in binds
    assert "--bind" not in command and "--unshare-net" in command
    assert command.index("--dir") < command.index(prefix)
    assert command[-5:] == ["--remount-ro", "/", "--chdir", "/", "--"]


@pytest.mark.parametrize("prefix", ["/usr", "/home/runner/python", "/tmp/fixture/base"])
def test_already_visible_interpreter_does_not_add_overlapping_alias(monkeypatch, prefix):
    monkeypatch.setattr(native.sys, "base_prefix", prefix)
    staged = SimpleNamespace(
        python=Path("/tmp/fixture/python/bin/python"),
        layout=SimpleNamespace(native=Path("/tmp/fixture/native")),
    )
    mapped = SimpleNamespace(
        bwrap="bwrap", data=Path("/tmp/fixture/data"), media=Path("/tmp/fixture/media")
    )
    command = native.namespace_command(None, mapped, staged)
    binds = [command[i + 1 : i + 3] for i, value in enumerate(command) if value == "--ro-bind"]
    assert binds.count([prefix, prefix]) == (1 if prefix == "/usr" else 0)
