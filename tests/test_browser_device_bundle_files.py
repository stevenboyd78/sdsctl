"""Canonical file evidence is never evidence of a usable current native ledger."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
from contextlib import closing
from dataclasses import replace

import pytest

from sds200 import browser_device_bundle as bundle
from sds200 import browser_device_profile as profiles
from sds200 import browser_device_registration as registration
from sds200.browser_device_recovery import BrowserDeviceRecovery, ExchangeFailure
from sds200.exceptions import ConfigurationError
from tests.test_browser_device_bundle import profile as profile
from tests.test_browser_device_bundle import public_key as public_key
from tests.test_browser_device_native import certificates as certificates
from tests.test_browser_device_profile import CREDENTIAL, private, snapshot
from tests.test_browser_device_registration import register
from tests.test_browser_device_registration import source as source

pytestmark = pytest.mark.skipif(sys.platform != "linux" or os.geteuid() == 0,
                               reason="Non-root Linux canonical private bundle inputs")


def files(source, profile, public_key):
    return registration._canonical_bundle_files(source, profile, public_key)


@pytest.mark.parametrize("mode", ["fresh", "claimed", "paused", "resumed"])
def test_file_evidence_matches_strict_validation_but_never_opens_ledger(
        source, profile, public_key, mode, monkeypatch):
    initial = profiles.inspect_browser_profile(profile)
    state = BrowserDeviceRecovery(profile / "recovery.sqlite", initial.identity)
    if mode == "claimed":
        state.claim_browser()
    elif mode == "paused":
        state.suspend()
    elif mode == "resumed":
        state.resume(state.suspend().revision)
    strict = registration._validated_bundle(source, profile, public_key, fresh=False)
    before = snapshot(profile), snapshot(source), snapshot(source / "extension")
    monkeypatch.setattr(BrowserDeviceRecovery, "inspect",
                        lambda *_: pytest.fail("Current ledger inspection from file evidence"))
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: pytest.fail("Unexpected SQLite open"))
    result, manifest, trust = files(source, profile, public_key)
    assert (result, manifest) == strict
    assert trust == hashlib.sha256((profile / "ca.pem").read_bytes()).hexdigest()
    assert CREDENTIAL not in repr((result, manifest, trust))
    assert (snapshot(profile), snapshot(source), snapshot(source / "extension")) == before


@pytest.mark.parametrize("damage", ["schema3", "schema999", "corrupt", "missing"])
def test_unusable_current_ledger_does_not_invalidate_bytes_or_enable_normal_apis(
        tmp_path, source, profile, public_key, damage):
    registered = register(tmp_path, source, profile, public_key)
    original = files(source, profile, public_key)
    ledger = profile / "recovery.sqlite"
    if damage.startswith("schema"):
        with closing(sqlite3.connect(ledger)) as db:
            db.execute("PRAGMA user_version=" + damage.removeprefix("schema"))
    elif damage == "corrupt":
        ledger.write_bytes(b"unusable retained state")
    else:
        ledger.rename(profile / "retained-ledger")
    before = snapshot(profile), snapshot(source), snapshot(tmp_path / "dedicated browser")
    assert files(source, profile, public_key) == original
    assert original[0] == registered
    for fresh in (True, False):
        with pytest.raises(profiles.BrowserProfileError):
            registration._validated_bundle(source, profile, public_key, fresh=fresh)
    with pytest.raises(profiles.BrowserProfileError):
        profiles.inspect_browser_profile(profile)
    with pytest.raises(registration.BrowserRegistrationError):
        registration.inspect_browser_registration(tmp_path / "dedicated browser",
            bundle=source, profile=profile, public_key=public_key)
    with pytest.raises(registration.BrowserRegistrationError):
        register(tmp_path, source, profile, public_key, name="must-not-register")
    with pytest.raises(bundle.BrowserBundleError):
        bundle.create_browser_bundle(tmp_path / "must-not-prepare",
                                     profile=profile, public_key=public_key)
    assert not (tmp_path / "must-not-register").exists()
    assert not (tmp_path / "must-not-prepare").exists()
    assert (snapshot(profile), snapshot(source), snapshot(tmp_path / "dedicated browser")) == before


@pytest.mark.parametrize("name", ["client.json", "device.secret", "ca.pem", "public-key"])
@pytest.mark.parametrize("damage", ["missing", "mode", "parent", "symlink", "hardlink", "fifo",
                                    "directory", "empty", "oversize"])
def test_file_reader_keeps_fixed_private_input_checks(
        tmp_path, source, profile, public_key, name, damage):
    path = public_key if name == "public-key" else profile / name
    if damage == "missing":
        path.rename(path.with_name("retained-input"))
    elif damage == "mode":
        path.chmod(0o644)
    elif damage == "parent":
        path.parent.chmod(0o755)
    elif damage == "hardlink":
        os.link(path, tmp_path / "extra-input-link")
    elif damage in {"symlink", "fifo", "directory"}:
        retained = path.with_name("retained-input")
        path.rename(retained)
        if damage == "symlink":
            path.symlink_to(retained)
        elif damage == "fifo":
            os.mkfifo(path, 0o600)
        else:
            path.mkdir(mode=0o700)
    else:
        path.write_bytes(b"" if damage == "empty" else b"x" * (128 * 1024 + 1))
    # Do not read FIFO test paths in the state snapshot.
    attributes = ("st_dev", "st_ino", "st_mode", "st_uid", "st_nlink", "st_size",
                  "st_mtime_ns", "st_ctime_ns")
    def metadata(p):
        info = p.lstat()
        return tuple(getattr(info, key) for key in attributes)
    before = [(p, metadata(p)) for p in (path, path.parent) if p.exists()]
    with pytest.raises((ValueError, RuntimeError, OSError, ConfigurationError, ExchangeFailure)):
        files(source, profile, public_key)
    assert [(p, metadata(p)) for p, _ in before] == before


@pytest.mark.parametrize("name,body", [
    ("client.json", b'{"version":1,"version":1}'),
    ("client.json", b'[]'),
    ("device.secret", b"operator-password"),
    ("device.secret", CREDENTIAL.encode() + b"\n\n"),
    ("device.secret", b"sdsctl-browser-session-v1." + b"c" * 64),
    ("ca.pem", b"-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n"),
    ("ca.pem", b"-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----\n"),
    ("public-key", b"-----BEGIN PUBLIC KEY-----\nAAAA\n-----END PUBLIC KEY-----\n"),
])
def test_file_reader_validates_contents_not_just_hashes(
        source, profile, public_key, name, body):
    path = public_key if name == "public-key" else profile / name
    private(path, body)
    before = snapshot(profile), snapshot(source)
    with pytest.raises((ValueError, RuntimeError, OSError, ConfigurationError, ExchangeFailure)):
        files(source, profile, public_key)
    assert (snapshot(profile), snapshot(source)) == before


@pytest.mark.parametrize("field,value", [
    ("device_id", "another-display"),
    ("origin", "https://192.168.1.20:8443"),
    ("extension_origin", "chrome-extension://" + "a" * 32 + "/"),
])
def test_valid_but_different_identity_cannot_reuse_bundle(
        source, profile, public_key, field, value):
    document = json.loads((profile / "client.json").read_bytes())
    assert document[field] != value
    private(profile / "client.json", json.dumps({**document, field: value}))
    with pytest.raises(ValueError):
        files(source, profile, public_key)


@pytest.mark.parametrize("target", ["bundle", "profile", "public-key"])
def test_same_bytes_at_different_paths_are_not_interchangeable(
        source, profile, public_key, target):
    paths = {"bundle": source, "profile": profile, "public-key": public_key}
    path = paths[target]
    moved = path.with_name(path.name + "-moved")
    path.rename(moved)
    paths[target] = moved
    with pytest.raises(ValueError):
        files(paths["bundle"], paths["profile"], paths["public-key"])


@pytest.mark.parametrize("changed", ["identity", "trust_sha256"])
def test_normal_validation_still_binds_live_inspection_to_file_inputs(
        source, profile, public_key, monkeypatch, changed):
    inspected = profiles.inspect_browser_profile(profile)
    monkeypatch.setattr(registration, "inspect_browser_profile",
                        lambda _: replace(inspected, **{changed: "e" * 64}))
    assert files(source, profile, public_key)[0].identity == inspected.identity
    with pytest.raises(ValueError):
        registration._validated_bundle(source, profile, public_key, fresh=False)
