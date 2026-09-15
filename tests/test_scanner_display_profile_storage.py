from __future__ import annotations

import base64
import hashlib
import json
import os
import selectors
import stat
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from sds200 import scanner_display_profile_storage as storage
from sds200.scanner_display_adapter import ScannerDisplayAdapter, ScannerDisplayStyle
from sds200.scanner_display_frame_preview import render_scanner_display_frame
from sds200.scanner_display_profile import MAX_PROFILE_BYTES, parse_scanner_display_profile
from sds200.scanner_display_profile_state import DisplayProfileBinding, DisplayProfileSourceKind
from sds200.scanner_display_profile_storage import (
    DisplayProfileSourceStatus as SourceStatus,
)
from sds200.scanner_display_profile_storage import (
    DisplayProfileStorageError,
    PersistentScannerDisplayProfile,
    initialize_display_profile_storage,
)
from sds200.scanner_display_profile_storage import (
    ProfileStorageFailure as Failure,
)
from sds200.xml_protocol import ScannerInfoParser

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX durable storage foundation")
ENDPOINT = UUID(int=1)
BINDING = DisplayProfileBinding(ENDPOINT, UUID(int=2), DisplayProfileSourceKind.MANUAL_IMPORT)
NOW = datetime(2026, 9, 15, 8, 0, tzinfo=UTC)
SOURCE = (
    b"Owner\tPRIVATE_SENTINEL\r\n"
    b"DisplayOption\t\t\t\t\t\tDEC\t\t\t\t\tOff\tAFS\tCOLOR\r\n"
    b"DispOptItems\tDispOptId=2\tDispLayoutId=1\tFrequency\tEmpty\r\n"
    b"DispColors\tDispColorId=2\tColorLayoutId=1\tffffff\t000000\r\n"
)
OTHER = SOURCE.replace(b"ffffff", b"123456")


@pytest.fixture
def files(tmp_path):
    source = tmp_path / "profile.cfg"
    source.write_bytes(SOURCE)
    source.chmod(0o640)
    root = tmp_path / "private-state"
    initialize_display_profile_storage(root, ENDPOINT)
    return source, root


def repository(files, endpoint=ENDPOINT):
    source, root = files
    return PersistentScannerDisplayProfile(
        source_path=source, state_directory=root, endpoint_id=endpoint
    )


def accept(repo, *, binding=BINDING, moment=NOW):
    preview = repo.prepare(binding, acquired_at=moment)
    return repo.commit(preview, imported_at=moment, confirm_source_change=True)


def manifest(files):
    return files[1] / "accepted-profile.json"


def assert_error(category, callable_):
    with pytest.raises(DisplayProfileStorageError) as raised:
        callable_()
    assert raised.value.category is category
    assert "PRIVATE_SENTINEL" not in str(raised.value)
    return raised.value


def test_initialization_is_explicit_and_does_not_import_or_touch_source(files):
    source, root = files
    before = source.read_bytes(), source.stat()
    repo = repository(files)
    result = repo.inspect()
    assert result.profile.last_good is None
    assert result.source_status is SourceStatus.NOT_IMPORTED
    assert (source.read_bytes(), source.stat()) == before
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE(manifest(files).stat().st_mode) == 0o600
    assert {item.name for item in root.iterdir()} == {"accepted-profile.json"}


def test_prepare_is_read_only_and_commit_survives_new_instance(files):
    repo = repository(files)
    initial = manifest(files).read_bytes()
    before_source = files[0].read_bytes(), files[0].stat()
    preview = repo.prepare(BINDING, acquired_at=NOW)
    assert manifest(files).read_bytes() == initial
    assert repository(files).inspect().profile.last_good is None
    assert "PRIVATE_SENTINEL" not in repr(preview)
    imported = repo.commit(preview, imported_at=NOW + timedelta(seconds=1))
    restored = repository(files).inspect()
    assert restored.profile.last_good == imported
    assert restored.source_status is SourceStatus.MATCHES_IMPORT
    assert "PRIVATE_SENTINEL" not in repr(restored)
    assert (files[0].read_bytes(), files[0].stat()) == before_source
    document = json.loads(manifest(files).read_bytes())
    assert base64.b64decode(document["accepted"]["raw"]) == SOURCE
    assert document["accepted"]["raw_sha256"] == hashlib.sha256(SOURCE).hexdigest()
    assert b"profile.cfg" not in manifest(files).read_bytes()
    assert b"private-state" not in manifest(files).read_bytes()


def test_actual_new_process_reads_same_accepted_revision(files):
    accepted = accept(repository(files))
    code = """
import sys
from pathlib import Path
from uuid import UUID
from sds200.scanner_display_profile_storage import PersistentScannerDisplayProfile
r = PersistentScannerDisplayProfile(
    source_path=Path(sys.argv[1]), state_directory=Path(sys.argv[2]), endpoint_id=UUID(int=1)
)
s = r.inspect()
assert s.profile.last_good.profile.revision == sys.argv[3]
assert s.source_status.value == 'matches_import'
print('restored')
"""
    result = subprocess.run(
        [sys.executable, "-c", code, *map(str, files), accepted.profile.revision],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "restored"


@pytest.mark.parametrize(
    "change", ["valid", "invalid", "missing", "oversized", "empty", "raw_only"]
)
def test_restart_preserves_last_good_without_automatically_importing_source_edits(files, change):
    accepted = accept(repository(files))
    before = manifest(files).read_bytes()
    if change == "missing":
        files[0].unlink()
    else:
        files[0].write_bytes(
            {
                "valid": OTHER,
                "invalid": b"PRIVATE_SENTINEL\nbroken",
                "empty": b"",
                "oversized": b"x" * (MAX_PROFILE_BYTES + 1),
                "raw_only": SOURCE.replace(b"PRIVATE_SENTINEL", b"DIFFERENT_PRIVATE"),
            }[change]
        )
    result = repository(files).inspect()
    assert result.profile.last_good == accepted
    assert (
        result.source_status
        is {
            "valid": SourceStatus.CHANGED,
            "raw_only": SourceStatus.CHANGED,
            "invalid": SourceStatus.INVALID,
            "empty": SourceStatus.INVALID,
            "oversized": SourceStatus.INVALID,
            "missing": SourceStatus.UNAVAILABLE,
        }[change]
    )
    assert manifest(files).read_bytes() == before


def test_explicit_refresh_updates_revision_without_mutating_source(files):
    repo = repository(files)
    old = accept(repo)
    files[0].write_bytes(OTHER)
    preview = repo.prepare(BINDING, acquired_at=NOW + timedelta(seconds=2))
    assert preview.previous_revision == old.profile.revision
    assert preview.profile.revision != old.profile.revision
    assert repository(files).inspect().profile.last_good == old
    current = repo.commit(preview, imported_at=NOW + timedelta(seconds=3))
    assert repository(files).inspect().profile.last_good == current
    assert files[0].read_bytes() == OTHER


@pytest.mark.parametrize(
    "change", ["rewrite", "replace", "touch", "permission", "raw_only", "remove"]
)
def test_source_change_during_review_invalidates_preview_without_publication(files, change):
    repo = repository(files)
    accepted = accept(repo)
    preview = repo.prepare(BINDING, acquired_at=NOW)
    if change == "remove":
        files[0].unlink()
    elif change == "replace":
        replacement = files[0].with_suffix(".new")
        replacement.write_bytes(SOURCE)
        replacement.replace(files[0])
    elif change == "touch":
        st = files[0].stat()
        os.utime(files[0], ns=(st.st_atime_ns, st.st_mtime_ns + 10_000))
    elif change == "permission":
        files[0].chmod(0o600)
    else:
        files[0].write_bytes(
            OTHER
            if change == "rewrite"
            else SOURCE.replace(b"PRIVATE_SENTINEL", b"PRIVATE_CHANGED!")
        )
    assert_error(
        Failure.SOURCE_UNAVAILABLE if change == "remove" else Failure.SOURCE_CHANGED,
        lambda: repo.commit(preview, imported_at=NOW),
    )
    assert repository(files).inspect().profile.last_good == accepted
    assert_error(Failure.INVALID_REVIEW, lambda: repo.commit(preview, imported_at=NOW))


def test_two_repository_previews_cannot_overwrite_each_other(files):
    a, b = repository(files), repository(files)
    first = a.prepare(BINDING, acquired_at=NOW)
    second = b.prepare(BINDING, acquired_at=NOW)
    accepted = a.commit(first, imported_at=NOW)
    assert_error(Failure.CONFLICT, lambda: b.commit(second, imported_at=NOW))
    assert b.inspect().profile.last_good == accepted


@pytest.mark.parametrize("action", ["copy", "foreign", "supersede", "cancel", "complete"])
def test_preview_identity_and_one_use(files, action):
    repo = repository(files)
    preview = repo.prepare(BINDING, acquired_at=NOW)
    if action == "copy":
        preview = replace(preview)
    elif action == "foreign":
        repo = repository(files)
    elif action == "supersede":
        repo.prepare(BINDING, acquired_at=NOW)
    elif action == "cancel":
        repo.cancel(preview)
    else:
        repo.commit(preview, imported_at=NOW)
    assert_error(Failure.INVALID_REVIEW, lambda: repo.commit(preview, imported_at=NOW))


def test_cancel_does_not_change_persisted_state(files):
    repo = repository(files)
    before = manifest(files).read_bytes()
    preview = repo.prepare(BINDING, acquired_at=NOW)
    repo.cancel(preview)
    assert manifest(files).read_bytes() == before
    assert_error(Failure.INVALID_REVIEW, lambda: repo.cancel(preview))


def test_new_source_requires_explicit_review_even_with_identical_bytes(files):
    repo = repository(files)
    accepted = accept(repo)
    binding = replace(BINDING, source_id=UUID(int=3))
    preview = repo.prepare(binding, acquired_at=NOW)
    assert preview.source_changed
    assert_error(Failure.INVALID_REVIEW, lambda: repo.commit(preview, imported_at=NOW))
    assert repo.inspect().profile.last_good == accepted
    preview = repo.prepare(binding, acquired_at=NOW)
    new = repo.commit(preview, imported_at=NOW, confirm_source_change=True)
    assert new.provenance.binding == binding


@pytest.mark.parametrize(
    "binding",
    [
        replace(BINDING, endpoint_id=UUID(int=7)),
        replace(BINDING, source_kind=DisplayProfileSourceKind.FAVORITES_SYNC),
        None,
    ],
)
def test_only_selected_endpoint_manual_import_allowed(files, binding):
    assert_error(
        Failure.INVALID_REVIEW, lambda: repository(files).prepare(binding, acquired_at=NOW)
    )


@pytest.mark.parametrize("moment", [NOW.replace(tzinfo=None), NOW - timedelta(seconds=1)])
def test_invalid_commit_timestamp_does_not_publish(files, moment):
    repo = repository(files)
    before = manifest(files).read_bytes()
    preview = repo.prepare(BINDING, acquired_at=NOW)
    assert_error(Failure.INVALID_REVIEW, lambda: repo.commit(preview, imported_at=moment))
    assert manifest(files).read_bytes() == before


@pytest.mark.parametrize("moment", [None, NOW.replace(tzinfo=None), NOW - timedelta(seconds=1)])
def test_invalid_acquisition_time_does_not_mislabel_valid_source_or_replace_import(files, moment):
    repo = repository(files)
    old = accept(repo)
    preview = repo.prepare(BINDING, acquired_at=NOW)
    assert_error(Failure.INVALID_REVIEW, lambda: repo.prepare(BINDING, acquired_at=moment))
    assert_error(Failure.INVALID_REVIEW, lambda: repo.commit(preview, imported_at=NOW))
    assert repo.inspect().profile.last_good == old
    assert repo.inspect().source_status is SourceStatus.MATCHES_IMPORT


@pytest.mark.parametrize("private", [True, False])
@pytest.mark.parametrize("kind", ["symlink", "parent_link", "hardlink", "fifo", "directory"])
def test_nonregular_or_linked_paths_are_never_followed_or_repaired(files, private, kind):
    accept(repository(files))
    path = manifest(files) if private else files[0]
    original = path.read_bytes()
    if kind == "parent_link":
        link = files[1].parent / "alias"
        link.symlink_to(path.parent, target_is_directory=True)
        candidate = (files[0], link) if private else (link / path.name, files[1])
    else:
        path.unlink()
        backup = path.with_suffix(".backup")
        backup.write_bytes(original)
        backup.chmod(0o600)
        if kind == "symlink":
            path.symlink_to(backup)
        elif kind == "hardlink":
            os.link(backup, path)
        elif kind == "fifo":
            os.mkfifo(path)
        else:
            path.mkdir()
        candidate = files
    if private:
        with pytest.raises(DisplayProfileStorageError):
            repository(candidate).inspect()
    else:
        result = repository(candidate).inspect()
        assert result.profile.last_good is not None
        assert result.source_status in {SourceStatus.UNAVAILABLE, SourceStatus.UNSAFE}
        with pytest.raises(DisplayProfileStorageError):
            repository(candidate).prepare(BINDING, acquired_at=NOW)


@pytest.mark.parametrize("target", ["directory", "state"])
def test_loose_private_permissions_are_refused_not_repaired(files, target):
    path = files[1] if target == "directory" else manifest(files)
    mode = 0o750 if target == "directory" else 0o640
    path.chmod(mode)
    assert_error(Failure.UNSAFE_PATH, lambda: repository(files).inspect())
    assert stat.S_IMODE(path.stat().st_mode) == mode


def test_existing_empty_partial_or_complete_directory_is_never_reinitialized(files, tmp_path):
    before = manifest(files).read_bytes()
    assert_error(
        Failure.WRITE_FAILED, lambda: initialize_display_profile_storage(files[1], ENDPOINT)
    )
    empty = tmp_path / "partial"
    empty.mkdir(mode=0o700)
    assert_error(Failure.WRITE_FAILED, lambda: initialize_display_profile_storage(empty, ENDPOINT))
    assert list(empty.iterdir()) == []
    assert manifest(files).read_bytes() == before


@pytest.mark.parametrize(
    "damage",
    [
        "truncated",
        "duplicate",
        "schema",
        "endpoint",
        "raw",
        "hash",
        "revision",
        "source_kind",
        "naive_time",
        "extra",
        "missing",
    ],
)
def test_invalid_state_never_silently_falls_back_to_source_or_gets_repaired(files, damage):
    accept(repository(files))
    doc = json.loads(manifest(files).read_bytes())
    if damage == "missing":
        manifest(files).unlink()
    else:
        if damage == "schema":
            doc["schema"] = True
        elif damage == "endpoint":
            doc["endpoint_id"] = str(UUID(int=9))
        elif damage == "extra":
            doc["unrecognized"] = "PRIVATE_SENTINEL"
        elif damage == "naive_time":
            doc["accepted"]["acquired_at"] = NOW.replace(tzinfo=None).isoformat()
        elif damage in {"raw", "hash", "revision", "source_kind"}:
            key = "raw_sha256" if damage == "hash" else damage
            doc["accepted"][key] = "PRIVATE_SENTINEL"
        raw = json.dumps(doc).encode()
        if damage == "truncated":
            raw = raw[:-10]
        elif damage == "duplicate":
            raw = raw.replace(b'"schema": 1', b'"schema": 1, "schema": 1')
        manifest(files).write_bytes(raw)
    before = manifest(files).read_bytes() if manifest(files).exists() else None
    expected = Failure.STATE_UNAVAILABLE if damage == "missing" else Failure.STATE_INVALID
    assert_error(expected, lambda: repository(files).inspect())
    assert_error(expected, lambda: repository(files).prepare(BINDING, acquired_at=NOW))
    assert (manifest(files).read_bytes() if manifest(files).exists() else None) == before


@pytest.mark.parametrize("fault", ["write", "file_sync", "replace"])
def test_failed_precommit_io_preserves_old_import_and_cleans_only_own_temp(
    files, monkeypatch, fault
):
    repo = repository(files)
    accepted = accept(repo)
    files[0].write_bytes(OTHER)
    preview = repo.prepare(BINDING, acquired_at=NOW)
    before = manifest(files).read_bytes()
    sentinel = files[1] / ".profile-unrelated.tmp"
    sentinel.write_bytes(b"KEEP")

    def fail(*args, **kwargs):
        raise OSError("PRIVATE_SENTINEL /private/path")

    monkeypatch.setattr(
        storage.os, {"write": "write", "file_sync": "fsync", "replace": "replace"}[fault], fail
    )
    assert_error(Failure.WRITE_FAILED, lambda: repo.commit(preview, imported_at=NOW))
    assert manifest(files).read_bytes() == before
    assert repo.inspect().profile.last_good == accepted
    assert {p.name for p in files[1].iterdir()} == {"accepted-profile.json", sentinel.name}
    assert sentinel.read_bytes() == b"KEEP"


def test_postrename_sync_failure_is_uncertain_and_fresh_process_recovers_whole_new_state(
    files, monkeypatch
):
    repo = repository(files)
    accept(repo)
    files[0].write_bytes(OTHER)
    preview = repo.prepare(BINDING, acquired_at=NOW)
    original = storage.os.fsync

    def fail_directory(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("PRIVATE_SENTINEL")
        original(fd)

    monkeypatch.setattr(storage.os, "fsync", fail_directory)
    assert_error(Failure.OUTCOME_UNCONFIRMED, lambda: repo.commit(preview, imported_at=NOW))
    assert_error(Failure.INVALID_REVIEW, lambda: repo.commit(preview, imported_at=NOW))
    recovered = repository(files).inspect()
    assert (
        recovered.profile.last_good.profile.revision
        == parse_scanner_display_profile(OTHER).revision
    )
    assert recovered.source_status is SourceStatus.MATCHES_IMPORT


def test_source_changes_during_read_are_refused(files, monkeypatch):
    original = storage.os.read
    changed = False

    def racing_read(fd, size):
        nonlocal changed
        data = original(fd, size)
        if not changed and b"PRIVATE_SENTINEL" in data:
            changed = True
            files[0].write_bytes(OTHER)
        return data

    monkeypatch.setattr(storage.os, "read", racing_read)
    assert_error(
        Failure.SOURCE_CHANGED, lambda: repository(files).prepare(BINDING, acquired_at=NOW)
    )


def test_restore_snapshot_plugs_into_existing_frame_adapter(files):
    accept(repository(files))
    restored = repository(files).inspect()
    adapter = ScannerDisplayAdapter(ENDPOINT, stale_after=5)
    session = adapter.begin_session(now=10)
    info = ScannerInfoParser().parse(
        "PSI",
        '<ScannerInfo V_Screen="conventional_scan"><ConvFrequency Freq="00949000"/></ScannerInfo>',
    )
    adapter.observe(session, info, sequence=1, received_at=10, now=10)
    frame = adapter.frame(restored.profile, now=10, style=ScannerDisplayStyle.SIMPLE)
    assert frame.profile_revision == restored.profile.last_good.profile.revision
    assert frame.provenance == restored.profile.last_good.provenance
    assert frame.profile_status.value == "last_imported"
    assert "00949000" in {value.text for value in frame.values}
    html = render_scanner_display_frame(frame)
    assert "00949000" in html
    assert "PRIVATE_SENTINEL" not in html
    assert str(files[0]) not in html


@pytest.mark.parametrize(
    "path", [Path("relative/profile.cfg"), Path("/"), Path("/tmp/../profile.cfg")]
)
def test_paths_must_be_explicit_absolute_nontraversing(files, path):
    assert_error(
        Failure.UNSAFE_PATH,
        lambda: PersistentScannerDisplayProfile(
            source_path=path, state_directory=files[1], endpoint_id=ENDPOINT
        ),
    )


def test_source_cannot_be_inside_private_accepted_store(files):
    assert_error(Failure.UNSAFE_PATH, lambda: repository((manifest(files), files[1])))


def test_recordings_and_config_neighbors_are_untouched(files):
    other = files[0].parent / "recordings"
    other.mkdir()
    (other / "saved.wav").write_bytes(b"SAVED RECORDING")
    config = files[0].parent / "config.toml"
    config.write_bytes(b"version = 1\n")
    accept(repository(files))
    assert (other / "saved.wav").read_bytes() == b"SAVED RECORDING"
    assert config.read_bytes() == b"version = 1\n"
    assert files[0].read_bytes() == SOURCE


@pytest.mark.parametrize("exclusive", [True, False])
def test_separate_process_directory_lock_never_blocks_or_loses_review(files, exclusive):
    repo = repository(files)
    preview = repo.prepare(BINDING, acquired_at=NOW)
    before = manifest(files).read_bytes()
    code = """
import fcntl, os, sys
fd = os.open(sys.argv[1], os.O_RDONLY | os.O_DIRECTORY)
fcntl.flock(fd, fcntl.LOCK_EX if sys.argv[2] == 'True' else fcntl.LOCK_SH)
print('locked', flush=True)
sys.stdin.read(1)
os.close(fd)
"""
    with subprocess.Popen(
        [sys.executable, "-c", code, str(files[1]), str(exclusive)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ) as child:
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout, selectors.EVENT_READ)
                assert selector.select(timeout=10), "lock helper did not start"
            assert child.stdout.readline() == b"locked\n"
            if exclusive:
                assert_error(Failure.BUSY, repo.inspect)
                assert_error(
                    Failure.BUSY, lambda: repository(files).prepare(BINDING, acquired_at=NOW)
                )
            else:
                assert repo.inspect().profile.last_good is None
            assert_error(Failure.BUSY, lambda: repo.commit(preview, imported_at=NOW))
            assert manifest(files).read_bytes() == before
        finally:
            child.communicate(b"x", timeout=10)
        assert child.returncode == 0
    # Lock acquisition failed before any commit attempt; the review is still usable.
    accepted = repo.commit(preview, imported_at=NOW)
    assert repository(files).inspect().profile.last_good == accepted


@pytest.mark.parametrize("boundary", ["before", "after"])
def test_abrupt_process_exit_around_rename_restores_only_a_complete_record(files, boundary):
    old = accept(repository(files))
    files[0].write_bytes(OTHER)
    code = """
import os, sys
from datetime import datetime, UTC
from pathlib import Path
from uuid import UUID
from sds200 import scanner_display_profile_storage as s
from sds200.scanner_display_profile_state import DisplayProfileBinding, DisplayProfileSourceKind
r = s.PersistentScannerDisplayProfile(
    source_path=Path(sys.argv[1]), state_directory=Path(sys.argv[2]), endpoint_id=UUID(int=1)
)
now = datetime(2026, 9, 15, 9, tzinfo=UTC)
p = r.prepare(
    DisplayProfileBinding(UUID(int=1), UUID(int=2), DisplayProfileSourceKind.MANUAL_IMPORT),
    acquired_at=now
)
original = os.replace
def interrupted(*args, **kwargs):
    if sys.argv[3] == 'after':
        original(*args, **kwargs)
    os._exit(73)
s.os.replace = interrupted
r.commit(p, imported_at=now)
"""
    child = subprocess.run(
        [sys.executable, "-c", code, *map(str, files), boundary],
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert child.returncode == 73, child.stderr
    revision = repository(files).inspect().profile.last_good.profile.revision
    assert revision == (
        old.profile.revision
        if boundary == "before"
        else parse_scanner_display_profile(OTHER).revision
    )
    leftovers = list(files[1].glob(".profile-*.tmp"))
    assert len(leftovers) == (1 if boundary == "before" else 0)
    for path in leftovers:
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert base64.b64decode(json.loads(path.read_bytes())["accepted"]["raw"]) == OTHER
    # Read-only restart does not purge unacknowledged transaction artifacts.
    repository(files).inspect()
    assert all(path.exists() for path in leftovers)


def test_state_race_at_publication_preserves_other_writer(files, monkeypatch):
    repo = repository(files)
    preview = repo.prepare(BINDING, acquired_at=NOW)
    other = json.loads(manifest(files).read_bytes())
    other["generation"] = str(UUID(int=77))
    other_bytes = json.dumps(other).encode()
    original = storage.os.fsync
    raced = False

    def race(fd):
        nonlocal raced
        original(fd)
        if not raced and stat.S_ISREG(os.fstat(fd).st_mode):
            raced = True
            manifest(files).write_bytes(other_bytes)

    monkeypatch.setattr(storage.os, "fsync", race)
    assert_error(Failure.CONFLICT, lambda: repo.commit(preview, imported_at=NOW))
    assert manifest(files).read_bytes() == other_bytes
    assert {p.name for p in files[1].iterdir()} == {"accepted-profile.json"}


def test_partial_writes_are_completed(files, monkeypatch):
    original = storage.os.write
    calls = []

    def partial(fd, data):
        calls.append(len(data))
        return original(fd, data[:17])

    monkeypatch.setattr(storage.os, "write", partial)
    accepted = accept(repository(files))
    assert len(calls) > 1
    assert repository(files).inspect().profile.last_good == accepted


@pytest.mark.parametrize("content", [b"", b"x" * (storage._STATE_LIMIT + 1)])
def test_empty_or_oversized_state_is_invalid_not_an_invalid_source(files, content):
    manifest(files).write_bytes(content)
    assert_error(Failure.STATE_INVALID, repository(files).inspect)
    assert manifest(files).read_bytes() == content


def test_root_replacement_after_save_reports_uncertainty_not_success(files, monkeypatch):
    repo = repository(files)
    preview = repo.prepare(BINDING, acquired_at=NOW)
    original = storage.os.replace
    moved = files[1].with_name("moved-private-state")

    def replace_then_move(*args, **kwargs):
        original(*args, **kwargs)
        files[1].rename(moved)
        files[1].mkdir(mode=0o700)
        (files[1] / "DO-NOT-TOUCH").write_bytes(b"KEEP")

    monkeypatch.setattr(storage.os, "replace", replace_then_move)
    assert_error(Failure.OUTCOME_UNCONFIRMED, lambda: repo.commit(preview, imported_at=NOW))
    assert_error(Failure.INVALID_REVIEW, lambda: repo.commit(preview, imported_at=NOW))
    assert {p.name for p in files[1].iterdir()} == {"DO-NOT-TOUCH"}
    assert repository((files[0], moved)).inspect().profile.last_good is not None


def test_failed_initial_directory_sync_preserves_partial_state_for_review(tmp_path, monkeypatch):
    root = tmp_path / "new-private-state"

    def fail(fd):
        raise OSError("PRIVATE_SENTINEL")

    monkeypatch.setattr(storage.os, "fsync", fail)
    assert_error(Failure.WRITE_FAILED, lambda: initialize_display_profile_storage(root, ENDPOINT))
    assert root.is_dir()
    assert list(root.iterdir()) == []
    assert_error(Failure.WRITE_FAILED, lambda: initialize_display_profile_storage(root, ENDPOINT))


def test_read_only_operator_source_is_supported_without_permission_changes(files):
    files[0].chmod(0o440)
    accepted = accept(repository(files))
    assert repository(files).inspect().profile.last_good == accepted
    assert stat.S_IMODE(files[0].stat().st_mode) == 0o440
    assert files[0].read_bytes() == SOURCE


def test_required_filesystem_protection_is_not_silently_disabled(files, monkeypatch):
    before = manifest(files).read_bytes()
    monkeypatch.delattr(storage.os, "O_NOFOLLOW")
    assert_error(Failure.UNSUPPORTED_PLATFORM, repository(files).inspect)
    assert manifest(files).read_bytes() == before
