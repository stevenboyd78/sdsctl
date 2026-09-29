"""Pytest-only locations behind sanitized refusals; never format private values."""

import json
import re
from pathlib import Path

SCRIPTS = Path(__file__).parents[1] / "scripts"
NAME = re.compile(r"(?:accept_)?supplemental_[a-z0-9_]+\.py")
CHILD_FRAME = re.compile(
    r'\s*File "'
    + re.escape(str(SCRIPTS))
    + r'/((?:accept_)?supplemental_[a-z0-9_]+\.py)", line ([0-9]{1,6})(?:,.*)?'
)
CHILD_NOTE = re.compile(
    r"cause [1-8]: scripts/((?:accept_)?supplemental_[a-z0-9_]+\.py):([0-9]{1,6})"
)


def failure_locations(error):
    """Bounded exception chain, restricted to this checkout's private scripts.

    Do not call str/repr on errors, inspect locals, read source lines, or print
    absolute paths. Production exceptions remain sanitized and unchanged.
    Cycles and long chains cannot turn failure reporting into unbounded work.
    """
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
            if path.parent == SCRIPTS and NAME.fullmatch(path.name):
                rows.append(f"cause {index + 1}: scripts/{path.name}:{trace.tb_lineno}")
            trace = trace.tb_next
        error = error.__cause__ if error.__cause__ is not None else error.__context__
    return "\n".join(rows)


def child_failure_locations(raw):
    """Extract bounded known-checkout locations, never arbitrary child stderr.

    Only a finished disposable fixture's bounded nonblocking stderr read is
    supplied here. Do not echo exception text, source lines, notes, PID values,
    local variables, or arbitrary paths. Empty output is not a success claim.
    """
    if type(raw) is not bytes:
        return ""
    rows = []
    for line in raw[:65536].decode("ascii", errors="replace").splitlines():
        match = CHILD_FRAME.fullmatch(line) or CHILD_NOTE.fullmatch(line)
        if match is not None:
            row = f"child: scripts/{match[1]}:{match[2]}"
            if row not in rows:
                rows.append(row)
            if len(rows) == 32:
                break
    return "\n".join(rows)


def child_result_failure_locations(raw):
    """Recover only location notes from complete, unread fixture result lines.

    The same stdout may contain private inputs and plans. Never report them or
    parse more than one bounded post-exit read. Truncated lines, non-results and
    arbitrary fields/notes are not evidence; absent output is not success.
    """
    if type(raw) is not bytes:
        return ""
    rows = []
    for line in raw[:65536].split(b"\n")[:-1]:
        try:
            result = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if type(result) is not dict or result.get("error") != "refused":
            continue
        notes = result.get("refusal_locations")
        if type(notes) is not list:
            continue
        for note in notes[:8]:
            if type(note) is not str:
                continue
            for row in child_failure_locations(note.encode("ascii", errors="replace")).splitlines():
                if row not in rows:
                    rows.append(row)
                if len(rows) == 32:
                    return "\n".join(rows)
    return "\n".join(rows)
