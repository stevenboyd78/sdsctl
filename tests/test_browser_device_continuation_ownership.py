"""Portable ownership contracts: real advisory locks, simulated browser ancestry.

No browser is launched here. Full retained-chain cases live in the namespace suite.
"""
from __future__ import annotations

import fcntl
import os
import sys
from contextlib import contextmanager
from dataclasses import replace

import pytest

from sds200 import browser_device_continuation_ownership as ownership
from sds200.browser_device_handoff import BrowserRecoveryHandoff
from sds200.browser_device_profile_access import BrowserProfileAccessError, browser_profile_access
from sds200.browser_device_startup import _launch_lock
from sds200.browser_device_worker import BrowserWorkerSelection
from tests.test_browser_device_bundle import profile as profile
from tests.test_browser_device_bundle import public_key as public_key
from tests.test_browser_device_native import certificates as certificates
from tests.test_browser_device_profile import CREDENTIAL, private
from tests.test_browser_device_registration import source as source
from tests.test_browser_device_resume_workflow import lab as lab
from tests.test_browser_device_resume_workflow import snap
from tests.test_browser_device_startup import inputs as inputs

pytestmark = pytest.mark.skipif(sys.platform != "linux" or os.geteuid() == 0,
                               reason="Non-root Linux scoped history ownership")
ERROR = ownership.BrowserContinuationOwnershipError


@pytest.fixture
def handoff(lab, tmp_path):
    root = tmp_path / "handoff"
    root.mkdir(mode=0o700)
    return BrowserRecoveryHandoff(root, **lab.args, recovery_bundle=tmp_path / "recovery",
                                  operation_id="a" * 64, browser_intent="b" * 64,
                                  supervised=True)


def selected(lab):
    return BrowserWorkerSelection(lab.args["bundle"], lab.args["public_key"])


@pytest.fixture(params=["stopped", "worker"])
def access(lab, handoff, monkeypatch, request):
    # Only ancestor discovery is simulated. All directories, permissions, inode
    # checks and nested/competing flock calls use actual temporary OS resources.
    @contextmanager
    def scope():
        if request.param == "stopped":
            with ownership._stopped_history_ownership(handoff) as owner:
                yield owner
        else:
            monkeypatch.setattr(ownership, "_browser_directory",
                                lambda *_: lab.args["directory"])
            with (_launch_lock(lab.args["directory"], create=False),
                  ownership._worker_history_ownership(handoff, configuration=lab.configuration,
                                                      selection=selected(lab)) as owner):
                yield owner

    return scope


def test_scope_is_read_only_private_and_expires(lab, handoff, access):
    before = snap(lab)
    with access() as owner:
        binding = owner.binding(handoff)
        assert binding == handoff._session._browser_binding(stopped=True)
        binding[0][0] += 1  # A returned copy cannot change the pinned identity.
        assert owner.binding(handoff) != binding
        for key in ("profile", "archives"):
            with (pytest.raises(BrowserProfileAccessError),
                  browser_profile_access(lab.args[key], exclusive=True)):
                pytest.fail("Exclusive writer entered owned history scope")
        with pytest.raises(BlockingIOError), _launch_lock(lab.args["directory"], create=False):
            pytest.fail("Competing launcher entered owned history scope")
        assert CREDENTIAL not in repr(owner)
        assert str(lab.args["profile"]) not in repr(owner)
    with pytest.raises(ERROR):
        owner.binding(handoff)
    assert snap(lab) == before


@pytest.mark.parametrize("change", ["wrong-handoff", "pid", "parent", "exception", "binding"])
def test_observed_failure_latches_even_if_original_state_returns(
        lab, handoff, access, monkeypatch, change):
    before = snap(lab)
    with pytest.raises(ERROR), access() as owner:
        original = owner._verify
        with monkeypatch.context() as patch:
            if change in {"pid", "parent"}:
                name = "getpid" if change == "pid" else "getppid"
                value = getattr(os, name)()
                patch.setattr(ownership.os, name, lambda: value + 1)
            elif change == "exception":
                def failure():
                    raise RuntimeError(CREDENTIAL + str(lab.args["profile"]))
                patch.setattr(owner, "_verify", failure)
            elif change == "binding":
                patch.setattr(owner, "_verify", lambda: [[0, 0], [0, 0]])
            with pytest.raises(ERROR) as error:
                owner.binding(object() if change == "wrong-handoff" else handoff)
            assert CREDENTIAL not in str(error.value)
            assert str(lab.args["profile"]) not in str(error.value)
        assert owner._verify == original
        with pytest.raises(ERROR):
            owner.binding(handoff)  # Restoring the fixture cannot revive the scope.
    assert snap(lab) == before


@pytest.mark.parametrize("change", ["root-inode", "lock-inode", "lock-mode", "lock-symlink",
    "profile-inode", "archives-inode", "profile-mode", "archives-mode", "configuration"])
def test_changed_named_state_is_rejected_inside_scope(lab, handoff, access, change):
    with pytest.raises(ERROR), access() as owner:
        if change == "configuration":
            path = lab.args["profile"] / "client.json"
            path.write_bytes(path.read_bytes().replace(b"display", b"other-device"))
        else:
            kind, mutation = change.rsplit("-", 1)
            path = (lab.args["directory"] / ".sdsctl-device-launch.lock" if kind == "lock"
                    else lab.args["directory" if kind == "root" else kind])
            if mutation == "mode":
                path.chmod(0o644 if kind == "lock" else 0o755)
            else:
                retained = path.with_name(path.name + ".retained")
                path.rename(retained)
                if mutation == "symlink":
                    path.symlink_to(retained)
                elif kind == "lock":
                    private(path, b"")
                else:
                    path.mkdir(mode=0o700)
        with pytest.raises(ERROR):
            owner.binding(handoff)
    with pytest.raises(ERROR):
        owner.binding(handoff)


@pytest.mark.parametrize("key", ["profile", "archives"])
def test_private_writer_prevents_either_scope(lab, access, key):
    before = snap(lab)
    with browser_profile_access(lab.args[key], exclusive=True), pytest.raises(ERROR), access():
        pytest.fail("History entered a competing private writer's scope")
    assert snap(lab) == before


@pytest.mark.parametrize("marker", ["SingletonLock", "SingletonSocket", "SingletonCookie"])
def test_stopped_never_adopts_busy_lock_or_singleton(lab, handoff, marker):
    root = lab.args["directory"]
    with (_launch_lock(root, create=False), pytest.raises(ERROR),
          ownership._stopped_history_ownership(handoff)):
        pytest.fail("Stopped reader adopted another owner")
    (root / marker).symlink_to("fictional-browser")
    with pytest.raises(ERROR), ownership._stopped_history_ownership(handoff):
        pytest.fail("Stopped reader ignored Singleton marker")
    assert (root / marker).is_symlink()  # No stale-lock cleanup or repair.


@pytest.mark.parametrize("marker", ["SingletonLock", "SingletonSocket", "SingletonCookie"])
def test_worker_accepts_singletons_only_under_verified_owner(lab, handoff, monkeypatch, marker):
    root = lab.args["directory"]
    monkeypatch.setattr(ownership, "_browser_directory", lambda *_: root)
    with _launch_lock(root, create=False):
        (root / marker).symlink_to("fictional-browser")
        with ownership._worker_history_ownership(handoff, configuration=lab.configuration,
                                                 selection=selected(lab)) as owner:
            assert owner.binding(handoff) == handoff._session._browser_binding(stopped=False)
    with (pytest.raises(ERROR),
          ownership._worker_history_ownership(handoff, configuration=lab.configuration,
                                              selection=selected(lab))):
        pytest.fail("Singleton without launcher owner was accepted")


@pytest.mark.parametrize("change", ["bundle", "public_key", "directory", "normal_bundle",
    "intent", "selection-type", "configuration", "configuration-type", "ancestor"])
def test_worker_rejects_wrong_selection_configuration_and_ancestor(
        lab, handoff, monkeypatch, change):
    config, selection = lab.configuration, selected(lab)
    root = lab.args["directory"]
    monkeypatch.setattr(ownership, "_browser_directory", lambda *_: root)
    if change in {"bundle", "public_key", "directory", "normal_bundle", "intent"}:
        selection = replace(selection, **{change: root if change != "intent" else "f" * 64})
    elif change == "selection-type":
        selection = object()
    elif change == "configuration":
        config = replace(config, device_id="other")
    elif change == "configuration-type":
        config = object()
    else:
        monkeypatch.setattr(ownership, "_browser_directory", lambda *_: root.parent)
    before = snap(lab)
    with (_launch_lock(root, create=False), pytest.raises(ERROR),
          ownership._worker_history_ownership(handoff, configuration=config,
                                              selection=selection)):
        pytest.fail("Wrong native owner was accepted")
    assert snap(lab) == before


def test_real_current_process_without_chromium_ancestor_is_rejected(lab, handoff):
    with (_launch_lock(lab.args["directory"], create=False), pytest.raises(ERROR),
          ownership._worker_history_ownership(handoff, configuration=lab.configuration,
                                              selection=selected(lab))):
        pytest.fail("The pytest process has no selected Chromium ancestor")


@pytest.mark.parametrize("change", ["ancestor", "unlock"])
def test_worker_rechecks_live_owner_and_does_not_repair_it(lab, handoff, monkeypatch, change):
    root = lab.args["directory"]
    monkeypatch.setattr(ownership, "_browser_directory", lambda *_: root)
    with (root / ".sdsctl-device-launch.lock").open("r+b") as locked:
        fcntl.flock(locked, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with (pytest.raises(ERROR),
              ownership._worker_history_ownership(handoff, configuration=lab.configuration,
                                                  selection=selected(lab)) as owner):
            if change == "ancestor":
                monkeypatch.setattr(ownership, "_browser_directory", lambda *_: root.parent)
            else:
                fcntl.flock(locked, fcntl.LOCK_UN)
            with pytest.raises(ERROR):
                owner.binding(handoff)


@pytest.mark.parametrize("invalid", ["type", "unsupervised"])
def test_both_entrypoints_refuse_invalid_handoff(lab, handoff, invalid):
    if invalid == "type":
        handoff = object()
    else:
        handoff._supervised = False
    for scope in (ownership._stopped_history_ownership(handoff),
                  ownership._worker_history_ownership(handoff, configuration=lab.configuration,
                                                      selection=selected(lab))):
        with pytest.raises(ERROR), scope:
            pytest.fail("Invalid historical handoff was accepted")


def test_exception_unwinds_ownership_and_sanitizes(lab, handoff, access):
    with pytest.raises(ERROR) as error, access() as owner:
        raise RuntimeError(CREDENTIAL)
    assert CREDENTIAL not in str(error.value)
    with pytest.raises(ERROR):
        owner.binding(handoff)
    with _launch_lock(lab.args["directory"], create=False):
        pass  # Failure did not strand the launch lock.
    for key in ("profile", "archives"):
        with browser_profile_access(lab.args[key], exclusive=True):
            pass
