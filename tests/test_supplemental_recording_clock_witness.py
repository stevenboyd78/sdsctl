"""Real self-only time namespace custody; faults never enter or change namespaces."""

import os
import time
from dataclasses import replace
from threading import Thread

import pytest

from . import test_supplemental_recording_clock as clock_tests

m, denied = clock_tests.m, clock_tests.denied


@pytest.fixture
def witness():
    obj = m.ClockWitness(m.read())
    try:
        yield obj
    finally:
        if not obj.closed:
            obj.close()


def test_actual_original_namespace_stays_open_across_fresh_reads(witness):
    original, fd = witness.original, witness.fd
    deadline = original.native_deadline(original.boottime_ns / m.NS + 10)
    first, second = witness.read(), witness.read()
    original.check_later(first)
    first.check_later(second)
    assert witness.original is original
    assert original.native_deadline(original.boottime_ns / m.NS + 10) == deadline
    assert (os.fstat(fd).st_dev, os.fstat(fd).st_ino) == original.namespace
    witness.close()
    witness.close()
    with pytest.raises(OSError):
        os.fstat(fd)
    denied(witness.read)


@pytest.mark.parametrize("attribute", ["fd", "path", "children_path", "namespace", "original"])
def test_replaced_custody_is_sticky_and_original_owned_descriptor_is_closed(witness, attribute):
    fd = witness.fd
    replacement_fd = os.open("/dev/null", os.O_RDONLY | os.O_CLOEXEC)
    values = dict(
        fd=replacement_fd,
        path="/proc/1/ns/time",
        children_path="/proc/1/ns/time_for_children",
        namespace=(0, 1),
        original=replace(witness.original, boot="a" * 32),
    )
    try:
        setattr(witness, attribute, values[attribute])
        denied(witness.read)
        assert witness.failed
        witness.close()
        os.fstat(replacement_fd)
        with pytest.raises(OSError):
            os.fstat(fd)
    finally:
        os.close(replacement_fd)


def test_reused_lost_descriptor_is_not_closed_as_if_still_owned(witness):
    fd = witness.fd
    os.close(fd)
    replacement = os.open("/dev/null", os.O_RDONLY | os.O_CLOEXEC)
    try:
        if replacement != fd:
            os.dup2(replacement, fd)
        denied(witness.read)
        denied(witness.close)
        os.fstat(fd)
        assert witness.closed
    finally:
        os.close(fd)
        if replacement != fd:
            os.close(replacement)


@pytest.mark.parametrize("which", ["path", "children_path"])
def test_changed_current_or_child_namespace_refuses_without_namespace_entry(
    witness, monkeypatch, which
):
    actual = os.stat
    selected = getattr(witness, which)

    def changed(path, *args, **kwargs):
        return actual("/proc/self/ns/net" if path == selected else path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(m.os, "stat", changed)
        denied(witness.read)
    assert witness.failed
    denied(witness.read)


@pytest.mark.parametrize("fault", ["boot", "namespace", "reverse", "suspend"])
def test_clock_change_does_not_refresh_original_binding(witness, monkeypatch, fault):
    original = witness.original
    observed = m.read()
    if fault == "boot":
        observed = replace(observed, boot="b" * 32)
    elif fault == "namespace":
        observed = replace(observed, namespace=(0, 2))
    elif fault == "reverse":
        observed = original
    else:
        observed = replace(observed, boottime_ns=observed.boottime_ns + 5 * m.NS)
    monkeypatch.setattr(m, "read", lambda: observed)
    denied(witness.read)
    assert witness.original is original and witness.failed


def test_read_uses_one_unrenewed_budget(witness, monkeypatch):
    actual_read, actual_monotonic = m.read, time.monotonic

    def late():
        result = actual_read()
        monkeypatch.setattr(m.time, "monotonic", lambda: actual_monotonic() + 2)
        return result

    monkeypatch.setattr(m, "read", late)
    denied(witness.read)


@pytest.mark.parametrize("method", ["read", "close"])
def test_wrong_thread_cannot_borrow_or_close_original_handle(witness, method):
    failures = []

    def other():
        try:
            getattr(witness, method)()
        except m.UnconfirmedClock:
            failures.append(True)

    thread = Thread(target=other)
    thread.start()
    thread.join(timeout=2)
    assert not thread.is_alive() and failures == [True]
    assert not witness.closed
    os.fstat(witness.fd)


def test_busy_witness_cannot_be_entered_again(witness):
    assert witness.lock.acquire(blocking=False)
    try:
        denied(witness.read)
    finally:
        witness.lock.release()
    assert witness.failed


@pytest.mark.parametrize("error", [KeyboardInterrupt(), SystemExit(75), OSError("PRIVATE")])
def test_interrupted_read_keeps_descriptor_until_owner_cleanup(witness, monkeypatch, error):
    def fail():
        raise error

    monkeypatch.setattr(m, "read", fail)
    if isinstance(error, Exception):
        denied(witness.read)
    else:
        with pytest.raises(type(error)):
            witness.read()
    assert witness.failed and not witness.closed and not witness.lock.locked()
    os.fstat(witness.fd)


def test_constructor_failure_after_open_closes_actual_original_descriptor(monkeypatch):
    original = m.read()
    actual_open, opened = os.open, []

    def open_file(*args, **kwargs):
        fd = actual_open(*args, **kwargs)
        opened.append(fd)
        return fd

    def fail():
        raise OSError("PRIVATE")

    monkeypatch.setattr(m.os, "open", open_file)
    monkeypatch.setattr(m, "read", fail)
    denied(lambda: m.ClockWitness(original))
    assert len(opened) == 1
    with pytest.raises(OSError):
        os.fstat(opened[0])
