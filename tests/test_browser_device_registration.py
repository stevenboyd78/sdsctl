from __future__ import annotations

import hashlib
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from sds200 import browser_device_registration as registration
from sds200 import cli
from sds200.browser_device_bundle import NATIVE_HOST
from sds200.browser_device_native import load_browser_native_configuration
from sds200.browser_device_recovery import BrowserDeviceRecovery
from tests.test_browser_device_bundle import create
from tests.test_browser_device_bundle import profile as profile
from tests.test_browser_device_bundle import public_key as public_key
from tests.test_browser_device_native import certificates as certificates
from tests.test_browser_device_profile import CREDENTIAL, private, snapshot

pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or os.geteuid() == 0, reason="Non-root Linux browser registration",
)


@pytest.fixture
def source(tmp_path, public_key, profile):
    return create(tmp_path, public_key, profile)


def register(tmp_path, source, profile, public_key, name="dedicated browser"):
    return registration.register_browser_directory(
        tmp_path / name, bundle=source, profile=profile, public_key=public_key,
    )


def test_registration_only_writes_new_private_directory(tmp_path, source, profile, public_key):
    before = snapshot(profile), snapshot(source), snapshot(source / "extension")
    result = register(tmp_path, source, profile, public_key)
    assert (snapshot(profile), snapshot(source), snapshot(source / "extension")) == before
    root = tmp_path / "dedicated browser"
    host = root / "NativeMessagingHosts" / (NATIVE_HOST + ".json")
    assert host.read_bytes() == (source / (NATIVE_HOST + ".json")).read_bytes()
    assert {str(p.relative_to(root)) for p in root.rglob("*")} == {
        "NativeMessagingHosts", "NativeMessagingHosts/" + NATIVE_HOST + ".json",
        ".sdsctl-browser-registration.json",
    }
    for path in [root, *root.rglob("*")]:
        assert path.stat().st_mode & 0o777 == (0o700 if path.is_dir() else 0o600)
        if path.is_file():
            assert CREDENTIAL.encode() not in path.read_bytes()
    receipt = json.loads((root / ".sdsctl-browser-registration.json").read_bytes())
    assert receipt["setup_url"] == result.setup_url
    assert receipt["identity"] == result.identity
    assert result.setup_url.endswith("/setup.html")
    assert json.loads(host.read_bytes())["allowed_origins"] == [
        f"chrome-extension://{result.extension_id}/",
    ]


@pytest.mark.parametrize("change", [
    "asset", "forged-receipt", "receipt", "extra", "extra-extension", "missing",
    "symlink", "hardlink", "fifo", "mode", "parent", "runtime", "profile-identity",
])
def test_edited_or_unsafe_bundle_is_rejected_before_creation(
    tmp_path, source, profile, public_key, change, monkeypatch,
):
    asset = source / "extension" / "worker.mjs"
    if change in {"asset", "forged-receipt"}:
        private(asset, b"// arbitrary caller code")
        if change == "forged-receipt":
            receipt = json.loads((source / "bundle.json").read_bytes())
            digest = hashlib.sha256(asset.read_bytes()).hexdigest()
            receipt["files"]["extension/worker.mjs"] = digest
            private(source / "bundle.json", json.dumps(receipt, sort_keys=True, indent=2) + "\n")
    elif change == "receipt":
        private(source / "bundle.json", b"private-do-not-echo")
    elif change == "extra":
        private(source / "unreviewed", b"extra")
    elif change == "extra-extension":
        private(source / "extension" / "unreviewed.js", b"extra")
    elif change == "missing":
        asset.unlink()
    elif change == "symlink":
        asset.rename(source / "original")
        asset.symlink_to(source / "original")
    elif change == "hardlink":
        os.link(asset, tmp_path / "alias")
    elif change == "fifo":
        asset.unlink()
        os.mkfifo(asset, 0o600)
    elif change == "mode":
        asset.chmod(0o644)
    elif change == "parent":
        asset.parent.chmod(0o755)
    elif change == "runtime":
        monkeypatch.setattr(sys, "executable", "/different/runtime/python")
    else:
        document = json.loads((profile / "client.json").read_bytes())
        document["device_id"] = "different"
        private(profile / "client.json", json.dumps(document))
    with pytest.raises(registration.BrowserRegistrationError, match="invalid or unsafe") as error:
        register(tmp_path, source, profile, public_key)
    assert "private-do-not-echo" not in str(error.value)
    assert not (tmp_path / "dedicated browser").exists()


@pytest.mark.parametrize("used", ["claimed", "paused", "resumed"])
def test_used_profile_cannot_register(tmp_path, source, profile, public_key, used):
    config = load_browser_native_configuration(profile)
    state = BrowserDeviceRecovery(profile / "recovery.sqlite", config.identity)
    if used == "claimed":
        state.claim_browser()
    elif used == "paused":
        state.suspend()
    else:
        state.resume(state.suspend().revision)
    before = snapshot(profile)
    with pytest.raises(registration.BrowserRegistrationError):
        register(tmp_path, source, profile, public_key)
    assert snapshot(profile) == before
    assert not (tmp_path / "dedicated browser").exists()


@pytest.mark.parametrize("kind", ["empty", "populated", "file", "symlink", "registered"])
def test_existing_destination_is_never_reused(tmp_path, source, profile, public_key, kind):
    root = tmp_path / "dedicated browser"
    if kind == "registered":
        register(tmp_path, source, profile, public_key)
    elif kind == "symlink":
        root.symlink_to(tmp_path / "absent", target_is_directory=True)
    elif kind == "file":
        private(root, b"keep")
    else:
        root.mkdir(mode=0o700)
        if kind == "populated":
            private(root / "keep", b"keep")
    with pytest.raises(registration.BrowserRegistrationError):
        register(tmp_path, source, profile, public_key)
    assert root.exists() or root.is_symlink()
    if kind == "populated":
        assert (root / "keep").read_bytes() == b"keep"


def test_partial_registration_retained_and_cannot_retry(
    tmp_path, source, profile, public_key, monkeypatch,
):
    write = registration._write

    def interrupted(fd, name, value):
        if name == ".sdsctl-browser-registration.json":
            raise OSError("private-do-not-echo")
        write(fd, name, value)

    monkeypatch.setattr(registration, "_write", interrupted)
    with pytest.raises(registration.BrowserRegistrationError, match="Retain any created directory"):
        register(tmp_path, source, profile, public_key)
    root = tmp_path / "dedicated browser"
    assert (root / "NativeMessagingHosts" / (NATIVE_HOST + ".json")).exists()
    assert not (root / ".sdsctl-browser-registration.json").exists()
    with pytest.raises(registration.BrowserRegistrationError, match="already exists"):
        register(tmp_path, source, profile, public_key)


def test_concurrent_registration_has_one_winner(tmp_path, source, profile, public_key):
    def attempt(_):
        try:
            register(tmp_path, source, profile, public_key)
            return True
        except registration.BrowserRegistrationError:
            return False

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(attempt, range(8))) == 1


def test_cli_requires_opt_in_and_only_prints_public_result(
    tmp_path, source, profile, public_key, capsys,
):
    args = ["browser-device-register", "--directory", str(tmp_path / "dedicated browser"),
            "--bundle", str(source), "--profile", str(profile), "--public-key", str(public_key)]
    with pytest.raises(SystemExit) as error:
        cli.main(args)
    assert error.value.code == 2
    assert not (tmp_path / "dedicated browser").exists()
    assert cli.main([*args, "--experimental"]) == 0
    output = capsys.readouterr()
    assert CREDENTIAL not in output.out + output.err
    assert "/setup.html" in output.out
    assert cli.main([*args, "--experimental"]) == 78


@pytest.mark.parametrize("used", ["fresh", "claimed", "paused", "resumed"])
def test_inspection_accepts_existing_state_without_mutation(
    tmp_path, source, profile, public_key, used,
):
    result = register(tmp_path, source, profile, public_key)
    config = load_browser_native_configuration(profile)
    state = BrowserDeviceRecovery(profile / "recovery.sqlite", config.identity)
    if used == "claimed":
        state.claim_browser()
    elif used == "paused":
        state.suspend()
    elif used == "resumed":
        state.resume(state.suspend().revision)
    root = tmp_path / "dedicated browser"
    # Internal Chromium files are deliberately not parsed or reset.
    private(root / "Local State", b"opaque private browser state")
    before = snapshot(profile), snapshot(source), snapshot(root)
    assert registration.inspect_browser_registration(
        root, bundle=source, profile=profile, public_key=public_key,
    ) == result
    assert (snapshot(profile), snapshot(source), snapshot(root)) == before


@pytest.mark.parametrize("change", ["receipt", "manifest", "extra", "missing", "mode", "link"])
def test_inspection_rejects_changed_registration_without_repair(
    tmp_path, source, profile, public_key, change,
):
    register(tmp_path, source, profile, public_key)
    root = tmp_path / "dedicated browser"
    receipt = root / ".sdsctl-browser-registration.json"
    manifest = root / "NativeMessagingHosts" / (NATIVE_HOST + ".json")
    if change in {"receipt", "manifest"}:
        private(receipt if change == "receipt" else manifest, b"private-do-not-echo")
    elif change == "extra":
        private(manifest.parent / "other.json", b"private-do-not-echo")
    elif change == "missing":
        receipt.unlink()
    elif change == "mode":
        root.chmod(0o755)
    else:
        receipt.rename(root / "original")
        receipt.symlink_to(root / "original")
    before = snapshot(profile), snapshot(root)
    with pytest.raises(registration.BrowserRegistrationError) as error:
        registration.inspect_browser_registration(
            root, bundle=source, profile=profile, public_key=public_key,
        )
    assert "private-do-not-echo" not in str(error.value)
    assert (snapshot(profile), snapshot(root)) == before
