"""Pytest-only locations behind sanitized refusals; never format private values."""

import re
from pathlib import Path

SCRIPTS = Path(__file__).parents[1] / "scripts"
NAME = re.compile(r"(?:accept_)?supplemental_[a-z0-9_]+\.py")


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
