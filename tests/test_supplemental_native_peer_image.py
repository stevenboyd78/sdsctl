"""Sealed ELF -> original-owner native ingress, OFFLINE process fixtures only.

The expected digest comes from this fixture's earlier reviewed local build step,
not from the launch-time source being verified. That is NOT independently trusted
installed publication, compiler/libc/runtime provenance or a production selector.
No host policy change, cgroup operation, service, App or scanner access is used.
"""

import errno
import fcntl
import hashlib
import os
import signal
import stat
import time
from contextlib import contextmanager

import pytest

from . import test_supplemental_native_peer_direct as direct

base = direct.base
layout, image_umask, supervised = direct.layout, direct.image_umask, direct.supervised
image, configured, pair = direct.image, direct.configured, direct.pair
helper, inputs, custody = direct.helper, direct.inputs, direct.custody
short_budget, binary, pytestmark = direct.short_budget, direct.binary, direct.pytestmark
direct_launcher = direct.direct_launcher
# Linux UAPI bits, including on Python builds with older userspace headers.
# Unsupported/restricted kernels must refuse; no executable-policy workaround.
MFD_EXEC, F_SEAL_EXEC = 0x0010, 0x0020
F_ADD_SEALS, F_GET_SEALS = 1033, 1034
SEALS = 0x0001 | 0x0002 | 0x0004 | 0x0008 | F_SEAL_EXEC  # SEAL, SHRINK, GROW, WRITE, EXEC
MAX_IMAGE = 8 * 1024 * 1024


def timely(cutoff):
    assert type(cutoff) is int and time.clock_gettime_ns(time.CLOCK_BOOTTIME) < cutoff


@contextmanager
def sealed_image(path, expected, cutoff):
    """Copy/check/seal/recheck within the original caller's readiness cutoff.

    This is fixture code, not an admitted runtime verifier. Its descriptor is
    caller-owned and retained across exec/adoption; there is no pathname fallback.
    The caller still owns exact-child cleanup after any post-spawn failure.
    """
    source = image_fd = None
    try:
        timely(cutoff)
        assert type(expected) is str and len(expected) == 64
        assert all(value in "0123456789abcdef" for value in expected)
        source = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
        initial = os.fstat(source)
        assert stat.S_ISREG(initial.st_mode) and 4 <= initial.st_size <= MAX_IMAGE
        assert os.pread(source, 4, 0) == b"\x7fELF"
        timely(cutoff)
        image_fd = os.memfd_create(
            "sdsctl-offline-reviewed-image", os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING | MFD_EXEC
        )
        os.fchmod(image_fd, 0o500)
        hashed, offset = hashlib.sha256(), 0
        while offset < initial.st_size:
            timely(cutoff)
            chunk = os.pread(source, min(65536, initial.st_size - offset), offset)
            assert chunk
            hashed.update(chunk)
            written = 0
            while written < len(chunk):
                timely(cutoff)
                size = os.pwrite(image_fd, chunk[written:], offset + written)
                assert size > 0
                written += size
            offset += len(chunk)
        observed = os.fstat(source)
        fields = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")
        assert all(getattr(initial, field) == getattr(observed, field) for field in fields)
        assert not os.pread(source, 1, offset) and hashed.hexdigest() == expected
        fcntl.fcntl(image_fd, F_ADD_SEALS, SEALS)
        assert fcntl.fcntl(image_fd, F_GET_SEALS) & SEALS == SEALS
        assert os.fstat(image_fd).st_size == initial.st_size and not os.get_inheritable(image_fd)
        # Verify the SEALED bytes, not just the stream used to populate them.
        sealed, offset = hashlib.sha256(), 0
        while offset < initial.st_size:
            timely(cutoff)
            chunk = os.pread(image_fd, min(65536, initial.st_size - offset), offset)
            assert chunk
            sealed.update(chunk)
            offset += len(chunk)
        assert sealed.hexdigest() == expected
        timely(cutoff)
        yield image_fd
    finally:
        for fd in (image_fd, source):
            if fd is not None:
                os.close(fd)


def sealed_parent(spawn, path, expected, anchors, args, *, inspect_image=None, **options):
    cutoff = int(args[2])
    with sealed_image(path, expected, cutoff) as fd:
        if inspect_image is not None:
            inspect_image(fd)
        timely(cutoff)
        return direct.direct_parent(spawn, path, anchors, args, executable_fd=fd, **options)


@pytest.fixture
def reviewed_binary(binary, tmp_path):
    # A prior trusted-fixture build is the expected input. Launch observes a
    # SEPARATE disposable copy; changing it cannot silently change this pin.
    raw = binary.read_bytes()
    expected = hashlib.sha256(raw).hexdigest()
    candidate = tmp_path / "candidate-watch"
    candidate.write_bytes(raw)
    candidate.chmod(0o500)
    return candidate, expected


@pytest.mark.parametrize("pair", ["preparation"], indirect=True)
def test_sealed_exact_image_exec_keeps_custody_after_source_path_replacement(
    custody, pair, reviewed_binary, direct_launcher
):
    candidate, expected = reviewed_binary
    before = len(os.listdir("/proc/self/fd"))
    raw, cutoff, identities = custody.plan.raw, custody.deadline_ns, []

    def inspect(fd):
        identities.append((os.fstat(fd).st_dev, os.fstat(fd).st_ino))
        for mutation in (
            lambda: os.pwrite(fd, b"changed", 0),
            lambda: os.ftruncate(fd, 0),
            lambda: os.ftruncate(fd, os.fstat(fd).st_size + 1),
            lambda: os.fchmod(fd, 0o400),
        ):
            with pytest.raises(OSError) as refused:
                mutation()
            assert refused.value.errno == errno.EPERM
        candidate.rename(candidate.with_name("original-preserved"))
        candidate.write_bytes(b"NOT AN EXECUTABLE: replacement must never run")

    launched = []

    def exact_spawn(result, handles, *args):
        # Observe the actual descriptor at the unchanged C clone/exec boundary.
        # The ready native process deliberately disables dumpability, so this is
        # not independent post-exec /proc image or installed-runtime inspection.
        opened = os.fstat(handles[3])
        assert [(opened.st_dev, opened.st_ino)] == identities
        assert fcntl.fcntl(handles[3], F_GET_SEALS) & SEALS == SEALS
        assert candidate.read_bytes().startswith(b"NOT AN EXECUTABLE")
        launched.append(True)
        return direct_launcher(result, handles, *args)

    with direct.ingress_tests.ingress(
        custody,
        candidate,
        after_exec=True,
        launcher=lambda path, anchors, args, **kw: sealed_parent(
            exact_spawn, path, expected, anchors, args, inspect_image=inspect, **kw
        ),
    ) as watch:
        assert launched == [True]
        assert not base.m.deadlines._readable(watch.fd)
        assert custody.plan.raw == raw and watch.deadline_ns == cutoff
        assert all(pair.counts[role]["container"] == 4 for role in pair.children)
        assert not any(witness.exited() for witness in pair.witnesses.values())
        os.write(watch.cancel, b"X")
        assert base.native_code(watch) == 13
        base.termination.exited(pair)
        assert watch.finish().returncode == 13
    assert len(os.listdir("/proc/self/fd")) == before


@pytest.mark.parametrize("pair", ["preparation"], indirect=True)
@pytest.mark.parametrize("fault", ["digest", "bytes", "symlink", "expired", "memfd", "seal"])
def test_unconfirmed_image_never_clones_or_renews_original_custody(
    custody, pair, reviewed_binary, direct_launcher, fault, monkeypatch
):
    candidate, expected = reviewed_binary
    before = len(os.listdir("/proc/self/fd"))
    raw, cutoff, spawned = custody.plan.raw, custody.deadline_ns, []
    if fault == "digest":
        expected = "0" * 64
    elif fault == "bytes":
        candidate.chmod(0o700)
        with candidate.open("r+b") as stream:
            stream.seek(32)
            stream.write(b"changed")
        candidate.chmod(0o500)
    elif fault == "symlink":
        target = candidate.with_name("original-preserved")
        candidate.rename(target)
        candidate.symlink_to(target)

    def reject_image(*args):
        raise PermissionError(errno.EPERM, "Offline injected executable-image refusal")

    if fault == "memfd":
        monkeypatch.setattr(os, "memfd_create", reject_image)
    elif fault == "seal":
        original = fcntl.fcntl

        def reject_seal(fd, command, *args):
            if command == F_ADD_SEALS:
                reject_image()
            return original(fd, command, *args)

        monkeypatch.setattr(fcntl, "fcntl", reject_seal)

    def spawn(*args):
        spawned.append(True)
        return direct_launcher(*args)

    def launch(path, anchors, args, **kw):
        if fault == "expired":
            args[2] = "1"  # Earlier, never a renewed or replacement allowance.
        return sealed_parent(spawn, path, expected, anchors, args, **kw)

    with (
        pytest.raises((AssertionError, OSError)),
        direct.ingress_tests.ingress(custody, candidate, after_exec=True, launcher=launch),
    ):
        pytest.fail("Unconfirmed executable reached native readiness")
    assert not spawned and custody.attempted and custody.armed_watch is None
    assert custody.plan.raw == raw and custody.deadline_ns == cutoff
    base.termination.exited(pair)
    with (
        pytest.raises(AssertionError),
        direct.ingress_tests.ingress(custody, candidate, after_exec=True, launcher=launch),
    ):
        pytest.fail("Consumed custody retried image launch")
    assert not spawned and len(os.listdir("/proc/self/fd")) == before


@pytest.mark.parametrize("pair", ["preparation"], indirect=True)
def test_sealed_exec_pending_interruption_reaps_original_child_before_adoption(
    custody, pair, reviewed_binary, direct_launcher
):
    candidate, expected = reviewed_binary
    before = len(os.listdir("/proc/self/fd"))
    raw, cutoff, observed = custody.plan.raw, custody.deadline_ns, []
    original_handler = signal.getsignal(signal.SIGUSR1)

    def pending(signum, frame):
        raise KeyboardInterrupt("Sealed exec interrupted before original handle adoption")

    def launch(path, anchors, args, **kw):
        return sealed_parent(
            direct_launcher, path, expected, anchors, args, fault="pending", observed=observed, **kw
        )

    try:
        signal.signal(signal.SIGUSR1, pending)
        with (
            pytest.raises(KeyboardInterrupt),
            direct.ingress_tests.ingress(custody, candidate, after_exec=True, launcher=launch),
        ):
            pytest.fail("Interrupted sealed launch reached native readiness")
    finally:
        signal.signal(signal.SIGUSR1, original_handler)
    assert len(observed) == 1 and all(value >= 0 for value in observed[0])
    with pytest.raises(ChildProcessError):
        os.waitpid(observed[0][0], os.WNOHANG)
    assert custody.attempted and custody.armed_watch is None
    assert custody.plan.raw == raw and custody.deadline_ns == cutoff
    base.termination.exited(pair)
    with (
        pytest.raises(AssertionError),
        direct.ingress_tests.ingress(custody, candidate, after_exec=True, launcher=launch),
    ):
        pytest.fail("Consumed interrupted custody retried sealed launch")
    assert len(os.listdir("/proc/self/fd")) == before
