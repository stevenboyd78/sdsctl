"""Target proc topology: pure fixtures plus actual self/owned-child proc reads.

These do not authenticate the outer launcher, installed images or App recovery.
The joined fixture uses synthetic Engine and cgroup metadata, not real Docker.
"""

import builtins
import io
import os
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_helper_qualification as qualification

m, helper, denied = qualification.m, qualification.helper, qualification.denied
image, configured = qualification.image, qualification.configured
layout, image_umask, supervised = (
    qualification.layout,
    qualification.image_umask,
    qualification.supervised,
)
BASE = b"29 33 0:26 / /proc rw,nosuid,nodev,noexec,relatime shared:12 - proc proc rw\n"
SYS = b"40 29 0:26 /sys /proc/sys ro,nosuid,nodev,noexec,relatime - proc proc rw\n"


def profile(raw=BASE, **kwargs):
    return m.helper_proc_mount_profile(raw, **(dict(mount_id=29, device=(0, 26)) | kwargs))


def rejected(callback):
    with pytest.raises(m.UnconfirmedHostLaunch) as caught:
        callback()
    assert str(caught.value) == m.MESSAGE and caught.value.__suppress_context__


def test_proc_root_and_optional_readonly_same_proc_sys_mount_are_distinct_profiles():
    assert len(profile()) == 64 and profile() == profile(BASE)
    assert profile(BASE + SYS) != profile()


@pytest.mark.parametrize(
    "suffix",
    [
        b"40 29 0:34 / /proc/sys/fs/binfmt_misc rw - autofs systemd-1 rw\n",
        b"40 29 0:4 /null /proc/kcore rw - tmpfs tmpfs rw\n",
        b"40 29 0:4 / /proc/acpi ro - tmpfs tmpfs ro\n",
        b"40 33 0:4 mnt:[123] /run/unrelated rw - nsfs nsfs rw\n",
        b"40 33 0:4 / /unrelated\\040path rw - tmpfs tmpfs rw\n",
        b"40 33 0:4 / /unrelated\\134path rw - tmpfs tmpfs rw\n",
        b"40 33 0:4 / /unrelated\\011path rw - tmpfs tmpfs rw\n",
        b"40 33 0:4 / /unrelated\\012path rw - tmpfs tmpfs rw\n",
    ],
)
def test_unrelated_masks_and_escaped_mount_paths_do_not_shadow_fixed_reads(suffix):
    assert len(profile(BASE + suffix)) == 64


@pytest.mark.parametrize(
    "target",
    [
        "/proc/123",
        "/proc/123/stat",
        "/proc/123/ns/time",
        "/proc/0",
        "/proc/self",
        "/proc/self/mountinfo",
        "/proc/thread-self/fdinfo",
        "/proc/sys/kernel",
        "/proc/sys/kernel/random",
        "/proc/sys/kernel/random/boot_id",
        "/proc/sys/kernel/random/boot_id/child",
    ],
)
def test_process_self_or_boot_clock_overmounts_refuse(target):
    raw = BASE + f"40 29 0:4 / {target} rw - tmpfs tmpfs rw\n".encode("ascii")
    rejected(lambda: profile(raw))


@pytest.mark.parametrize(
    "before,after",
    [
        (b"/ /proc", b"/other /proc"),
        (b" - proc ", b" - tmpfs "),
        (b"0:26", b"0:27"),
        (b"29 33", b"28 33"),
        (b"nodev,", b""),
        (b"nosuid,", b""),
        (b"noexec,", b""),
        (b"rw,", b"ro,rw,"),
        (b"rw,", b""),
        (b"/proc", b"/proc/"),
        (b"/proc", b"//proc"),
        (b"/proc", b"/proc/../proc"),
        (b"/proc", b"/proc\\057other"),
        (b"29 33", b"29  33"),
        (b"29 33", b"29\t33"),
    ],
)
def test_root_mount_geometry_device_flags_and_structure_must_match_actual_fd(before, after):
    rejected(lambda: profile(BASE.replace(before, after)))


@pytest.mark.parametrize(
    "before,after",
    [
        (b"/sys /proc/sys", b"/ /proc/sys"),
        (b"0:26", b"0:27"),
        (b"ro,", b"rw,"),
        (b"ro,", b"ro,rw,"),
        (b" - proc ", b" - tmpfs "),
    ],
)
def test_sys_readonly_remount_must_preserve_original_proc_superblock(before, after):
    rejected(lambda: profile(BASE + SYS.replace(before, after)))


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"PRIVATE\n",
        BASE[:-1],
        BASE + b"\n",
        BASE + BASE,
        BASE + BASE.replace(b"29 33", b"30 33"),
        BASE + SYS + SYS,
        b"x" * (1024 * 1024 + 1),
        BASE.replace(b"proc rw", b"proc\x00 rw"),
        BASE.replace(b"proc rw", b"proc\r rw"),
        BASE.replace(b"proc rw", b"proc\xff rw"),
    ],
)
def test_truncated_oversized_duplicate_and_noncanonical_tables_refuse(raw):
    rejected(lambda: profile(raw))


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(mount_id=0),
        dict(mount_id=True),
        dict(mount_id=28),
        dict(device=[0, 26]),
        dict(device=(False, 26)),
        dict(device=(-1, 26)),
        dict(device=(0, 27)),
    ],
)
def test_open_descriptor_facts_are_exact_not_coerced(kwargs):
    rejected(lambda: profile(**kwargs))


def test_actual_self_proc_directory_is_bound_to_kernel_mountinfo_and_fdinfo():
    fd = os.open("/proc", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        raw = Path("/proc/self/mountinfo").read_bytes()
        fdinfo = Path(f"/proc/self/fdinfo/{fd}").read_text()
        mount_id = int(re.search(r"^mnt_id:\s*([0-9]+)$", fdinfo, flags=re.MULTILINE)[1])
        result = profile(
            raw, mount_id=mount_id, device=(os.major(info.st_dev), os.minor(info.st_dev))
        )
        assert len(result) == 64
    finally:
        os.close(fd)


def proc_closed(helper):
    assert helper.proc_fds
    for fd in helper.proc_fds:
        with pytest.raises(OSError):
            os.fstat(fd)
    assert helper.witness.fd >= 0 and not helper.witness.exited()


def test_original_target_proc_descriptor_is_retained_during_hashing_then_closed(
    helper, monkeypatch
):
    actual = m.runtime.Layout.verify_supervised

    def inspect(self, *args, **kwargs):
        assert len(helper.proc_fds) == 1 and helper.proc_reads >= 2
        os.fstat(helper.proc_fds[0])
        return actual(self, *args, **kwargs)

    monkeypatch.setattr(m.runtime.Layout, "verify_supervised", inspect)
    assert helper.obj() is None
    assert helper.proc_reads == 4
    proc_closed(helper)


@pytest.mark.parametrize(
    "fault", ["table", "identity", "mount_id", "inode", "self", "pid", "cgroup", "user"]
)
def test_proc_view_drift_after_runtime_refuses_and_closes_original_handle(
    helper, monkeypatch, fault
):
    actual = m.runtime.Layout.verify_supervised
    read_file, stat, readlink = builtins.open, os.stat, os.readlink

    def changed(self, *args, **kwargs):
        result = actual(self, *args, **kwargs)
        if fault == "table":
            raw = Path(f"/proc/{helper.child.pid}/mountinfo").read_bytes()
            helper.proc_mount_fault = raw + b"2147483647 1 0:4 / /other rw - tmpfs tmpfs rw\n"
        elif fault == "identity":
            helper.proc_cgroup_fault = b"0::/wrong\n"
        elif fault in ("mount_id", "inode"):

            def read(path, *args, **kwargs):
                if str(path).startswith(f"/proc/{os.getpid()}/fdinfo/"):
                    with read_file(path, *args, **kwargs) as stream:
                        raw = stream.read()
                    field = b"mnt_id" if fault == "mount_id" else b"ino"
                    return io.BytesIO(re.sub(field + rb":\s*[0-9]+", field + b":\t0", raw))
                return read_file(path, *args, **kwargs)

            monkeypatch.setattr(builtins, "open", read)
        elif fault == "self":
            monkeypatch.setattr(
                m.os,
                "readlink",
                lambda path, *a, **k: (
                    "0" if str(path).endswith("/proc/self") else readlink(path, *a, **k)
                ),
            )
        else:

            def different(path, *args, **kwargs):
                info = stat(path, *args, **kwargs)
                if path == f"/proc/{helper.child.pid}/ns/{fault}":
                    return SimpleNamespace(st_dev=info.st_dev, st_ino=info.st_ino + 1)
                return info

            monkeypatch.setattr(m.os, "stat", different)
        return result

    monkeypatch.setattr(m.runtime.Layout, "verify_supervised", changed)
    denied(helper.obj)
    proc_closed(helper)


@pytest.mark.parametrize("error", [KeyboardInterrupt(), SystemExit(75), OSError("PRIVATE")])
def test_interrupted_original_proc_check_releases_owned_fd_not_pidfd(helper, monkeypatch, error):
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(m.runtime.Layout, "verify_supervised", fail)
    if isinstance(error, Exception):
        denied(helper.obj)
    else:
        with pytest.raises(type(error)):
            helper.obj()
    proc_closed(helper)


def test_partial_proc_open_failure_keeps_original_root_cleanup_and_pidfd(helper, monkeypatch):
    actual = m.os.open

    def fail(path, *args, **kwargs):
        if path == f"/proc/{helper.child.pid}/root/proc":
            raise OSError("PRIVATE")
        return actual(path, *args, **kwargs)

    monkeypatch.setattr(m.os, "open", fail)
    denied(helper.obj)
    assert not helper.proc_fds
    qualification.roots_closed(helper)


def test_proc_close_failure_still_releases_outer_roots_and_keeps_borrowed_pidfd(
    helper, monkeypatch
):
    actual = m.os.close

    def fail(fd):
        actual(fd)
        if fd in helper.proc_fds:
            raise OSError("PRIVATE close acknowledgement")

    monkeypatch.setattr(m.os, "close", fail)
    denied(helper.obj)
    proc_closed(helper)
    qualification.roots_closed(helper)
