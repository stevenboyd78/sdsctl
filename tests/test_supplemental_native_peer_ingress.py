"""Real original-FD socket transfer -> native watcher; OFFLINE fixtures only.

The inherited standard-I/O anchors model, but do not invoke or qualify, a host
service launcher. Runtime/cgroup/source provenance remains synthetic. No App,
scanner, host service, Engine or installed platform calls are made here.
"""

import array
import fcntl
import os
import select
import signal
import socket
import time
from contextlib import contextmanager

import pytest

from . import test_supplemental_native_peer_watch as base

layout, image_umask, supervised = base.layout, base.image_umask, base.supervised
image, configured, pair = base.image, base.configured, base.pair
helper, inputs, custody = base.helper, base.inputs, base.custody
short_budget, binary, pytestmark = base.short_budget, base.binary, base.pytestmark
m = base.m
MODE = "--offline-original-peer-ingress-v1"
PACKET = b"original-peer-handles-v1"


def spawn_standard(binary, anchors, args, *, extra=()):
    """Only fd0/1/2 are ingress anchors; fd9 is the opened executable.

    No peer handles are inherited. Fixed target slots intentionally differ from
    the recvmsg-allocated numbers. This is NOT a qualified systemd launch.
    """
    executable = os.open(binary, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    copies = []
    try:
        for fd in (*anchors, executable, *extra):
            copies.append(fcntl.fcntl(fd, fcntl.F_DUPFD_CLOEXEC, 20))
        slots = [0, 1, 2, 9, *range(10, 10 + len(extra))]
        return os.posix_spawn(
            "/proc/self/fd/9",
            [str(binary), *args],
            {},
            file_actions=[
                (os.POSIX_SPAWN_DUP2, fd, slot) for fd, slot in zip(copies, slots, strict=True)
            ],
            setsigmask=(),
            setsigdef=(signal.SIGCHLD,),
        )
    finally:
        os.close(executable)
        for fd in copies:
            os.close(fd)


@contextmanager
def ingress(custody, binary, *, fault=None, after_exec=False, extra=()):
    channels = []

    def spawner(binary, handles, args, *, extra):
        sender, receiver = socket.socketpair(
            socket.AF_UNIX,
            (socket.SOCK_STREAM if fault == "stream_channel" else socket.SOCK_SEQPACKET)
            | socket.SOCK_NONBLOCK
            | socket.SOCK_CLOEXEC,
        )
        channels.extend((sender, receiver))
        receiver.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        args = [MODE, *args[1:]]
        anchors = [receiver.fileno(), handles[2], handles[5]]
        rights = list(handles)
        payload = PACKET
        if fault == "wrong_outer_anchor":
            anchors[1] = handles[0]
        elif fault == "wrong_namespace_anchor":
            anchors[2] = handles[0]
        elif fault == "substituted_outer":
            rights[2] = handles[0]
        elif fault == "substituted_namespace":
            rights[5] = handles[0]
        elif fault == "missing_right":
            rights.pop()
        elif fault == "extra_rights":
            rights.extend([handles[0]] * 20)
        elif fault == "no_rights":
            rights.clear()
        elif fault == "wrong_payload":
            payload = b"replacement-peer-handles"
        elif fault == "truncated_payload":
            payload = PACKET * 100
        elif fault == "zero_packet":
            payload = b""
        elif fault == "no_passcred":
            receiver.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 0)
        elif fault == "blocking_channel":
            receiver.setblocking(True)
        elif fault == "expired":
            args[2] = "1"
        elif fault == "renewed":
            args[2] = str(time.clock_gettime_ns(time.CLOCK_BOOTTIME) + 3 * 10**9)
        elif fault == "boot":
            args[3] = "0" * 32
        elif fault == "alias_peer":
            rights[1] = handles[0]
        elif fault == "bad_cancel":
            rights[3] = handles[0]

        def send():
            ancillary = [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", rights))]
            assert sender.sendmsg([payload], ancillary if rights else []) == len(payload)

        def send_and_close():
            if fault in {"no_message", "late_no_message"}:
                return
            if fault == "inherited_sender":
                # Socket SO_PEERCRED is the original parent's, but the actual
                # packet SCM_CREDENTIALS belongs to this disposable fork child.
                pid = os.fork()
                if pid == 0:
                    try:
                        send()
                        os._exit(0)
                    except BaseException:
                        os._exit(1)
                assert os.waitpid(pid, 0) == (pid, 0)
            else:
                send()
            if fault in {"second_packet", "second_rights", "second_empty"}:
                if fault == "second_rights":
                    send()
                else:
                    assert sender.send(b"" if fault == "second_empty" else b"X") >= 0
            if fault != "no_eof":
                sender.shutdown(socket.SHUT_WR)

        if not after_exec:
            send_and_close()
        if fault == "late_no_message":
            # Spend part of the ORIGINAL capture window before exec. Native
            # ingress must not receive a fresh two seconds after launch.
            time.sleep(m.CAPTURE_SECONDS * 0.6)
        if fault in {"writer_dies", "observer_dies"}:
            target = handles[0 if fault == "writer_dies" else 1]
            signal.pidfd_send_signal(target, signal.SIGKILL)
            assert select.select([target], [], [], 1)[0]
        pid = spawn_standard(binary, anchors, args, extra=extra)
        # Keep no receiver copy in the sender: rejected queued SCM_RIGHTS must
        # retire with the native process, not keep its readiness pipe alive.
        receiver.close()
        if after_exec:
            send_and_close()
        return pid

    try:
        with base.native(
            custody,
            binary,
            fault=None if fault is None else lambda *_: None,
            spawner=spawner,
            extra=extra,
        ) as watch:
            yield watch
    finally:
        for channel in channels:
            channel.close()


@pytest.mark.parametrize("pair", [False, "preparation"], indirect=True)
@pytest.mark.parametrize("after_exec", [False, True])
def test_original_transfer_recollection_cancel_and_no_fd_leak(custody, pair, binary, after_exec):
    before = len(os.listdir("/proc/self/fd"))
    raw, deadline = pair.plan.raw, custody.deadline_ns
    with ingress(custody, binary, after_exec=after_exec) as watch:
        assert all(pair.counts[role]["container"] == 4 for role in pair.children)
        assert not any(w.exited() for w in pair.witnesses.values())
        assert pair.plan.raw == raw and watch.deadline_ns == deadline
        os.write(watch.cancel, b"X")
        assert base.native_code(watch) == 13
        base.termination.exited(pair)  # Native exit observed before caller cleanup.
        assert watch.finish().returncode == 13
    assert len(os.listdir("/proc/self/fd")) == before


@pytest.mark.parametrize(
    "fault",
    [
        "wrong_outer_anchor",
        "wrong_namespace_anchor",
        "substituted_outer",
        "substituted_namespace",
        "missing_right",
        "extra_rights",
        "no_rights",
        "wrong_payload",
        "truncated_payload",
        "zero_packet",
        "no_passcred",
        "blocking_channel",
        "stream_channel",
        "expired",
        "renewed",
        "boot",
        "alias_peer",
        "bad_cancel",
        "writer_dies",
        "observer_dies",
        "inherited_sender",
        "second_packet",
        "second_rights",
        "second_empty",
    ],
)
def test_rejected_transfer_no_ready_no_untrusted_signals_no_retry(custody, pair, binary, fault):
    before = len(os.listdir("/proc/self/fd"))
    with ingress(custody, binary, fault=fault) as watch:
        committed = fault in {"bad_cancel", "writer_dies", "observer_dies"}
        assert base.native_code(watch) == (70 if committed else 64)
        if committed:
            base.termination.exited(pair)
        else:
            # No target authority before authenticated commit; ORIGINAL outer
            # independently retires its own peers after any incomplete transfer.
            assert not any(w.exited() for w in pair.witnesses.values())
        base.termination.refused(watch.finish)
        base.termination.exited(pair)
        with pytest.raises(AssertionError), ingress(custody, binary):
            pytest.fail("one-attempt case reopened")
    assert len(os.listdir("/proc/self/fd")) == before


@pytest.mark.parametrize("fault", ["no_message", "no_eof", "late_no_message"])
def test_ingress_wait_ends_at_original_ready_cutoff(custody, pair, binary, fault):
    start = time.monotonic()
    with ingress(custody, binary, fault=fault) as watch:
        assert base.native_code(watch) == 64
        assert time.monotonic() - start < m.CAPTURE_SECONDS + 0.5
        assert not any(w.exited() for w in pair.witnesses.values())
        base.termination.refused(watch.finish)
        base.termination.exited(pair)


@pytest.mark.parametrize("role", ["writer", "observer"])
def test_transferred_handles_survive_sender_close_and_peer_loss(custody, pair, binary, role):
    with ingress(custody, binary) as watch:
        custody.close()
        for witness in pair.witnesses.values():
            witness.close()
        pair.children[role].stdin.close()
        assert pair.children[role].wait(timeout=2) == 0
        other = next(child for name, child in pair.children.items() if name != role)
        assert other.wait(timeout=2) == -signal.SIGKILL
        assert base.native_code(watch) == 11
        assert watch.finish().returncode == 11


@pytest.mark.parametrize("short_budget", [True], indirect=True)
def test_transferred_watch_keeps_private_original_timer_with_both_peers_stopped(
    custody, pair, binary
):
    with ingress(custody, binary) as watch:
        for witness in pair.witnesses.values():
            signal.pidfd_send_signal(witness.fd, signal.SIGSTOP)
        os.timerfd_settime_ns(custody.timer, initial=0)
        cutoff = custody.deadline_ns
        custody.close()
        time.sleep(max(0, cutoff / 1e9 - time.clock_gettime(time.CLOCK_BOOTTIME)) + 0.05)
        assert base.native_code(watch) == 10
        base.termination.exited(pair)
        assert watch.finish().returncode == 10


def test_ingress_closes_accidentally_inherited_other_writer(custody, pair, binary):
    read, write = os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        with ingress(custody, binary, extra=(write,)) as watch:
            os.close(write)
            write = -1
            assert select.select([read], [], [], 1)[0] and os.read(read, 1) == b""
            os.write(watch.cancel, b"X")
            assert base.native_code(watch) == 13
            base.termination.exited(pair)
            assert watch.finish().returncode == 13
    finally:
        os.close(read)
        if write >= 0:
            os.close(write)
