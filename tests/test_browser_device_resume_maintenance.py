"""Synthetic native-ledger retirement; no browser/server/production acceptance."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import asdict, replace
from threading import Event

import pytest

from sds200 import browser_device_resume_maintenance as maintenance
from sds200.browser_device_recovery import ExchangeFailure, RecoveryMode
from sds200.browser_device_resume import BrowserResumeError
from sds200.browser_device_resume_maintenance import (
    BrowserResumeMaintenance,
    BrowserResumeMaintenanceError,
)
from tests.test_browser_device_native import certificates as certificates
from tests.test_browser_device_native import root as root
from tests.test_browser_device_resume import (
    commit,
    evidence,
    no_proof,
    prepare,
    rows,
    sql,
)
from tests.test_browser_device_resume import lab as lab

pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or os.geteuid() == 0, reason="Non-root Linux native maintenance",
)


@pytest.fixture
def core(lab):
    return BrowserResumeMaintenance(lab.root, clock=lambda: lab.clock[0])


@pytest.fixture
def archive(tmp_path):
    return tmp_path / "native-history.json"


def history(lab):
    with closing(sqlite3.connect(lab.ledger.path)) as db:
        return db.execute("SELECT * FROM browser_resume ORDER BY revision,digest").fetchall()


def snapshot(lab):
    return {p.name: p.read_bytes() for p in lab.root.iterdir() if p.is_file()}


def test_read_only_review_contains_no_private_ticket_or_hash_material(lab, core):
    approval = prepare(lab)
    before = snapshot(lab)
    review = core.review()
    assert review.mode is RecoveryMode.PAUSED and review.revision == approval.revision
    assert review.approvals == review.pending == 1
    assert review.expires_at == review.created_at + 120
    assert review.fingerprint not in repr(review) and approval.ticket not in repr(review)
    assert snapshot(lab) == before


@pytest.mark.parametrize("phase", ["prepared", "claimed", "failed", "cancelled", "complete"])
def test_retirement_preserves_evidence_and_stop_without_replaying(lab, core, archive, phase):
    first = prepare(lab)
    commit(lab, first)
    lab.ledger.suspend()
    last = prepare(lab)
    if phase == "complete":
        commit(lab, last)
        lab.ledger.suspend()
    else:
        sql(lab, f"UPDATE browser_resume SET phase='{phase}' WHERE phase='prepared'")
    review = core.review()
    before = history(lab)
    other = {k: v for k, v in snapshot(lab).items() if k != "recovery.sqlite"}
    result = core.retire(review, archive=archive)
    saved = json.loads(archive.read_bytes())
    assert [tuple(row[column] for column in maintenance._COLUMNS)
            for row in saved["before"]["approvals"]] == before
    assert archive.stat().st_mode & 0o777 == 0o600
    assert first.ticket.encode() not in archive.read_bytes()
    assert last.ticket.encode() not in archive.read_bytes()
    assert (lab.root / "device.secret").read_bytes().strip() not in archive.read_bytes()
    assert (result.revision, result.mode, result.archived, result.cancelled) == (
        review.revision + 1, RecoveryMode.PAUSED, 1, int(phase in {"prepared", "claimed"}))
    assert len(rows(lab)) == 1
    assert rows(lab)[0][1] == ("cancelled" if phase in {"prepared", "claimed"} else phase)
    assert {k: v for k, v in snapshot(lab).items() if k != "recovery.sqlite"} == other
    assert lab.ledger.authenticate(lambda: pytest.fail("Retirement authenticated")).session is None
    for old in (first, last):
        with pytest.raises(BrowserResumeError):
            commit(lab, old, prove_server=no_proof)
    assert core.confirm(review, archive=archive) == result
    after = snapshot(lab)
    with pytest.raises(BrowserResumeMaintenanceError):
        core.retire(review, archive=archive.parent / "replay.json")
    assert not (archive.parent / "replay.json").exists()
    assert snapshot(lab) == after
    # New permission is a separate explicitly prepared/confirmed operation.
    fresh = prepare(lab)
    assert fresh.ticket not in {first.ticket, last.ticket}
    assert lab.ledger.inspect().mode is RecoveryMode.PAUSED
    with pytest.raises(BrowserResumeMaintenanceError):
        core.confirm(review, archive=archive)  # Exact after-state is now superseded.


@pytest.mark.parametrize("mode", [m for m in RecoveryMode if m is not RecoveryMode.ACTIVE])
def test_each_terminal_mode_is_preserved_without_reading_credentials(lab, core, archive, mode,
                                                                   monkeypatch):
    if mode is not RecoveryMode.PAUSED:
        lab.ledger.resume(lab.ledger.inspect().revision)

        def fail():
            raise ExchangeFailure(mode)

        lab.ledger.authenticate(fail)
    prepare(lab)
    original = maintenance._private_read

    def read(parent, name, limit):
        assert name not in {"device.secret", "ca.pem"}
        return original(parent, name, limit)

    monkeypatch.setattr(maintenance, "_private_read", read)
    assert core.retire(core.review(), archive=archive).mode is mode
    assert lab.ledger.inspect().mode is mode


def test_full_history_can_be_archived_without_schema_downgrade(lab, core, archive):
    # Real production bound, not a relaxed test-only limit.
    for _ in range(128):
        prepare(lab)
        lab.ledger.suspend()
    with pytest.raises(BrowserResumeError):
        prepare(lab)
    assert core.retire(core.review(), archive=archive).archived == 127
    with closing(sqlite3.connect(lab.ledger.path)) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (2,)
    assert len(rows(lab)) == 1
    prepare(lab)
    assert len(rows(lab)) == 2


@pytest.mark.parametrize("kind", ["version1", "active", "corrupt", "clock", "wal"])
def test_review_refuses_unsafe_state_without_repair_or_sidecars(lab, core, kind):
    if kind != "version1":
        prepare(lab)
    if kind == "active":
        lab.ledger.resume(lab.ledger.inspect().revision)
    elif kind == "corrupt":
        sql(lab, "UPDATE browser_resume SET phase='unexpected'")
    elif kind == "clock":
        lab.clock[0] -= 20
    elif kind == "wal":
        sql(lab, "PRAGMA journal_mode=WAL")
    before = snapshot(lab)
    with pytest.raises(BrowserResumeMaintenanceError):
        core.review()
    assert snapshot(lab) == before


@pytest.mark.parametrize("kind", ["pause", "commit", "clock", "expired", "history", "config"])
def test_changed_review_never_creates_an_archive(lab, core, archive, kind):
    approval = prepare(lab)
    review = core.review()
    if kind == "pause":
        lab.ledger.suspend()
    elif kind == "commit":
        commit(lab, approval)
    elif kind == "clock":
        lab.clock[0] -= 0.001
    elif kind == "expired":
        lab.clock[0] += 120
    elif kind == "history":
        sql(lab, "UPDATE browser_resume SET phase='claimed'")
    else:
        config = json.loads((lab.root / "client.json").read_bytes())
        config["device_id"] = "other"
        (lab.root / "client.json").write_text(json.dumps(config))
    before = snapshot(lab)
    with pytest.raises(BrowserResumeMaintenanceError):
        core.retire(review, archive=archive)
    assert snapshot(lab) == before and not archive.exists()


@pytest.mark.parametrize("change", [
    {"revision": True}, {"revision": 0}, {"fingerprint": "bad"}, {"mode": "paused"},
    {"approvals": True}, {"pending": 2}, {"created_at": float("nan")}, {"expires_at": 0},
])
def test_invalid_review_is_redacted_and_nonmutating(lab, core, archive, change):
    approval = prepare(lab)
    review = replace(core.review(), **change)
    before = snapshot(lab)
    with pytest.raises(BrowserResumeMaintenanceError) as caught:
        core.retire(review, archive=archive)
    assert approval.ticket not in str(caught.value)
    assert snapshot(lab) == before and not archive.exists()


@pytest.mark.parametrize("kind", ["existing", "symlink", "hardlink", "fifo", "public", "nested",
                                 "relative", "missing-parent"])
def test_unsafe_archive_targets_are_not_overwritten(lab, core, archive, kind):
    prepare(lab)
    review = core.review()
    if kind == "existing":
        archive.write_bytes(b"keep")
    elif kind == "symlink":
        archive.symlink_to(archive.parent / "absent")
    elif kind == "hardlink":
        os.link(lab.root / "device.secret", archive)
    elif kind == "fifo":
        os.mkfifo(archive)
    elif kind == "public":
        parent = archive.parent / "public"
        parent.mkdir(mode=0o755)
        archive = parent / "archive.json"
    elif kind == "nested":
        archive = lab.root / "archive.json"
    elif kind == "relative":
        archive = type(archive)("relative.json")
    else:
        archive = archive.parent / "missing" / "archive.json"
    before = snapshot(lab)
    with pytest.raises(BrowserResumeMaintenanceError):
        core.retire(review, archive=archive)
    assert snapshot(lab) == before
    if kind == "existing":
        assert archive.read_bytes() == b"keep"


def test_review_does_not_authorize_a_copied_same_identity_profile(lab, core, archive, tmp_path):
    prepare(lab)
    review = core.review()
    copied = tmp_path / "copied"
    shutil.copytree(lab.root, copied)
    other = BrowserResumeMaintenance(copied, clock=lambda: lab.clock[0])
    with pytest.raises(BrowserResumeMaintenanceError):
        other.retire(review, archive=archive)
    assert not archive.exists()


@pytest.mark.parametrize("stage", ["file-write", "directory-sync", "readback", "expired", "sql"])
def test_failure_retains_archive_without_claiming_completion(
    lab, core, archive, monkeypatch, stage,
):
    prepare(lab)
    review = core.review()
    before = snapshot(lab)

    def fail(*_):
        raise OSError("PRIVATE sentinel")

    with monkeypatch.context() as patch:
        if stage == "file-write":
            write = maintenance._write

            def partial(fd, name, value):
                write(fd, name, value[:17])
                fail()

            patch.setattr(maintenance, "_write", partial)
        elif stage == "directory-sync":
            sync = maintenance.os.fsync

            def fsync(fd):
                if os.path.isdir(f"/proc/self/fd/{fd}"):
                    fail()
                sync(fd)

            patch.setattr(maintenance.os, "fsync", fsync)
        elif stage == "readback":
            patch.setattr(maintenance, "_private_read", fail)
        elif stage == "expired":
            write_archive = core._archive

            def late(path, document):
                write_archive(path, document)
                lab.clock[0] += 120

            patch.setattr(core, "_archive", late)
        else:
            save = core._recovery._save

            def rollback(db, state):
                save(db, state)
                fail()

            patch.setattr(core._recovery, "_save", rollback)
        with pytest.raises(BrowserResumeMaintenanceError) as caught:
            core.retire(review, archive=archive)
        assert "PRIVATE" not in str(caught.value)
    assert snapshot(lab) == before and archive.exists()
    with pytest.raises(BrowserResumeMaintenanceError):
        core.confirm(review, archive=archive)
    with pytest.raises(BrowserResumeMaintenanceError):
        core.retire(review, archive=archive)


def test_lost_postcommit_reply_is_confirmed_read_only_not_replayed(lab, core, archive, monkeypatch):
    prepare(lab)
    review = core.review()
    with monkeypatch.context() as patch:
        patch.setattr(core, "confirm", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError()))
        with pytest.raises(BrowserResumeMaintenanceError):
            core.retire(review, archive=archive)
    after = snapshot(lab)
    lab.clock[0] += 1000  # Confirmation does not reuse an expired mutation review.
    result = core.confirm(review, archive=archive)
    assert result.mode is RecoveryMode.PAUSED and result.revision == review.revision + 1
    assert snapshot(lab) == after


def test_inflight_commit_loses_to_retirement(lab, core, archive):
    approval = prepare(lab)
    entered, release = Event(), Event()

    def proof(config, record):
        entered.set()
        assert release.wait(5)
        return evidence(config, record)

    with ThreadPoolExecutor() as pool:
        future = pool.submit(commit, lab, approval, prove_server=proof)
        try:
            assert entered.wait(5)
            review = core.review()
            assert review.pending == 1
            result = core.retire(review, archive=archive)
        finally:
            release.set()
        with pytest.raises(BrowserResumeError):
            future.result(timeout=5)
    assert core.confirm(review, archive=archive) == result
    assert rows(lab)[0][1] == "cancelled"


def test_two_retirements_cannot_consume_the_same_review(lab, core, archive):
    prepare(lab)
    review = core.review()

    def retire(path):
        try:
            return core.retire(review, archive=path)
        except BrowserResumeMaintenanceError:
            return None

    paths = [archive, archive.with_name("competing.json")]
    with ThreadPoolExecutor() as pool:
        results = list(pool.map(retire, paths))
    assert sum(result is not None for result in results) == 1
    assert sum(path.exists() for path in paths) == 1


def test_process_death_after_archive_is_not_false_completion(lab, core, archive):
    prepare(lab)
    review = core.review()
    before = snapshot(lab)
    script = """
import json, os, sys
from pathlib import Path
from sds200.browser_device_recovery import RecoveryMode
from sds200.browser_device_resume_maintenance import (
    BrowserResumeMaintenance, BrowserResumeMaintenanceReview)
value = json.load(sys.stdin)
value['mode'] = RecoveryMode(value['mode'])
core = BrowserResumeMaintenance(Path(sys.argv[1]))
save = core._archive
def interrupted(path, document):
    save(path, document)
    os._exit(73)
core._archive = interrupted
core.retire(BrowserResumeMaintenanceReview(**value), archive=Path(sys.argv[2]))
"""
    result = subprocess.run([sys.executable, "-c", script, str(lab.root), str(archive)],
                            input=json.dumps(asdict(review)), capture_output=True, text=True,
                            timeout=10)
    assert result.returncode == 73 and result.stdout == result.stderr == ""
    assert archive.exists() and snapshot(lab) == before
    with pytest.raises(BrowserResumeMaintenanceError):
        core.confirm(review, archive=archive)


@pytest.mark.parametrize("kind", ["after", "before", "review", "duplicate", "mode"])
def test_confirmation_rejects_edited_or_unsafe_archive(lab, core, archive, kind):
    prepare(lab)
    review = core.review()
    core.retire(review, archive=archive)
    document = json.loads(archive.read_bytes())
    if kind == "after":
        document["after"]["state"]["mode"] = "active"
    elif kind == "before":
        document["before"]["approvals"][0]["phase"] = "complete"
    elif kind == "review":
        document["review"]["pending"] = 0
    if kind == "duplicate":
        archive.write_bytes(archive.read_bytes().replace(b'{', b'{"version":1,', 1))
    elif kind == "mode":
        archive.chmod(0o644)
    else:
        archive.write_text(json.dumps(document))
    before = snapshot(lab)
    with pytest.raises(BrowserResumeMaintenanceError):
        core.confirm(review, archive=archive)
    assert snapshot(lab) == before


def test_native_protocol_has_no_maintenance_action(lab):
    from tests.test_browser_device_native import invoke

    prepare(lab)
    before = snapshot(lab)
    for action in ("retire", "resume-retire", "resume-history", "maintenance"):
        result = invoke(lab.root, action)
        assert result["ok"] is False
    assert snapshot(lab) == before
