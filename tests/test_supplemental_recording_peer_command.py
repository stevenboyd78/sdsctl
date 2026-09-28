"""Fixed passive writer flow with actual outer/observer processes and private files.

Engine/confinement/input provenance and final acceptance are synthetic. This is
not an installed qualification, independently bounded outer or App action grant.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_writer_channel as writer

m, transport = writer.prep, writer.transport
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
    connection,
) = (
    writer.layout,
    writer.tree,
    writer.routing,
    writer.projection,
    writer.binding,
    writer.directory,
    writer.prepared,
    writer.joined,
    writer.before_handoff,
    writer.service_case,
    writer.connection,
)


@pytest.fixture
def command(connection, monkeypatch):
    c = connection
    c.owners, c.services, c.links = [], [], []
    create = m.startups.Startup.__init__
    poll = m.startups.Startup.poll
    c.submit = True

    def owning(owner, *args, **kwargs):
        create(owner, *args, **kwargs)
        c.owners.append(owner)

    def polling(owner):
        if c.submit:
            c.submit = False
            writer.assembly.baseline_tests.integration.startups.submit(owner)
            plan = owner.original.plan
            config = dict(
                plan=plan.raw.decode(),
                sha256=plan.sha256,
                identities=[vars(item) for item in c.identities.values()],
                declaration=c.s.expected_input_digest,
            )
            transport.command(c.outer, config | dict(writer=os.getpid(), observer=c.observer.pid))
            transport.command(
                c.observer,
                config
                | dict(
                    role="observer",
                    local=c.table[c.observer.pid],
                    outer=c.table[c.outer.pid],
                    outer_pid=c.outer.pid,
                    peer=c.table[os.getpid()],
                    peer_pid=os.getpid(),
                ),
            )
        return poll(owner)

    def forbidden(*args, **kwargs):
        pytest.fail("Passive command selected App/scanner/recording/service execution")

    for cls, collection in (
        (writer.assembly.operator.IdleService, c.services),
        (m.bootstrap.links.Link, c.links),
    ):
        initial = cls.__init__

        def capture(value, *args, initial=initial, collection=collection, **kwargs):
            initial(value, *args, **kwargs)
            collection.append(value)

        monkeypatch.setattr(cls, "__init__", capture)
    for name in ("run", "start_native", "start_recording", "prepare_launch"):
        monkeypatch.setattr(writer.assembly.operator.IdleService, name, forbidden)
    monkeypatch.setattr(writer.assembly.operator.Inbox, "consume", forbidden)
    monkeypatch.setattr(m.bootstrap.links.Link, "_send", forbidden)
    monkeypatch.setattr(m.startups.Startup, "__init__", owning)
    monkeypatch.setattr(m.startups.Startup, "poll", polling)
    return c


@pytest.mark.parametrize("service_case", ["published-preparation-inputs"], indirect=True)
@pytest.mark.parametrize("connection", ["command"], indirect=True)
def test_fixed_writer_flow_joins_all_original_owners_and_retires_passively(command, capsys):
    c = command
    before = writer.preflight_probe.fds()
    assert c.run() == 75
    assert capsys.readouterr().out == m.MILESTONE + "\n"
    assert len(c.owners) == len(c.services) == len(c.links) == 1
    owner, service, link = c.owners[0], c.services[0], c.links[0]
    assert owner.closed and not owner.failed and owner.accepted and owner.service_used
    assert owner.declaration.closed and owner.clock.closed and link.closed and service.closed
    assert service.original is owner.original and service.clock_witness is owner.clock
    assert service.dispatch._original_observe.__self__ is link and not service.used
    assert len(c.s.clocks) == 3 and all(clock.closed for clock in c.s.clocks)
    assert len(c.s.cached_calls) == 1
    assert owner.projected.host.manifest_sha256 == c.baseline_pin
    assert len(service.journal.entries) == 1
    assert all(channel.fileno() == -1 for channel in (link.channel.incoming, link.channel.outgoing))
    assert not list((c.s.root / "inbox").iterdir())
    assert json.loads(transport.line(c.outer))["prepared"]
    assert [transport.line(c.outer) for _ in range(4)] == [
        "permission-listening",
        "permission-sent",
        "connected",
        "delivered",
    ]
    assert json.loads(transport.line(c.observer))["received"]
    assert writer.preflight_probe.fds() == before


def test_ordinary_invocation_refuses_before_imports_or_any_input_read(tmp_path):
    # Not the fixed image path; even isolated flags and plausible argv cannot
    # admit this local checkout as an installed command.
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            str(Path(m.__file__)),
            "0" * 32,
            "a" * 64,
            "b" * 64,
            "1:1:" + "c" * 64,
            m.MODE,
        ],
        cwd=tmp_path,
        capture_output=True,
        timeout=5,
    )
    assert result.returncode == 64 and not result.stdout
    assert result.stderr.decode() == m.MESSAGE + "\n"
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("service_case", ["published-preparation-inputs"], indirect=True)
@pytest.mark.parametrize("connection", ["command"], indirect=True)
@pytest.mark.parametrize("fault", ["permission", "inputs", "baseline-peer", "interrupt"])
def test_command_failure_before_publication_preserves_original_bounds(
    command, monkeypatch, capsys, fault
):
    c = command
    before = writer.preflight_probe.fds()
    wait = m.preflight.Permission.wait

    def waiting(permission):
        wait(permission)
        if fault == "permission":
            raise m.preflight.UnconfirmedPermission("PRIVATE unconfirmed permission")
        if fault == "inputs":
            (c.s.peer_input_root / m.inputs_module.NAME).write_bytes(b"PRIVATE changed inputs")

    def after_cached():
        if fault == "interrupt":
            raise KeyboardInterrupt
        if fault == "baseline-peer":
            c.observer.kill()
            c.observer.wait(timeout=3)

    monkeypatch.setattr(m.preflight.Permission, "wait", waiting)
    c.s.after_cached = after_cached
    exception = KeyboardInterrupt if fault == "interrupt" else m.UnconfirmedPreparation
    with pytest.raises(exception) as caught:
        c.run()
    if fault != "interrupt":
        assert str(caught.value) == m.MESSAGE and caught.value.__suppress_context__
    assert capsys.readouterr().out == "" and not c.services and not c.links
    assert not list(c.s.root.iterdir())
    assert all(owner.closed and owner.failed and owner.declaration.closed for owner in c.owners)
    assert all(clock.closed for clock in c.s.clocks)
    assert len(c.owners) == (0 if fault == "permission" else 1)
    assert len(c.s.cached_calls) == (1 if fault in {"baseline-peer", "interrupt"} else 0)
    assert writer.preflight_probe.fds() == before


@pytest.mark.parametrize("service_case", ["published-preparation-inputs"], indirect=True)
@pytest.mark.parametrize("connection", ["command"], indirect=True)
@pytest.mark.parametrize(
    "fault",
    [
        "acceptance-input",
        "acceptance-peer",
        "acceptance-expired",
        "connection-close",
        "connection-expired",
        "startup-close",
        "declaration-close",
    ],
)
def test_command_late_failure_never_reports_success_or_reopens_case(
    command, monkeypatch, capsys, fault
):
    c = command
    before = writer.preflight_probe.fds()
    original_files = {}
    poll, close_connection, close_owner = (
        m.startups.Startup.poll,
        m.connections.Connection.close,
        m.startups.Startup.close,
    )
    close_declaration = m.inputs_module.declarations.Declaration.close

    def polling(owner):
        result = poll(owner)
        original_files.update(
            {p.relative_to(c.s.root): p.read_bytes() for p in c.s.root.rglob("*") if p.is_file()}
        )
        if fault == "acceptance-input":
            (c.s.peer_input_root / m.inputs_module.NAME).write_bytes(
                b"PRIVATE changed at acceptance"
            )
        elif fault == "acceptance-peer":
            c.observer.kill()
            c.observer.wait(timeout=3)
        elif fault == "acceptance-expired":
            monkeypatch.setattr(
                m,
                "time",
                SimpleNamespace(monotonic=lambda: owner.offer.deadline + 1, sleep=m.time.sleep),
            )
        return result

    def connection_close(connection):
        was_open = not connection.closed
        close_connection(connection)
        if fault == "connection-close" and was_open and connection.root == m.handoff_root(c.case):
            raise RuntimeError("PRIVATE connection retirement failure")
        if fault == "connection-expired" and was_open and connection.root == m.handoff_root(c.case):
            monkeypatch.setattr(
                m,
                "time",
                SimpleNamespace(monotonic=lambda: connection.deadline + 1, sleep=m.time.sleep),
            )

    def owner_close(owner):
        was_open = not owner.closed
        close_owner(owner)
        if fault == "startup-close" and owner in c.owners and was_open:
            raise RuntimeError("PRIVATE startup retirement failure")

    def declaration_close(declaration):
        was_open = not declaration.closed
        close_declaration(declaration)
        if (
            fault == "declaration-close"
            and was_open
            and any(owner.declaration is declaration for owner in c.owners)
        ):
            raise RuntimeError("PRIVATE declaration retirement failure")

    monkeypatch.setattr(m.startups.Startup, "poll", polling)
    monkeypatch.setattr(m.connections.Connection, "close", connection_close)
    monkeypatch.setattr(m.startups.Startup, "close", owner_close)
    monkeypatch.setattr(m.inputs_module.declarations.Declaration, "close", declaration_close)
    with pytest.raises(m.UnconfirmedPreparation) as caught:
        c.run()
    assert str(caught.value) == m.MESSAGE and caught.value.__suppress_context__
    assert capsys.readouterr().out == ""
    assert len(c.owners) == 1 and c.owners[0].failed and c.owners[0].closed
    assert c.owners[0].declaration.closed and all(clock.closed for clock in c.s.clocks)
    assert len(c.s.cached_calls) == 1
    if fault.startswith("acceptance-"):
        assert not c.links and not c.services and not (c.s.root / "journal").exists()
    else:
        assert c.links[0].closed and c.services[0].closed and not c.services[0].used
        assert (c.s.root / "journal/0000.json").exists()
    files = {p.relative_to(c.s.root): p.read_bytes() for p in c.s.root.rglob("*") if p.is_file()}
    assert {"startup-claim.json", "plan.json", "startup-acceptance.json"} <= {str(p) for p in files}
    assert all(files[name] == raw for name, raw in original_files.items())
    assert writer.preflight_probe.fds() == before
