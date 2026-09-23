#!/usr/bin/env python3
"""Read-only CPython 3.14/Linux runtime evidence, separate from product source.

No candidate interpreter, loader, ldd, package manager or observed code is run.
The caller supplies a separately authenticated, unshadowed image/rootfs and an
independently reconstructed expected pin. Observations do not authenticate
themselves or authorize an App, host helper, exec, recording or recovery action.
"""

from __future__ import annotations

import hashlib
import math
import os
import posixpath
import re
import stat
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import supplemental_handoff_process as processes
from supplemental_handoff_files import DIRECTORY, identity
from supplemental_handoff_policy import checksum, digest

KIND = "finite-recording-python314-runtime-v1"
MESSAGE = "Recording interpreter evidence is unconfirmed; do not launch the private runtime."
TREES = (
    "usr/local",
    "usr/lib",
    "usr/lib64",
    "etc/ssl",
    "usr/share/ca-certificates",
    "etc/ld.so.conf.d",
)
FILES = ("etc/ld.so.cache", "etc/ld.so.conf")
ALIASES = {"lib": "usr/lib", "lib64": "usr/lib64"}
ABSENT = (
    "etc/ld.so.preload",
    "usr/local/pyvenv.cfg",
    "usr/local/bin/pyvenv.cfg",
    "usr/local/lib/python314.zip",
)
REQUIRED = (
    "usr/local/bin/python",
    "usr/local/lib/libpython3.14.so.1.0",
    "usr/local/lib/python3.14/os.py",
    "usr/local/lib/python3.14/site.py",
    "usr/local/lib/python3.14/encodings/__init__.py",
    "usr/local/lib/python3.14/site-packages",
    "lib64/ld-linux-x86-64.so.2",
)
ROOT_UID = ROOT_GID = 0
MAX_ENTRIES = 20000
MAX_DEPTH = 24
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_SECONDS = 20.0
ENV_KEYS = frozenset(
    {"PATH", "PYTHON_VERSION", "PYTHON_SHA256", "PYTHONDONTWRITEBYTECODE", "PYTHONUNBUFFERED"}
)
PATH_PARTS = frozenset(
    {"/usr/local/bin", "/usr/local/sbin", "/usr/bin", "/usr/sbin", "/bin", "/sbin"}
)
SUPERVISED_KEYS = ENV_KEYS | {"TZ", "SUPERVISOR_TOKEN", "HASSIO_TOKEN"}
PROCESS_KEYS = SUPERVISED_KEYS | {"HOME", "HOSTNAME"}
FIXED_EXEC_PATH = "/usr/local/bin:/usr/bin:/bin"


class UnconfirmedRuntime(ValueError):
    """No observed path, environment value or content is disclosed."""


def require(value):
    if not value:
        raise UnconfirmedRuntime(MESSAGE)


def environment(values):
    """Closed IMAGE/CONTAINER Config.Env profile, not os.environ after Python.

    -I does not sanitize the ELF loader environment. Do not filter a supplied
    list: duplicates, extra keys and loader hooks must fail. This narrow offline
    profile does not yet admit Supervisor credentials or other App environment.
    Matching it is not evidence that the process actually inherited that list.
    """
    try:
        require(type(values) is list and len(values) == len(ENV_KEYS))
        parsed = {}
        for item in values:
            require(type(item) is str and 1 <= len(item) <= 1024)
            require(all(32 <= ord(c) < 127 for c in item) and "=" in item)
            key, value = item.split("=", 1)
            require(key in ENV_KEYS and key not in parsed)
            parsed[key] = value
        require(set(parsed) == ENV_KEYS)
        require(parsed["PYTHONDONTWRITEBYTECODE"] == parsed["PYTHONUNBUFFERED"] == "1")
        require(re.fullmatch(r"3\.14\.(0|[1-9][0-9]{0,2})", parsed["PYTHON_VERSION"]))
        digest(parsed["PYTHON_SHA256"])
        parts = parsed["PATH"].split(":")
        require(1 <= len(parts) <= 16 and parts[0] == "/usr/local/bin")
        require(set(parts) <= PATH_PARTS)
        return checksum({"schema": 1, "kind": KIND + "-environment", "values": parsed})
    except Exception:
        raise UnconfirmedRuntime(MESSAGE) from None


def _environment_values(values, keys):
    """Internal closed parser; never filter unknowns before validating shape."""
    require(type(values) is list and len(values) == len(keys))
    parsed = {}
    for item in values:
        require(type(item) is str and 1 <= len(item) <= 1024)
        require(all(32 <= ord(c) < 127 for c in item) and "=" in item)
        key, value = item.split("=", 1)
        require(key in keys and key not in parsed)
        parsed[key] = value
    require(set(parsed) == keys)
    return parsed


def _supervised_values(values, *, image_environment_sha256, timezone):
    digest(image_environment_sha256)
    require(type(timezone) is str and 1 <= len(timezone) <= 64)
    # A pinned zoneinfo name, not a TZ file path or arbitrary POSIX TZ program.
    # Existence/zoneinfo bytes belong to independent runtime qualification.
    require(re.fullmatch(r"[A-Za-z][A-Za-z0-9_+-]*(?:/[A-Za-z][A-Za-z0-9_+-]*)*", timezone))
    parsed = _environment_values(values, SUPERVISED_KEYS)
    require(parsed["TZ"] == timezone)
    require(
        environment([key + "=" + parsed[key] for key in sorted(ENV_KEYS)])
        == image_environment_sha256
    )
    for key in ("SUPERVISOR_TOKEN", "HASSIO_TOKEN"):
        token = parsed[key]
        # Opaque credential shape only, never authentication or an assumption
        # that both token values match. Exact bytes enter the fingerprint below.
        require(32 <= len(token) <= 512 and all(33 <= ord(c) < 127 for c in token))
    return parsed


def supervised_environment(values, *, image_environment_sha256, timezone):
    """Explicit eight-key Supervisor Config.Env profile; no default admission.

    The original five-key environment() is unchanged. Image values must match
    their independently reconstructed pin, and TZ must equal the caller's
    separately pinned timezone. Exact opaque credential values contribute to
    the hash, but are never returned, persisted, or printed here. Rotation must
    not silently satisfy an earlier container pin. Input lists are unchanged.

    This does not authenticate credentials, execute code, qualify an installed
    App, or prove which environment a process actually inherited. Never pass
    filtered Config.Env or os.environ as a substitute for original input.
    """
    try:
        parsed = _supervised_values(
            values, image_environment_sha256=image_environment_sha256, timezone=timezone
        )
        return checksum({"schema": 1, "kind": KIND + "-supervised-environment", "values": parsed})
    except Exception:
        raise UnconfirmedRuntime(MESSAGE) from None


def supervised_process_environment(
    raw,
    *,
    configured,
    configured_sha256,
    image_environment_sha256,
    timezone,
    hostname,
    fixed_exec,
):
    """Pure comparison of bounded startup /proc environ bytes, not a collector.

    The host must bind the read to the ORIGINAL retained process/container and
    check identity before/after it. A matching byte string supplies no process
    provenance. Only HOME=/root, the exact pinned hostname, and (when explicitly
    selected) the existing fixed Engine exec PATH may differ from Config.Env.
    No arbitrary overrides, filtering, credential export, retry, or execution.
    PID1 uses fixed_exec=False; operator/probe/web and their children use True.
    """
    try:
        digest(configured_sha256)
        require(type(fixed_exec) is bool)
        require(type(hostname) is str)
        require(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}[A-Za-z0-9])?", hostname))
        expected = _supervised_values(
            configured, image_environment_sha256=image_environment_sha256, timezone=timezone
        )
        require(
            checksum({"schema": 1, "kind": KIND + "-supervised-environment", "values": expected})
            == configured_sha256
        )
        expected.update(HOME="/root", HOSTNAME=hostname)
        if fixed_exec:
            expected["PATH"] = FIXED_EXEC_PATH
        require(type(raw) is bytes and 0 < len(raw) <= 16384 and raw.endswith(b"\0"))
        parsed = _environment_values(raw[:-1].decode("ascii").split("\0"), PROCESS_KEYS)
        require(parsed == expected)
        return checksum(
            {
                "schema": 1,
                "kind": KIND + "-supervised-process-environment",
                "configured": configured_sha256,
                "fixed_exec": fixed_exec,
                "values": parsed,
            }
        )
    except Exception:
        raise UnconfirmedRuntime(MESSAGE) from None


@dataclass(frozen=True)
class ProcessEnvironment:
    sha256: str
    process: processes.ProcessIdentity
    observed_at: float


def collect_supervised_process_environment(witness, *, deadline, **profile):
    """Two bounded startup reads through an already live-bound original pidfd.

    No process lookup/rebinding, exec, signal, environment rewrite or authority.
    Caller retains the witness and independently checks original container,
    image, namespace and fixed command around this collection. The absolute
    monotonic deadline must have at most one second remaining and is never
    refreshed here; an independently supervised outer bound covers kernel I/O.
    Only a digest, original identity and observation START time are returned.
    """
    fd = -1
    try:
        require(type(witness) is processes.ProcessWitness and os.geteuid() == ROOT_UID)
        require(type(deadline) in (int, float) and math.isfinite(deadline))
        started = time.monotonic()
        require(0 < deadline - started <= 1)
        original = witness.identity
        require(type(original) is processes.ProcessIdentity)
        original_fd = witness.fd
        require(type(original_fd) is int and original_fd >= 0)
        original_fd_identity = identity(os.fstat(original_fd))

        def check():
            require(time.monotonic() < deadline)
            require(witness.identity == original and witness.fd == original_fd)
            require(identity(os.fstat(original_fd)) == original_fd_identity)
            require(not witness.exited())
            require(processes.read_identity(original.pid, original.container_id) == original)
            require(time.monotonic() < deadline and not witness.exited())

        check()
        path = f"/proc/{original.pid}/environ"
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == ROOT_UID)
        file_identity = identity(info)

        def read():
            require(time.monotonic() < deadline)
            os.lseek(fd, 0, os.SEEK_SET)
            result = bytearray()
            while True:
                require(time.monotonic() < deadline)
                chunk = os.read(fd, 16385 - len(result))
                if not chunk:
                    break
                result.extend(chunk)
                require(len(result) <= 16384)
            require(identity(os.fstat(fd)) == file_identity)
            return bytes(result)

        first = read()
        check()
        require(read() == first)
        require(identity(os.stat(path, follow_symlinks=False)) == file_identity)
        result = supervised_process_environment(first, **profile)
        check()
        return ProcessEnvironment(result, original, started)
    except Exception:
        raise UnconfirmedRuntime(MESSAGE) from None
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except Exception:
                raise UnconfirmedRuntime(MESSAGE) from None


def _resolve(path, entries):
    """Resolve only inventoried lexical links, never follow a filesystem link."""
    pending, seen = path.split("/"), set()
    while True:
        resolved = []
        for index, part in enumerate(pending):
            resolved.append(part)
            name = "/".join(resolved)
            entry = entries.get(name)
            if entry is not None and entry["kind"] == "symlink":
                require(name not in seen and len(seen) < 40)
                seen.add(name)
                target = entry["target"]
                combined = posixpath.normpath(posixpath.join("/", *resolved[:-1], target))
                require(combined.startswith("/") and not combined.startswith("//"))
                pending = combined.removeprefix("/").split("/") + pending[index + 1 :]
                break
            # Parents above selected roots are intentionally not inventoried;
            # those below a selected root must be real, observed directories.
            if index < len(pending) - 1 and entry is not None:
                require(entry["kind"] == "directory")
        else:
            final = "/".join(resolved)
            require(final in entries and entries[final]["kind"] != "symlink")
            return final


@dataclass(frozen=True)
class Evidence:
    sha256: str
    entry_count: int
    file_count: int
    total_bytes: int


@dataclass(frozen=True)
class Layout:
    root: Path

    def _snapshot(self, deadline):
        opened, entries, total, count = [], {}, 0, 0

        def timely():
            require(time.monotonic() < deadline)

        def safe(info, *, link=False):
            require(info.st_uid == ROOT_UID and info.st_gid == ROOT_GID)
            if not link:
                require(stat.S_IMODE(info.st_mode) & 0o7022 == 0)

        def directory(parent, name):
            before = os.stat(name, dir_fd=parent, follow_symlinks=False)
            child = os.open(name, DIRECTORY, dir_fd=parent)
            opened.append((parent, name, child, identity(before)))
            require(identity(os.fstat(child)) == identity(before))
            return child

        def record(parent, name, relative, depth=0):
            nonlocal total, count
            timely()
            require(depth <= MAX_DEPTH and len(entries) < MAX_ENTRIES and relative not in entries)
            require(0 < len(name.encode()) <= 255 and all(ord(c) >= 32 for c in name))
            before = os.stat(name, dir_fd=parent, follow_symlinks=False)
            link = stat.S_ISLNK(before.st_mode)
            safe(before, link=link)
            meta = {
                "mode": stat.S_IMODE(before.st_mode),
                "uid": before.st_uid,
                "gid": before.st_gid,
            }
            if stat.S_ISDIR(before.st_mode):
                entries[relative] = {"kind": "directory", **meta}
                child = os.open(name, DIRECTORY, dir_fd=parent)
                try:
                    require(identity(os.fstat(child)) == identity(before))
                    with os.scandir(child) as children:
                        for entry in children:
                            record(child, entry.name, relative + "/" + entry.name, depth + 1)
                    require(identity(os.fstat(child)) == identity(before))
                finally:
                    os.close(child)
            elif link:
                target = os.readlink(name, dir_fd=parent)
                require(0 < len(target.encode()) <= 4096 and all(ord(c) >= 32 for c in target))
                entries[relative] = {"kind": "symlink", "target": target, **meta}
            else:
                require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1)
                require(0 <= before.st_size <= MAX_FILE_BYTES)
                total += before.st_size
                count += 1
                require(total <= MAX_TOTAL_BYTES)
                fd = os.open(
                    name, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
                )
                try:
                    require(identity(os.fstat(fd)) == identity(before))
                    hashed, size = hashlib.sha256(), 0
                    while True:
                        timely()
                        chunk = os.read(fd, min(65536, before.st_size + 1 - size))
                        if not chunk:
                            break
                        size += len(chunk)
                        require(size <= before.st_size)
                        hashed.update(chunk)
                    require(size == before.st_size and identity(os.fstat(fd)) == identity(before))
                    entries[relative] = {
                        "kind": "file",
                        "size": size,
                        "sha256": hashed.hexdigest(),
                        **meta,
                    }
                finally:
                    os.close(fd)
            require(
                identity(os.stat(name, dir_fd=parent, follow_symlinks=False)) == identity(before)
            )

        try:
            anchor = os.open("/", DIRECTORY)
            opened.append((None, None, anchor, None))
            opened[-1] = (None, None, anchor, identity(os.fstat(anchor)))
            root = anchor
            for part in self.root.parts[1:]:
                root = directory(root, part)
            safe(os.fstat(root))

            def parent_for(relative):
                parent = root
                for name in PurePosixPath(relative).parts[:-1]:
                    parent = directory(parent, name)
                    safe(os.fstat(parent))
                return parent

            for path in (*TREES, *FILES, *ALIASES):
                parent = parent_for(path)
                record(parent, PurePosixPath(path).name, path)
                expected = "directory" if path in TREES else "file" if path in FILES else "symlink"
                require(entries[path]["kind"] == expected)
                if path in ALIASES:
                    require(entries[path]["target"] == ALIASES[path])
            for path in ABSENT:
                parent = parent_for(path)
                try:
                    os.stat(PurePosixPath(path).name, dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    require(False)
            for path, entry in entries.items():
                if entry["kind"] == "symlink":
                    _resolve(path, entries)
                if path.startswith("usr/local/"):
                    require(not PurePosixPath(path).name.endswith("._pth"))
                # Startup executes .pth and customization code before the
                # application can verify itself; none is allowed in this image.
                if path.startswith("usr/local/lib/python3.14/"):
                    name = PurePosixPath(path).name
                    require(not name.endswith(".pth"))
                    require(not re.match(r"(?:sitecustomize|usercustomize)(?:\.|$)", name))
            for path in REQUIRED:
                final = _resolve(path, entries)
                expected = "directory" if path.endswith("site-packages") else "file"
                require(entries[final]["kind"] == expected)
            for parent, name, fd, before in opened:
                require(identity(os.fstat(fd)) == before)
                if parent is not None:
                    require(identity(os.stat(name, dir_fd=parent, follow_symlinks=False)) == before)
            timely()
            return entries, count, total
        finally:
            for _, _, fd, _ in reversed(opened):
                os.close(fd)

    def observe(self):
        """Two bounded full reads; no mtime, asset, bytecode or library exclusion.

        This pins the listed interpreter/library/TLS/loader trees, not all of
        Linux, /opt helpers, process mappings or mount topology. Code-root mounts,
        exact image/configuration, dynamic-loader environment, kernel/process
        identity and original independent deadlines remain separate obligations.
        Kernel I/O stalls require an independently supervised outer deadline.
        """
        try:
            require(type(self.root) is type(Path()) and self.root.is_absolute())
            require(".." not in self.root.parts and not str(self.root).startswith("//"))
            deadline = time.monotonic() + MAX_SECONDS
            first, count, size = self._snapshot(deadline)
            second, count2, size2 = self._snapshot(deadline)
            require((first, count, size) == (second, count2, size2))
            require(time.monotonic() < deadline)
            return Evidence(
                checksum({"schema": 1, "kind": KIND, "entries": first, "absent": ABSENT}),
                len(first),
                count,
                size,
            )
        except Exception:
            raise UnconfirmedRuntime(MESSAGE) from None

    def verify(self, expected_sha256):
        try:
            digest(expected_sha256)
            observed = self.observe()
            require(observed.sha256 == expected_sha256)
            return observed
        except Exception:
            raise UnconfirmedRuntime(MESSAGE) from None


if __name__ == "__main__":
    raise SystemExit("Read-only interpreter evidence only; no runtime launch enabled.")
