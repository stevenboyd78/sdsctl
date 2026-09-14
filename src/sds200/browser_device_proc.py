"""Fixed recovery-bundle mount point; never a path selected by a browser message.

Chromium needs /proc to describe its own PID namespace. A separate read-only
host proc mount supplies native owner checks without changing Chromium's view.
"""
from __future__ import annotations

import ctypes
import os
import stat
from pathlib import Path

HOST_PROC = "host-proc"


def check_host_proc(path: Path, *, mounted: bool) -> None:
    """Validate either the empty private mount point or actual read-only procfs."""
    if not path.is_absolute() or path.resolve() != path:
        raise ValueError()
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        named = path.lstat()
        if (info.st_dev, info.st_ino) != (named.st_dev, named.st_ino):
            raise ValueError()
        if mounted:
            # Linux statfs starts with a native long filesystem magic. Reserve
            # more than the full structure for either supported word size.
            data = ctypes.create_string_buffer(256)
            libc = ctypes.CDLL(None, use_errno=True)
            libc.fstatfs.argtypes = [ctypes.c_int, ctypes.c_void_p]
            libc.fstatfs.restype = ctypes.c_int
            if (libc.fstatfs(fd, data) != 0
                    or ctypes.c_long.from_buffer(data).value != 0x9FA0
                    or not os.fstatvfs(fd).f_flag & os.ST_RDONLY):
                raise ValueError()
        elif (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700
              or os.listdir(fd)):
            raise ValueError()
    finally:
        os.close(fd)
