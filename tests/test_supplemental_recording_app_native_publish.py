"""Original accepted Startup and real local native-tree publication.

App/cache/HAOS ancestry are synthetic, as in the idle-only publisher tests.
No App, native runtime, network, scanner or recording is started here.
"""

import hashlib
import os
import stat

import pytest

from . import test_supplemental_recording_app_publish as publishers

m, assembly = publishers.m, publishers.assembly
(
    layout,
    tree,
    routing,
    projection,
    binding,
    directory,
    prepared,
    joined,
    before_handoff,
    service_case,
    publishing,
) = (
    publishers.layout,
    publishers.tree,
    publishers.routing,
    publishers.projection,
    publishers.binding,
    publishers.directory,
    publishers.prepared,
    publishers.joined,
    publishers.before_handoff,
    publishers.service_case,
    publishers.publishing,
)


def denied(owner):
    with pytest.raises(m.UnconfirmedPublication) as caught:
        m.publish_native(owner)
    assert str(caught.value) == m.MESSAGE and caught.value.__suppress_context__
    assert not owner.lock.locked()


def test_precreated_tree_copies_only_original_baseline_without_recapture(publishing, monkeypatch):
    s, owner = publishing, publishing.publisher_owner
    plan = owner.original.plan
    original = plan.raw, owner.clock, owner.projected.native_manifest

    def forbidden(*args, **kwargs):
        pytest.fail("Native publication tried to recapture the recording baseline")

    with monkeypatch.context() as patch:
        patch.setattr(m.plans.projection.recording.evidence, "capture_baseline", forbidden)
        result = m.publish_native(owner)
    assert type(result) is m.NativePublished and type(result) is not m.Published
    assert result.baseline_sha256 == plan.native_baseline_sha256
    baseline = (s.native / "baseline/baseline.json").read_bytes()
    assert baseline == original[2]
    assert hashlib.sha256(baseline).hexdigest() == result.baseline_sha256
    names = ["app-start", "baseline", "idle", "launch", "receipts", "sockets"]
    assert sorted(p.name for p in s.native.iterdir()) == names
    assert [name for name, _ in result.directory_identities] == names
    for name, identity in result.directory_identities:
        assert m.files.identity((s.native / name).stat())[:6] == identity
        assert stat.S_IMODE((s.native / name).stat().st_mode) == 0o700
    for name in ("launch", "sockets", "receipts"):
        assert not list((s.native / name).iterdir())
    assert not (s.native / "launch/launch.json").exists()
    assert result.root_identity == m.files.identity(s.native.stat())
    assert [name for name, _ in result.file_identities] == [
        "idle/lease.json",
        "baseline/baseline.json",
        "app-start/launch.json",
    ]
    assert (plan.raw, owner.clock, owner.projected.native_manifest) == original
    assert len(s.cached_calls) == 1 and not owner.service_used
    assert not owner.closed and not owner.failed


@pytest.mark.parametrize("first", ["idle", "native"])
def test_one_owner_cannot_switch_publication_profiles(publishing, first):
    s = publishing
    owner = s.publisher_owner
    (m.publish if first == "idle" else m.publish_native)(owner)
    files = {p.relative_to(s.native): p.read_bytes() for p in s.native.rglob("*.json")}
    publishers.denied(owner)
    denied(owner)
    assert files == {p.relative_to(s.native): p.read_bytes() for p in s.native.rglob("*.json")}


@pytest.mark.parametrize("fault", ["extra_file", "replacement", "symlink", "mode"])
def test_reserved_output_directories_cannot_change_during_publication(
    publishing, monkeypatch, fault
):
    s, write = publishing, m.os.write
    touched = []

    def altered(fd, raw):
        result = write(fd, raw)
        if b'"kind":"finite-recording-app-idle-launch-v1"' in raw:
            target = s.native / "sockets"
            if fault == "extra_file":
                (target / "unplanned").write_bytes(b"PRIVATE")
            elif fault == "mode":
                target.chmod(0o755)
            else:
                target.rename(s.native / "preserved-sockets")
                if fault == "replacement":
                    target.mkdir(mode=0o700)
                else:
                    target.symlink_to(s.native / "preserved-sockets", target_is_directory=True)
            touched.append(True)
        return result

    monkeypatch.setattr(m.os, "write", altered)
    denied(s.publisher_owner)
    assert touched == [True]
    assert (s.native / "baseline/baseline.json").is_file()
    assert (s.native / "app-start/launch.json").is_file()
    assert s.publisher_owner.app_idle_publication_used
    denied(s.publisher_owner)


def test_failure_before_final_receipt_preserves_original_manifest(publishing, monkeypatch):
    s, write = publishing, m.os.write

    def failed(fd, raw):
        if b'"kind":"finite-recording-app-idle-launch-v1"' in raw:
            raise OSError("PRIVATE failed receipt")
        return write(fd, raw)

    monkeypatch.setattr(m.os, "write", failed)
    denied(s.publisher_owner)
    assert (
        s.native / "baseline/baseline.json"
    ).read_bytes() == s.publisher_owner.projected.native_manifest
    assert (s.native / "app-start/launch.json").read_bytes() == b""
    denied(s.publisher_owner)
    os.fstat(s.publisher_owner.clock.fd)


def test_native_tree_does_not_weaken_existing_bridge_inputs(publishing, monkeypatch):
    s, bridge = publishing, publishers.bridge_tests.m
    result = m.publish_native(s.publisher_owner)
    monkeypatch.setattr(bridge, "DATA", s.data)
    monkeypatch.setattr(bridge, "ROOT_UID", os.geteuid())
    monkeypatch.setattr(bridge, "ROOT_GID", os.getegid())
    monkeypatch.setattr(bridge, "_gate", lambda: None)
    calls = []

    def executing(executable, args):
        calls.append((executable, args))
        raise publishers.bridge_tests.WouldExec

    monkeypatch.setattr(bridge.os, "execv", executing)
    with pytest.raises(publishers.bridge_tests.WouldExec):
        bridge.run(["--case", s.publisher_owner.original.plan.case])
    assert len(calls) == 1 and calls[0][1][-1] == result.lease_sha256
    assert not list((s.native / "launch").iterdir())
    denied(s.publisher_owner)


def test_original_projection_failure_prevents_case_creation(publishing):
    owner = publishing.publisher_owner
    original, owner.projected = owner.projected, object()
    try:
        denied(owner)
        assert not publishing.native.exists()
    finally:
        owner.projected = original
