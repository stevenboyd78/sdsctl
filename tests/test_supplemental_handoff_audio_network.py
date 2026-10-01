"""Opt-in RTP handoff evidence; synthetic hosts only, no real port or App I/O."""

import io
from copy import deepcopy
from pathlib import Path

import pytest

from . import test_supplemental_handoff_host as ht
from . import test_supplemental_handoff_observer as ot
from . import test_supplemental_handoff_service as st

h, o, p, s = ht.h, ot.o, ht.p, st.s


def container_network(candidate):
    return {
        "HostConfig": {
            "NetworkMode": "bridge",
            "PublishAllPorts": False,
            "RestartPolicy": {"Name": "no", "MaximumRetryCount": 0},
            "PortBindings": {"50000/udp": [{"HostIp": "", "HostPort": "50000"}]}
            if candidate
            else {},
        },
        "NetworkSettings": {
            "Ports": {
                "50000/udp": [
                    {"HostIp": "0.0.0.0", "HostPort": "50000"},
                    {"HostIp": "::", "HostPort": "50000"},
                ],
            }
            if candidate
            else {}
        },
    }


def port(ip="0.0.0.0"):
    return {"Type": "udp", "PrivatePort": 50000, "PublicPort": 50000, "IP": ip}


def listing():
    return {"Id": "a" * 64, "Names": ["/app_" + p.CANDIDATE], "State": "running", "Ports": [port()]}


@pytest.mark.parametrize("slug", [p.NORMAL, p.CANDIDATE])
def test_audio_mode_is_explicit_and_never_changes_normal_ports(slug):
    mapped = {"50000/udp": 50000, "50443/tcp": None, "8443/tcp": None}
    raw = ht.config(slug=slug, network=mapped)
    with pytest.raises(p.UnsafeHandoff):
        h.app_configuration(raw, slug=slug)
    if slug == p.CANDIDATE:
        assert h.app_configuration(raw, slug=slug, network=h.AUDIO_NETWORK).slug == slug
        with pytest.raises(p.UnsafeHandoff):
            h.app_configuration(ht.config(slug=slug), slug=slug, network=h.AUDIO_NETWORK)
    else:
        with pytest.raises(p.UnsafeHandoff):
            h.app_configuration(raw, slug=slug, network=h.AUDIO_NETWORK)
        assert h.app_configuration(ht.config(), slug=slug, network=h.AUDIO_NETWORK).slug == slug


@pytest.mark.parametrize("value", [True, 50000.0, "50000", 50001, None])
def test_audio_supervisor_mapping_is_exact_integer(value):
    with pytest.raises(p.UnsafeHandoff):
        h.app_configuration(
            ht.config(
                slug=p.CANDIDATE,
                network={
                    "50000/udp": value,
                    "50443/tcp": None,
                    "8443/tcp": None,
                },
            ),
            slug=p.CANDIDATE,
            network=h.AUDIO_NETWORK,
        )


@pytest.mark.parametrize("policy", [None, True, {}, "audio", "candidate-rtp-50001-v1"])
def test_unknown_network_modes_are_not_inferred(policy):
    with pytest.raises(p.UnsafeHandoff):
        h.app_configuration(ht.config(), slug=p.NORMAL, network=policy)
    with pytest.raises(p.UnsafeHandoff):
        ot.observer(ot.Host(), network=policy)


@pytest.mark.parametrize("candidate", [False, True])
def test_actual_docker_network_must_match_role(candidate):
    h.audio_container_network(container_network(candidate), candidate=candidate)
    with pytest.raises(p.UnsafeHandoff):
        h.audio_container_network(container_network(not candidate), candidate=candidate)


@pytest.mark.parametrize(
    "fault",
    [
        "host",
        "other_bridge",
        "publish_all",
        "missing_bindings",
        "missing_runtime",
        "extra_tcp",
        "wrong_port",
        "loopback",
        "missing_ipv4",
        "duplicate",
        "extra_binding",
        "number_port",
        "extra_runtime",
        "null_runtime",
    ],
)
def test_actual_audio_mapping_drift_is_refused(fault):
    value = container_network(True)
    config, runtime = value["HostConfig"], value["NetworkSettings"]["Ports"]
    if fault in ("host", "other_bridge"):
        config["NetworkMode"] = fault
    elif fault == "publish_all":
        config["PublishAllPorts"] = True
    elif fault == "missing_bindings":
        config["PortBindings"] = {}
    elif fault == "missing_runtime":
        value["NetworkSettings"] = {}
    elif fault == "extra_tcp":
        config["PortBindings"]["8443/tcp"] = [{"HostIp": "", "HostPort": "8443"}]
    elif fault == "wrong_port":
        runtime["50000/udp"][0]["HostPort"] = "50001"
    elif fault == "loopback":
        runtime["50000/udp"][0]["HostIp"] = "127.0.0.1"
    elif fault == "missing_ipv4":
        runtime["50000/udp"].pop(0)
    elif fault == "duplicate":
        runtime["50000/udp"][1] = deepcopy(runtime["50000/udp"][0])
    elif fault == "extra_binding":
        config["PortBindings"]["50000/udp"].append({"HostIp": "::", "HostPort": "50000"})
    elif fault == "number_port":
        runtime["50000/udp"][0]["HostPort"] = 50000
    elif fault == "extra_runtime":
        runtime["8443/tcp"] = None
    else:
        runtime["50000/udp"] = None
    with pytest.raises(p.UnsafeHandoff):
        h.audio_container_network(value, candidate=True)


@pytest.mark.parametrize(
    "fault",
    [
        "other_owner",
        "wrong_target",
        "exited",
        "missing_ports",
        "duplicate",
        "wrong_ip",
        "malformed",
    ],
)
def test_published_rtp_port_has_only_one_qualified_owner(fault):
    value = listing()
    assert h.rtp_port_owners([value]) == ("a" * 64 + "/0.0.0.0",)
    if fault == "other_owner":
        value["Names"] = ["/unrelated"]
    elif fault == "wrong_target":
        value["Ports"][0]["PrivatePort"] = 50001
    elif fault == "exited":
        value["State"] = "exited"
    elif fault == "missing_ports":
        value.pop("Ports")
    elif fault == "duplicate":
        value["Ports"].append(port())
    elif fault == "wrong_ip":
        value["Ports"][0]["IP"] = "127.0.0.1"
    else:
        value["Ports"][0]["PublicPort"] = "50000"
    with pytest.raises(p.UnsafeHandoff):
        h.rtp_port_owners([value])


def test_unpublished_or_unrelated_transport_is_not_udp_owner():
    value = listing()
    value["Names"] = ["/unrelated"]
    value["Ports"][0].pop("PublicPort")
    assert h.rtp_port_owners([value]) == ()
    value["Ports"] = [port() | {"Type": "tcp"}]
    assert h.rtp_port_owners([value]) == ()


def table(port=50000, ipv6=False):
    return (
        "  sl local_address rem_address st tx_queue rx_queue "
        "tr tm->when retrnsmt uid timeout inode\n"
        + f"1: {'0' * (32 if ipv6 else 8)}:{port:04X} 0:0 07 0 0 0 0 0 42\n"
    ).encode()


@pytest.mark.parametrize("ipv6", [False, True])
def test_host_udp_table_requires_free_audio_port(ipv6):
    assert not h.udp_table_idle(table(ipv6=ipv6), ipv6=ipv6)
    assert h.udp_table_idle(table(49999, ipv6), ipv6=ipv6)


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"bad\n",
        b"local_address\nbad\n",
        b"local_address\n1: bad 0 0 0 0 0 0 0 0\n",
        b"x" * (512 * 1024 + 1),
    ],
)
def test_malformed_or_oversized_udp_table_is_not_idle(raw):
    with pytest.raises(p.UnsafeHandoff):
        h.udp_table_idle(raw, ipv6=False)


def test_host_port_probe_reads_only_launcher_mounted_host_tables(monkeypatch):
    paths = []

    def opened(path, mode):
        assert mode == "rb"
        paths.append(str(path))
        return io.BytesIO(table(49999, str(path).endswith("udp6")))

    monkeypatch.setattr(Path, "open", opened)
    h.require_host_rtp_idle()
    assert paths == ["/opt/sdsctl-host-udp/udp", "/opt/sdsctl-host-udp/udp6"]


def test_missing_host_table_is_not_replaced_with_helper_namespace(monkeypatch):
    def missing(*_args):
        raise FileNotFoundError

    monkeypatch.setattr(Path, "open", missing)
    with pytest.raises(FileNotFoundError):
        h.require_host_rtp_idle()


def test_audio_launcher_adds_only_two_readonly_host_table_mounts():
    normal = s.decode_plan(st.plan_value())
    audio = s.decode_plan(st.plan_value() | {"schema": 2, "network": h.AUDIO_NETWORK})
    command = s.launch(audio, "d" * 64)
    standard = s.launch(normal, "d" * 64)
    mounts = [
        f"type=bind,source=/proc/1/net/{table},target=/opt/sdsctl-host-udp/{table},readonly"
        for table in ("udp", "udp6")
    ]
    assert all(item in command and item not in standard for item in mounts)
    stripped = list(command)
    for item in mounts:
        pos = stripped.index(item)
        assert stripped[pos - 1] == "--mount"
        del stripped[pos - 1 : pos + 1]
    assert tuple(stripped) == standard
    assert "--cap-add=SYS_PTRACE" not in command and "--network=none" in command


def audio_host(monkeypatch, *, running=False):
    host = ot.Host()
    if running:
        host.values.pop("app_" + p.NORMAL)
        host.values["app_" + p.CANDIDATE] = ot.recovery_tests.container("app_" + p.CANDIDATE, 4)
    for name, value in host.values.items():
        if name.startswith("app_"):
            value.update(container_network(name == "app_" + p.CANDIDATE))
    original_read, original_list = host.read, host.containers

    def read(key):
        result = h.supervisor_data(original_read(key))
        if key == "candidate":
            result["network"]["50000/udp"] = 50000
        return ot.response(result)

    def containers():
        return [
            v
            | {
                "Ports": [port(), port("::")]
                if v["Names"] == ["/app_" + p.CANDIDATE] and v["State"] == "running"
                else []
            }
            for v in original_list()
        ]

    host.read, host.containers = read, containers
    idle_checks = []
    monkeypatch.setattr(o, "require_host_rtp_idle", lambda: idle_checks.append(True))
    seals = tuple(
        o.AppSeal(
            slug,
            host.versions[slug],
            ot.IMAGE,
            h.app_configuration(host.read(key), slug=slug, network=h.AUDIO_NETWORK).settings_sha256,
            host.files[slug],
        )
        for slug, key in ((p.NORMAL, "normal"), (p.CANDIDATE, "candidate"))
    )
    observer = o.HostObserver(
        host,
        host,
        seals=seals,
        installed_versions=host.versions,
        other_scanner_apps=frozenset({ot.OTHER}),
        core_image=ot.IMAGE,
        core_generation=h.generation(host.container(h.CORE), name=h.CORE, image=ot.IMAGE),
        core_version="2026.9.3",
        read_clock=host.clock,
        collect_files=host.collect_files,
        read_native=host.native,
        network=h.AUDIO_NETWORK,
    )
    return host, observer, idle_checks


@pytest.mark.parametrize("running", [False, True])
def test_audio_observer_checks_actual_runtime_and_does_not_change_recording_gate(
    monkeypatch, running
):
    host, observer, idle = audio_host(monkeypatch, running=running)
    sample = observer.read()
    assert len(idle) == (0 if running else 2)
    assert sample.observation.candidate.state == ("running" if running else "stopped")
    host.native_state = (True, True)
    sample = observer.read()
    assert (
        sample.observation.candidate if running else sample.observation.normal
    ).recording is True


@pytest.mark.parametrize("running", [False, True])
def test_mapping_mismatch_refuses_observer_evidence(monkeypatch, running):
    host, observer, _ = audio_host(monkeypatch, running=running)
    name = "app_" + (p.CANDIDATE if running else p.NORMAL)
    host.values[name]["HostConfig"]["NetworkMode"] = "host"
    with pytest.raises(p.UnsafeHandoff):
        observer.read()


def test_busy_host_port_prevents_reader_owner_handoff(monkeypatch):
    _, observer, _ = audio_host(monkeypatch)

    def busy():
        raise p.UnsafeHandoff("Port occupied")

    monkeypatch.setattr(o, "require_host_rtp_idle", busy)
    with pytest.raises(p.UnsafeHandoff):
        observer.read()


def test_new_network_mode_requires_new_explicit_plan_schema():
    old = st.plan_value()
    assert s.decode_plan(old).network == h.READER_NETWORK
    with pytest.raises(p.UnsafeHandoff):
        s.decode_plan(old | {"network": h.AUDIO_NETWORK})
    assert s.decode_plan(old | {"schema": 2, "network": h.AUDIO_NETWORK}).network == h.AUDIO_NETWORK
    for value in (
        {"schema": 2},
        {"schema": 2, "network": h.READER_NETWORK},
        {"schema": 2, "network": True},
        {"schema": 3, "network": h.AUDIO_NETWORK},
    ):
        with pytest.raises(p.UnsafeHandoff):
            s.decode_plan(old | value)
