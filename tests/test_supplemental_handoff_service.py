"""Private service assembly with real journals and fake host I/O; no real Apps."""

import hashlib
import importlib.util
import json
import sys
from copy import deepcopy
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from . import test_supplemental_handoff_app_read as app_tests
from . import test_supplemental_handoff_operator as operator_tests
from . import test_supplemental_handoff_protected as protected_tests  # noqa: F401
from . import test_supplemental_handoff_recovery as recovery_tests

NAME = "supplemental_handoff_service"
spec = importlib.util.spec_from_file_location(
    NAME, Path(__file__).resolve().parents[1] / "scripts" / (NAME + ".py")
)
s = importlib.util.module_from_spec(spec)
sys.modules[NAME] = s
spec.loader.exec_module(s)
p, h, r = recovery_tests.p, recovery_tests.h, recovery_tests.r
CASE, BOOT, IMAGE = recovery_tests.CASE, recovery_tests.BOOT, recovery_tests.IMAGE


def plan_value():
    layouts = []
    seals = []
    versions = {
        p.NORMAL: "0.30.0-normal",
        p.CANDIDATE: "0.30.0-candidate",
        "published_sds200": "0.30.0",
    }
    for slug in (p.NORMAL, p.CANDIDATE):
        data = Path("/mnt/data/supervisor/apps/data") / slug
        media = Path("/mnt/data/supervisor/media") / slug
        layout = s.ProtectedLayout(
            slug=slug,
            context=Path("/mnt/data/supervisor/apps/local") / slug.removeprefix("local_"),
            data=data,
            media=media.parent,
            recordings=media / "recordings",
            deployment=data / "deployment.toml",
            configuration=data / "profile.toml",
            accepted=data / "accepted/accepted-profile.json",
            source=media / "profiles/profile.cfg",
            image_package_sha256="a" * 64,
        )
        layouts.append({k: str(v) if isinstance(v, Path) else v for k, v in asdict(layout).items()})
        seals.append(
            asdict(
                s.AppSeal(slug, versions[slug], IMAGE, "b" * 64, s.ProtectedFiles(*("a" * 64,) * 4))
            )
        )
    host = recovery_tests.Host()
    return dict(
        schema=1,
        case=CASE,
        boot=BOOT,
        source="c" * 40,
        firmware="Version 1.26.01",
        helper_image=IMAGE,
        cli_image=IMAGE,
        cli_generation=h.generation(host.container(h.CLI), name=h.CLI, image=IMAGE),
        core_image=IMAGE,
        core_generation="f" * 64,
        core_version="2026.9.3",
        normal_generation=h.generation(
            host.container("app_" + p.NORMAL), name="app_" + p.NORMAL, image=IMAGE
        ),
        code={name: "e" * 64 for name in s.MODULES},
        seals=seals,
        layouts=layouts,
        installed_versions=versions,
        other_scanner_apps=["published_sds200"],
    )


@pytest.fixture
def plan():
    return s.decode_plan(plan_value())


def test_reviewed_plan_derives_only_scoped_probe_paths(plan):
    assert plan.root == Path("/mnt/data/sdsctl-handoff-" + CASE)
    assert plan.bundle == Path("/mnt/data/sdsctl-handoff-code-" + CASE)
    assert plan.probes[p.NORMAL] == app_tests.a.ProbePaths(
        "/data/deployment.toml", "/media/" + p.NORMAL + "/recordings"
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema", True),
        ("case", "../case"),
        ("boot", "unknown"),
        ("source", "main"),
        ("firmware", "bad\nfirmware"),
        ("helper_image", "image:latest"),
        ("cli_generation", "bad"),
        ("core_version", ""),
        ("code", {}),
        ("other_scanner_apps", []),
        ("layouts", []),
        ("seals", []),
        ("installed_versions", {}),
        ("extra", True),
    ],
)
def test_unqualified_plan_refused(field, value):
    data = plan_value()
    data[field] = value
    with pytest.raises((p.UnsafeHandoff, TypeError, ValueError)):
        s.decode_plan(data)


@pytest.mark.parametrize(
    "fault",
    [
        "shared_source",
        "shared_recordings",
        "other_recordings_profile",
        "package",
        "duplicate_seal",
        "missing_app",
        "code_extra",
    ],
)
def test_plan_cross_bindings_are_not_inferred(fault):
    data = plan_value()
    if fault == "shared_source":
        data["layouts"][1]["source"] = data["layouts"][0]["source"]
    elif fault == "shared_recordings":
        data["layouts"][1]["recordings"] = data["layouts"][0]["recordings"] + "/child"
    elif fault == "other_recordings_profile":
        data["layouts"][1]["source"] = data["layouts"][0]["recordings"] + "/profile.cfg"
    elif fault == "package":
        data["layouts"][1]["image_package_sha256"] = "c" * 64
    elif fault == "duplicate_seal":
        data["seals"][1] = deepcopy(data["seals"][0])
    elif fault == "missing_app":
        data["installed_versions"].pop(p.CANDIDATE)
    else:
        data["code"]["other.py"] = "a" * 64
    with pytest.raises((p.UnsafeHandoff, TypeError, ValueError)):
        s.decode_plan(data)


def test_plan_loader_requires_private_file_hash_and_exact_case_path(tmp_path, monkeypatch):
    root = tmp_path / "case"
    root.mkdir(mode=0o700)
    monkeypatch.setattr(s.Plan, "root", property(lambda _: root))
    raw = p.encode(plan_value())
    file = root / "plan.json"
    file.write_bytes(raw)
    file.chmod(0o600)
    expected = hashlib.sha256(raw).hexdigest()
    assert s.load_plan(root, expected).case == CASE
    with pytest.raises(p.UnsafeHandoff):
        s.load_plan(root, "0" * 64)
    file.chmod(0o644)
    with pytest.raises(p.UnsafeHandoff):
        s.load_plan(root, expected)


def test_readonly_bundle_requires_exact_content_owner_and_modes(plan, monkeypatch):
    values = {
        name: dict(mode=0o444, uid=0, gid=0, sha256=hashed) for name, hashed in plan.code.items()
    }
    monkeypatch.setattr(s, "inventory", lambda _: deepcopy(values))
    s.verify_bundle(plan)
    key = next(iter(values))
    values[key]["mode"] = 0o644
    with pytest.raises(p.UnsafeHandoff):
        s.verify_bundle(plan)
    values[key]["mode"] = 0o444
    values[key]["sha256"] = "f" * 64
    with pytest.raises(p.UnsafeHandoff):
        s.verify_bundle(plan)


def test_launch_is_independent_networkless_finite_and_has_no_app_command(plan):
    command = s.launch(plan, "d" * 64)
    assert command[0] == "/usr/bin/systemd-run"
    assert "--property=Restart=no" in command and "--property=RuntimeMaxSec=1510s" in command
    assert "--pid=host" in command and "--cgroupns=host" in command
    assert "--network=none" in command and "--read-only" in command
    assert "--cap-drop=ALL" in command and "--cap-add=DAC_READ_SEARCH" in command
    assert not any(
        item in ("--privileged", "--net=host", "start", "stop", "ha") for item in command
    )
    assert command[-2:] == (str(plan.root), "d" * 64)
    assert (
        "--property=ExecStopPost=-/usr/bin/docker stop --time=2 sdsctl-handoff-" + CASE in command
    )


def test_report_is_exclusive_and_never_claims_recovery_implicitly(tmp_path):
    tmp_path.chmod(0o700)
    s.publish_report(tmp_path, "outcome.json", {"restoration_verified": False})
    assert json.loads((tmp_path / "outcome.json").read_bytes()) == {"restoration_verified": False}
    with pytest.raises(FileExistsError):
        s.publish_report(tmp_path, "outcome.json", {"restoration_verified": True})
    assert (tmp_path / "outcome.json").stat().st_mode & 0o777 == 0o600


@pytest.fixture
def assembly(plan, tmp_path, monkeypatch):
    root = tmp_path / "case"
    root.mkdir(mode=0o700)
    for name in ("journal", "inbox"):
        (root / name).mkdir(mode=0o700)
    monkeypatch.setattr(s.Plan, "root", property(lambda _: root))
    host = recovery_tests.Host()
    original = host.read
    pins = {seal.slug: seal.pin for seal in plan.seals}

    def observe():
        sample = original()
        return replace(
            sample,
            observation=replace(
                sample.observation,
                normal=replace(sample.observation.normal, pin=pins[p.NORMAL]),
                candidate=replace(sample.observation.candidate, pin=pins[p.CANDIDATE]),
            ),
        )

    host.read = observe
    monkeypatch.setattr(r, "read_identity", host.identity)
    monkeypatch.setattr(r, "ProcessWitness", host.witness)
    monkeypatch.setattr(s, "verify_bundle", lambda _: None)
    fresh_calls = []
    monkeypatch.setattr(s, "fresh_candidate", lambda *_: fresh_calls.append(True))
    monkeypatch.setattr(s, "Docker", lambda: host)
    monkeypatch.setattr(s, "AppReads", lambda *a, **k: SimpleNamespace(read=lambda *_: None))
    monkeypatch.setattr(s, "SupervisorReads", lambda *a, **k: None)
    monkeypatch.setattr(s, "HostObserver", lambda *a, **k: SimpleNamespace(read=host.read))
    monkeypatch.setattr(s, "read_clock", host.clock)
    return plan, host, root, fresh_calls


@pytest.mark.parametrize("network", [s.READER_NETWORK, s.AUDIO_NETWORK])
def test_service_preparation_without_operator_request_changes_no_app(
    assembly, monkeypatch, network
):
    plan, host, root, fresh_calls = assembly
    plan = replace(plan, network=network)
    observed = []

    def observer(*args, **kwargs):
        observed.append(kwargs["network"])
        return SimpleNamespace(read=host.read)

    monkeypatch.setattr(s, "HostObserver", observer)

    def wait(seconds):
        assert seconds == 0.25
        ready = json.loads((root / "ready.json").read_bytes())
        assert ready["no_app_handoff_attempted"] and not host.sent
        host.now += 301

    monkeypatch.setattr(s, "time", SimpleNamespace(sleep=wait))
    outcome = s.run(plan)
    assert outcome["phase"] == "review" and outcome["restoration_verified"] is False
    assert host.sent == [] and fresh_calls == [True] and all(w.closed for w in host.handles)
    assert observed == [network]


@pytest.mark.parametrize("network", [s.READER_NETWORK, s.AUDIO_NETWORK])
def test_real_journal_and_inbox_complete_four_command_cycle_on_fake_host(
    assembly, monkeypatch, network
):
    plan, host, root, _ = assembly
    plan = replace(plan, network=network)
    notices = []

    def wait(seconds):
        assert seconds == 0.25
        host.now += 1
        ready = json.loads((root / "ready.json").read_bytes())
        kind = (
            "request"
            if not notices
            else "finish"
            if len(host.sent) == 2 and "finish" not in notices
            else None
        )
        if kind:
            operator_tests.o.publish(
                root / "inbox",
                operator_tests.o.notice(kind, CASE, BOOT, ready["baseline"], host.now),
            )
            notices.append(kind)

    monkeypatch.setattr(s, "time", SimpleNamespace(sleep=wait))
    outcome = s.run(plan)
    assert outcome["phase"] == "complete" and outcome["restoration_verified"] is True
    assert host.sent == [
        ("stop", p.NORMAL),
        ("start", p.CANDIDATE),
        ("stop", p.CANDIDATE),
        ("start", p.NORMAL),
    ]
    assert notices == ["request", "finish"] and all(w.closed for w in host.handles)
    with p.Journal(root / "journal") as journal:
        assert len(journal.machine.state.exited_processes) == 2
    assert json.loads((root / "outcome.json").read_bytes()) == outcome


def test_existing_notice_cannot_be_adopted_as_fresh_service_request(assembly, monkeypatch):
    plan, host, root, fresh_calls = assembly
    (root / "inbox/request.json").write_bytes(b"{}")
    with pytest.raises(p.UnsafeHandoff):
        s.run(plan)
    assert not host.sent and not fresh_calls and not (root / "ready.json").exists()


@pytest.mark.parametrize("fault", ["boot", "bundle", "generation", "native_unhealthy"])
def test_unqualified_service_baseline_never_publishes_ready(assembly, monkeypatch, fault):
    plan, host, root, _ = assembly
    if fault == "boot":
        host.boot = "f" * 32
    elif fault == "bundle":

        def reject(_):
            raise p.UnsafeHandoff("changed bundle")

        monkeypatch.setattr(s, "verify_bundle", reject)
    elif fault == "generation":
        plan = replace(plan, normal_generation="f" * 64)
    else:
        host.read_fault = lambda sample: replace(
            sample,
            observation=replace(
                sample.observation, normal=replace(sample.observation.normal, healthy=False)
            ),
        )
    with pytest.raises(p.UnsafeHandoff):
        s.run(plan)
    assert not host.sent and not (root / "ready.json").exists()
    assert all(w.closed for w in host.handles)


def test_service_expiry_restores_without_operator_finish_even_with_bad_inbox(assembly, monkeypatch):
    plan, host, root, _ = assembly
    requested = False
    advanced = False
    candidate_waits = 0

    def wait(_):
        nonlocal requested, advanced, candidate_waits
        host.now += 1
        if not requested:
            ready = json.loads((root / "ready.json").read_bytes())
            operator_tests.o.publish(
                root / "inbox",
                operator_tests.o.notice("request", CASE, BOOT, ready["baseline"], host.now),
            )
            requested = True
        elif len(host.sent) == 2 and not advanced:
            candidate_waits += 1
            if candidate_waits == 2:  # Candidate observed healthy and pidfd bound.
                (root / "inbox/finish.json").write_bytes(b"invalid")
                host.now += p.TRIAL_SECONDS + 1
                advanced = True

    monkeypatch.setattr(s, "time", SimpleNamespace(sleep=wait))
    result = s.run(plan)
    assert result["restoration_verified"] is True
    assert host.sent == [
        ("stop", p.NORMAL),
        ("start", p.CANDIDATE),
        ("stop", p.CANDIDATE),
        ("start", p.NORMAL),
    ]
    assert all(w.closed for w in host.handles)


def test_completed_service_reentry_cannot_replay_commands(assembly, monkeypatch):
    plan, host, root, _ = assembly

    def wait(_):
        host.now += 301

    monkeypatch.setattr(s, "time", SimpleNamespace(sleep=wait))
    first = s.run(plan)
    assert first["phase"] == "review"
    with pytest.raises(FileExistsError):
        s.run(plan)
    assert not host.sent
    assert json.loads((root / "outcome.json").read_bytes()) == first


def test_fresh_candidate_rejects_retained_container_and_case(plan, tmp_path, monkeypatch):
    with pytest.raises(p.UnsafeHandoff):
        s.fresh_candidate(
            plan, SimpleNamespace(containers=lambda: [{"Names": ["/app_" + p.CANDIDATE]}])
        )
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    original = s.data_directory
    monkeypatch.setattr(s, "data_directory", lambda _: original(data))
    host = SimpleNamespace(containers=lambda: [])
    s.fresh_candidate(plan, host)
    (data / ("sdsctl-supplemental-acceptance-" + CASE)).mkdir()
    with pytest.raises(ValueError, match="already exists"):
        s.fresh_candidate(plan, host)


@pytest.mark.parametrize("mode", [0o700, 0o755, 0o777, 0o775, 0o750])
def test_supervisor_data_root_permissions_are_observed_not_repaired(tmp_path, mode):
    import os

    tmp_path.chmod(mode)
    if mode in (0o700, 0o755):
        fd = s.data_directory(tmp_path)
        os.close(fd)
    else:
        with pytest.raises(p.UnsafeHandoff):
            s.data_directory(tmp_path)
    assert tmp_path.stat().st_mode & 0o777 == mode
