"""Actual source/runtime files and owned pidfd, synthetic App/idle metadata.

Original publication pins are a fixture, not a claimed accepted Startup. The
publisher and actual PID1 bridge are tested separately. Host path aliasing is
explicit; no installed App, scanner, service or recovery is qualified here.
"""

import hashlib
import importlib.util
import os
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

from . import test_supplemental_recording_app_publish as publication_tests  # noqa: F401
from . import test_supplemental_recording_candidate_qualification as candidates

candidate = candidates.candidate
layout, image_umask, supervised = candidates.layout, candidates.image_umask, candidates.supervised
image, configured = candidates.image, candidates.configured
NAME = "qualify_supplemental_recording_app"
SPEC = importlib.util.spec_from_file_location(
    NAME, Path(candidates.m.__file__).with_name(NAME + ".py")
)
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)
pytestmark = pytest.mark.parametrize("candidate", ["app_bridge"], indirect=True)


@pytest.fixture
def app(candidate, tmp_path, monkeypatch):
    return setup_app(candidate, tmp_path, monkeypatch)


def setup_app(candidate, tmp_path, monkeypatch, *, data=None):
    """Allow original test-owned data to exist before the App publication pins."""
    s, p = candidate, candidate.plan
    s.data = tmp_path / "app-data" if data is None else data
    if data is None:
        s.data.mkdir(mode=0o700)
    else:
        assert data.is_dir() and data.is_relative_to(tmp_path)
    s.case_root = s.data / p.native_root.name
    s.case_root.mkdir(mode=0o700)
    for name in ("idle", "app-start"):
        (s.case_root / name).mkdir(mode=0o700)

    def put(path, value):
        raw = m.base.encode(value)
        path.write_bytes(raw)
        path.chmod(0o600)
        return hashlib.sha256(raw).hexdigest()

    put(s.case_root / "idle/lease.json", p.lease)
    receipt_sha = put(
        s.case_root / "app-start/launch.json",
        dict(
            schema=1,
            kind="finite-recording-app-idle-launch-v1",
            case=p.case,
            plan_sha256=p.sha256,
            lease_sha256=p.lease_sha256,
        ),
    )
    s.published = m.publication.Published(
        p.sha256,
        p.lease_sha256,
        receipt_sha,
        m.files.identity(s.case_root.stat()),
        tuple(
            (name, m.files.identity((s.case_root / name).stat()))
            for name in ("idle/lease.json", "app-start/launch.json")
        ),
    )
    # Native idle claim is independently authenticated by the existing Idle
    # collector, which is explicitly synthetic in this fixture.
    put(s.case_root / "idle/consumed.json", dict(synthetic_native_idle_claim=True))
    put(
        s.case_root / "app-start/consumed.json",
        dict(
            schema=1,
            kind="finite-recording-app-idle-consumed-v1",
            case=p.case,
            plan_sha256=p.sha256,
            lease_sha256=p.lease_sha256,
            receipt_sha256=receipt_sha,
        ),
    )
    command = m.argv(p)
    s.container.update(Path=command[0], Args=list(command[1:]))
    s.container["Config"].update(Entrypoint=[command[0]], Cmd=list(command[1:]))
    monkeypatch.setattr(m.publication, "ROOT_UID", os.geteuid())
    monkeypatch.setattr(m.publication, "ROOT_GID", os.getegid())
    monkeypatch.setattr(m.publication, "_data", lambda plan: s.data)

    @contextmanager
    def alias(path, guard):
        assert path in (s.case_root, s.root / "usr/local/libexec")
        fd = os.open(path, m.files.DIRECTORY)
        original = m.files.identity(os.fstat(fd))

        def unchanged():
            guard()
            assert m.files.identity(os.fstat(fd)) == original == m.files.identity(path.stat())

        try:
            unchanged()
            yield fd, unchanged
            unchanged()
        finally:
            os.close(fd)

    monkeypatch.setattr(m.publication, "_chain", alias)
    s.make_app = lambda: m.AppCandidateQualification(
        p,
        s.idle,
        s.witness,
        s.docker,
        published=s.published,
        bridge_sha256=hashlib.sha256(s.bridge_raw).hexdigest(),
        image_environment_sha256=m.launch.runtime.environment(s.image_env),
        timezone=candidates.env.TIMEZONE,
        hostname=candidates.env.HOSTNAME,
        architecture="amd64",
    )
    return s


def test_explicit_app_profile_brackets_inputs_with_full_qualification(app):
    obj, original = app.make_app(), app.plan.raw
    assert obj() is None and obj.elapsed_seconds < 2
    first = obj.consumption
    value = object()
    assert obj.during(lambda: value) is value
    assert obj.consumption is first and obj.plan.raw == original
    assert not app.witness.exited()


def test_old_direct_policy_still_refuses_the_bridge(app):
    old = app.make()
    candidates.denied(old)


def test_native_preparation_is_not_accepted_by_idle_only_qualification(app):
    p = app.published
    app.published = m.publication.NativePublished(
        p.plan_sha256,
        p.lease_sha256,
        p.receipt_sha256,
        p.root_identity,
        p.file_identities,
        app.plan.native_baseline_sha256,
        (),
    )
    candidates.launch.denied(app.make_app)


@pytest.mark.parametrize(
    "fault",
    [
        "claim",
        "receipt",
        "lease",
        "extra",
        "root_mode",
        "bridge",
        "bridge_missing",
        "consumption_reset",
    ],
)
def test_no_changed_input_can_be_adopted_on_later_read(app, fault):
    obj = app.make_app()
    obj()
    names = {
        "claim": "app-start/consumed.json",
        "receipt": "app-start/launch.json",
        "lease": "idle/lease.json",
    }
    if fault in names:
        path = app.case_root / names[fault]
        changed = path.with_name("replacement")
        changed.write_bytes(path.read_bytes())
        changed.chmod(0o600)
        changed.replace(path)
    elif fault == "extra":
        (app.case_root / "app-start/unplanned").mkdir()
    elif fault == "root_mode":
        app.case_root.chmod(0o755)
    elif fault == "consumption_reset":
        obj.consumption = None
    elif fault == "bridge_missing":
        (app.root / "usr/local/libexec/sdsctl-recording-app-idle.py").unlink()
    else:
        path = app.root / "usr/local/libexec/sdsctl-recording-app-idle.py"
        path.chmod(0o644)  # This test owns the synthetic immutable-image fixture.
        path.write_bytes(b"PRIVATE")
        path.chmod(0o444)
    candidates.denied(obj)
    os.fstat(app.witness.fd)


@pytest.mark.parametrize("fault", ["command", "image", "published", "digest", "wrong_claim"])
def test_independent_command_and_original_input_pins_are_required(app, monkeypatch, fault):
    if fault == "published":
        object.__setattr__(app.published, "plan_sha256", "f" * 64)
        candidates.launch.denied(app.make_app)
        return
    obj = app.make_app()
    if fault == "command":
        app.container["Config"]["Cmd"][-1] = "0" * 32
    elif fault == "image":
        original = m.launch.plans.ordinary.Docker.image

        def changed(*args):
            value = original(*args)
            value["Config"]["Cmd"][-1] = "0" * 32
            return value

        monkeypatch.setattr(m.launch.plans.ordinary.Docker, "image", changed)
    elif fault == "digest":
        obj.bridge_sha256 = "f" * 64
    else:
        (app.case_root / "app-start/consumed.json").write_bytes(b"{}")
    candidates.denied(obj)


def test_replacement_during_contributing_observation_poisoned(app):
    obj = app.make_app()

    def observe():
        (app.case_root / "app-start/consumed.json").write_bytes(b"{}")

    candidates.launch.denied(lambda: obj.during(observe))
    assert obj.failed
    candidates.launch.denied(obj)
