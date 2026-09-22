"""Read-only host evidence fixtures; no real HA, filesystem import or scanner."""

import importlib.util
import json
import sys
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from . import test_supplemental_handoff_host as host_tests
from . import test_supplemental_handoff_recovery as recovery_tests

for name in ("supplemental_handoff_observer",):
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, Path(__file__).resolve().parents[1] / "scripts" / (name + ".py")
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
o = sys.modules["supplemental_handoff_observer"]
h, p = host_tests.h, host_tests.p
IMAGE, BOOT = recovery_tests.IMAGE, recovery_tests.BOOT
OTHER = "published_sds200"


def response(data):
    return p.encode({"result": "ok", "data": data})


class Host:
    def __init__(self):
        self.values = {
            h.CLI: recovery_tests.container(h.CLI, 1),
            h.CORE: recovery_tests.container(h.CORE, 2),
            "app_" + p.NORMAL: recovery_tests.container("app_" + p.NORMAL, 3),
        }
        self.versions = {
            p.NORMAL: "0.30.0-normal",
            p.CANDIDATE: "0.30.0-candidate",
            OTHER: "0.30.0",
        }
        self.now, self.boot, self.jobs = 10.0, BOOT, True
        self.hook = None
        self.reads = []
        self.private_options = {"normal": "one", "candidate": "two"}
        self.files = {slug: o.ProtectedFiles(*("a" * 64,) * 4) for slug in (p.NORMAL, p.CANDIDATE)}
        self.native_state = (True, False)
        for value in self.values.values():
            value["HostConfig"] = {"RestartPolicy": {"Name": "no", "MaximumRetryCount": 0}}

    def container(self, key):
        return deepcopy(self.values[key])

    def containers(self):
        return [
            {
                "Id": v["Id"],
                "Names": [v["Name"]],
                "ImageID": v["Image"],
                "State": v["State"]["Status"],
            }
            for v in self.values.values()
        ]

    def image(self, identity):
        return {"Id": identity, "Os": "linux", "Architecture": "amd64"}

    def state(self, slug):
        value = self.values.get("app_" + slug)
        return "started" if value and value["State"]["Status"] != "exited" else "stopped"

    def read(self, key):
        self.reads.append(key)
        if self.hook:
            self.hook(key)
        if key == "apps":
            return response(
                {
                    "addons": [
                        {"slug": slug, "version": version, "state": self.state(slug)}
                        for slug, version in self.versions.items()
                    ]
                }
            )
        if key == "jobs":
            return host_tests.jobs(host_tests.node(done=self.jobs))
        if key == "core":
            # Deliberately no state field: actual CLI response has none.
            return response({"version": "2026.9.3"})
        slug = p.NORMAL if key == "normal" else p.CANDIDATE
        return host_tests.config(
            slug=slug,
            version=self.versions[slug],
            state=self.state(slug),
            options={"private": self.private_options[key]},
        )

    def collect_files(self, slug, container):
        assert container is None or container["Name"] == "/app_" + slug
        return self.files[slug]

    def native(self, slug, incarnation):
        assert "app_" + slug in self.values
        return o.NativeState(incarnation, *self.native_state)

    def clock(self):
        return self.boot, self.now


@pytest.fixture
def host():
    return Host()


def observer(host, **changes):
    seals = tuple(
        o.AppSeal(
            slug,
            host.versions[slug],
            IMAGE,
            h.app_configuration(host.read(key), slug=slug).settings_sha256,
            host.files[slug],
        )
        for slug, key in ((p.NORMAL, "normal"), (p.CANDIDATE, "candidate"))
    )
    args = dict(
        seals=seals,
        installed_versions=host.versions,
        other_scanner_apps=frozenset({OTHER}),
        core_image=IMAGE,
        core_generation=h.generation(host.container(h.CORE), name=h.CORE, image=IMAGE),
        core_version="2026.9.3",
        read_clock=host.clock,
        collect_files=host.collect_files,
        read_native=host.native,
    )
    args.update(changes)
    return o.HostObserver(host, host, **args)


def test_normal_baseline_is_joined_without_invented_core_state(host):
    read = observer(host)
    result = read.read()
    assert result.boot_id == BOOT
    assert result.observation.normal.pin == read.seals[p.NORMAL].pin
    assert result.observation.normal.state == "running"
    assert result.observation.normal.healthy is True
    assert result.observation.normal.recording is False
    assert result.observation.candidate.state == "stopped"
    assert result.observation.other_owners_stopped and result.observation.core_running
    assert set(host.reads) == {"normal", "candidate", "core", "jobs", "apps"}


def test_known_scanner_app_cannot_be_omitted_from_owner_gate(host):
    with pytest.raises(p.UnsafeHandoff):
        observer(host, other_scanner_apps=frozenset())


@pytest.mark.parametrize("slug,version", [("bad/path", "1"), ("valid", ""), ("valid", None)])
def test_inventory_seal_requires_valid_names_and_versions(host, slug, version):
    host.versions[slug] = version
    with pytest.raises(p.UnsafeHandoff):
        observer(host)


@pytest.mark.parametrize("version", ["", "bad\nversion", 1, "a" * 129])
def test_core_version_seal_is_bounded(host, version):
    with pytest.raises(p.UnsafeHandoff):
        observer(host, core_version=version)


@pytest.mark.parametrize("part", ["context", "package", "profile", "recordings", "options"])
def test_changed_protected_identity_is_reported_not_masked(host, part):
    read = observer(host)
    if part == "options":
        host.private_options["normal"] = "changed"
    else:
        host.files[p.NORMAL] = replace(host.files[p.NORMAL], **{part: "b" * 64})
    assert read.read().observation.normal.pin != read.seals[p.NORMAL].pin


def test_changed_package_is_never_imported_by_native_collector(host):
    read = observer(host)
    host.files[p.NORMAL] = replace(host.files[p.NORMAL], package="b" * 64)

    def forbidden(*_):
        pytest.fail("Unqualified App code must not be executed")

    read.read_native = forbidden
    sample = read.read()
    assert sample.observation.normal.pin != read.seals[p.NORMAL].pin
    assert sample.observation.normal.healthy is None


@pytest.mark.parametrize("state", [(None, None), (False, False), (True, True)])
def test_native_unknown_unhealthy_and_active_recording_survive(host, state):
    read = observer(host)
    host.native_state = state
    app = read.read().observation.normal
    assert (app.healthy, app.recording) == state


def test_missing_cached_response_preserves_running_identity_not_health(host):
    read = observer(host)

    def unavailable(*_):
        raise TimeoutError("private diagnostic must not escape")

    read.read_native = unavailable
    app = read.read().observation.normal
    assert app.state == "running" and app.generation is not None
    assert app.healthy is None and app.recording is None


def exited_normal(host):
    value = host.values["app_" + p.NORMAL]
    value["State"].update(
        Status="exited", Running=False, Pid=0, ExitCode=0, FinishedAt="2026-09-22T00:00:03Z"
    )
    return value


def test_confirmed_retained_exit_does_not_read_native_or_stopped_overlay(host):
    read = observer(host)
    exited_normal(host)

    def forbidden(*_):
        pytest.fail("Exited container must not run an exec")

    def files(slug, container):
        assert container is None
        return host.files[slug]

    read.read_native, read.collect_files = forbidden, files
    app = read.read().observation.normal
    assert app.state == "stopped" and app.generation is None
    assert app.healthy is None and app.recording is None


@pytest.mark.parametrize(
    "key,value",
    [
        ("Running", True),
        ("Pid", 12),
        ("Pid", False),
        ("Paused", True),
        ("Restarting", True),
        ("Dead", True),
        ("ExitCode", None),
        ("OOMKilled", None),
        ("Error", None),
        ("FinishedAt", "0001-01-01T00:00:00Z"),
    ],
)
def test_retained_exit_requires_full_process_state(host, key, value):
    read = observer(host)
    exited_normal(host)["State"][key] = value
    with pytest.raises(p.UnsafeHandoff):
        read.read()


@pytest.mark.parametrize("exited", [False, True])
@pytest.mark.parametrize(
    "policy",
    [
        None,
        {},
        {"Name": "always", "MaximumRetryCount": 0},
        {"Name": "no", "MaximumRetryCount": False},
        {"Name": "no", "MaximumRetryCount": 1},
    ],
)
def test_scanner_container_cannot_restart_independently(host, exited, policy):
    read = observer(host)
    if exited:
        exited_normal(host)
    host.values["app_" + p.NORMAL]["HostConfig"]["RestartPolicy"] = policy
    with pytest.raises(p.UnsafeHandoff):
        read.read()


def test_retained_exit_identity_is_rechecked(host):
    read = observer(host)
    value = exited_normal(host)

    def files(slug, container):
        if slug == p.CANDIDATE:
            value["State"]["FinishedAt"] = "2026-09-22T00:00:04Z"
        return host.files[slug]

    read.collect_files = files
    with pytest.raises(p.UnsafeHandoff):
        read.read()


@pytest.mark.parametrize(
    "fault",
    [
        "image",
        "core_restart",
        "core_paused",
        "native_generation",
        "unlisted",
        "old_prefix",
        "version",
        "missing_app",
        "slow",
        "boot",
        "clock_backwards",
    ],
)
def test_ambiguous_or_changed_host_evidence_is_refused(host, fault):
    read = observer(host)
    if fault == "image":
        host.values["app_" + p.NORMAL]["Image"] = "sha256:" + "f" * 64
    elif fault == "core_restart":
        host.values[h.CORE]["State"]["Pid"] += 1
    elif fault == "core_paused":
        host.values[h.CORE]["State"]["Paused"] = True
    elif fault == "native_generation":
        read.read_native = lambda *_: o.NativeState("f" * 64, True, False)
    elif fault in ("unlisted", "old_prefix"):
        name = "app_unlisted" if fault == "unlisted" else "addon_" + OTHER
        host.values[name] = recovery_tests.container(name, 9)
    elif fault == "version":
        host.versions[OTHER] = "changed"
    elif fault == "missing_app":
        host.versions.pop(OTHER)
    else:

        def hook(key):
            if key == "candidate":
                if fault == "slow":
                    host.now += 2.1
                elif fault == "boot":
                    host.boot = "e" * 32
                else:
                    host.now -= 0.1

        host.hook = hook
    with pytest.raises(p.UnsafeHandoff):
        read.read()


def test_missing_or_paused_container_is_not_stopped_without_supervisor_agreement(host):
    read = observer(host)
    host.values["app_" + p.NORMAL]["State"]["Status"] = "paused"
    assert read.read().observation.normal.state == "unknown"


def test_other_owner_and_busy_jobs_block_authority(host):
    read = observer(host)
    host.jobs = False
    host.values["app_" + OTHER] = recovery_tests.container("app_" + OTHER, 8)
    sample = read.read()
    assert not sample.observation.jobs_idle and not sample.observation.other_owners_stopped


def test_generation_change_during_native_read_is_refused(host):
    read = observer(host)

    def native(slug, incarnation):
        host.values["app_" + slug]["State"]["Pid"] += 1
        return o.NativeState(incarnation, True, False)

    read.read_native = native
    with pytest.raises(p.UnsafeHandoff):
        read.read()


def test_container_or_app_inventory_change_during_collection_is_refused(host):
    read = observer(host)

    def hook(key):
        if key == "candidate":
            host.values["app_" + OTHER] = recovery_tests.container("app_" + OTHER, 8)

    host.hook = hook
    with pytest.raises(p.UnsafeHandoff):
        read.read()


@pytest.mark.parametrize(
    "part", ["slug", "version", "state", "duplicate", "missing", "empty", "wire_key"]
)
def test_bad_supervisor_inventory_refused(host, part):
    raw = json.loads(host.read("apps"))
    values = raw["data"]["addons"]
    if part in ("slug", "version", "state"):
        values[0][part] = True
    elif part == "duplicate":
        values.append(deepcopy(values[0]))
    elif part == "missing":
        values[0].pop("slug")
    elif part == "empty":
        values.clear()
    else:
        raw["data"]["apps"] = raw["data"].pop("addons")
    with pytest.raises(p.UnsafeHandoff):
        o.installed_apps(p.encode(raw))


@pytest.mark.parametrize(
    "field,value",
    [
        ("Id", "bad"),
        ("Names", []),
        ("Names", ["/x", "/y"]),
        ("Names", ["/a/b"]),
        ("ImageID", None),
        ("State", "unknown"),
    ],
)
def test_bad_docker_inventory_refused(host, field, value):
    values = host.containers()
    values[0][field] = value
    with pytest.raises(p.UnsafeHandoff):
        o.container_index(values)


def test_duplicate_docker_identity_and_name_refused(host):
    values = host.containers()
    values.append(deepcopy(values[0]))
    with pytest.raises(p.UnsafeHandoff):
        o.container_index(values)
    values[-1]["Id"] = "f" * 64
    with pytest.raises(p.UnsafeHandoff):
        o.container_index(values)


class ReadDocker(host_tests.FakeDocker):
    def __init__(self):
        super().__init__()
        self.exec["ExitCode"] = None
        self.calls = []

    def create_execution(self, cid, command, *, attach=False):
        assert command in h.READS.values() and attach
        self.exec = host_tests.execution(command)
        self.exec["ExitCode"] = None
        self.calls.append("create")
        return host_tests.EID

    def start_execution(self, eid, *, attach=False):
        assert attach
        self.calls.append("start")
        self.exec["ExitCode"] = 0
        return host_tests.frame(host_tests.jobs())


def test_supervisor_reader_only_uses_fixed_new_read_execution():
    docker = ReadDocker()
    read = o.SupervisorReads(
        docker,
        image=host_tests.IMAGE,
        incarnation=h.generation(docker.cli, name=h.CLI, image=host_tests.IMAGE),
    )
    assert h.supervisor_jobs_idle(read.read("jobs"))
    assert docker.calls == ["create", "start"]
    for key in ("stop", "rebuild", "anything"):
        with pytest.raises(p.UnsafeHandoff):
            read.read(key)
    assert docker.calls == ["create", "start"]


def test_lost_read_response_does_not_replay_that_execution():
    docker = ReadDocker()
    read = o.SupervisorReads(
        docker,
        image=host_tests.IMAGE,
        incarnation=h.generation(docker.cli, name=h.CLI, image=host_tests.IMAGE),
    )

    def lost(eid, *, attach):
        docker.calls.append("start")
        raise TimeoutError("private")

    docker.start_execution = lost
    with pytest.raises(TimeoutError):
        read.read("jobs")
    assert docker.calls == ["create", "start"]
