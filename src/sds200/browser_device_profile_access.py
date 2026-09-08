"""Linux advisory ownership of an existing private native-profile directory.

Native requests share the directory lock; maintenance and future private-input
writers take it exclusively. No lock files, initialization or repairs occur.
This serializes cooperating callers, not arbitrary same-account/root edits.
"""

from __future__ import annotations

import os
import stat
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class BrowserProfileAccessError(RuntimeError):
    def __init__(self) -> None:
        super().__init__(
            "Private browser profile is busy, changed or unsafe; nothing was repaired.")


@contextmanager
def browser_profile_access(root: Path, *, exclusive: bool) -> Iterator[tuple[int, int]]:
    """Nonblocking OS lock, pinned to the existing canonical directory inode.

    Always acquire this before a SQLite transaction. Future input writers must
    also stop/fence affected state and durably handle partial replacement; the
    lock alone does NOT implement credential replacement or permission to resume.
    Never rename/unlink the locked profile or accept paths from browser messages.
    """
    descriptor = None
    try:
        if (sys.platform != "linux" or type(exclusive) is not bool
                or not isinstance(root, Path) or not root.is_absolute() or root.resolve() != root):
            raise ValueError()
        import fcntl

        descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        opened = os.fstat(descriptor)
        if opened.st_uid != os.geteuid() or stat.S_IMODE(opened.st_mode) != 0o700:
            raise ValueError()
        fcntl.flock(descriptor, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        identity = (opened.st_dev, opened.st_ino)

        def check() -> None:
            named = root.lstat()
            if (root.resolve() != root or not stat.S_ISDIR(named.st_mode)
                    or named.st_uid != os.geteuid() or stat.S_IMODE(named.st_mode) != 0o700
                    or (named.st_dev, named.st_ino) != identity):
                raise ValueError()

        check()
        yield identity
        check()
    except Exception:
        raise BrowserProfileAccessError() from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
