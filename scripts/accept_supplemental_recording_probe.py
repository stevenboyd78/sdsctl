#!/usr/bin/env python3
"""Fixed one-shot cached probe over an independently qualified exec attachment.

Not installed. Input/output framing, matching hashes and exit0 authenticate
nothing by themselves. The host must qualify image/interpreter/environment,
source, original container/actor/clock witnesses and the exact exec around this
operation. No listener, scanner request, demand, recording action, signal or
ownership restoration is exposed. A partial frame is not usable health evidence.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import math
import re
import sys
import time
from pathlib import Path

MESSAGE = "Finite recording cached probe is unconfirmed; no ownership change is authorized."
FIELDS = {"plan", "plan-sha256", "source-sha256", "runtime-root", "probe-by"}
MAX_SECONDS = 8


def require(value):
    if not value:
        raise ValueError(MESSAGE)


def arguments(argv):
    require(type(argv) is list and len(argv) == 2 * len(FIELDS))
    require(all(type(part) is str for part in argv))
    require(set(argv[::2]) == {"--" + name for name in FIELDS})
    result = {name[2:]: value for name, value in zip(argv[::2], argv[1::2], strict=True)}
    for name in ("plan-sha256", "source-sha256"):
        require(re.fullmatch("[0-9a-f]{64}", result[name]) is not None)
    for name in ("plan", "runtime-root"):
        path = Path(result[name])
        require(path.is_absolute() and path != Path("/") and ".." not in path.parts)
        require(str(path) == result[name])
    require(Path(result["plan"]).name == "launch.json")
    result["probe-by"] = float(result["probe-by"])
    require(math.isfinite(result["probe-by"]))
    require(0 < result["probe-by"] - time.monotonic() <= MAX_SECONDS)
    return result


def run(argv):
    require(sys.flags.isolated == 1 and sys.flags.dont_write_bytecode == 1)
    args = arguments(argv)
    entry = Path(__file__)
    require(entry.is_absolute() and entry.resolve() == entry)
    require(entry.name == "accept_supplemental_recording_probe.py")
    # An already source-qualified fixed directory, not a caller PYTHONPATH.
    sys.path.insert(0, str(entry.parent))
    logging.disable(logging.CRITICAL)
    source = importlib.import_module("supplemental_recording_source")
    layout = source.Layout(Path(args["runtime-root"]), entry.parent)
    runtime = importlib.util.find_spec("sds200")
    require(runtime is not None and runtime.origin == str(layout.runtime / "__init__.py"))
    layout.verify(args["source-sha256"])
    probe = importlib.import_module("supplemental_recording_probe")
    # Load the already source-qualified reader graph while waiting for the
    # single request. Do NOT read a plan, profile, cache or process here: those
    # observations must remain fresh and request/actor-bound in probe.sample().
    importlib.import_module("supplemental_recording_launch_plan")
    wire = importlib.import_module("supplemental_recording_wire")
    stream = wire.Stream(0, 1, role="probe")
    try:
        request = stream.receive(deadline=args["probe-by"])
        result = probe.sample(
            request,
            Path(args["plan"]),
            plan_sha256=args["plan-sha256"],
            source_sha256=args["source-sha256"],
            probe_by=args["probe-by"],
        )
        for name in source.MODULES:
            module = sys.modules.get(name)
            if module is not None:
                require(getattr(module, "__file__", None) == str(entry.parent / (name + ".py")))
        layout.verify(args["source-sha256"])
        stream.send(result, deadline=args["probe-by"])
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
