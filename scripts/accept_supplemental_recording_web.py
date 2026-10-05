#!/usr/bin/env python3
"""Uninstalled fixed finite ingress process; not recording/start authority.

The host must qualify this exact exec, image, interpreter, runtime/environment,
original Ready and independent init/recovery deadline BEFORE invocation. A
matching request hash is not that qualification. No command, listener address,
auth bypass, deadline renewal, reconnect or ownership restoration is accepted.

This separate process is never a child of the native guardian. It makes no
recording success/exit claim. EOF, a reply, or a closed listener cannot replace
the host's original retained process/Engine exit witnesses. A frozen process
still requires the independently armed original container recovery deadline.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import importlib.util
import logging
import math
import os
import re
import select
import sys
import time
from contextlib import suppress
from pathlib import Path

MESSAGE = "Finite test dashboard ended or is unconfirmed; preserve this case, do not retry."
FIELDS = {"plan", "plan-sha256", "source-sha256", "runtime-root", "ready-by", "request-sha256"}
HOST, PORT = "0.0.0.0", 8099


def require(value):
    if not value:
        raise ValueError(MESSAGE)


def arguments(argv):
    require(type(argv) is list and len(argv) == 2 * len(FIELDS))
    require(all(type(part) is str for part in argv))
    require(set(argv[::2]) == {"--" + name for name in FIELDS})
    result = {name[2:]: value for name, value in zip(argv[::2], argv[1::2], strict=True)}
    for name in ("plan-sha256", "source-sha256", "request-sha256"):
        require(re.fullmatch("[0-9a-f]{64}", result[name]) is not None)
    for name in ("plan", "runtime-root"):
        path = Path(result[name])
        require(path.is_absolute() and path != Path("/") and ".." not in path.parts)
        require(str(path) == result[name])
    require(Path(result["plan"]).name == "launch.json")
    result["ready-by"] = float(result["ready-by"])
    require(math.isfinite(result["ready-by"]))
    require(0 < result["ready-by"] - time.monotonic() <= 600)
    return result


def quiet(stream, deadline):
    """The original attachment must stay open and send no second request.

    EOF or even one extra byte consumes the case. This observes only this
    already-owned channel, not host identity or actual process completion.
    """
    stream._check(deadline)
    require(stream.reads == 1 and stream.read_limit == stream.write_limit == 1)
    require(not select.select([stream.fds[0]], [], [], 0)[0])


class _Gate:
    """No HTTP admission before the last original-input/source recheck."""

    def __init__(self, service):
        self.service, self.open = service, False

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan" or self.open:
            await self.service(scope, receive, send)
        elif scope["type"] == "http":
            await send(
                {
                    "type": "http.response.start",
                    "status": 503,
                    "headers": [
                        (b"content-type", b"text/plain"),
                        (b"cache-control", b"no-store"),
                        (b"x-content-type-options", b"nosniff"),
                        (b"content-length", b"0"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": b""})
        elif scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
        else:
            raise ValueError(MESSAGE)


async def serve(prepared, stream, qualify):
    """One fixed ingress server, terminated on original expiry/lost continuity.

    Cancellation here is NOT worker/process exit evidence. The executable's
    terminal os._exit prevents Python's executor join from extending shutdown;
    the independent original host deadline still covers blocked/frozen code.
    """
    import uvicorn
    from supplemental_recording_web_peer import Peers
    from supplemental_recording_web_service import Service

    peers = Peers(prepared.expected, prepared.sockets)
    server = task = None
    gate = None
    try:
        prepared.recheck()
        qualify()
        quiet(stream, prepared.ready_by)
        gate = _Gate(Service(peers, home_assistant_ingress=True))
        config = uvicorn.Config(
            gate,
            host=HOST,
            port=PORT,
            loop="asyncio",
            http="h11",
            ws="none",
            interface="asgi3",
            lifespan="on",
            workers=1,
            reload=False,
            access_log=False,
            log_config=None,
            proxy_headers=False,
            server_header=False,
            limit_concurrency=32,
            backlog=32,
            timeout_keep_alive=2,
            timeout_graceful_shutdown=0.5,
        )
        server = uvicorn.Server(config)
        task = asyncio.create_task(server.serve())
        while not server.started:
            require(not task.done())
            quiet(stream, prepared.ready_by)
            peers.check()
            await asyncio.sleep(0.01)
        prepared.recheck()
        qualify()
        quiet(stream, prepared.ready_by)
        peers.check()
        require(not task.done() and len(server.servers) == 1)
        sockets = server.servers[0].sockets
        require(len(sockets) == 1 and sockets[0].getsockname() == (HOST, PORT))
        gate.open = True
        stream.send(
            {
                "schema": 1,
                "kind": "finite-recording-web-listening",
                "request_sha256": prepared.request_sha256,
                "pid": os.getpid(),
                "observed_at": time.monotonic(),
                "deadline": prepared.expected.deadline,
            },
            deadline=prepared.ready_by,
        )
        while True:
            quiet(stream, prepared.expected.deadline)
            peers.check()
            require(not task.done())
            await asyncio.sleep(min(0.1, prepared.expected.deadline - time.monotonic()))
    finally:
        if gate is not None:
            gate.open = False
        peers.close()  # Wake native workers BEFORE awaiting server cleanup.
        if server is not None:
            server.should_exit = True
            for listener in getattr(server, "servers", ()):
                listener.close()
        if task is not None:
            # A local cleanup bound, never a new service or recovery lifetime.
            remaining = max(0, min(0.5, prepared.expected.deadline + 3 - time.monotonic()))
            await asyncio.wait({task}, timeout=remaining)
            if task.done():
                if not task.cancelled():
                    task.exception()
            else:
                task.cancel()


def run(argv):
    require(sys.flags.isolated == 1 and sys.flags.dont_write_bytecode == 1)
    args = arguments(argv)
    entry = Path(__file__)
    require(entry.is_absolute() and entry.resolve() == entry)
    require(entry.name == "accept_supplemental_recording_web.py")
    sys.path.insert(0, str(entry.parent))
    logging.disable(logging.CRITICAL)
    source = importlib.import_module("supplemental_recording_source")
    layout = source.Layout(Path(args["runtime-root"]), entry.parent)
    runtime = importlib.util.find_spec("sds200")
    require(runtime is not None and runtime.origin == str(layout.runtime / "__init__.py"))

    def qualify():
        for name in source.MODULES:
            module = sys.modules.get(name)
            if module is not None:
                require(getattr(module, "__file__", None) == str(entry.parent / (name + ".py")))
        layout.verify(args["source-sha256"])
        require(time.monotonic() < args["ready-by"])

    qualify()
    plans = importlib.import_module("supplemental_recording_web_plan")
    wire = importlib.import_module("supplemental_recording_wire")
    stream = wire.Stream(0, 1, role="web")
    loop = None
    try:
        request = stream.receive(deadline=args["ready-by"])
        require(hashlib.sha256(wire.encode(request)).hexdigest() == args["request-sha256"])
        prepared = plans.prepare(
            request,
            Path(args["plan"]),
            plan_sha256=args["plan-sha256"],
            source_sha256=args["source-sha256"],
        )
        require(prepared.ready_by == args["ready-by"])
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(serve(prepared, stream, qualify))
    finally:
        if loop is not None:
            # Do not call asyncio.run()/shutdown_default_executor(): threads
            # with unconfirmed cleanup must not hold up this terminal process.
            loop.close()
            asyncio.set_event_loop(None)
        stream.close()


def main(argv=None):
    with suppress(BaseException):
        run(sys.argv[1:] if argv is None else argv)
    # A terminal web process cannot report recording success or safe restore.
    with suppress(OSError):
        os.write(2, (MESSAGE + "\n").encode("ascii"))
    return 70


if __name__ == "__main__":
    os._exit(main())
