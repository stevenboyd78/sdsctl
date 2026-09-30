#!/usr/bin/env python3
"""Private fixed child entrypoint; not installed or a public recording command.

Only an independently source-qualified guardian may supply these inherited fds
and pinned inputs. It must bind this child and arm an independent hard watchdog
BEFORE releasing the gate. Self-supplied source/plan hashes are not authorization.
No ordinary daemon args, environment configuration, listening socket or retry.
"""

from __future__ import annotations

import argparse
import ctypes
import errno
import fcntl
import importlib
import logging
import math
import os
import re
import signal
import socket
import stat
import sys
import time
from pathlib import Path

MESSAGE = "Finite recording child is unconfirmed; preserve the case and do not retry."
FIELDS = (
    "plan",
    "plan-sha256",
    "source-sha256",
    "incoming-fd",
    "outgoing-fd",
    "gate-fd",
    "parent-pid",
    "parent-ticks",
    "parent-uid",
    "parent-gid",
    "ready-by",
)


def require(value):
    if not value:
        raise ValueError(MESSAGE)


def _failure_locations(error):
    """Bounded source locations for the explicit offline test boundary only."""
    source = Path(__file__).parent
    seen, rows = set(), []
    for index in range(8):
        if not isinstance(error, BaseException) or id(error) in seen:
            break
        seen.add(id(error))
        trace = error.__traceback__
        for _ in range(64):
            if trace is None:
                break
            path = Path(trace.tb_frame.f_code.co_filename)
            if path.parent == source and re.fullmatch(
                r"(?:accept_)?supplemental_[a-z0-9_]+\.py", path.name
            ):
                row = f"cause {index + 1}: scripts/{path.name}:{trace.tb_lineno}"
                if row not in rows:
                    rows.append(row)
                    if len(rows) == 32:
                        return tuple(rows)
            trace = trace.tb_next
        error = error.__cause__ if error.__cause__ is not None else error.__context__
    return tuple(rows)


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(MESSAGE)


def arguments(argv):
    require(type(argv) is list and len(argv) == 2 * len(FIELDS))
    require(all(type(item) is str for item in argv))
    require(set(argv[::2]) == {"--" + name for name in FIELDS})
    parser = _Parser(add_help=False, allow_abbrev=False)
    for name in FIELDS:
        kind = (
            float
            if name == "ready-by"
            else (int if name.endswith("-fd") or name.startswith("parent-") else str)
        )
        parser.add_argument("--" + name, required=True, type=kind)
    args = parser.parse_args(argv)
    for digest in (args.plan_sha256, args.source_sha256):
        require(re.fullmatch("[0-9a-f]{64}", digest) is not None)
    path = Path(args.plan)
    require(path.is_absolute() and str(path) == args.plan and ".." not in path.parts)
    require(path.name == "launch.json")
    require(1 < args.parent_pid < 2**31 and 0 < args.parent_ticks < 2**63)
    require(args.parent_uid == os.geteuid() and args.parent_gid == os.getegid())
    require(math.isfinite(args.ready_by) and 0 < args.ready_by - time.monotonic() <= 600)
    fds = (args.incoming_fd, args.outgoing_fd, args.gate_fd)
    require(len(set(fds)) == 3 and all(2 < fd < 2**31 for fd in fds))
    return args


def _guardian(args):
    require(os.getppid() == args.parent_pid)
    with open(f"/proc/{args.parent_pid}/stat", "rb", buffering=0) as source:
        raw = source.read(4097)
    require(len(raw) <= 4096)
    prefix, delimiter, suffix = raw.rpartition(b") ")
    require(delimiter and prefix.startswith(str(args.parent_pid).encode() + b" ("))
    fields = suffix.split()
    require(len(fields) >= 20 and fields[0] in (b"R", b"S", b"D", b"T", b"t", b"I"))
    require(fields[19].isdigit() and int(fields[19]) == args.parent_ticks)


def _parent_death(args):
    # Before even importing the native graph or waiting at the launch gate.
    _guardian(args)
    prctl = ctypes.CDLL(None, use_errno=True).prctl
    prctl.restype = ctypes.c_int
    prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
    require(prctl(1, signal.SIGKILL, 0, 0, 0) == 0)
    actual = ctypes.c_int()
    require(prctl(2, ctypes.addressof(actual), 0, 0, 0) == 0 and actual.value == signal.SIGKILL)
    _guardian(args)


def _descriptors(allowed):
    # A fixed launcher passes only the gate and two directional endpoints.
    # Enumerate incrementally; after closing the iterator its own transient fd
    # is absent. Non-inheritable descriptors were created after exec (ctypes,
    # for example, can retain a CLOEXEC libffi fd). They are not capabilities
    # inherited from the guardian. Do not allow unrelated inheritable ones.
    names = []
    with os.scandir("/proc/self/fd") as entries:
        for entry in entries:
            require(len(names) < 16 and entry.name.isdecimal())
            names.append(int(entry.name))
    for fd in names:
        try:
            os.fstat(fd)
        except OSError as error:
            require(error.errno == errno.EBADF)
        else:
            require(not os.get_inheritable(fd) or fd in allowed)


def run(argv):
    # Isolated Python ignores PYTHONPATH/user site; -B prevents this finite run
    # mutating its authenticated source inventory by publishing bytecode.
    require(sys.flags.isolated == 1 and sys.flags.dont_write_bytecode == 1)
    args = arguments(argv)
    _parent_death(args)
    require(
        all(os.get_inheritable(fd) for fd in (args.incoming_fd, args.outgoing_fd, args.gate_fd))
    )
    _descriptors({0, 1, 2, args.incoming_fd, args.outgoing_fd, args.gate_fd})
    incoming = outgoing = None
    gate = args.gate_fd
    try:
        require(stat.S_ISFIFO(os.fstat(gate).st_mode))
        require(re.fullmatch(r"pipe:\[[0-9]+\]", os.readlink(f"/proc/self/fd/{gate}")) is not None)
        require(fcntl.fcntl(gate, fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDONLY)
        for fd in (args.incoming_fd, args.outgoing_fd, gate):
            os.set_inheritable(fd, False)
        incoming, outgoing = (
            socket.socket(fileno=args.incoming_fd),
            socket.socket(fileno=args.outgoing_fd),
        )
        for endpoint, credentials in ((incoming, 1), (outgoing, 0)):
            require(endpoint.family == socket.AF_UNIX)
            require(endpoint.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) == socket.SOCK_SEQPACKET)
            require(endpoint.getsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED) == credentials)
            require(endpoint.getsockname() in ("", b"") and endpoint.getpeername() in ("", b""))
        require(os.read(gate, 2) == b"1")
        os.close(gate)
        gate = -1
        _guardian(args)
        require(time.monotonic() < args.ready_by)
        # This directory's COMPLETE fixed inventory and the installed product
        # package/image must already have been authenticated by the guardian.
        # Resolving a caller-selected PYTHONPATH or ignoring extra files is not
        # permitted. This child check does not authenticate its own source.
        source = Path(__file__)
        require(source.is_absolute() and source.resolve() == source)
        require(source.name == "accept_supplemental_recording.py")
        sys.path.insert(0, str(source.parent))
        logging.disable(logging.CRITICAL)
        control = importlib.import_module("supplemental_recording_control")
        plan = control.launch.load(
            Path(args.plan),
            expected_sha256=args.plan_sha256,
            expected_source_sha256=args.source_sha256,
        )
        context = control.Context(plan, args.ready_by)
        control.execute(
            control.Channels(incoming, outgoing),
            context,
            parent_pid=args.parent_pid,
            parent_start_ticks=args.parent_ticks,
            parent_uid=args.parent_uid,
            parent_gid=args.parent_gid,
        )
    finally:
        if gate >= 0:
            os.close(gate)
        if incoming is not None:
            incoming.close()
        if outgoing is not None:
            outgoing.close()


def main(argv=None):
    try:
        run(sys.argv[1:] if argv is None else argv)
        return 0
    except BaseException as error:
        if os.environ.get("SDSCTL_TEST_FAILURE_LOCATIONS") == "1":
            for location in _failure_locations(error):
                print(location, file=sys.stderr)
        print(MESSAGE, file=sys.stderr)
        return 70


if __name__ == "__main__":
    raise SystemExit(main())
