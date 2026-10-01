"""Serialized host-proc fixtures; these do not claim live Docker namespace auth."""

import importlib.util
import io
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from . import test_supplemental_handoff_process as processes
from . import test_supplemental_recording_clock as clocks  # noqa: F401

DOMAIN_NAME = "supplemental_recording_time_domain"
DOMAIN_SPEC = importlib.util.spec_from_file_location(
    DOMAIN_NAME, Path(processes.w.__file__).with_name(DOMAIN_NAME + ".py")
)
domain_module = importlib.util.module_from_spec(DOMAIN_SPEC)
sys.modules[DOMAIN_NAME] = domain_module
DOMAIN_SPEC.loader.exec_module(domain_module)

NAME = "supplemental_recording_namespace"
SPEC = importlib.util.spec_from_file_location(
    NAME, Path(processes.w.__file__).with_name(NAME + ".py")
)
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)
CID = "c" * 64
NS = tuple((4, 100 + i) for i in range(5))


def stat(pid, parent, ticks, *, state="S", comm="operator"):
    return f"{pid} ({comm}) " + " ".join([state, str(parent), *(["0"] * 17), str(ticks), "1", "2"])


def status(pid, local, parent):
    return (
        f"Name:\toperator\nTgid:\t{pid}\nPid:\t{pid}\nPPid:\t{parent}\n"
        f"Uid:\t0\t0\t0\t0\nGid:\t0\t0\t0\t0\nNSpid:\t{pid}\t{local}\n"
    )


def actor(pid, local, parent, ticks, **kwargs):
    return m.decode(
        pid,
        CID,
        stat(pid, parent, ticks, **kwargs),
        status(pid, local, parent),
        f"0::/system.slice/docker-{CID}.scope\n",
        NS,
    )


def family():
    return (
        actor(1000, 1, 90, 100),
        actor(1001, 2, 90, 200),
        actor(1002, 3, 1001, 300),
        actor(1003, 4, 1001, 400),
    )


def reports(actors):
    return {
        key: {"pid": item.local_pid, "start_ticks": item.start_ticks, "uid": 0, "gid": 0}
        for key, item in zip(("guardian", "native", "watchdog"), actors[1:], strict=True)
    }


def match(actors, **changes):
    options = dict(
        expected_init=processes.w.ProcessIdentity(1000, 100, CID),
        reported=reports(family()),
        host_user=NS[3],
        host_time=NS[4],
    )
    return m.match(*actors, **(options | changes))


def test_host_pids_are_not_arithmetically_assumed_to_be_container_pids():
    actors = family()
    assert match(actors) == actors
    assert actors[1].host_pid == 1001 and actors[1].local_pid == 2
    changed = reports(actors)
    changed["guardian"]["pid"] = 1001
    with pytest.raises(m.UnconfirmedNamespace):
        match(actors, reported=changed)


@pytest.mark.parametrize("comm", ["spaces here", "odd) (", "line\nbreak", "operator)"])
def test_proc_comm_does_not_shift_tick_or_parent_fields(comm):
    assert actor(1001, 2, 90, 200, comm=comm) == family()[1]


@pytest.mark.parametrize("state", ["D", "T", "t", "Z", "X", "?"])
def test_frozen_uninterruptible_or_dead_actor_is_not_a_live_read(state):
    with pytest.raises(m.UnconfirmedNamespace):
        actor(1001, 2, 90, 200, state=state)


@pytest.mark.parametrize(
    "before,after",
    [
        ("NSpid:\t1001\t2", "NSpid:\t1001"),
        ("NSpid:\t1001\t2", "NSpid:\t1001\t2\t1"),
        ("NSpid:\t1001\t2", "NSpid:\t1002\t2"),
        ("NSpid:\t1001\t2", "NSpid:\t1001\t0"),
        ("NSpid:\t1001\t2", "NSpid:\t1001\t-2"),
        ("NSpid:\t1001\t2", "NSpid:\t1001\t٢"),
        ("NSpid:\t1001\t2", "NSpid:\t1001\t2147483648"),
        ("Uid:\t0\t0\t0\t0", "Uid:\t0\t1\t0\t0"),
        ("Gid:\t0\t0\t0\t0", "Gid:\t0\t0\t0"),
        ("Pid:\t1001", "Pid:\t1002"),
        ("Tgid:\t1001", "Tgid:\t1002"),
        ("PPid:\t90", "PPid:\t99"),
        ("PPid:\t90", "PPid:\t90\nPPid:\t90"),
        ("Tgid:\t1001\n", ""),
    ],
)
def test_status_disagreement_mapping_depth_and_credentials_fail_closed(before, after):
    with pytest.raises(m.UnconfirmedNamespace):
        m.decode(
            1001,
            CID,
            stat(1001, 90, 200),
            status(1001, 2, 90).replace(before, after),
            f"0::/system.slice/docker-{CID}.scope\n",
            NS,
        )


@pytest.mark.parametrize(
    "group",
    [
        "0::/\n",
        "0::/../system.slice/docker-" + CID + ".scope\n",
        "0::/system.slice/docker-" + CID + ".scope/child\n",
        "0::/system.slice/docker-" + "d" * 64 + ".scope\n",
    ],
)
def test_only_exact_host_relative_docker_cgroup_is_admitted(group):
    with pytest.raises(m.UnconfirmedNamespace):
        m.decode(1001, CID, stat(1001, 90, 200), status(1001, 2, 90), group, NS)


@pytest.mark.parametrize("role", range(4))
@pytest.mark.parametrize("namespace", range(5))
def test_actor_cannot_use_another_pid_mount_net_user_or_clock_namespace(role, namespace):
    actors = list(family())
    names = list(actors[role].namespaces)
    names[namespace] = (4, 900)
    actors[role] = replace(actors[role], namespaces=tuple(names))
    with pytest.raises(m.UnconfirmedNamespace):
        match(actors)


@pytest.mark.parametrize("field", ["host_user", "host_time"])
def test_shared_container_user_or_time_namespace_must_also_match_host(field):
    with pytest.raises(m.UnconfirmedNamespace):
        match(family(), **{field: (4, 900)})


@pytest.mark.parametrize("role", ["guardian", "native", "watchdog"])
@pytest.mark.parametrize("field", ["pid", "start_ticks", "uid", "gid"])
def test_report_identity_must_match_kernel_mapping(role, field):
    report = reports(family())
    report[role][field] += 1
    with pytest.raises(m.UnconfirmedNamespace):
        match(family(), reported=report)


@pytest.mark.parametrize(
    "role,field,value",
    [
        (0, "local_pid", 9),
        (0, "start_ticks", 99),
        (1, "host_pid", 1000),
        (2, "local_pid", 2),
        (3, "parent", 99),
        (2, "parent", 1000),
        (1, "parent", 1003),
        (3, "container_id", "e" * 64),
    ],
)
def test_init_identity_tree_and_uniqueness_are_checked(role, field, value):
    actors = list(family())
    actors[role] = replace(actors[role], **{field: value})
    with pytest.raises(m.UnconfirmedNamespace):
        match(actors)


@pytest.mark.parametrize("bad", [[], NS[:4], tuple([*NS[:4], (4, 0)]), tuple([*NS[:4], (True, 9)])])
def test_namespace_identity_types_are_not_coerced(bad):
    with pytest.raises(m.UnconfirmedNamespace):
        m.decode(
            1001,
            CID,
            stat(1001, 90, 200),
            status(1001, 2, 90),
            f"0::/system.slice/docker-{CID}.scope\n",
            bad,
        )


@pytest.fixture
def proc_files(monkeypatch):
    files = {
        "stat": stat(1001, 90, 200).encode(),
        "status": status(1001, 2, 90).encode(),
        "cgroup": f"0::/system.slice/docker-{CID}.scope\n".encode(),
    }
    fixture = SimpleNamespace(files=files, reads=[], stats=[], opened=[])

    def opened(path, mode, *, buffering):
        assert path.startswith("/proc/1001/") and mode == "rb" and buffering == 0
        name = path.removeprefix("/proc/1001/")
        fixture.reads.append(name)
        result = io.BytesIO(files[name])
        fixture.opened.append(result)
        return result

    def statted(path):
        assert path.startswith("/proc/1001/ns/")
        name = path.removeprefix("/proc/1001/ns/")
        fixture.stats.append(name)
        dev, ino = NS[m.NAMESPACES.index(name)]
        return SimpleNamespace(st_dev=dev, st_ino=ino)

    monkeypatch.setattr(m, "open", opened, raising=False)
    monkeypatch.setattr(m, "os", SimpleNamespace(stat=statted))
    return fixture


def test_reader_has_only_fixed_bounded_proc_reads_and_closes_descriptors(proc_files):
    assert m.read(1001, CID) == family()[1]
    assert proc_files.reads == ["stat", "status", "cgroup"]
    assert proc_files.stats == list(m.NAMESPACES)
    assert all(stream.closed for stream in proc_files.opened)


@pytest.mark.parametrize("where", ["stat", "open"])
@pytest.mark.parametrize("error", [FileNotFoundError, PermissionError, OSError])
def test_only_missing_proc_path_has_distinct_unavailable_result(
    proc_files, monkeypatch, where, error
):
    def fail(*args, **kwargs):
        raise error("PRIVATE fixed proc path")

    monkeypatch.setattr(m.os if where == "stat" else m, where, fail)
    with pytest.raises(m.UnconfirmedNamespace) as caught:
        m.read(1001, CID)
    assert type(caught.value) is (
        m.ProcUnavailable if error is FileNotFoundError else m.UnconfirmedNamespace
    )
    assert str(caught.value) == m.MESSAGE and caught.value.__suppress_context__
    assert all(stream.closed for stream in proc_files.opened)


@pytest.mark.parametrize("name,limit", [("stat", 4096), ("status", 16384), ("cgroup", 4096)])
def test_oversize_proc_input_is_sanitized_and_closed(proc_files, name, limit):
    proc_files.files[name] = b"PRIVATE" * limit
    with pytest.raises(m.UnconfirmedNamespace) as caught:
        m.read(1001, CID)
    assert str(caught.value) == m.MESSAGE
    assert all(stream.closed for stream in proc_files.opened)


def test_late_proc_observation_is_not_fresh(proc_files, monkeypatch):
    def now():
        return 2.0 if proc_files.opened else 0.0

    monkeypatch.setattr(m.time, "monotonic", now)
    with pytest.raises(m.UnconfirmedNamespace):
        m.read(1001, CID)
    assert all(stream.closed for stream in proc_files.opened)
