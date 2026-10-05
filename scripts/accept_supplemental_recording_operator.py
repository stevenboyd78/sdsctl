#!/usr/bin/env python3
"""Private fixed exec operator; not installed and not a public recording API.

The independent host must authenticate image/interpreter/source, exact exec and
container identities, pins, and durable start intent before using this program.
Its inherited stdin/stdout are only framing, NOT authentication. No arbitrary
command, public listener, reconnect, recorder retry or ownership restoration.
"""

from __future__ import annotations

import argparse
import importlib
import logging
import math
import os
import re
import sys
import time
from dataclasses import asdict
from pathlib import Path

MESSAGE = "Finite recording operator is unconfirmed; preserve this case and do not replay."
FIELDS = ("plan", "plan-sha256", "source-sha256", "runtime-root", "ready-by")
WRITE_SECONDS = 2


def require(value):
    if not value:
        raise ValueError(MESSAGE)


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(MESSAGE)


def arguments(argv):
    require(type(argv) is list and len(argv) == 2 * len(FIELDS))
    require(all(type(part) is str for part in argv))
    require(set(argv[::2]) == {"--" + name for name in FIELDS})
    parser = _Parser(add_help=False, allow_abbrev=False)
    for name in FIELDS:
        parser.add_argument("--" + name, type=float if name == "ready-by" else str, required=True)
    args = parser.parse_args(argv)
    for value in (args.plan_sha256, args.source_sha256):
        require(re.fullmatch("[0-9a-f]{64}", value) is not None)
    for value in (args.plan, args.runtime_root):
        path = Path(value)
        require(
            path.is_absolute()
            and path != Path("/")
            and str(path) == value
            and ".." not in path.parts
        )
    require(Path(args.plan).name == "launch.json")
    require(math.isfinite(args.ready_by) and 0 < args.ready_by - time.monotonic() <= 600)
    return args


def _envelope(guardian, session, phase, body):
    require(type(session) is guardian.Session)
    return {
        "schema": 1,
        "kind": "finite-recording-operator",
        "phase": phase,
        "context": session.context.payload(),
        "guardian": {
            "pid": os.getpid(),
            "start_ticks": guardian.control.returns._identity(os.getpid())[1],
            "uid": os.geteuid(),
            "gid": os.getegid(),
        },
        "native": {
            "pid": session.process.pid,
            "start_ticks": session.ticks,
            "uid": os.geteuid(),
            "gid": os.getegid(),
        },
        "watchdog": {
            "pid": session.watch.pid,
            "start_ticks": session.watch.ticks,
            "uid": os.geteuid(),
            "gid": os.getegid(),
            "deadline": session.watch.deadline,
            "grace": session.watch.grace,
        },
        "body": body,
    }


def _received(guardian, session, received):
    require(type(received) is guardian.control.returns.Received)
    require((received.pid, received.start_ticks) == (session.process.pid, session.ticks))
    require((received.uid, received.gid) == (os.geteuid(), os.getegid()))
    return asdict(received) | {"raw": received.raw.decode("ascii")}


def _begin(guardian, session, request):
    fields = guardian.control.launch.protected._mapping
    fields(request, {"schema", "kind", "phase", "context", "body"})
    require(type(request["schema"]) is int and request["schema"] == 1)
    require(request["kind"] == "finite-recording-operator" and request["phase"] == "begin")
    require(request["context"] == session.context.payload())
    body = fields(request["body"], {"binding", "intent_at", "intent_sha256"})
    parts = fields(
        body["binding"],
        {"manifest", "contract", "generation", "projection", "source", "start_by", "finish_by"},
    )
    p = session.context.plan
    binding = guardian.control.returns.Binding(
        p.stored,
        p.generation,
        p.projection_sha256,
        p.source_sha256,
        parts["start_by"],
        parts["finish_by"],
    )
    require(binding.payload() == parts)
    # Actual Parent validates original context, readiness, start/finish bounds
    # and the host's intent fields. A hash alone is not operator authorization.
    session.begin(binding, intent_at=body["intent_at"], intent_sha256=body["intent_sha256"])


def run(argv):
    require(sys.flags.isolated == 1 and sys.flags.dont_write_bytecode == 1)
    args = arguments(argv)
    source_file = Path(__file__)
    require(source_file.is_absolute() and source_file.resolve() == source_file)
    require(source_file.name == "accept_supplemental_recording_operator.py")
    # Host source qualification precedes execution/import. No caller-selected
    # helper root, PYTHONPATH or alternate interpreter is accepted here.
    sys.path.insert(0, str(source_file.parent))
    logging.disable(logging.CRITICAL)
    guardian = importlib.import_module("supplemental_recording_guardian")
    wire = importlib.import_module("supplemental_recording_wire")
    stream = wire.Stream(0, 1, role="operator")
    try:
        layout = guardian.source.Layout(Path(args.runtime_root), source_file.parent)
        with guardian.open_session(
            layout,
            Path(args.plan),
            plan_sha256=args.plan_sha256,
            source_sha256=args.source_sha256,
            ready_by=args.ready_by,
        ) as session:
            ready = session.receive_ready()
            stream.send(
                _envelope(
                    guardian, session, "ready", {"received": _received(guardian, session, ready)}
                ),
                deadline=min(args.ready_by, time.monotonic() + WRITE_SECONDS),
            )
            request = stream.receive(deadline=args.ready_by)
            _begin(guardian, session, request)
            for phase in ("started", "completed"):
                received = session.receive()
                deadline = (
                    session.receiver.binding.start_by
                    if phase == "started"
                    else min(session.receiver.binding.finish_by, session.receiver.plan.finish_by)
                )
                stream.send(
                    _envelope(
                        guardian,
                        session,
                        phase,
                        {"received": _received(guardian, session, received)},
                    ),
                    deadline=min(deadline, time.monotonic() + WRITE_SECONDS),
                )
            exited = session.wait()
            # This records independently reaped native/watchdog facts; the host
            # must still inspect this exec AND bind/wait container init itself.
            stream.send(
                _envelope(guardian, session, "exited", asdict(exited)),
                deadline=min(
                    session.watch.deadline + session.watch.grace, time.monotonic() + WRITE_SECONDS
                ),
            )
    finally:
        stream.close()


def main(argv=None):
    try:
        run(sys.argv[1:] if argv is None else argv)
        return 0
    except BaseException:
        print(MESSAGE, file=sys.stderr)
        return 70


if __name__ == "__main__":
    raise SystemExit(main())
