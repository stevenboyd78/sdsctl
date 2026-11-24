from __future__ import annotations

import copy
import hashlib
import json
import shutil
import urllib.request
from collections.abc import Mapping
from pathlib import Path

import pytest

from scripts import sync_theme_fonts

ROOT = Path(__file__).resolve().parents[1]


def _font_fixture(tmp_path: Path) -> Path:
    manifest = json.loads((ROOT / sync_theme_fonts.MANIFEST_PATH).read_text())
    for entry in manifest["files"]:
        source = ROOT / entry["path"]
        target = tmp_path / entry["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    target_manifest = tmp_path / sync_theme_fonts.MANIFEST_PATH
    target_manifest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / sync_theme_fonts.MANIFEST_PATH, target_manifest)
    return tmp_path


def _payload(source: str) -> bytes:
    if source.endswith("/OFL.txt"):
        return sync_theme_fonts.OFL_MARKER + b"\n" + source.encode()
    return b"\x00\x01\x00\x00" + source.encode()


def _snapshot(root: Path) -> dict[str, bytes]:
    manifest = sync_theme_fonts.load_manifest(root)
    paths = [
        sync_theme_fonts.MANIFEST_PATH,
        *(Path(entry["path"]) for entry in manifest["files"]),
    ]
    return {str(path): (root / path).read_bytes() for path in paths}


def test_check_verifies_the_packaged_pinned_font_set(capsys: pytest.CaptureFixture[str]) -> None:
    assert sync_theme_fonts.main(["--check", "--root", str(ROOT)]) == 0
    output = capsys.readouterr()
    assert "Verified 20 pinned Google Fonts assets" in output.out
    assert output.err == ""


def test_check_mode_never_uses_the_network(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden_fetch(source: str, timeout: float) -> bytes:
        raise AssertionError((source, timeout))

    monkeypatch.setattr(sync_theme_fonts, "fetch_asset", forbidden_fetch)
    assert sync_theme_fonts.main(["--root", str(ROOT)]) == 0
    assert "Verified 20 pinned Google Fonts assets" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        (lambda manifest: manifest["files"][0].update(path="../escape.ttf"), "unsafe"),
        (
            lambda manifest: manifest["files"][0].update(
                source=manifest["files"][0]["source"].replace(
                    "raw.githubusercontent.com", "example.invalid"
                )
            ),
            "raw URL",
        ),
        (
            lambda manifest: manifest["files"][0].update(
                source=manifest["files"][0]["source"].replace(
                    manifest["googleFontsRevision"], "main"
                )
            ),
            "pinned",
        ),
        (
            lambda manifest: manifest["files"][0].update(
                source=manifest["files"][0]["source"].replace("/ofl/", "/ofl/%2e%2e/")
            ),
            "canonical",
        ),
        (
            lambda manifest: manifest["files"][0].update(
                source=manifest["files"][0]["source"] + "?download=1"
            ),
            "raw URL",
        ),
        (
            lambda manifest: manifest["files"][0].update(
                source=manifest["files"][0]["source"].replace("https://", "https://maintainer@")
            ),
            "raw URL",
        ),
    ),
)
def test_manifest_validation_rejects_unsafe_provenance(mutation, message: str) -> None:
    manifest = copy.deepcopy(sync_theme_fonts.load_manifest(ROOT))
    mutation(manifest)
    with pytest.raises(sync_theme_fonts.FontSyncError, match=message):
        sync_theme_fonts.validate_manifest(manifest)


def test_manifest_validation_rejects_incomplete_and_duplicate_entries() -> None:
    incomplete = copy.deepcopy(sync_theme_fonts.load_manifest(ROOT))
    incomplete.pop("files")
    with pytest.raises(sync_theme_fonts.FontSyncError, match="missing"):
        sync_theme_fonts.validate_manifest(incomplete)

    duplicated = copy.deepcopy(sync_theme_fonts.load_manifest(ROOT))
    duplicated["files"].append(copy.deepcopy(duplicated["files"][0]))
    with pytest.raises(sync_theme_fonts.FontSyncError, match="unique"):
        sync_theme_fonts.validate_manifest(duplicated)


@pytest.mark.parametrize(
    ("path", "payload", "message"),
    (
        ("src/sds200/web_assets/fonts/demo.ttf", b"not-a-font", "sfnt"),
        ("src/sds200/web_assets/fonts/demo.txt", b"not-a-license", "OFL"),
    ),
)
def test_payload_validation_rejects_malformed_assets(
    path: str, payload: bytes, message: str
) -> None:
    with pytest.raises(sync_theme_fonts.FontSyncError, match=message):
        sync_theme_fonts._validate_payload(path, payload)


def test_payload_validation_rejects_oversized_assets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sync_theme_fonts, "MAX_ASSET_BYTES", 4)
    with pytest.raises(sync_theme_fonts.FontSyncError, match="exceeds"):
        sync_theme_fonts._validate_payload("src/sds200/web_assets/fonts/demo.ttf", b"12345")


def test_redirect_handler_rejects_all_redirects() -> None:
    request = urllib.request.Request("https://raw.githubusercontent.com/google/fonts/source")
    with pytest.raises(sync_theme_fonts.FontSyncError, match="redirects are not allowed"):
        sync_theme_fonts._RejectRedirects().redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "https://raw.githubusercontent.com/google/fonts/redirected",
        )


def test_synchronize_stages_and_rehashes_every_declared_asset(tmp_path: Path) -> None:
    root = _font_fixture(tmp_path)
    revision = "a" * 40
    seen: list[str] = []

    def fetcher(source: str, timeout: float) -> bytes:
        assert timeout == 7.0
        seen.append(source)
        return _payload(source)

    count = sync_theme_fonts.synchronize_assets(root, revision, timeout=7.0, fetcher=fetcher)
    manifest = sync_theme_fonts.load_manifest(root)

    assert count == 20
    assert manifest["googleFontsRevision"] == revision
    assert len(seen) == 20
    for entry in manifest["files"]:
        content = (root / entry["path"]).read_bytes()
        assert revision in entry["source"]
        assert len(content) == entry["bytes"]
        assert hashlib.sha256(content).hexdigest() == entry["sha256"]
    assert sync_theme_fonts.verify_local_assets(root, manifest) == 20


def test_download_failure_leaves_the_existing_set_unchanged(tmp_path: Path) -> None:
    root = _font_fixture(tmp_path)
    before = _snapshot(root)
    calls = 0

    def fetcher(source: str, timeout: float) -> bytes:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise sync_theme_fonts.FontSyncError("fixture download failed")
        return _payload(source)

    with pytest.raises(sync_theme_fonts.FontSyncError, match="fixture download failed"):
        sync_theme_fonts.synchronize_assets(root, "b" * 40, fetcher=fetcher)

    assert _snapshot(root) == before


def test_commit_failure_rolls_back_every_replaced_asset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _font_fixture(tmp_path)
    before = _snapshot(root)
    real_atomic_write = sync_theme_fonts._atomic_write
    calls = 0

    def flaky_atomic_write(path: Path, content: bytes) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("fixture replace failed")
        real_atomic_write(path, content)

    monkeypatch.setattr(sync_theme_fonts, "_atomic_write", flaky_atomic_write)
    with pytest.raises(sync_theme_fonts.FontSyncError, match="transaction failed"):
        sync_theme_fonts.synchronize_assets(
            root, "c" * 40, fetcher=lambda source, timeout: _payload(source)
        )

    assert _snapshot(root) == before


def test_post_commit_verification_failure_rolls_back_the_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _font_fixture(tmp_path)
    before = _snapshot(root)
    real_verify = sync_theme_fonts.verify_local_assets
    calls = 0

    def flaky_verify(verify_root: Path, manifest: Mapping[str, object] | None = None) -> int:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise sync_theme_fonts.FontSyncError("fixture verification failed")
        return real_verify(verify_root, manifest)

    monkeypatch.setattr(sync_theme_fonts, "verify_local_assets", flaky_verify)
    with pytest.raises(sync_theme_fonts.FontSyncError, match="transaction failed"):
        sync_theme_fonts.synchronize_assets(
            root, "d" * 40, fetcher=lambda source, timeout: _payload(source)
        )

    assert _snapshot(root) == before


def test_local_tampering_fails_check_mode(tmp_path: Path) -> None:
    root = _font_fixture(tmp_path)
    manifest = sync_theme_fonts.load_manifest(root)
    target = root / manifest["files"][0]["path"]
    target.write_bytes(target.read_bytes() + b"tampered")

    with pytest.raises(sync_theme_fonts.FontSyncError, match="byte count mismatch"):
        sync_theme_fonts.verify_local_assets(root)
