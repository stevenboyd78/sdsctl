#!/usr/bin/env python3
"""One-use generation-bound native input publication, NOT native execution.

Uninstalled and not selected by any service/source profile. Borrow the accepted
Startup, its original projection/clock and the original native-idle reader.
Writing a launch input grants no Engine/native/recording authority. Installed
profile/port provenance, active source selection and independent recovery remain
external obligations. No retry, cleanup of residue or replacement clock exists.
"""

from __future__ import annotations

import fcntl
import hashlib
import os
import stat
import time
from dataclasses import asdict, dataclass

import qualify_supplemental_recording_app as qualification
import supplemental_recording_launch_plan as native

publication = qualification.publication
base, files, plans = qualification.base, qualification.files, publication.plans
require = qualification.require
MAX_SECONDS = 2.0


def _service_custody(owner, plan):
    """Retained accepted service, not a new read of the expired startup offer.

    The service must have been assembled within its original acceptance window
    and still hold the Startup's borrowed clock/plan. This follows the same
    continuing-custody rule as Startup.idle_service(), without renewing consent
    or admitting a merely accepted but never assembled/retired owner.
    """
    require(type(owner) is publication.startup.Startup)
    require(owner.accepted and owner.service_used and owner._service_active)
    require(owner.lock.locked() and callable(owner._service_invalidate))
    require(owner.app_idle_publication_used and owner._service_inputs is not None)
    owner._input()
    owner._binding()
    require(owner.original.recheck() is plan)
    require(type(owner.clock) is plans.clock.ClockWitness)
    require(plans._same_plan_value(owner.clock.original, plan.original_clock))
    observed = owner.clock.read()
    plan.check_clock(observed)
    owner._input()
    owner._binding()


def _description(owner, original, specification, profile_sha256):
    """Canonical original data only; never capture a new recording baseline."""
    plan = original.plan
    require(type(specification) is native.construction.Specification)
    specification.__post_init__()
    base.digest(profile_sha256)
    plan.check_projection(owner.projected)
    require(specification.firmware == plan.firmware)
    require(specification.sockets == plan.native_root / "sockets")
    require(specification.receipts == plan.native_root / "receipts")
    stored = owner.projected.native
    require(3 + specification.read_window_seconds + 10 < stored.contract.maximum_recording_seconds)
    # Same endpoint spelling as the native transport, without constructing one
    # or looking up DNS. The original contract independently pins this target.
    authority = specification.host
    if specification.rtsp_port != 554:
        authority += ":" + str(specification.rtsp_port)
    endpoint = "rtsp://" + authority + "/au:scanner.au"
    require(hashlib.sha256(endpoint.encode()).hexdigest() == stored.contract.audio_endpoint_sha256)
    layout = owner.projected.layout
    deployment = plan.native_root.parent / layout.deployment.relative_to(layout.data)
    fields = asdict(specification)
    for key in ("sockets", "receipts"):
        fields[key] = str(fields[key])
    raw = base.encode(
        dict(
            schema=1,
            kind="finite-recording-native-launch-v1",
            specification=fields,
            baseline=dict(
                directory=str(plan.native_root / "baseline"),
                contract=asdict(stored.contract),
                sha256=plan.native_baseline_sha256,
            ),
            profile=dict(deployment=str(deployment), sha256=profile_sha256),
            generation=original.generation,
            source_sha256=plan.candidate_runtime.source,
            projection_sha256=plan.projection_sha256,
            host_plan_sha256=plan.sha256,
        )
    )
    require(0 < len(raw) <= native.MAX_BYTES)
    return raw


@dataclass(frozen=True)
class LaunchPublished:
    """Original publication receipt, not a launch permission or new baseline."""

    raw: bytes
    sha256: str
    file_identity: tuple
    directory_identity: tuple
    guardian_identity: tuple


class NativeLaunchQualification(qualification.NativeIdleQualification):
    """Fresh read-only phase after ONE acknowledged original launch-file write.

    Carry all original consumption/directory identities forward; only the
    explicitly published launch leaf, empty guardian directory and parent
    metadata may change. Output
    directories still must be empty. Existing action/PostBegin gates do not
    accept this class. It cannot create a second publication or renew readiness.
    """

    def __init__(self, owner, original, published):
        self.failed, self.elapsed_seconds = False, None
        try:
            require(type(owner) is publication.startup.Startup)
            require(type(original) is qualification.NativeIdleQualification)
            require(type(published) is LaunchPublished)
            require(original.consumption is not None and original.elapsed_seconds is not None)
            self.startup, self.candidate, self.launch_inputs = owner, original, published
            self.original_objects = (
                owner.original,
                owner.clock,
                owner.projected,
                original.plan,
                original.idle,
                original.witness,
                original.docker,
                original.published,
                original.consumption,
            )
            self.candidate_pins = original.original
            self.launch_pins = self._launch_pins()
            self._binding()
            super().__init__(
                original.plan,
                original.idle,
                original.witness,
                original.docker,
                published=original.published,
                bridge_sha256=original.bridge_sha256,
                **{
                    name: getattr(original, name)
                    for name in qualification.AppRetainedQualification.PROFILE
                },
            )
            # Retain, do not learn, the expected post-write consumption join.
            expected = tuple(
                (
                    name,
                    published.directory_identity,
                    (published.file_identity, (published.guardian_identity, None)),
                )
                if name == "launch"
                else (name, directory, leaf)
                for name, directory, leaf in original.consumption[-1]
            )
            self.consumption = self._original_consumption = (*original.consumption[:-1], expected)
        except BaseException as error:
            self._fail(error)

    def _launch_pins(self):
        p = self.launch_inputs
        require(type(p) is LaunchPublished)
        require(type(p.raw) is bytes and 0 < len(p.raw) <= native.MAX_BYTES)
        require(hashlib.sha256(p.raw).hexdigest() == p.sha256)
        for identity in (p.file_identity, p.directory_identity, p.guardian_identity):
            require(type(identity) is tuple and len(identity) == 9)
            require(all(type(value) is int for value in identity))
        return p.raw, p.sha256, p.file_identity, p.directory_identity, p.guardian_identity

    def _binding(self):
        owner, original = self.startup, self.candidate
        require(type(owner) is publication.startup.Startup)
        require(type(original) is qualification.NativeIdleQualification)
        require(not original.failed and not original.lock.locked())
        require(original.owner == (os.getpid(), qualification.launch.get_ident()))
        require(original.native_launch_used is True)
        require(original.native_launch_publication is self.launch_inputs)
        require(original._pins() == original.original == self.candidate_pins)
        require(self._launch_pins() == self.launch_pins)
        require(
            all(
                a is b
                for a, b in zip(
                    (
                        owner.original,
                        owner.clock,
                        owner.projected,
                        original.plan,
                        original.idle,
                        original.witness,
                        original.docker,
                        original.published,
                        original.consumption,
                    ),
                    self.original_objects,
                    strict=True,
                )
            )
        )
        _service_custody(owner, original.plan)
        original.plan.check_projection(owner.projected)

    def _pins(self):
        self._binding()
        return super()._pins() + self.launch_pins

    def _directory_input(self, fd, name, deadline):
        if name != "launch":
            return super()._directory_input(fd, name, deadline)
        require(sorted(os.listdir(fd)) == ["guardian", "launch.json"])
        raw, identity = qualification._file(fd, "launch.json", deadline, limit=native.MAX_BYTES)
        p = self.launch_inputs
        require(raw == p.raw and identity == p.file_identity)
        require(files.identity(os.fstat(fd)) == p.directory_identity)
        child = os.open("guardian", files.DIRECTORY, dir_fd=fd)
        try:
            before = files.identity(os.fstat(child))
            publication._secure(os.fstat(child))
            require(stat.S_IMODE(before[2]) == 0o700)
            require(before[:6] == p.guardian_identity[:6])
            leaf = self._guardian_contents(child, before, deadline)
            require(files.identity(os.fstat(child)) == before)
            require(files.identity(os.stat("guardian", dir_fd=fd, follow_symlinks=False)) == before)
            require(sorted(os.listdir(fd)) == ["guardian", "launch.json"])
            require(files.identity(os.fstat(fd)) == p.directory_identity)
            return identity, (before, leaf)
        finally:
            os.close(child)

    def _directory_identity(self, name):
        if name == "launch":
            return self.launch_inputs.directory_identity[:6]
        return super()._directory_identity(name)

    def _guardian_contents(self, fd, identity, deadline):
        self._guard(deadline)
        require(identity == self.launch_inputs.guardian_identity and not os.listdir(fd))
        return None


def publish_launch(owner, original, *, specification, profile_sha256):
    """Consume one attempt; return only a freshly verified READ-ONLY successor.

    Two complete source/runtime/process qualifications bracket the file write,
    all within this operation's original two-second/ready deadline. Actual
    native load/target/profile validation still precedes native execution.
    Partial writes/fsync/close uncertainty preserve residue and poison the
    original reader. Borrowed Startup/clock/process handles are never closed.
    """
    owned, locked, claimed, successor = [], False, False, None
    try:
        require(type(original) is qualification.NativeIdleQualification)
        require(original.native_launch_used is False)
        original.native_launch_used = True
        claimed = True
        require(original.native_launch_publication is None)
        require(type(owner) is publication.startup.Startup)
        end = min(time.monotonic() + MAX_SECONDS, original.plan.lease["ready_by"])
        objects = owner.original, owner.clock, owner.projected, original.plan

        def guard():
            require(time.monotonic() < end)
            require(original.native_launch_used is True)
            require(
                all(
                    a is b
                    for a, b in zip(
                        (owner.original, owner.clock, owner.projected, original.plan),
                        objects,
                        strict=True,
                    )
                )
            )
            _service_custody(owner, original.plan)
            original._guard(end)

        guard()
        raw = _description(owner, original, specification, profile_sha256)
        require(original() is None)
        guard()
        require(original.lock.acquire(blocking=False))
        locked = True
        root = publication._data(original.plan) / original.plan.native_root.name
        with publication._chain(root, guard) as (directory, unchanged):
            require(files.identity(os.fstat(directory)) == original.published.root_identity)
            fd = os.open("launch", files.DIRECTORY, dir_fd=directory)
            owned.append(fd)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            before = next(info for name, info, _ in original.consumption[-1] if name == "launch")
            require(files.identity(os.fstat(fd)) == before and not os.listdir(fd))
            original._inputs(end)
            unchanged()
            os.mkdir("guardian", mode=0o700, dir_fd=fd)
            child = os.open("guardian", files.DIRECTORY, dir_fd=fd)
            owned.append(child)
            publication._secure(os.fstat(child))
            guardian_identity = files.identity(os.fstat(child))
            require(stat.S_IMODE(guardian_identity[2]) == 0o700 and not os.listdir(child))
            os.fsync(child)
            output = os.open(
                "launch.json",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=fd,
            )
            owned.append(output)
            require(os.write(output, raw) == len(raw))
            os.fsync(output)
            os.fsync(fd)
            observed, identity = qualification._file(fd, "launch.json", end, limit=native.MAX_BYTES)
            require(observed == raw and identity == files.identity(os.fstat(output)))
            after = files.identity(os.fstat(fd))
            require(after[:5] == before[:5] and after[5] == before[5] + 1)
            require(sorted(os.listdir(fd)) == ["guardian", "launch.json"])
            require(files.identity(os.fstat(child)) == guardian_identity and not os.listdir(child))
            require(
                files.identity(os.stat("guardian", dir_fd=fd, follow_symlinks=False))
                == guardian_identity
            )
            require(
                files.identity(os.stat("launch", dir_fd=directory, follow_symlinks=False)) == after
            )
            unchanged()
            receipt = LaunchPublished(
                raw, hashlib.sha256(raw).hexdigest(), identity, after, guardian_identity
            )
        # Close each owned fd ONCE, before claiming a successful publication.
        closing, owned = list(reversed(owned)), []
        publication._close(closing)
        original.native_launch_publication = receipt
        original.lock.release()
        locked = False
        successor = NativeLaunchQualification(owner, original, receipt)
        require(successor() is None)
        guard()
        return successor
    except BaseException as error:
        if claimed:
            original.failed, original.elapsed_seconds = True, None
        if successor is not None:
            successor.failed, successor.elapsed_seconds = True, None
        if not isinstance(error, Exception):
            raise
        raise qualification.launch.UnconfirmedHostLaunch(qualification.launch.MESSAGE) from None
    finally:
        if locked:
            original.lock.release()
        try:
            publication._close(list(reversed(owned)))
        except BaseException as error:
            if claimed:
                original.failed, original.elapsed_seconds = True, None
            if successor is not None:
                successor.failed, successor.elapsed_seconds = True, None
            if not isinstance(error, Exception):
                raise
            raise qualification.launch.UnconfirmedHostLaunch(qualification.launch.MESSAGE) from None
