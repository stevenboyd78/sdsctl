from __future__ import annotations

import os
import sqlite3
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from contextlib import closing, suppress
from pathlib import Path

import pytest

import sds200.browser_device_store as store_module
from sds200.browser_device_store import (
    BrowserDeviceBinding,
    BrowserDeviceState,
    BrowserDeviceStore,
    BrowserDeviceStoreError,
)

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Private POSIX authority foundation")


def test_pause_binding_cannot_pause_rotated_or_resumed_generation(store) -> None:
    issued = store.enroll("one")
    old = store.authenticate("one", issued.credential)
    rotated = store.rotate("one")
    assert store.pause_binding(old) is None
    current = store.authenticate("one", rotated.credential)
    paused = store.pause_binding(current)
    assert paused is not None and paused.state is BrowserDeviceState.PAUSED
    assert store.pause_binding(current) is None
    assert BrowserDeviceStore(store.path).authenticate("one", rotated.credential) is None
    store.transition("one", BrowserDeviceState.ACTIVE)
    assert store.pause_binding(current) is None
    assert store.authenticate("one", rotated.credential) is not None


@pytest.fixture
def store(tmp_path: Path) -> BrowserDeviceStore:
    root = tmp_path / "authority"
    root.mkdir(mode=0o700)
    return BrowserDeviceStore.initialize(root / "devices.sqlite")


def test_enrollment_survives_reopen_without_persisting_secret(store: BrowserDeviceStore) -> None:
    issued = store.enroll("display-one")
    binding = store.authenticate("display-one", issued.credential)
    assert binding == BrowserDeviceBinding("display-one", 1)
    assert issued.credential not in repr(issued)
    assert issued.credential.encode() not in store.path.read_bytes()
    assert issued.credential not in repr(store.inventory())
    reopened = BrowserDeviceStore(store.path)
    assert reopened.authenticate("display-one", issued.credential) == binding
    assert reopened.is_current(binding)
    assert store.path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("bad", ["password", "", "a" * 43, "sdsctl-browser-v1." + "z" * 64,
                                  "sdsctl-browser-v1." + "0" * 64])
def test_other_credentials_are_denied(store: BrowserDeviceStore, bad: str) -> None:
    store.enroll("display")
    assert store.authenticate("display", bad) is None
    assert store.authenticate("unknown", bad) is None


def test_rotation_is_device_specific_and_old_binding_never_revives(
    store: BrowserDeviceStore,
) -> None:
    one = store.enroll("one")
    two = store.enroll("two")
    old = store.authenticate("one", one.credential)
    other = store.authenticate("two", two.credential)
    replacement = store.rotate("one")
    assert replacement.record.generation == 2
    assert replacement.credential != one.credential
    assert store.authenticate("one", one.credential) is None
    assert store.authenticate("two", replacement.credential) is None
    assert not store.is_current(old)
    assert store.is_current(other)
    assert store.authenticate("one", replacement.credential) == BrowserDeviceBinding("one", 2)


def test_pause_persists_rotation_preserves_pause_and_resume_invalidates_old_binding(
    store: BrowserDeviceStore,
) -> None:
    issued = store.enroll("display")
    old = store.authenticate("display", issued.credential)
    paused = store.transition("display", BrowserDeviceState.PAUSED)
    assert paused.generation == 2
    reopened = BrowserDeviceStore(store.path)
    assert reopened.authenticate("display", issued.credential) is None
    assert not reopened.is_current(old)
    replacement = reopened.rotate("display")
    assert replacement.record.state is BrowserDeviceState.PAUSED
    assert reopened.authenticate("display", replacement.credential) is None
    resumed = reopened.transition("display", BrowserDeviceState.ACTIVE)
    assert resumed.generation == 4
    assert not reopened.is_current(old)
    assert reopened.authenticate("display", replacement.credential) == BrowserDeviceBinding(
        "display", 4,
    )


def test_revocation_is_persistent_terminal_and_id_is_not_reused(store: BrowserDeviceStore) -> None:
    issued = store.enroll("display")
    binding = store.authenticate("display", issued.credential)
    revoked = store.transition("display", BrowserDeviceState.REVOKED)
    reopened = BrowserDeviceStore(store.path)
    assert reopened.transition("display", BrowserDeviceState.REVOKED) == revoked
    assert reopened.authenticate("display", issued.credential) is None
    assert not reopened.is_current(binding)
    for operation in [lambda: reopened.enroll("display"), lambda: reopened.rotate("display"),
                      lambda: reopened.transition("display", BrowserDeviceState.ACTIVE)]:
        with pytest.raises(BrowserDeviceStoreError):
            operation()


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o666])
def test_unsafe_database_permissions_fail_closed(store: BrowserDeviceStore, mode: int) -> None:
    issued = store.enroll("display")
    store.path.chmod(mode)
    with pytest.raises(BrowserDeviceStoreError):
        store.authenticate("display", issued.credential)


def test_missing_authority_is_not_recreated_or_cached(store: BrowserDeviceStore) -> None:
    issued = store.enroll("display")
    store.path.rename(store.path.with_suffix(".retired"))
    with pytest.raises(BrowserDeviceStoreError):
        store.authenticate("display", issued.credential)
    assert not store.path.exists()


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "directory", "fifo"])
def test_non_private_regular_file_is_rejected(store: BrowserDeviceStore, kind: str) -> None:
    original = store.path.with_suffix(".saved")
    store.path.rename(original)
    if kind == "symlink":
        store.path.symlink_to(original)
    elif kind == "hardlink":
        os.link(original, store.path)
    elif kind == "directory":
        store.path.mkdir(mode=0o700)
    else:
        os.mkfifo(store.path, 0o600)
    with pytest.raises(BrowserDeviceStoreError):
        store.inventory()


def test_initialize_never_overwrites_existing_authority(store: BrowserDeviceStore) -> None:
    issued = store.enroll("display")
    with pytest.raises(BrowserDeviceStoreError):
        BrowserDeviceStore.initialize(store.path)
    assert store.authenticate("display", issued.credential) is not None


@pytest.mark.parametrize("content", [b"", b"fictional-secret corrupt database"])
def test_corrupt_authority_is_redacted(store: BrowserDeviceStore, content: bytes) -> None:
    store.path.write_bytes(content)
    with pytest.raises(BrowserDeviceStoreError) as caught:
        store.inventory()
    assert "fictional-secret" not in str(caught.value)
    assert caught.value.__suppress_context__


def test_unknown_schema_version_is_rejected(store: BrowserDeviceStore) -> None:
    with closing(sqlite3.connect(store.path)) as connection, connection:
        connection.execute("PRAGMA user_version=999")
    with pytest.raises(BrowserDeviceStoreError):
        store.inventory()


def test_private_parent_is_required(tmp_path: Path) -> None:
    root = tmp_path / "public"
    root.mkdir(mode=0o755)
    with pytest.raises(BrowserDeviceStoreError):
        BrowserDeviceStore.initialize(root / "devices.sqlite")
    assert not (root / "devices.sqlite").exists()


def test_simultaneous_store_instances_serialize_rotation(store: BrowserDeviceStore) -> None:
    issued = store.enroll("display")
    def rotate(index: int):
        return BrowserDeviceStore(store.path).rotate("display")
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(rotate, range(12)))
    assert sorted(result.record.generation for result in results) == list(range(2, 14))
    accepted = [result for result in results if store.authenticate("display", result.credential)]
    assert len(accepted) == 1
    assert accepted[0].record.generation == 13
    assert store.authenticate("display", issued.credential) is None


@pytest.mark.parametrize("generation", [True, 0, -1, 1.0, 2**63])
def test_binding_rejects_ambiguous_generation(generation) -> None:
    with pytest.raises(BrowserDeviceStoreError):
        BrowserDeviceBinding("display", generation)


def _pause_in_child(path: Path) -> int:
    return BrowserDeviceStore(path).transition("display", BrowserDeviceState.PAUSED).generation


def test_separate_process_transition_is_seen_by_existing_instance(
    store: BrowserDeviceStore,
) -> None:
    issued = store.enroll("display")
    binding = store.authenticate("display", issued.credential)
    with ProcessPoolExecutor(max_workers=1) as executor:
        assert executor.submit(_pause_in_child, store.path).result(timeout=10) == 2
    assert not store.is_current(binding)
    assert store.authenticate("display", issued.credential) is None


def test_capacity_failure_rolls_back_without_affecting_existing_devices(
    store: BrowserDeviceStore, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(store_module, "_LIMIT", 2)
    first = store.enroll("one")
    store.enroll("two")
    with pytest.raises(BrowserDeviceStoreError):
        store.enroll("three")
    assert len(store.inventory()) == 2
    assert store.authenticate("one", first.credential) is not None


@pytest.mark.parametrize("column, value", [
    ("generation", 0), ("generation", "not-a-number"), ("state", "operator"),
    ("verifier", "invalid-private-data"),
])
def test_malformed_authority_record_fails_closed(
    store: BrowserDeviceStore, column: str, value: object,
) -> None:
    issued = store.enroll("display")
    with closing(sqlite3.connect(store.path)) as connection, connection:
        # Column names are fixed test parameters, never caller-provided SQL.
        connection.execute(f"UPDATE devices SET {column}=?", (value,))
    with pytest.raises(BrowserDeviceStoreError):
        store.authenticate("display", issued.credential)


def test_generation_cannot_wrap_or_reset(store: BrowserDeviceStore) -> None:
    issued = store.enroll("display")
    with closing(sqlite3.connect(store.path)) as connection, connection:
        connection.execute("UPDATE devices SET generation=?", (2**63 - 1,))
    with pytest.raises(BrowserDeviceStoreError):
        store.rotate("display")
    assert store.authenticate("display", issued.credential).generation == 2**63 - 1


def test_rotation_racing_revocation_cannot_reactivate_device(store: BrowserDeviceStore) -> None:
    issued = store.enroll("display")
    def operation(index: int) -> None:
        other = BrowserDeviceStore(store.path)
        if index == 4:
            other.transition("display", BrowserDeviceState.REVOKED)
        else:
            # Rotation after the revocation transaction must fail.
            with suppress(BrowserDeviceStoreError):
                other.rotate("display")
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(operation, range(8)))
    assert store.inventory()[0].state is BrowserDeviceState.REVOKED
    assert store.authenticate("display", issued.credential) is None
