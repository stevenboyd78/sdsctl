"""LOCAL PROBE ONLY. Copied into a private fixture; never register for production."""

import json
import os
import re
import signal
import ssl
import stat
import struct
import sys
import time
import urllib.request
from pathlib import Path

from browser_device_protocol import BrowserDeviceAction, read_browser_device_request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def deadline_expired(signum, frame):
    raise TimeoutError


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate")
        result[key] = value
    return result


def authenticate():
    # Bound the entire helper lifetime, including a stalled native input pipe.
    signal.signal(signal.SIGALRM, deadline_expired)
    signal.alarm(10)
    root = Path(__file__).parent
    config = json.loads((root / "fixture.json").read_text())
    if sys.argv[1:] != [config["extension_origin"]]:
        raise ValueError("caller")
    request = read_browser_device_request(sys.stdin.buffer)
    if request is None or request.action != BrowserDeviceAction.AUTHENTICATE:
        raise ValueError("action")
    descriptor = os.open(root / "fictional-device.secret", os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise ValueError("owner/type")
        if stat.S_IMODE(info.st_mode) != 0o600 or not 0 < info.st_size <= 256:
            raise ValueError("mode/size")
        credential = stream.read(257).decode("ascii")
    context = ssl.create_default_context(cafile=str(root / "ca.pem"))
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), NoRedirect(),
        urllib.request.HTTPSHandler(context=context),
    )
    http_request = urllib.request.Request(
        config["origin"] + "/fixture/enroll",
        data=b"{}", method="POST", headers={"Authorization": "Bearer " + credential},
    )
    with opener.open(http_request, timeout=3) as response:
        payload = response.read(4097)
    if len(payload) > 4096:
        raise ValueError("size")
    result = json.loads(payload, object_pairs_hook=unique_object)
    if type(result) is not dict or set(result) != {"token", "expires"}:
        raise ValueError("response")
    if type(result["token"]) is not str or not re.fullmatch("[a-f0-9]{64}", result["token"]):
        raise ValueError("token")
    now = time.time()
    if type(result["expires"]) is not int or not now < result["expires"] <= now + 120:
        raise ValueError("expiry")
    return {"ok": True, **result}


if __name__ == "__main__":
    try:
        result = authenticate()
    except Exception:
        # Never emit input, HTTP errors, tokens or exception details to stderr.
        result = {"ok": False}
    signal.alarm(0)
    payload = json.dumps(result).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("=I", len(payload)) + payload)
    sys.stdout.buffer.flush()
