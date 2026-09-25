"""Real command/peer/clock/files, synthetic Docker and installed App inputs.

No installed command, confinement, original-input provenance or scanner test is
claimed by these aliases. The actual sender runs in a distinct owned process.
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_permission_probe as peers
from . import test_supplemental_recording_startup_manifest as manifests

NAME = "supplemental_recording_service_command"
SPEC = importlib.util.spec_from_file_location(NAME, Path(peers.m.__file__).with_name(NAME + ".py"))
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)

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
    manifest_case,
) = (
    manifests.layout,
    manifests.tree,
    manifests.routing,
    manifests.projection,
    manifests.binding,
    manifests.directory,
    manifests.prepared,
    manifests.joined,
    manifests.before_handoff,
    manifests.service_case,
    manifests.manifest_case,
)
operator = manifests.baseline_tests.integration.m
OBSERVER = peers.OBSERVER.replace('"d" * 64,', 'value["baseline_sha256"],')


@pytest.fixture
def command_case(manifest_case, monkeypatch):
    s = manifest_case
    s.startup.close()  # Unused fixture owner; the command acquires its own declaration.
    s.owners, s.services, s.sequence = [], [], []
    monkeypatch.setattr(m.permission, "ROOT_UID", os.geteuid())
    monkeypatch.setattr(m.permission.domains, "ROOT_UID", os.geteuid())

    def identity(pid, cid):
        return m.peer.process.process_identity(
            pid,
            cid,
            Path(f"/proc/{pid}/stat").read_text(),
            f"0::/system.slice/docker-{cid}.scope\n",
        )

    monkeypatch.setattr(m.peer.process, "read_identity", identity)
    monkeypatch.setattr(m.peer, "current_identity", lambda: identity(os.getpid(), "b" * 64))
    monkeypatch.setattr(m, "baseline_root", lambda _: s.manifest_directory)
    # Keep the fixture's real Docker class and explicitly synthetic transport.
    monkeypatch.setattr(m.startup.plans.ordinary.Docker, "__new__", lambda cls: s.docker)
    owner_init, service_init = m.startup.Startup.__init__, operator.IdleService.__init__

    def owning(owner, original):
        owner_init(owner, original)
        s.startup = owner
        s.owners.append(owner)

    def assembling(service, *args, **kwargs):
        service_init(service, *args, **kwargs)
        s.services.append(service)

    monkeypatch.setattr(m.startup.Startup, "__init__", owning)
    monkeypatch.setattr(operator.IdleService, "__init__", assembling)
    poll = m.startup.Startup.poll
    s.submit = True

    def polling(owner):
        result = poll(owner)
        if result is None and s.submit:
            s.submit = False
            manifests.baseline_tests.integration.startups.submit(owner)
        return result

    monkeypatch.setattr(m.startup.Startup, "poll", polling)

    def forbidden(*args, **kwargs):
        pytest.fail("Preparation selected a service phase or scanner/App action")

    for name in (
        "run",
        "start_native",
        "cancel_native",
        "start_recording",
        "finish_recording",
        "observe_recording",
        "abandon_recording",
        "prepare_launch",
        "prepare_candidate",
        "observe_candidate",
    ):
        monkeypatch.setattr(operator.IdleService, name, forbidden)
    monkeypatch.setattr(operator.Inbox, "consume", forbidden)
    monkeypatch.setattr(operator.Publisher, "publish", forbidden)
    # Assert dependency-ordered cleanup using actual retained handles.
    for cls, label in (
        (operator.IdleService, "service"),
        (operator.launch.bootstrap.Journal, "journal"),
        (m.startup.Startup, "startup"),
        (m.peer.clock.ClockWitness, "clock"),
        (m.permission.Permission, "permission"),
        (m.peer.PeerConnection, "channel"),
        (m.permission.domains.ZeroDomain, "domain"),
        (m.peer.process.ProcessWitness, "pidfd"),
        (m.peer.declaration.Declaration, "declaration"),
    ):
        close = cls.close

        def closing(obj, close=close, label=label):
            if label in ("service", "journal"):
                assert not s.startup.clock.closed
            s.sequence.append((label, obj))
            return close(obj)

        monkeypatch.setattr(cls, "close", closing)

    def run(mode="send", digest=None):
        with tempfile.TemporaryDirectory(prefix="service-peer-") as directory:
            peer_root = Path(directory)
            monkeypatch.setattr(m.peer, "peer_root", lambda _: peer_root)
            child = subprocess.Popen(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    "-c",
                    OBSERVER,
                    str(Path(m.__file__).parent),
                    str(peer_root / m.peer.NAME),
                    mode,
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
            selected = digest or s.manifest_digest
            try:
                child.stdin.write(
                    json.dumps(
                        dict(
                            template=json.loads(s.template.raw),
                            baseline_sha256=selected,
                        )
                    ).encode()
                    + b"\n"
                )
                assert peers.peers.line(child) == b"ready\n"
                return m.prepare_idle_service(
                    s.declaration.root, s.template.sha256, selected, identity(child.pid, "a" * 64)
                )
            finally:
                child.stdin.close()
                child.wait(timeout=5)
                output, errors = child.stdout.read(), child.stderr.read()
                child.stdout.close()
                child.stderr.close()
                assert child.returncode == 0 and not errors, (output, errors)

    s.run = run
    return s


def test_original_peer_through_separate_acceptance_to_passive_assembly(command_case, capsys):
    s, before = command_case, peers.fds()
    assert s.run() == 75
    assert capsys.readouterr().out == m.MILESTONE + "\n"
    assert len(s.owners) == len(s.services) == 1
    owner, service = s.owners[0], s.services[0]
    assert owner.accepted and owner.service_used and owner.closed and not owner.failed
    assert service.closed and not service.used and service.original is owner.original
    assert service.clock_witness is owner.clock and owner.clock is not s.clocks[0]
    assert len(s.clocks) == 3 and all(c.closed for c in s.clocks)
    assert len(s.cached_calls) == len(s.samples) == 1
    assert len(s.preflights) == 2 and not s.preflights[-1].used
    assert owner.projected.host.manifest_sha256 == s.manifest_digest
    assert s.manifest_path.read_bytes() == s.manifest_raw
    assert {p.name for p in s.root.iterdir()} == {
        "startup-claim.json",
        "plan.json",
        "startup-acceptance.json",
        "journal",
        "inbox",
    }
    assert len(service.journal.entries) == 1 and not list((s.root / "inbox").iterdir())
    labels = [label for label, _ in s.sequence]
    assert labels.index("service") < labels.index("journal") < labels.index("startup")
    assert labels[-6:] == ["permission", "channel", "domain", "pidfd", "clock", "declaration"]
    assert peers.fds() == before


def test_no_host_manifest_or_publication_before_permission(command_case, monkeypatch, capsys):
    s = command_case

    def forbidden(*args, **kwargs):
        pytest.fail("Pre-permission command selected host/baseline/plan operations")

    monkeypatch.setattr(manifests.protected, "load_baseline", forbidden)
    monkeypatch.setattr(m.startup.publication.Publisher, "publish", forbidden)
    peers.denied(lambda: s.run("refuse"))
    assert not s.owners and not s.preflights and not s.cached_calls and not s.services
    assert not list(s.root.iterdir()) and capsys.readouterr().out == ""


def test_bad_baseline_digest_stops_before_host_or_service_clock(command_case, capsys):
    s = command_case
    peers.denied(lambda: s.run(digest="f" * 64))
    assert not s.preflights and not s.cached_calls and not s.services
    assert not list(s.root.iterdir()) and len(s.clocks) == 2
    assert s.startup.closed and s.startup.failed and all(c.closed for c in s.clocks)
    assert capsys.readouterr().out == "" and s.manifest_path.read_bytes() == s.manifest_raw


@pytest.mark.parametrize("fault", ["late", "missing", "assembly", "close", "interrupt"])
def test_no_completion_claim_after_partial_failure(command_case, monkeypatch, capsys, fault):
    s = command_case
    if fault in ("late", "missing"):
        s.submit = fault == "late"

        def expire(seconds):
            monkeypatch.setattr(m.time, "monotonic", lambda: s.startup.offer.deadline)

        monkeypatch.setattr(m.time, "sleep", expire)
    elif fault == "close":
        close = operator.IdleService.close

        def failed(service):
            close(service)
            raise OSError("PRIVATE uncertain close")

        monkeypatch.setattr(operator.IdleService, "close", failed)
    else:
        init = operator.IdleService.__init__

        def failed(service, *args, **kwargs):
            if fault == "interrupt":
                raise KeyboardInterrupt("PRIVATE")
            init(service, *args, **kwargs)
            service.failed = True

        monkeypatch.setattr(operator.IdleService, "__init__", failed)
    if fault == "interrupt":
        with pytest.raises(KeyboardInterrupt):
            s.run()
    else:
        peers.denied(s.run)
    assert capsys.readouterr().out == "" and (s.root / "plan.json").is_file()
    assert (s.root / "startup-claim.json").is_file()
    assert s.startup.closed and all(c.closed for c in s.clocks)
    assert s.manifest_path.read_bytes() == s.manifest_raw
    if fault in ("late", "missing"):
        assert not s.services and not (s.root / "journal").exists()


def test_frozen_clock_cannot_extend_acceptance_loop(monkeypatch):
    sleeps = []
    monkeypatch.setattr(m.time, "monotonic", lambda: 1.0)
    monkeypatch.setattr(m.time, "sleep", sleeps.append)
    owner = SimpleNamespace(offer=SimpleNamespace(deadline=16.0), poll=lambda: None)
    with pytest.raises(m.permission.UnconfirmedPermission):
        m._accepted_assembly(owner, None)
    assert len(sleeps) == 151 and max(sleeps) == 0.1


def test_lost_completion_output_does_not_retry_or_erase_preparation(command_case, monkeypatch):
    s, calls = command_case, []

    def lost(*args, **kwargs):
        calls.append(args)
        assert s.startup.closed and s.services[0].closed
        assert all(c.closed for c in s.clocks)
        raise OSError("PRIVATE lost output acknowledgment")

    monkeypatch.setattr(m, "print", lost, raising=False)
    peers.denied(s.run)
    assert calls == [(m.MILESTONE,)] and len(s.services) == 1
    assert (s.root / "journal/0000.json").exists()
    assert s.manifest_path.read_bytes() == s.manifest_raw


def test_uncertain_acceptance_write_preserves_files_without_assembly(
    command_case, monkeypatch, capsys
):
    s = command_case
    publish = manifests.baseline_tests.integration.startups.submit
    calls = []

    def lost(owner):
        calls.append(True)
        publish(owner)
        raise OSError("PRIVATE publication acknowledgment lost")

    monkeypatch.setattr(manifests.baseline_tests.integration.startups, "submit", lost)
    peers.denied(s.run)
    assert calls == [True] and not s.services
    assert (s.root / "startup-acceptance.json").is_file()
    assert not (s.root / "journal").exists() and s.startup.closed
    assert capsys.readouterr().out == ""


def test_baseline_is_case_bound_and_not_the_writable_case_or_declaration():
    case = peers.peers.template_tests.value()["plan"]["case"]
    assert m.baseline_root(case) == Path("/mnt/data/sdsctl-recording-baseline-" + case)
    assert m.baseline_root(case) != m.peer.declaration.declaration_root(case)
    assert m.baseline_root(case) != m.peer.peer_root(case)


@pytest.mark.parametrize("flag", [m.MODE, "--permission-probe", "--startup-probe", "--run"])
def test_uninstalled_or_other_commands_refuse_before_imports(flag):
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            m.__file__,
            "/mnt/data/invalid",
            "a" * 64,
            "b" * 64,
            "2:3:" + "c" * 64,
            flag,
        ],
        capture_output=True,
        timeout=3,
    )
    assert result.returncode == 64 and result.stdout == b""
    assert result.stderr.decode().strip() == m.MESSAGE
