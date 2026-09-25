#!/usr/bin/env python3
"""Uninstalled read-only fixed App-command/actual idle-process qualification.

This explicit subclass is NOT admitted by existing exact-type launch/service
gates. All original source/runtime/environment/process/clock checks and the
whole two-second limit remain. Installed image staging, host input provenance,
active-phase wiring and independent recovery are separate obligations.
"""

from __future__ import annotations

import hashlib
import os
import stat

import supplemental_recording_app_publish as publication
import supplemental_recording_host_launch as launch

files, base = publication.files, launch.base
BRIDGE = "/usr/local/libexec/sdsctl-recording-app-idle.py"
require = launch.require


def argv(plan):
    return ("/usr/local/bin/python", "-I", "-B", BRIDGE, "--case", plan.case)


def _file(directory, name, end, *, mode=0o600, limit=4096):
    before = os.stat(name, dir_fd=directory, follow_symlinks=False)
    require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1)
    require((before.st_uid, before.st_gid) == (publication.ROOT_UID, publication.ROOT_GID))
    require(stat.S_IMODE(before.st_mode) == mode and 0 < before.st_size <= limit)
    raw = publication.startup.publication.protected.evidence.read_bytes(
        directory, name, limit=limit, deadline=end
    )
    require(
        files.identity(os.stat(name, dir_fd=directory, follow_symlinks=False))
        == files.identity(before)
    )
    return raw, files.identity(before)


class _AppInputs:
    """Shared read-only App input policy; no standalone phase authority."""

    PUBLICATION = publication.Published
    INPUT_FILES = ("idle/lease.json", "app-start/launch.json")

    def _publication(self, plan, published, bridge_sha256):
        require(type(published) is self.PUBLICATION)
        require(published.plan_sha256 == plan.sha256)
        require(published.lease_sha256 == plan.lease_sha256)
        for pin in (published.receipt_sha256, bridge_sha256):
            base.digest(pin)
        require(type(published.root_identity) is tuple and len(published.root_identity) == 9)
        require(all(type(value) is int for value in published.root_identity))
        require(
            type(published.file_identities) is tuple
            and len(published.file_identities) == len(self.INPUT_FILES)
        )
        require(tuple(name for name, _ in published.file_identities) == self.INPUT_FILES)
        for _, identity in published.file_identities:
            require(type(identity) is tuple and len(identity) == 9)
            require(all(type(value) is int for value in identity))
        self.published, self.bridge_sha256 = published, bridge_sha256
        self.consumption = self._original_consumption = None

    def _app_pins(self):
        p = self.published
        require(type(p) is self.PUBLICATION)
        require(self.consumption is self._original_consumption)
        return (
            p.plan_sha256,
            p.lease_sha256,
            p.receipt_sha256,
            p.root_identity,
            p.file_identities,
            self.bridge_sha256,
        )

    def _pins(self):
        return super()._pins() + self._app_pins()

    def _root_entries(self):
        return ("app-start", "idle")

    def _additional_inputs(self, directory, deadline):
        return None

    def _startup_argv(self):
        return argv(self.plan)

    def _image_startup(self, config):
        require(config.get("Entrypoint") == [self._startup_argv()[0]])
        require(config.get("Cmd") == list(self._startup_argv()[1:]))

    def _inputs(self, deadline):
        plan, p = self.plan, self.published
        expected_files = dict(p.file_identities)
        root = publication._data(plan) / plan.native_root.name

        def guard():
            self._guard(deadline)

        owned = []
        try:
            with publication._chain(root, guard) as (directory, unchanged):
                require(files.identity(os.fstat(directory)) == p.root_identity)
                require(tuple(sorted(os.listdir(directory))) == self._root_entries())
                observed = []
                for name, filename, expected in (
                    ("idle", "lease.json", base.encode(plan.lease)),
                    (
                        "app-start",
                        "launch.json",
                        base.encode(
                            dict(
                                schema=1,
                                kind="finite-recording-app-idle-launch-v1",
                                case=plan.case,
                                plan_sha256=plan.sha256,
                                lease_sha256=plan.lease_sha256,
                            )
                        ),
                    ),
                ):
                    fd = os.open(name, files.DIRECTORY, dir_fd=directory)
                    owned.append(fd)
                    info = files.identity(os.fstat(fd))
                    publication._secure(os.fstat(fd))
                    require(stat.S_IMODE(info[2]) == 0o700)
                    require(sorted(os.listdir(fd)) == sorted([filename, "consumed.json"]))
                    raw, identity = _file(fd, filename, deadline)
                    require(raw == expected and identity == expected_files[name + "/" + filename])
                    if name == "app-start":
                        require(hashlib.sha256(raw).hexdigest() == p.receipt_sha256)
                        consumed, consumed_identity = _file(fd, "consumed.json", deadline)
                        require(
                            consumed
                            == base.encode(
                                dict(
                                    schema=1,
                                    kind="finite-recording-app-idle-consumed-v1",
                                    case=plan.case,
                                    plan_sha256=plan.sha256,
                                    lease_sha256=plan.lease_sha256,
                                    receipt_sha256=p.receipt_sha256,
                                )
                            )
                        )
                        observed.append(consumed_identity)
                    unchanged()
                    require(files.identity(os.fstat(fd)) == info)
                    require(
                        files.identity(os.stat(name, dir_fd=directory, follow_symlinks=False))
                        == info
                    )
                    observed.append(info)
                additional = self._additional_inputs(directory, deadline)
                if additional is not None:
                    observed.append(additional)
                require(files.identity(os.fstat(directory)) == p.root_identity)
                result = tuple(observed)
            if self.consumption is None:
                require(self._original_consumption is None)
                self.consumption = self._original_consumption = result
            require(self.consumption == result)
            return base.checksum(dict(publication=self._app_pins(), consumption=result))
        finally:
            publication._close(list(reversed(owned)))

    def _metadata(self, deadline):
        merged, stamp, configured = super()._metadata(deadline)
        consumed = self._inputs(deadline)
        with publication._chain(merged / "usr/local/libexec", lambda: self._guard(deadline)) as (
            fd,
            unchanged,
        ):
            raw, _ = _file(fd, "sdsctl-recording-app-idle.py", deadline, mode=0o444, limit=65536)
            require(hashlib.sha256(raw).hexdigest() == self.bridge_sha256)
            unchanged()
        self._guard(deadline)
        return merged, base.checksum(dict(metadata=stamp, app_inputs=consumed)), configured


class AppCandidateQualification(_AppInputs, launch.CandidateQualification):
    """Borrow original publication pins; do not learn them from App inputs.

    Docker Config and image defaults must both name this exact case's bridge.
    Existing Idle.read independently checks the actual post-exec PID1 against
    plan.idle_argv. Neither configured argv is substituted for that process read.
    First observed consumption/directory identities remain fixed thereafter.
    """

    def __init__(self, plan, idle, witness, docker, *, published, bridge_sha256, **profile):
        self.failed, self.elapsed_seconds = False, None
        try:
            self._publication(plan, published, bridge_sha256)
            super().__init__(plan, idle, witness, docker, **profile)
        except BaseException as error:
            self._fail(error)


class NativeIdleQualification(AppCandidateQualification):
    """Original larger tree, but ONLY while native launch/outputs remain empty.

    Independently compare the copied baseline and every precreated directory
    against the original publisher's pins. This is not a launch-input qualifier
    or a PostBegin reader. A later launch.json is refused, not silently adopted;
    binding and verifying that new input is a separate explicit phase.
    Existing exact-type action/retained gates do not accept this class.
    """

    PUBLICATION = publication.NativePublished
    INPUT_FILES = ("idle/lease.json", "baseline/baseline.json", "app-start/launch.json")
    DIRECTORIES = ("app-start", "baseline", "idle", "launch", "receipts", "sockets")

    def _publication(self, plan, published, bridge_sha256):
        super()._publication(plan, published, bridge_sha256)
        require(published.baseline_sha256 == plan.native_baseline_sha256)
        identities = published.directory_identities
        require(type(identities) is tuple and len(identities) == len(self.DIRECTORIES))
        require(tuple(name for name, _ in identities) == self.DIRECTORIES)
        for _, identity in identities:
            require(type(identity) is tuple and len(identity) == 6)
            require(all(type(value) is int for value in identity))

    def _app_pins(self):
        return super()._app_pins() + (
            self.published.baseline_sha256,
            self.published.directory_identities,
        )

    def _root_entries(self):
        return self.DIRECTORIES

    def _additional_inputs(self, directory, deadline):
        p = self.published
        expected = dict(p.directory_identities)
        descriptors, observed = [], []
        try:
            for name in self.DIRECTORIES:
                self._guard(deadline)
                fd = os.open(name, files.DIRECTORY, dir_fd=directory)
                descriptors.append(fd)
                info = os.fstat(fd)
                publication._secure(info)
                require(stat.S_IMODE(info.st_mode) == 0o700)
                before = files.identity(info)
                require(before[:6] == expected[name])
                if name == "baseline":
                    require(os.listdir(fd) == ["baseline.json"])
                    raw, identity = _file(
                        fd,
                        "baseline.json",
                        deadline,
                        limit=publication.plans.projection.recording.MAX_MANIFEST_BYTES,
                    )
                    require(identity == dict(p.file_identities)["baseline/baseline.json"])
                    require(hashlib.sha256(raw).hexdigest() == self.plan.native_baseline_sha256)
                    observed.append(identity)
                elif name in ("launch", "sockets", "receipts"):
                    require(not os.listdir(fd))
                require(files.identity(os.fstat(fd)) == before)
                require(
                    files.identity(os.stat(name, dir_fd=directory, follow_symlinks=False)) == before
                )
                observed.append(before)
                self._guard(deadline)
            return tuple(observed)
        finally:
            publication._close(list(reversed(descriptors)))


class AppRetainedQualification(_AppInputs, launch.RetainedQualification):
    """Fresh post-begin reads with the ORIGINAL App input/consumption pins.

    Borrow the actual original App candidate reader and PostBegin capability;
    no caller-supplied replacement profile or receipt is accepted. Its previous
    success is only a continuity prerequisite, never cached qualification. Every
    call rechecks complete source/runtime/environment/process/App input evidence
    under the original stop deadline, not the expired ready deadline. Existing
    exact-type Launch/Start/IdleService gates do not admit this class.
    """

    PROFILE = (
        "image_environment_sha256",
        "timezone",
        "hostname",
        "architecture",
        "runtime_workers",
    )

    def __init__(self, original, continuity):
        self.failed, self.elapsed_seconds = False, None
        try:
            require(type(original) is AppCandidateQualification)
            require(type(continuity) is launch.idle_module.PostBegin)
            require(continuity.plan is original.plan and continuity.idle is original.idle)
            require(original.consumption is not None and original.elapsed_seconds is not None)
            self.candidate = original
            self.candidate_objects = (
                original,
                original.plan,
                original.idle,
                original.witness,
                original.docker,
                original.published,
                original.consumption,
            )
            self.candidate_profile = original.original
            self._publication(original.plan, original.published, original.bridge_sha256)
            self.consumption = self._original_consumption = original.consumption
            self._prebegin()
            super().__init__(
                continuity,
                original.witness,
                original.docker,
                **{name: getattr(original, name) for name in self.PROFILE},
            )
        except BaseException as error:
            self._fail(error)

    def _prebegin(self):
        original = self.candidate
        require(type(original) is AppCandidateQualification and not original.failed)
        require(original.owner == (os.getpid(), launch.get_ident()))
        require(not original.lock.locked())
        require(
            all(
                a is b
                for a, b in zip(
                    (
                        original,
                        original.plan,
                        original.idle,
                        original.witness,
                        original.docker,
                        original.published,
                        original.consumption,
                    ),
                    self.candidate_objects,
                    strict=True,
                )
            )
        )
        require(original._pins() == original.original == self.candidate_profile)
        require(self.published is original.published)
        require(self.consumption is original.consumption)
        require(self.bridge_sha256 == original.bridge_sha256)

    def _pins(self):
        self._prebegin()
        return super()._pins()
