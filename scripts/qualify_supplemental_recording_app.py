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


class AppCandidateQualification(launch.CandidateQualification):
    """Borrow original publication pins; do not learn them from App inputs.

    Docker Config and image defaults must both name this exact case's bridge.
    Existing Idle.read independently checks the actual post-exec PID1 against
    plan.idle_argv. Neither configured argv is substituted for that process read.
    First observed consumption/directory identities remain fixed thereafter.
    """

    def __init__(self, plan, idle, witness, docker, *, published, bridge_sha256, **profile):
        self.failed, self.elapsed_seconds = False, None
        try:
            require(type(published) is publication.Published)
            require(published.plan_sha256 == plan.sha256)
            require(published.lease_sha256 == plan.lease_sha256)
            for pin in (published.receipt_sha256, bridge_sha256):
                base.digest(pin)
            require(type(published.root_identity) is tuple and len(published.root_identity) == 9)
            require(all(type(value) is int for value in published.root_identity))
            require(
                type(published.file_identities) is tuple and len(published.file_identities) == 2
            )
            require(
                tuple(name for name, _ in published.file_identities)
                == ("idle/lease.json", "app-start/launch.json")
            )
            for _, identity in published.file_identities:
                require(type(identity) is tuple and len(identity) == 9)
                require(all(type(value) is int for value in identity))
            self.published, self.bridge_sha256 = published, bridge_sha256
            self.consumption = self._original_consumption = None
            super().__init__(plan, idle, witness, docker, **profile)
        except BaseException as error:
            self._fail(error)

    def _pins(self):
        p = self.published
        require(type(p) is publication.Published)
        require(self.consumption is self._original_consumption)
        return super()._pins() + (
            p.plan_sha256,
            p.lease_sha256,
            p.receipt_sha256,
            p.root_identity,
            p.file_identities,
            self.bridge_sha256,
        )

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
                require(sorted(os.listdir(directory)) == ["app-start", "idle"])
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
                require(files.identity(os.fstat(directory)) == p.root_identity)
                result = tuple(observed)
            if self.consumption is None:
                require(self._original_consumption is None)
                self.consumption = self._original_consumption = result
            require(self.consumption == result)
            return base.checksum(dict(publication=self._pins()[-6:], consumption=result))
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
