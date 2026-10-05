"""Real local inventories/pidfd with synthetic App/idle/baseline provenance.

The actual accepted baseline copy is tested separately by the native publisher.
This fixture never qualifies an installed App, native launch, or recording.
"""

import hashlib
import os
from dataclasses import replace

import pytest

from . import test_supplemental_recording_app_qualification as apps

m, candidates = apps.m, apps.candidates
candidate, app = apps.candidate, apps.app
layout, image_umask, supervised = apps.layout, apps.image_umask, apps.supervised
image, configured = apps.image, apps.configured
pytestmark = pytest.mark.parametrize("candidate", ["app_native"], indirect=True)


@pytest.fixture
def native(app):
    s, original = app, app.published
    for name in ("baseline", "launch", "sockets", "receipts"):
        (s.case_root / name).mkdir(mode=0o700)
    manifest = s.case_root / "baseline/baseline.json"
    manifest.write_bytes(s.native_baseline)
    manifest.chmod(0o600)
    s.published = m.publication.NativePublished(
        original.plan_sha256,
        original.lease_sha256,
        original.receipt_sha256,
        m.files.identity(s.case_root.stat()),
        tuple(
            (name, m.files.identity((s.case_root / name).stat()))
            for name in m.NativeIdleQualification.INPUT_FILES
        ),
        s.plan.native_baseline_sha256,
        tuple(
            (name, m.files.identity((s.case_root / name).stat())[:6])
            for name in m.NativeIdleQualification.DIRECTORIES
        ),
    )
    s.make_native = lambda **changes: m.NativeIdleQualification(
        s.plan,
        s.idle,
        s.witness,
        s.docker,
        **(
            dict(
                published=s.published,
                bridge_sha256=hashlib.sha256(s.bridge_raw).hexdigest(),
                image_environment_sha256=m.launch.runtime.environment(s.image_env),
                timezone=candidates.env.TIMEZONE,
                hostname=candidates.env.HOSTNAME,
                architecture="amd64",
            )
            | changes
        ),
    )
    return s


@pytest.mark.parametrize("workers", [1, 2])
def test_fresh_native_idle_tree_is_read_only_and_cannot_replace_other_phases(native, workers):
    s = native
    qualifier = s.make_native(runtime_workers=workers)
    before = s.plan.raw, s.plan.lease, s.published
    fds = len(os.listdir("/proc/self/fd"))
    assert qualifier() is None
    assert qualifier.during(lambda: "read only") == "read only"
    assert len(os.listdir("/proc/self/fd")) == fds
    assert before == (s.plan.raw, s.plan.lease, s.published)
    assert qualifier.elapsed_seconds < 2 and not s.witness.exited()
    candidates.launch.denied(s.make_app)
    candidates.launch.denied(lambda: m.AppRetainedQualification(qualifier, object()))
    assert type(qualifier) is not m.launch.CandidateQualification
    assert not any(hasattr(qualifier, name) for name in ("start", "begin", "restore"))


@pytest.mark.parametrize("directory", ["baseline", "launch", "receipts", "sockets"])
@pytest.mark.parametrize("fault", ["mode", "replacement", "symlink", "extra"])
def test_native_input_directories_are_not_an_exclusion(native, directory, fault):
    s, qualifier = native, native.make_native()
    qualifier()
    path = s.case_root / directory
    if fault == "mode":
        path.chmod(0o755)
    elif fault == "extra":
        (path / "unplanned").write_bytes(b"PRIVATE")
    else:
        moved = s.case_root / (directory + "-preserved")
        path.rename(moved)
        if fault == "symlink":
            path.symlink_to(moved, target_is_directory=True)
        else:
            path.mkdir(mode=0o700)
    candidates.denied(qualifier)
    os.fstat(s.witness.fd)


@pytest.mark.parametrize("fault", ["changed", "same_bytes_replacement", "unsafe_mode", "missing"])
def test_original_manifest_cannot_be_recaptured_or_replaced(native, fault):
    s, qualifier = native, native.make_native()
    manifest = s.case_root / "baseline/baseline.json"
    if fault == "changed":
        manifest.write_bytes(b'{"changed":true}')
    elif fault == "unsafe_mode":
        manifest.chmod(0o644)
    elif fault == "missing":
        manifest.unlink()
    else:
        replacement = manifest.with_name("replacement")
        replacement.write_bytes(manifest.read_bytes())
        replacement.chmod(0o600)
        replacement.replace(manifest)
    candidates.denied(qualifier)


@pytest.mark.parametrize("fault", ["baseline_pin", "names", "identity", "file_order", "type"])
def test_only_exact_original_native_publication_shape_is_accepted(native, fault):
    s, p = native, native.published
    if fault == "baseline_pin":
        s.published = replace(p, baseline_sha256="f" * 64)
    elif fault == "names":
        s.published = replace(p, directory_identities=p.directory_identities[:-1])
    elif fault == "identity":
        names = list(p.directory_identities)
        name, identity = names[0]
        names[0] = name, (*identity[:5], True)
        s.published = replace(p, directory_identities=tuple(names))
    elif fault == "file_order":
        s.published = replace(p, file_identities=tuple(reversed(p.file_identities)))
    else:
        s.published = object()
    candidates.launch.denied(s.make_native)


@pytest.mark.parametrize(
    "target", ["launch/launch.json", "sockets/api.sock", "receipts/result.json"]
)
def test_new_runtime_input_during_read_is_not_silently_promoted(native, target):
    s, qualifier = native, native.make_native()

    def observe():
        (s.case_root / target).write_bytes(b"PRIVATE unqualified phase change")
        return "unverified"

    candidates.launch.denied(lambda: qualifier.during(observe))
    assert qualifier.failed and qualifier.elapsed_seconds is None
    candidates.launch.denied(qualifier)


def test_publication_pins_cannot_be_changed_after_construction(native):
    qualifier = native.make_native()
    object.__setattr__(native.published, "directory_identities", ())
    candidates.denied(qualifier)
