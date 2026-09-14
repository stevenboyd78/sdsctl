"""Pure public-key conversion reuse never caches file or native authority."""
from __future__ import annotations

import os
import subprocess
from dataclasses import FrozenInstanceError

import pytest

from sds200 import browser_device_bundle as bundle
from tests.test_browser_device_bundle import public_key as public_key
from tests.test_browser_device_native import certificates as certificates
from tests.test_browser_device_profile import private


@pytest.fixture(autouse=True)
def isolated_conversion_cache():
    cached = getattr(bundle, "_validate_extension_der", None)
    if cached is not None:
        cached.cache_clear()
    yield
    if cached is not None:
        cached.cache_clear()


def test_same_public_bytes_validate_once_but_return_separate_values(public_key, monkeypatch):
    original = subprocess.run
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return original(args, **kwargs)

    monkeypatch.setattr(bundle.subprocess, "run", run)
    first = bundle.browser_extension_identity(public_key)
    second = bundle.browser_extension_identity(public_key)
    assert first == second and first is not second
    assert len(calls) == 2  # one pubcheck and one canonical DER conversion
    assert all(kwargs["timeout"] == 5 for _, kwargs in calls)
    with pytest.raises(FrozenInstanceError):
        first.extension_id = "caller-mutated-result"
    assert bundle.browser_extension_identity(public_key) == second
    assert len(calls) == 2


@pytest.mark.parametrize("change", [
    "mode", "parent", "symlink", "hardlink", "fifo", "removed", "bad-pem",
])
def test_warm_conversion_still_revalidates_file(public_key, tmp_path, change):
    bundle.browser_extension_identity(public_key)
    if change == "mode":
        public_key.chmod(0o644)
    elif change == "parent":
        tmp_path.chmod(0o755)
    elif change == "symlink":
        target = tmp_path / "moved.pem"
        public_key.rename(target)
        public_key.symlink_to(target)
    elif change == "hardlink":
        os.link(public_key, tmp_path / "duplicate.pem")
    elif change == "fifo":
        public_key.unlink()
        os.mkfifo(public_key, 0o600)
    elif change == "removed":
        public_key.unlink()
    else:
        private(public_key, b"not-a-public-key-do-not-echo")
    with pytest.raises(bundle.BrowserBundleError) as failure:
        bundle.browser_extension_identity(public_key)
    assert "do-not-echo" not in str(failure.value)


def test_new_key_bytes_require_fresh_validation(public_key, certificates, monkeypatch):
    replacement = subprocess.run(
        ["openssl", "x509", "-pubkey", "-noout", "-in", str(certificates[1][0])],
        capture_output=True, check=True,
    ).stdout
    original = subprocess.run
    calls = []

    def run(args, **kwargs):
        calls.append(kwargs["input"])
        return original(args, **kwargs)

    monkeypatch.setattr(bundle.subprocess, "run", run)
    first = bundle.browser_extension_identity(public_key)
    private(public_key, replacement)
    second = bundle.browser_extension_identity(public_key)
    assert first != second
    assert len(calls) == 4 and calls[0] != calls[2]


def test_warm_conversion_does_not_hide_missing_openssl(public_key, monkeypatch):
    bundle.browser_extension_identity(public_key)
    monkeypatch.setattr(bundle.shutil, "which", lambda *args, **kwargs: None)
    with pytest.raises(bundle.BrowserBundleError):
        bundle.browser_extension_identity(public_key)


def test_failed_validation_is_not_cached(public_key, monkeypatch):
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 1, b"do-not-echo", b"do-not-echo")

    monkeypatch.setattr(bundle.subprocess, "run", run)
    for _ in range(2):
        with pytest.raises(bundle.BrowserBundleError) as failure:
            bundle.browser_extension_identity(public_key)
        assert "do-not-echo" not in str(failure.value)
    assert len(calls) == 2


def test_conversion_cache_is_bounded_and_executable_selected(monkeypatch):
    calls = []

    def run(args, **kwargs):
        calls.append(args[0])
        return subprocess.CompletedProcess(args, 0, kwargs["input"], b"")

    monkeypatch.setattr(bundle.subprocess, "run", run)
    for index in range(16):
        bundle._validate_extension_der(bytes([index]), "/one/openssl")
    assert bundle._validate_extension_der.cache_info().currsize == 8
    bundle._validate_extension_der(bytes([15]), "/two/openssl")
    assert calls[-2:] == ["/two/openssl", "/two/openssl"]
