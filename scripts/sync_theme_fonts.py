#!/usr/bin/env python3
"""Verify or synchronize the pinned Google Fonts WebUI asset manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit

MANIFEST_PATH = Path("docs/theme-font-sources.json")
FONT_PATH_PREFIX = PurePosixPath("src/sds200/web_assets/fonts")
RAW_HOST = "raw.githubusercontent.com"
REVISION_PATTERN = re.compile(r"[0-9a-f]{40}")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
OFL_MARKER = b"SIL OPEN FONT LICENSE Version 1.1"
MAX_ASSET_BYTES = 16 * 1024 * 1024
Fetcher = Callable[[str, float], bytes]


class FontSyncError(ValueError):
    """Raised when provenance or asset validation fails closed."""


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise FontSyncError(f"redirects are not allowed while fetching {req.full_url!r}")


def _require_revision(value: object, *, field: str = "revision") -> str:
    if not isinstance(value, str) or REVISION_PATTERN.fullmatch(value) is None:
        raise FontSyncError(f"{field} must be an exact 40-character lowercase commit SHA")
    return value


def _require_asset_path(value: object) -> str:
    if not isinstance(value, str):
        raise FontSyncError("font manifest paths must be strings")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise FontSyncError(f"unsafe font asset path: {value!r}")
    if path.parent != FONT_PATH_PREFIX:
        raise FontSyncError(f"font asset escapes the packaged font directory: {value!r}")
    if path.suffix not in {".ttf", ".txt"}:
        raise FontSyncError(f"unsupported font asset type: {value!r}")
    return value


def _require_source(value: object, *, revision: str, asset_path: str) -> str:
    if not isinstance(value, str):
        raise FontSyncError("font manifest sources must be strings")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise FontSyncError(f"font source is not a valid URL: {value!r}") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname != RAW_HOST
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise FontSyncError(f"font source is not an exact HTTPS Google Fonts raw URL: {value!r}")
    prefix = f"/google/fonts/{revision}/ofl/"
    if not parsed.path.startswith(prefix) or parsed.path.endswith("/"):
        raise FontSyncError(f"font source is outside the pinned Google Fonts OFL tree: {value!r}")
    decoded_path = unquote(parsed.path)
    path_parts = decoded_path.split("/")
    if (
        len(path_parts) != 7
        or path_parts[:5] != ["", "google", "fonts", revision, "ofl"]
        or "\\" in decoded_path
        or any(part in {"", ".", ".."} for part in path_parts[1:])
    ):
        raise FontSyncError(f"font source path is not canonical: {value!r}")
    if asset_path.endswith(".txt"):
        if not decoded_path.endswith("/OFL.txt"):
            raise FontSyncError(f"license source must name OFL.txt: {value!r}")
    elif not decoded_path.lower().endswith(".ttf"):
        raise FontSyncError(f"font source must name a TrueType file: {value!r}")
    return value


def validate_manifest(document: object) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise FontSyncError("font provenance manifest must be a JSON object")
    if set(document) != {"purpose", "googleFontsRevision", "files"}:
        raise FontSyncError("font provenance manifest has unknown or missing top-level fields")
    if not isinstance(document["purpose"], str) or not document["purpose"].strip():
        raise FontSyncError("font provenance purpose must be a non-empty string")
    revision = _require_revision(document["googleFontsRevision"], field="googleFontsRevision")
    files = document["files"]
    if not isinstance(files, list) or not files:
        raise FontSyncError("font provenance manifest must declare at least one file")

    seen_paths: set[str] = set()
    seen_sources: set[str] = set()
    for index, entry in enumerate(files):
        if not isinstance(entry, dict) or set(entry) != {"path", "source", "bytes", "sha256"}:
            raise FontSyncError(f"font manifest entry {index} has unknown or missing fields")
        asset_path = _require_asset_path(entry["path"])
        source = _require_source(entry["source"], revision=revision, asset_path=asset_path)
        byte_count = entry["bytes"]
        if isinstance(byte_count, bool) or not isinstance(byte_count, int) or byte_count <= 0:
            raise FontSyncError(f"font manifest entry {index} has an invalid byte count")
        digest = entry["sha256"]
        if not isinstance(digest, str) or SHA256_PATTERN.fullmatch(digest) is None:
            raise FontSyncError(f"font manifest entry {index} has an invalid SHA-256 digest")
        if asset_path in seen_paths or source in seen_sources:
            raise FontSyncError("font provenance paths and sources must be unique")
        seen_paths.add(asset_path)
        seen_sources.add(source)
    return document


def load_manifest(root: Path) -> dict[str, Any]:
    try:
        document = json.loads((root / MANIFEST_PATH).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FontSyncError(f"cannot read {MANIFEST_PATH}: {exc}") from exc
    return validate_manifest(document)


def _asset_target(root: Path, relative_path: str) -> Path:
    root = root.resolve()
    target = (root / relative_path).resolve()
    packaged_fonts = (root / FONT_PATH_PREFIX).resolve()
    if target.parent != packaged_fonts:
        raise FontSyncError(f"font target escapes the packaged font directory: {relative_path!r}")
    return target


def _validate_payload(asset_path: str, content: bytes) -> None:
    if not content:
        raise FontSyncError(f"font asset is empty: {asset_path}")
    if len(content) > MAX_ASSET_BYTES:
        raise FontSyncError(f"font asset exceeds {MAX_ASSET_BYTES} bytes: {asset_path}")
    if asset_path.endswith(".txt"):
        if OFL_MARKER not in content:
            raise FontSyncError(f"font license does not contain the OFL 1.1 marker: {asset_path}")
        return
    if content[:4] not in {b"\x00\x01\x00\x00", b"OTTO", b"true", b"ttcf"}:
        raise FontSyncError(f"font asset does not have a recognized sfnt header: {asset_path}")


def verify_local_assets(root: Path, manifest: Mapping[str, Any] | None = None) -> int:
    document = validate_manifest(dict(manifest)) if manifest is not None else load_manifest(root)
    for entry in document["files"]:
        target = _asset_target(root, entry["path"])
        try:
            content = target.read_bytes()
        except OSError as exc:
            raise FontSyncError(f"cannot read packaged font asset {entry['path']}: {exc}") from exc
        _validate_payload(entry["path"], content)
        if len(content) != entry["bytes"]:
            raise FontSyncError(f"byte count mismatch for {entry['path']}")
        if hashlib.sha256(content).hexdigest() != entry["sha256"]:
            raise FontSyncError(f"SHA-256 mismatch for {entry['path']}")
    return len(document["files"])


def _source_at_revision(source: str, *, old_revision: str, new_revision: str) -> str:
    parsed = urlsplit(source)
    segments = parsed.path.split("/")
    if len(segments) < 6 or segments[1:3] != ["google", "fonts"] or segments[3] != old_revision:
        raise FontSyncError(f"cannot update malformed Google Fonts source: {source!r}")
    segments[3] = new_revision
    candidate = urlunsplit(("https", RAW_HOST, "/".join(segments), "", ""))
    return candidate


def fetch_asset(source: str, timeout: float) -> bytes:
    request = urllib.request.Request(
        source,
        headers={
            "Accept": "application/octet-stream",
            "Accept-Encoding": "identity",
            "User-Agent": "sdsctl-theme-font-sync/1",
        },
    )
    opener = urllib.request.build_opener(_RejectRedirects())
    try:
        with opener.open(request, timeout=timeout) as response:
            if response.geturl() != source:
                raise FontSyncError(f"font source changed URL unexpectedly: {source!r}")
            status = getattr(response, "status", None)
            if status != 200:
                raise FontSyncError(f"font source returned HTTP {status}: {source!r}")
            content = response.read(MAX_ASSET_BYTES + 1)
    except FontSyncError:
        raise
    except (OSError, urllib.error.URLError) as exc:
        raise FontSyncError(f"failed to fetch {source!r}: {exc}") from exc
    if len(content) > MAX_ASSET_BYTES:
        raise FontSyncError(f"download exceeds {MAX_ASSET_BYTES} bytes: {source!r}")
    return content


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def synchronize_assets(
    root: Path,
    revision: str,
    *,
    timeout: float = 30.0,
    fetcher: Fetcher = fetch_asset,
) -> int:
    revision = _require_revision(revision)
    root = root.resolve()
    manifest_path = root / MANIFEST_PATH
    try:
        current_bytes = manifest_path.read_bytes()
    except OSError as exc:
        raise FontSyncError(f"cannot read {MANIFEST_PATH}: {exc}") from exc
    current = load_manifest(root)
    verify_local_assets(root, current)
    old_revision = current["googleFontsRevision"]

    downloaded: list[tuple[dict[str, Any], bytes]] = []
    for entry in current["files"]:
        source = _source_at_revision(
            entry["source"], old_revision=old_revision, new_revision=revision
        )
        candidate = {**entry, "source": source}
        _require_source(source, revision=revision, asset_path=entry["path"])
        try:
            content = fetcher(source, timeout)
        except FontSyncError:
            raise
        except Exception as exc:
            raise FontSyncError(f"failed to fetch {source!r}: {exc}") from exc
        _validate_payload(entry["path"], content)
        candidate["bytes"] = len(content)
        candidate["sha256"] = hashlib.sha256(content).hexdigest()
        downloaded.append((candidate, content))

    updated = {
        "purpose": current["purpose"],
        "googleFontsRevision": revision,
        "files": [entry for entry, _content in downloaded],
    }
    validate_manifest(updated)
    updated_bytes = (json.dumps(updated, indent=2) + "\n").encode()

    originals: dict[Path, bytes] = {}
    try:
        for entry in current["files"]:
            target = _asset_target(root, entry["path"])
            originals[target] = target.read_bytes()
    except OSError as exc:
        raise FontSyncError(f"cannot stage existing font assets for rollback: {exc}") from exc
    originals[manifest_path] = current_bytes
    committed: list[Path] = []
    try:
        for entry, content in downloaded:
            target = _asset_target(root, entry["path"])
            _atomic_write(target, content)
            committed.append(target)
        _atomic_write(manifest_path, updated_bytes)
        committed.append(manifest_path)
        verify_local_assets(root, updated)
    except Exception as exc:
        rollback_errors: list[str] = []
        for target in reversed(committed):
            try:
                _atomic_write(target, originals[target])
            except Exception as rollback_exc:  # pragma: no cover - catastrophic filesystem failure
                rollback_errors.append(f"{target}: {rollback_exc}")
        detail = f"; rollback errors: {', '.join(rollback_errors)}" if rollback_errors else ""
        raise FontSyncError(f"font asset transaction failed: {exc}{detail}") from exc

    return len(downloaded)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="verify local assets (default)")
    mode.add_argument("--sync", action="store_true", help="download a new pinned revision")
    parser.add_argument("--revision", help="exact 40-character Google Fonts commit SHA")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args(argv)
    if args.sync and args.revision is None:
        parser.error("--sync requires --revision")
    if not args.sync and args.revision is not None:
        parser.error("--revision is only valid with --sync")
    if not 0 < args.timeout <= 120:
        parser.error("--timeout must be greater than zero and no more than 120 seconds")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.sync:
            count = synchronize_assets(args.root, args.revision, timeout=args.timeout)
            print(f"Synchronized {count} pinned Google Fonts assets at {args.revision}.")
        else:
            count = verify_local_assets(args.root)
            revision = load_manifest(args.root)["googleFontsRevision"]
            print(f"Verified {count} pinned Google Fonts assets at {revision}.")
    except FontSyncError as exc:
        print(f"Font verification failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
