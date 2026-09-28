"""Original App-published input bytes consumed in a disposable mount namespace.

Actual source/profile/baseline files, original publication/qualification, and
native preflight are joined without rewriting any published path or digest.
The child can read its fixed /data, /media, /opt and /usr/local aliases; all
binds are read-only except the original empty guardian directory in claim tests.
It has no network namespace connectivity and additionally refuses socket
construction. No native runtime/Ready/recording is started.
App/Engine/Startup and interpreter-image provenance remain synthetic boundaries.
"""

import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from . import test_scanner_display_deployment as profiles
from . import test_supplemental_recording_app_actual_source as sources
from . import test_supplemental_recording_app_launch as launches

candidate_module = sources.candidate_module
app_module = sources.qualifiers.apps
m = launches.m
layout, image_umask, supervised, image, configured, staged = (
    sources.layout,
    sources.image_umask,
    sources.supervised,
    sources.image,
    sources.configured,
    sources.staged,
)
native = sources.native
pytestmark = pytest.mark.parametrize("candidate", ["app_native"], indirect=True)


@pytest.fixture(scope="module")
def bwrap():
    command = shutil.which("bwrap")
    if command is None:
        pytest.skip("Local namespace preflight requires bubblewrap")
    try:
        result = subprocess.run(
            [command, "--unshare-user", "--unshare-net", "--ro-bind", "/", "/", "--", "/bin/true"],
            capture_output=True,
            timeout=5,
            check=False,
        )
    except subprocess.TimeoutExpired:
        pytest.skip("Local namespace preflight capability probe timed out")
    if result.returncode:
        pytest.skip("Local unprivileged mount/user/network namespaces unavailable")
    return command


@pytest.fixture
def mapped(tmp_path, bwrap):
    data, media = tmp_path / "native-data", tmp_path / "native-media"
    data.mkdir(mode=0o700)
    media.mkdir(mode=0o700)
    return SimpleNamespace(data=data, media=media, bwrap=bwrap)


def seed_projection(mapped, protected, case):
    """Only original fixture setup: no observed profile or baseline is repaired."""
    projection = candidate_module.m.plans.projection
    recording = projection.recording
    host_source = mapped.media / protected.source.relative_to(projection.HOST_MEDIA)
    host_source.parent.mkdir(mode=0o700, parents=True)
    host_source.write_bytes(profiles.SOURCE)
    host_source.chmod(0o600)
    state = mapped.data / "accepted"
    configuration = mapped.data / "profile.toml"
    fields = dict(
        version=1,
        endpoint_id=str(profiles.UUID(int=1)),
        source_id=str(profiles.UUID(int=2)),
        scanner_target=getattr(mapped, "scanner_target", profiles.TARGET),
        source_path=str(host_source),
        state_directory=str(state),
    )
    configuration.write_bytes(profiles.document(fields))
    configuration.chmod(0o600)
    config = profiles.load_scanner_display_configuration(configuration)
    profiles.initialize_display_profile_storage(state, config.binding.endpoint_id)
    repository = config.repository()
    offered = repository.prepare(config.binding, acquired_at=datetime.now(UTC))
    repository.commit(offered, imported_at=datetime.now(UTC))

    # The accepted profile binding stays identical. Final container paths are
    # written once during preparation, before any profile/plan/source owner.
    fields.update(
        source_path=str(
            projection.NATIVE_MEDIA / protected.source.relative_to(projection.HOST_MEDIA)
        ),
        state_directory="/data/accepted",
    )
    configuration.write_bytes(profiles.document(fields))
    deployment = mapped.data / "deployment.toml"
    deployment.write_bytes(
        profiles.document(profiles.fields(Path("/data"), profile_config="/data/profile.toml"))
    )
    deployment.chmod(0o600)
    cached = m.native.cached
    mapped.profile = cached.checksum(
        {
            "deployment": cached.sha(deployment.read_bytes()),
            "configuration": cached.sha(configuration.read_bytes()),
            "accepted": cached.sha((state / "accepted-profile.json").read_bytes()),
            "source": cached.sha(host_source.read_bytes()),
        }
    )
    root = mapped.media / protected.recordings.relative_to(projection.HOST_MEDIA)
    root.mkdir(mode=0o700, parents=True)
    previous = root / "previous.wav"
    previous.write_bytes(b"old evidence unchanged")
    previous.chmod(0o600)
    baseline = recording.evidence.capture_baseline(root, case)
    writer = recording.monitor.Writer(os.geteuid(), os.getegid(), 0o644)
    raw = recording.manifest_bytes(
        replace(baseline, root=protected.recordings),
        writer,
        hashlib.sha256(
            getattr(mapped, "endpoint", "rtsp://192.0.2.25/au:scanner.au").encode()
        ).hexdigest(),
    )
    mapped.recordings, mapped.source = root, host_source
    return projection.project(protected, recording._decode(raw))


@pytest.fixture
def candidate(supervised, image, configured, monkeypatch, request, staged, mapped):
    yield from candidate_module.setup_candidate(
        supervised,
        image,
        configured,
        monkeypatch,
        request,
        source_tree=staged.layout,
        projection_factory=lambda protected, case: seed_projection(mapped, protected, case),
    )


@pytest.fixture
def app(candidate, tmp_path, monkeypatch, mapped):
    return app_module.setup_app(candidate, tmp_path, monkeypatch, data=mapped.data)


@pytest.fixture
def launch_case(native, monkeypatch, mapped):
    for state in launches.setup_launch_case(native, monkeypatch):
        state.publish = lambda state=state: m.publish_launch(
            state.startup,
            state.original,
            specification=state.spec,
            profile_sha256=mapped.profile,
        )
        yield state


DRIVER = r"""
import json, os, socket, subprocess, sys
from pathlib import Path
sys.path.insert(0, '/opt/sdsctl-supplemental-recording')
import supplemental_recording_source as source
import supplemental_recording_launch_plan as native
import supplemental_recording_guardian as guardian
def forbidden(*args, **kwargs):
    raise AssertionError('Read-only native preflight attempted an active operation')
socket.socket = socket.getaddrinfo = subprocess.Popen = forbidden
path, plan_pin, source_pin, runtime, ready_by, claim = sys.argv[1:]
assert sys.prefix == '/usr/local'
try:
    layout = source.Layout(Path(runtime), Path('/opt/sdsctl-supplemental-recording'))
    evidence = layout.verify(source_pin)
    plan = native.load(Path(path), expected_sha256=plan_pin, expected_source_sha256=source_pin)
    if claim == 'yes':
        context = guardian.control.Context(plan, float(ready_by))
        guardian._claim(Path(path).parent / 'guardian', context, evidence,
            context.ready_by + plan.stored.contract.maximum_recording_seconds)
except (source.UnconfirmedSource, native.UnconfirmedLaunchPlan,
        guardian.UnconfirmedGuardian, native.protected.UnconfirmedProtection):
    print(json.dumps({'phase': 'refused'}), flush=True)
else:
    print(json.dumps(dict(phase='preflight', source=evidence.sha256, plan=plan.sha256,
        host_plan=plan.host_plan_sha256, projection=plan.projection_sha256,
        profile=plan.profile_sha256, generation=plan.generation,
        manifest=plan.stored.manifest_sha256, uid=os.geteuid(), gid=os.getegid())), flush=True)
"""


def namespace_command(state, mapped, staged, *, writable=(), loopback_peers=False, recording=False):
    # bwrap builds a disposable root, not mount points on the host. All fixture
    # and host dependency binds are read-only. No privileged helper, Docker,
    # installed App, scanner network or external namespace is entered.
    command = [
        mapped.bwrap,
        "--unshare-user",
        "--die-with-parent",
        "--tmpfs",
        "/",
    ]
    if not loopback_peers:
        command += ["--unshare-net"]
    for path in ("/usr", "/lib", "/lib64", "/etc", "/home", "/tmp"):
        if Path(path).exists():
            command += ["--ro-bind", path, path]
    command += [
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--dir",
        "/opt",
        "--ro-bind",
        str(staged.python.parent.parent),
        "/usr/local",
        "--ro-bind",
        str(staged.layout.native),
        "/opt/sdsctl-supplemental-recording",
        "--ro-bind",
        str(mapped.data),
        "/data",
        "--ro-bind",
        str(mapped.media),
        "/media",
    ]
    for name in writable:
        assert name in ("launch/guardian", "sockets", "receipts")
        command += [
            "--bind",
            str(state.case_root / name),
            str(state.plan.native_root / name),
        ]
    if recording:
        # Only this test's original recording directory; all other media and
        # all profile/plan/source bytes stay read-only in the namespace.
        relative = mapped.recordings.relative_to(mapped.media)
        assert relative.parts and ".." not in relative.parts
        command += ["--bind", str(mapped.recordings), str(Path("/media") / relative)]
    command += [
        "--remount-ro",
        "/",
        "--chdir",
        "/",
        "--",
    ]
    return command


def check_native(state, mapped, staged, publication, *, claim=False):
    command = namespace_command(
        state, mapped, staged, writable=("launch/guardian",) if claim else ()
    )
    command += [
        "/usr/local/bin/python",
        "-I",
        "-B",
        "-c",
        DRIVER,
        str(state.plan.native_root / "launch/launch.json"),
        publication.sha256,
        staged.pin,
        "/" + str(candidate_module.m.plans.fixed.PACKAGE),
        str(state.plan.lease["ready_by"]),
        "yes" if claim else "no",
    ]
    result = subprocess.run(
        command, capture_output=True, timeout=10, check=False, env={"PATH": "/usr/bin:/bin"}
    )
    assert result.returncode == 0, result.stderr.decode()[:4096]
    assert result.stderr == b"" and len(result.stdout) < 4096
    return json.loads(result.stdout)


def test_actual_guardian_consumes_only_original_prepared_directory(launch_case, mapped, staged):
    s = launch_case
    qualified = s.publish()
    receipt = qualified.launch_inputs
    original = s.plan.raw, s.plan.lease, receipt.raw
    directory = s.case_root / "launch/guardian"
    before = m.files.identity(directory.stat())
    result = check_native(s, mapped, staged, receipt, claim=True)
    assert result["phase"] == "preflight" and result["source"] == staged.pin
    assert m.files.identity(directory.stat())[:6] == before[:6] == receipt.guardian_identity[:6]
    target = directory / "launch-claimed.json"
    value = json.loads(target.read_bytes())
    assert value["context"]["launch"] == receipt.sha256
    assert value["context"]["ready_by"] == s.plan.lease["ready_by"]
    assert value["context"]["profile"] == mapped.profile
    assert value["source"]["sha256"] == staged.pin
    assert target.stat().st_mode & 0o7777 == 0o600
    claim = target.read_bytes(), m.files.identity(target.stat())
    # Deliberate fixture-only refusal test: this cannot renew/overwrite a claim.
    assert check_native(s, mapped, staged, receipt, claim=True) == {"phase": "refused"}
    assert claim == (target.read_bytes(), m.files.identity(target.stat()))
    assert original == (s.plan.raw, s.plan.lease, receipt.raw)
    assert (mapped.recordings / "previous.wav").read_bytes() == b"old evidence unchanged"
    for name in ("receipts", "sockets"):
        assert not tuple((s.case_root / name).iterdir())
    # Actual claim is not authenticated Ready or permission to begin.
    launches.denied(qualified)


def test_original_app_publication_passes_actual_namespace_preflight(launch_case, mapped, staged):
    s = launch_case
    original = s.plan.raw, s.plan.lease, s.published, s.native_baseline
    qualified = s.publish()
    publication = qualified.launch_inputs
    assert publication.raw == (s.case_root / "launch/launch.json").read_bytes()
    result = check_native(s, mapped, staged, publication)
    assert result == dict(
        phase="preflight",
        source=staged.pin,
        plan=publication.sha256,
        host_plan=s.plan.sha256,
        projection=s.plan.projection_sha256,
        profile=mapped.profile,
        generation=s.original.generation,
        manifest=s.plan.native_baseline_sha256,
        uid=os.geteuid(),
        gid=os.getegid(),
    )
    assert qualified() is None
    assert original == (s.plan.raw, s.plan.lease, s.published, s.native_baseline)
    assert (mapped.recordings / "previous.wav").read_bytes() == b"old evidence unchanged"
    for name in ("receipts", "sockets"):
        assert not tuple((s.case_root / name).iterdir())
    launches.denied(s.publish)


@pytest.mark.parametrize("fault", ["profile", "recording", "launch", "helper_mode"])
def test_original_native_preflight_refuses_drift_without_repinning(
    launch_case, mapped, staged, fault
):
    s = launch_case
    qualified = s.publish()
    publication = qualified.launch_inputs
    if fault == "profile":
        mapped.source.write_bytes(mapped.source.read_bytes() + b"\nPRIVATE_CHANGED\n")
    elif fault == "recording":
        (mapped.recordings / "previous.wav").write_bytes(b"PRIVATE_CHANGED")
    elif fault == "launch":
        (s.case_root / "launch/launch.json").write_bytes(publication.raw + b" ")
    else:
        staged.layout.native.chmod(0o775)
    assert check_native(s, mapped, staged, publication) == {"phase": "refused"}
    for name in ("receipts", "sockets"):
        assert not tuple((s.case_root / name).iterdir())
    launches.denied(s.publish)
