"""Fixed local maintenance selection, input ownership and evidence retention."""

from __future__ import annotations

import json
import os
import selectors
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

import sds200.browser_assets
from sds200 import browser_device_resume_boundary as boundary
from sds200.browser_device_profile_access import BrowserProfileAccessError, browser_profile_access
from sds200.browser_device_recovery import ExchangeFailure, RecoveryMode
from sds200.browser_device_resume_boundary import BrowserResumeBoundary, BrowserResumeBoundaryError
from tests.test_browser_device_native import certificates as certificates
from tests.test_browser_device_native import configure, invoke, private
from tests.test_browser_device_native import root as root
from tests.test_browser_device_resume import INTENT, prepare
from tests.test_browser_device_resume import lab as lab
from tests.test_browser_device_resume_maintenance import snapshot
from tests.test_browser_device_retirement import _JOINED

pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or os.geteuid() == 0, reason="Non-root Linux native boundary",
)


@pytest.fixture
def archives(tmp_path):
    path = tmp_path / "maintenance archives"
    path.mkdir(mode=0o700)
    return path


@pytest.fixture
def core(lab, archives):
    return BrowserResumeBoundary(lab.root, archives=archives, clock=lambda: lab.clock[0])


def selection(lab, core, kind):
    if kind == "retire":
        prepare(lab)
    return core.review(kind=kind, browser_intent=INTENT)


@pytest.mark.parametrize("kind", ["retire", "reconcile"])
@pytest.mark.parametrize("mode", [mode for mode in RecoveryMode if mode is not RecoveryMode.ACTIVE])
def test_fixed_paths_redacted_review_and_read_only_restart_confirmation(lab, core, archives,
                                                                      kind, mode):
    if mode is not RecoveryMode.PAUSED:
        lab.ledger.resume(lab.ledger.inspect().revision)

        def fail():
            raise ExchangeFailure(mode)

        lab.ledger.authenticate(fail)
    review = selection(lab, core, kind)
    before = snapshot(lab)
    assert not list(archives.iterdir())
    assert all(value not in repr(review) for value in
               (INTENT, review.binding, review.operation_id, review.native.fingerprint))
    proof = core.execute(review)
    assert proof.mode is mode and proof.revision == review.native.revision + 1
    assert proof.intent == INTENT and proof.identity == lab.configuration.identity
    assert set(archives.iterdir()) == {archives / review.operation_id}
    files = list((archives / review.operation_id).iterdir())
    assert {path.name for path in files} == {"review.json", "native-history.json"}
    assert (archives / review.operation_id).stat().st_mode & 0o777 == 0o700
    for path in files:
        assert path.stat().st_mode & 0o777 == 0o600
        assert before["device.secret"].strip() not in path.read_bytes()
    assert {k: v for k, v in snapshot(lab).items() if k != "recovery.sqlite"} == {
        k: v for k, v in before.items() if k != "recovery.sqlite"}
    after = snapshot(lab), {path.name: path.read_bytes() for path in files}
    fresh = BrowserResumeBoundary(lab.root, archives=archives, clock=lambda: lab.clock[0] + 121)
    assert fresh.confirm(operation_id=review.operation_id, browser_intent=INTENT) == proof
    assert (snapshot(lab), {path.name: path.read_bytes() for path in files}) == after
    with pytest.raises(BrowserResumeBoundaryError):
        core.execute(review)
    with pytest.raises(BrowserResumeBoundaryError):
        fresh.execute(review)  # Only confirmation, never execution, survives restart.
    assert lab.ledger.authenticate(lambda: pytest.fail("Unexpected authentication")).session is None


@pytest.mark.parametrize("kind", ["retire", "reconcile"])
def test_shared_reader_and_exclusive_writer_contract_in_real_native_process(lab, core, kind):
    review = selection(lab, core, kind)
    before = snapshot(lab)
    with browser_profile_access(lab.root, exclusive=True):
        assert invoke(lab.root, "status") == {"version": 1, "ok": False, "mode": "setup_error"}
        with pytest.raises(BrowserResumeBoundaryError):
            core.execute(review)
    assert snapshot(lab) == before
    with browser_profile_access(lab.root, exclusive=False):
        assert invoke(lab.root, "status")["ok"]
    with pytest.raises(BrowserResumeBoundaryError):
        core.execute(review)  # Busy is not an automatic mutation retry.


@pytest.mark.parametrize("target", ["profile", "archives"])
@pytest.mark.parametrize("exclusive", [True, False])
def test_lock_contention_blocks_mutation_but_shared_review_is_allowed(lab, core, archives,
                                                                   target, exclusive):
    path = lab.root if target == "profile" else archives
    before = snapshot(lab)
    with browser_profile_access(path, exclusive=exclusive):
        if exclusive:
            with pytest.raises(BrowserResumeBoundaryError):
                core.review(kind="reconcile", browser_intent=INTENT)
        else:
            review = core.review(kind="reconcile", browser_intent=INTENT)
            with pytest.raises(BrowserResumeBoundaryError):
                core.execute(review)
    assert snapshot(lab) == before and not list(archives.iterdir())


@pytest.mark.parametrize("name", ["client.json", "device.secret", "ca.pem"])
@pytest.mark.parametrize("change", ["rewrite", "replace-same", "missing", "mode"])
def test_input_changes_invalidate_review_even_if_content_is_restored(lab, core, archives,
                                                                  name, change):
    review = core.review(kind="reconcile", browser_intent=INTENT)
    path = lab.root / name
    original = path.read_bytes()
    # A cooperating future writer owns the same exclusive directory lock.
    with browser_profile_access(lab.root, exclusive=True):
        if change == "rewrite":
            private(path, b"changed")
            private(path, original)
        elif change == "replace-same":
            replacement = lab.root / "staged"
            private(replacement, original)
            replacement.replace(path)
        elif change == "missing":
            path.unlink()
        else:
            path.chmod(0o644)
    before = snapshot(lab)
    with pytest.raises(BrowserResumeBoundaryError):
        core.execute(review)
    assert snapshot(lab) == before and not list(archives.iterdir())


@pytest.mark.parametrize("kind", ["retire", "reconcile"])
def test_later_input_change_invalidates_persisted_confirmation(lab, core, archives, kind):
    review = selection(lab, core, kind)
    core.execute(review)
    with browser_profile_access(lab.root, exclusive=True):
        private(lab.root / "device.secret", "sdsctl-browser-v1." + "e" * 64)
    fresh = BrowserResumeBoundary(lab.root, archives=archives, clock=lambda: lab.clock[0])
    with pytest.raises(BrowserResumeBoundaryError):
        fresh.confirm(operation_id=review.operation_id, browser_intent=INTENT)
    assert lab.ledger.inspect().mode is RecoveryMode.PAUSED


@pytest.mark.parametrize("kind", ["retire", "reconcile"])
def test_missing_private_inputs_are_preserved_and_addition_invalidates(lab, core, archives, kind):
    if kind == "retire":
        prepare(lab)
    (lab.root / "device.secret").unlink()
    (lab.root / "ca.pem").unlink()
    review = core.review(kind=kind, browser_intent=INTENT)
    core.execute(review)
    assert not (lab.root / "device.secret").exists() and not (lab.root / "ca.pem").exists()
    private(lab.root / "device.secret", "sdsctl-browser-v1." + "e" * 64)
    with pytest.raises(BrowserResumeBoundaryError):
        core.confirm(operation_id=review.operation_id, browser_intent=INTENT)


@pytest.mark.parametrize("value", [None, True, "", "../elsewhere", "/tmp/elsewhere", "F" * 64])
def test_paths_cannot_be_supplied_as_operation_ids_or_intents(lab, core, archives, value):
    before = snapshot(lab)
    with pytest.raises(BrowserResumeBoundaryError):
        core.confirm(operation_id=value, browser_intent=INTENT)
    with pytest.raises(BrowserResumeBoundaryError):
        core.review(kind="reconcile", browser_intent=value)
    assert snapshot(lab) == before and not list(archives.iterdir())


@pytest.mark.parametrize("target", ["profile", "archives"])
def test_renamed_directory_cannot_inherit_selected_ownership(lab, core, archives, target):
    review = core.review(kind="reconcile", browser_intent=INTENT)
    path = lab.root if target == "profile" else archives
    old = path.with_name(path.name + "-old")
    path.rename(old)
    shutil.copytree(old, path)
    with pytest.raises(BrowserResumeBoundaryError):
        core.execute(review)
    assert not list(archives.iterdir())


@pytest.mark.parametrize("change", ["nested", "parent", "same", "symlink", "public", "missing"])
def test_unsafe_archive_directory_is_not_created_or_repaired(lab, archives, change):
    path = archives
    if change == "nested":
        path = lab.root / "archives"
        path.mkdir(mode=0o700)
    elif change == "parent":
        path = lab.root.parent
    elif change == "same":
        path = lab.root
    elif change == "symlink":
        path = archives.with_name("alias")
        path.symlink_to(archives, target_is_directory=True)
    elif change == "public":
        archives.chmod(0o755)
    else:
        path = archives / "missing"
    before = snapshot(lab)
    with pytest.raises(BrowserResumeBoundaryError):
        BrowserResumeBoundary(lab.root, archives=path)
    assert snapshot(lab) == before


@pytest.mark.parametrize("change", ["forged-review", "wrong-intent", "duplicate-review",
                                   "older-intent", "matching-reconcile"])
def test_only_exact_live_review_and_latest_matching_history_can_execute(
    lab, core, archives, change,
):
    if change == "older-intent":
        prepare(lab)
        lab.ledger.suspend()
        prepare(lab, browser_intent="e" * 64)
        with pytest.raises(BrowserResumeBoundaryError):
            core.review(kind="retire", browser_intent=INTENT)
    elif change == "matching-reconcile":
        prepare(lab)
        lab.ledger.suspend()
        with pytest.raises(BrowserResumeBoundaryError):
            core.review(kind="reconcile", browser_intent=INTENT)
    else:
        review = core.review(kind="reconcile", browser_intent=INTENT)
        if change == "duplicate-review":
            with pytest.raises(BrowserResumeBoundaryError):
                core.review(kind="reconcile", browser_intent=INTENT)
        elif change == "forged-review":
            with pytest.raises(BrowserResumeBoundaryError):
                core.execute(replace(review))
        else:
            core.execute(review)
            with pytest.raises(BrowserResumeBoundaryError):
                core.confirm(operation_id=review.operation_id, browser_intent="e" * 64)
    assert lab.ledger.inspect().mode is RecoveryMode.PAUSED


@pytest.mark.parametrize("kind", ["retire", "reconcile"])
@pytest.mark.parametrize("stage", ["review-write", "core-write", "lost-reply"])
def test_partial_evidence_is_never_reused_or_automatically_replayed(lab, core, archives,
                                                                monkeypatch, kind, stage):
    review = selection(lab, core, kind)
    before = snapshot(lab)

    def fail(*args, **kwargs):
        raise OSError("fictional private error")

    with monkeypatch.context() as patch:
        if stage == "review-write":
            patch.setattr(boundary, "_write", fail)
        elif stage == "core-write":
            patch.setattr(boundary.BrowserResumeMaintenance, "_archive", fail)
        else:
            patch.setattr(core, "_confirm", fail)
        with pytest.raises(BrowserResumeBoundaryError) as caught:
            core.execute(review)
        assert "fictional" not in str(caught.value)
    assert (archives / review.operation_id).is_dir()
    with pytest.raises(BrowserResumeBoundaryError):
        core.execute(review)
    fresh = BrowserResumeBoundary(lab.root, archives=archives, clock=lambda: lab.clock[0])
    if stage == "lost-reply":
        assert fresh.confirm(operation_id=review.operation_id, browser_intent=INTENT).mode is (
            RecoveryMode.PAUSED)
    else:
        assert snapshot(lab) == before
        with pytest.raises(BrowserResumeBoundaryError):
            fresh.confirm(operation_id=review.operation_id, browser_intent=INTENT)


@pytest.mark.parametrize("change", ["review", "native", "symlink", "hardlink", "duplicate"])
def test_edited_or_unsafe_fixed_evidence_is_refused(lab, core, archives, change):
    review = core.review(kind="reconcile", browser_intent=INTENT)
    core.execute(review)
    path = archives / review.operation_id / "review.json"
    if change == "native":
        path = path.with_name("native-history.json")
    original = path.read_bytes()
    if change == "symlink":
        other = archives / "other"
        private(other, original)
        path.unlink()
        path.symlink_to(other)
    elif change == "hardlink":
        os.link(path, archives / "other")
    elif change == "duplicate":
        private(path, original.replace(b'"version":1', b'"version":1,"version":1'))
    else:
        private(path, b'{}')
    before = snapshot(lab)
    with pytest.raises(BrowserResumeBoundaryError):
        core.confirm(operation_id=review.operation_id, browser_intent=INTENT)
    assert snapshot(lab) == before


def test_os_releases_profile_ownership_after_process_death(lab):
    script = """
import sys
from pathlib import Path
from sds200.browser_device_profile_access import browser_profile_access
with browser_profile_access(Path(sys.argv[1]),exclusive=True):
    print('ready',flush=True)
    sys.stdin.readline()
"""
    child = subprocess.Popen([sys.executable, "-c", script, str(lab.root)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True)
    try:
        with selectors.DefaultSelector() as ready:
            ready.register(child.stdout, selectors.EVENT_READ)
            assert ready.select(timeout=5)
            assert child.stdout.readline() == "ready\n"
        with pytest.raises(BrowserProfileAccessError), browser_profile_access(
            lab.root, exclusive=False,
        ):
            pytest.fail("Exclusive child lock not held")
        child.kill()
        child.communicate(timeout=5)
        with browser_profile_access(lab.root, exclusive=True):
            pass
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=5)


_BRIDGE = """
import json,sys
from dataclasses import asdict
from pathlib import Path
from sds200.browser_device_native import load_browser_native_configuration
from sds200.browser_device_recovery import BrowserDeviceRecovery
from sds200.browser_device_resume_boundary import BrowserResumeBoundary
root,operation,archives,action=sys.argv[1:5]
root,archives=Path(root),Path(archives)
body=json.load(sys.stdin)
config=load_browser_native_configuration(root)
ledger=BrowserDeviceRecovery(root/'recovery.sqlite',config.identity)
try:
    if action=='confirm':
        assert set(body)=={'identity','intent'} and body['identity']==config.identity
        result=asdict(BrowserResumeBoundary(root,archives=archives).confirm(
            operation_id=operation,browser_intent=body['intent']))
    elif action=='invalidate':
        ledger.resume(ledger.inspect().revision);ledger.suspend();result={'changed':True}
    else:
        assert action=='native' and body=={'version':1,'action':'suspend'}
        value=ledger.suspend()
        result={'version':1,'ok':True,'mode':value.mode,'revision':value.revision,
                'retry_after':0,'renew_after':0}
except Exception:
    result={'refused':True}
print(json.dumps(result))
"""


@pytest.mark.parametrize("origin", ["https://display.example", "https://192.0.2.18:8443",
                                    "https://[fd00::18]:8443"])
@pytest.mark.parametrize("kind", ["retire", "reconcile"])
@pytest.mark.parametrize("case", ["healthy", "wrong-intent", "uncommitted", "native-change",
                                  "lost-write-ack"])
def test_fixed_boundary_joins_browser_coordinator_without_paths_in_requests(
    lab, archives, tmp_path, monkeypatch, origin, kind, case,
):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js unavailable")
    # Preserve the fixture's real SQLite identity binding for each origin.
    from sds200.browser_device_native import load_browser_native_configuration
    from sds200.browser_device_recovery import BrowserDeviceRecovery
    from sds200.browser_device_resume import BrowserDeviceResume

    configure(lab.root, origin)
    config = load_browser_native_configuration(lab.root)
    with lab.ledger._connection() as db:
        db.execute("UPDATE recovery SET identity=?", (config.identity,))
    lab.configuration = config
    lab.ledger = BrowserDeviceRecovery(lab.ledger.path, config.identity)
    lab.core = BrowserDeviceResume(lab.root)
    core = BrowserResumeBoundary(lab.root, archives=archives)
    review = selection(lab, core, kind)
    if case == "uncommitted":
        with monkeypatch.context() as patch:
            patch.setattr(boundary.BrowserResumeMaintenance, "_archive",
                          lambda *_, **__: (_ for _ in ()).throw(OSError()))
            with pytest.raises(BrowserResumeBoundaryError):
                core.execute(review)
    else:
        core.execute(review)
    bridge = tmp_path / "boundary-bridge.py"
    private(bridge, _BRIDGE)
    module = Path(sds200.browser_assets.__file__).with_name("browser_device_recovery.mjs")
    result = subprocess.run(
        [node, "--input-type=module", "-e", _JOINED, str(module), str(lab.root),
         review.operation_id, str(archives), sys.executable, str(bridge), case,
         config.identity, origin, "boundary"], capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""
    assert json.loads(result.stdout)["auth"] == 0
    assert lab.ledger.inspect().mode is RecoveryMode.PAUSED
