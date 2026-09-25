#!/usr/bin/env python3
"""Finite original-owner startup custody, uninstalled and without App actions.

Connects retained declaration, original clock, exclusive plan publication and
independent acceptance. The explicit prepare_service() library path first reads
the complete pre-handoff host baseline; idle_service() can then assemble the
passive journal/service. The finite command selects neither method. No native
worker, scanner or recording operation is selected. Accepted bytes are NOT
installed qualification or operator approval. The caller must keep this owner
alive until later borrowers release its plan and clock.
"""

from __future__ import annotations

import fcntl
import os
import stat
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from threading import Lock, get_ident

PROBE_MESSAGE = "Finite recording startup observation ended; preserve this case and do not restart."

# Direct execution is only the explicitly named action-free observation mode.
# This command is not selected by existing HelperQualification or an App/service.
# Never bootstrap imports from cwd or an environment-controlled helper location.
if __name__ == "__main__":
    try:
        allowed = (
            len(sys.argv) == 4
            and sys.argv[3] == "--startup-probe"
            and sys.flags.isolated == sys.flags.dont_write_bytecode == 1
            and os.geteuid() == os.getegid() == 0
            and os.getcwd() == "/"
            and Path(__file__)
            == Path("/opt/sdsctl-recording-host/supplemental_recording_service_startup.py")
        )
    except Exception:
        allowed = False
    if not allowed:
        print(PROBE_MESSAGE, file=sys.stderr)
        raise SystemExit(64)
    sys.path.insert(0, "/opt/sdsctl-recording-host")

try:
    import supplemental_recording_service_acceptance as acceptance
    import supplemental_recording_service_declaration as declaration
except Exception:
    if __name__ == "__main__":
        print(PROBE_MESSAGE, file=sys.stderr)
        raise SystemExit(75) from None
    raise

publication, offers = acceptance.publication, acceptance.offers
plans = declaration.codec.plans
MESSAGE = "Recording startup is unconfirmed; preserve the case and do not restart it."


class UnconfirmedStartup(ValueError):
    """No ambiguous startup result is permission to construct another owner."""


def require(value):
    if not value:
        raise UnconfirmedStartup(MESSAGE)


class Startup:
    """One prepare/accept attempt, owning clock/models/plan but not declaration.

    Construction claims the original caller-owned Declaration in memory without
    capturing a clock or writing. prepare() explicitly captures ONE original
    clock and exclusively publishes its offer. poll() never renews the original
    offer's finite bound; acceptance returns the same retained CasePlan.

    For later separately qualified service assembly, accepted_input() rechecks
    the original accepted input and clock. IdleService may BORROW these exact
    handles. Close this startup owner only AFTER that borrower is closed. The
    optional idle_service() context closes its borrower before releasing the
    owner; it does not run it or establish readiness, recording or restoration.
    """

    def __init__(self, original):
        self.owner = os.getpid(), get_ident(), os.geteuid(), os.getegid()
        self.lock, self._cleanup = Lock(), []
        self.failed = self.closed = self.used = self.accepted = False
        self.service_used = self._service_active = False
        # Separate optional App-start publication; no existing command selects it.
        self.app_idle_publication_used = False
        self._service_invalidate = None
        self.clock = self.offer = self.publisher = self.original = self.reader = None
        self.baseline = self.projected = self._service_inputs = None
        try:
            require(type(original) is declaration.Declaration)
            require(original.owner == self.owner and original.startup_owner is None)
            self.declaration = self._declaration = original
            original.startup_owner = self
            self.template = self._template = original.recheck()
            self.expected = self._expected = original.expected
            self._input()
        except BaseException as error:
            self._fail(error)

    def _state(self):
        require(not self.failed and not self.closed)
        require(self.owner == (os.getpid(), get_ident(), os.geteuid(), os.getegid()))
        require(self.declaration is self._declaration and self.template is self._template)
        require(self.expected == self._expected)
        require(self.declaration.startup_owner is self)
        require(not self.declaration.failed and not self.declaration.closed)

    def _input(self):
        self._state()
        require(self.declaration.recheck() is self.template)
        require(self.template.sha256 == self.expected)
        self._state()

    def _binding(self):
        self._state()
        require(self.used)
        require(
            all(
                a is b
                for a, b in zip(
                    (self.clock, self.offer, self.publisher, self.original, self.reader),
                    self.objects,
                    strict=True,
                )
            )
        )
        require(self.offer.clock_witness is self.clock)
        require(self.publisher.published is self.original)
        require(self.reader.publisher is self.publisher)
        require(not self.clock.closed and not self.clock.failed)
        require(not self.reader.closed and not self.reader.failed)
        require(self.reader.accepted is self.accepted)
        if self._service_inputs is None:
            require(self.baseline is None and self.projected is None)
        else:
            baseline, projected, prepared_sha256 = self._service_inputs
            require(self.baseline is baseline and self.projected is projected)
            require(
                plans.base.checksum(self.offer.plan.preparation(baseline, projected))
                == prepared_sha256
            )

    def _guard(self):
        self._input()
        self._binding()
        self.reader._guard(
            acceptance.time.monotonic() + acceptance.MAX_SECONDS, accepted=self.accepted
        )
        self._input()
        self.offer._check()
        self._binding()

    def prepare(self):
        """Publish without an App/Engine read; the action-free probe uses this."""
        return self._prepare(None)

    def prepare_service(self, projected, docker):
        """Explicit one-use baseline read BEFORE the one service clock origin.

        All non-clock inputs come from the retained independent declaration.
        A separate temporary preflight clock/plan bounds the existing complete
        host observer and fixed normal-App cache read. It is never published,
        accepted, journaled or substituted for the service's original clock.
        The final plan requires that observation to predate issued_at by at
        most two seconds. A late or failed read ends this owner, without retry.

        After independent acceptance, accepted_input() rechecks this same
        baseline and projection as well as the original published plan. They
        remain in baseline/projected for separately qualified journal/service
        assembly. No journal, App mutation, scanner request or service starts.
        Source/runtime/confinement and input provenance remain external gates.
        """
        return self._prepare((projected, docker, None))

    def prepare_service_from_baseline(self, directory, expected_sha256, docker):
        """Load an independently pinned ORIGINAL manifest before the host read.

        Uses the existing private bounded reader and the template's candidate
        contract. The caller authenticates the manifest digest separately; it
        must not derive approval from whatever bytes happen to be present.
        Projection changes only the declared native path alias, never captures
        or reseals current files. All projection pins are checked before any
        host/cache read. Subsequent complete host checks remain mandatory.

        The loaded original values stay in projected through acceptance and
        service assembly; acceptance does not reopen/adopt a replacement file.
        This consumes the same one prepare attempt as the other entry paths,
        never selects a service run, and is not invoked by --startup-probe.
        """
        return self._prepare((None, docker, (directory, expected_sha256)))

    def _capture_service_origin(self, projected, docker, persisted, preflight_guard=None):
        # This explicit library path is not invoked by --startup-probe.
        # The module is already in the separately qualified startup graph.
        import supplemental_recording_host_launch as launch

        check = preflight_guard if preflight_guard is not None else lambda: None
        check()
        preflight = plans.clock.ClockWitness(plans.clock.read())
        problem = None
        try:
            check()
            plan = self.template.preview(preflight.original)
            if persisted is not None:
                directory, expected_sha256 = persisted
                check()
                original = plans.projection.recording.load_baseline(
                    directory,
                    expected_contract=plan.candidate.contract,
                    expected_sha256=expected_sha256,
                )
                check()
                layout = next(item for item in plan.layouts if item.slug == plans.base.CANDIDATE)
                projected = plans.projection.project(layout, original)
                plan.check_projection(projected)
            reader = launch.PreHandoffHost(plan, projected, docker)
            self._input()
            check()
            sample = reader.read()
            check()
            require(type(sample) is plans.bootstrap.recovery.Sample)
            sample.__post_init__()
            require(sample.boot_id == plan.boot and reader.used and not reader.failed)
            observed = preflight.read()
            require(
                plan.deadlines.issued_at
                <= sample.observation.sampled_at
                <= sample.now
                <= observed.boottime_ns / plans.clock.NS
            )
            self._input()
            check()
            self.clock = plans.clock.ClockWitness(plans.clock.read())
            self._cleanup.append(self.clock.close)
            check()
            observed.check_later(self.clock.original)
            final = self.template.preview(self.clock.original)
            prepared = final.preparation(sample.observation, projected)
            self.baseline, self.projected = sample.observation, projected
            self._service_inputs = self.baseline, projected, plans.base.checksum(prepared)
        except BaseException as error:
            problem = error
            raise
        finally:
            try:
                preflight.close()
            except BaseException as cleanup:
                # Ordinary cleanup uncertainty must not hide an interruption.
                if (
                    problem is None
                    or isinstance(problem, Exception)
                    or not isinstance(cleanup, Exception)
                ):
                    raise

    def _prepare(self, service_inputs, *, preflight_guard=None):
        # Only the separate Permission adapter supplies this additional guard.
        # A callback alone is not source qualification or permission; existing
        # explicit library callers retain their separate external prerequisites.
        # No command or legacy helper profile imports/selects that adapter.
        acquired = False
        try:
            require(self.lock.acquire(blocking=False))
            acquired = True
            self._input()
            require(not self.used)
            self.used = True
            check = preflight_guard if preflight_guard is not None else lambda: None
            check()
            if service_inputs is None:
                require(preflight_guard is None)
                self.clock = plans.clock.ClockWitness(plans.clock.read())
                self._cleanup.append(self.clock.close)
            else:
                self._capture_service_origin(*service_inputs, preflight_guard=preflight_guard)
            check()
            self._input()
            self.offer = offers.Offer(self.template, self.expected, self.clock)
            self._cleanup.append(self.offer.close)
            self.publisher = publication.Publisher(self.offer)
            check()
            self.original = self.publisher.publish()
            self._cleanup.append(self.original.close)
            check()
            self.reader = acceptance.Acceptance(self.publisher)
            self._cleanup.append(self.reader.close)
            self.objects = self.clock, self.offer, self.publisher, self.original, self.reader
            self._guard()
            check()
            return self.original
        except BaseException as error:
            self._fail(error)
        finally:
            if acquired:
                self.lock.release()

    def poll(self):
        acquired = False
        try:
            require(self.lock.acquire(blocking=False))
            acquired = True
            require(not self.accepted)
            self._guard()
            result = self.reader.poll()
            if result is not None:
                self.accepted = True
                require(result is self.offer.plan and result.raw == self.original.recheck().raw)
            self._guard()
            return self.original if self.accepted else None
        except BaseException as error:
            self._fail(error)
        finally:
            if acquired:
                self.lock.release()

    def accepted_input(self):
        """Original accepted handles only; never service/action authorization."""
        acquired = False
        try:
            require(self.lock.acquire(blocking=False))
            acquired = True
            require(self.accepted)
            self._guard()
            return self.original
        except BaseException as error:
            self._fail(error)
        finally:
            if acquired:
                self.lock.release()

    def _service_directories(self, original, guard):
        """Create exactly two children through the retained case descriptor.

        The generic private-directory reader deliberately pins link count.
        This creation step instead accounts for each intended mkdir explicitly,
        without weakening that reader or adopting a reopened case pathname.
        An independent open description keeps the short exclusive flock from
        remaining on the caller's retained descriptor after this step.
        """
        directory, problem = -1, None
        try:
            guard()
            retained = original._directories[-1][2]
            directory = os.open(".", declaration.files.DIRECTORY, dir_fd=retained)
            fcntl.flock(directory, fcntl.LOCK_EX | fcntl.LOCK_NB)
            before = declaration.files.identity(os.fstat(retained))[:6]
            require(before[5] >= 2)
            names = {publication.CLAIM, "plan.json", acceptance.NAME}
            identities = {}

            def check():
                guard()
                expected = (*before[:5], before[5] + len(identities))
                require(declaration.files.identity(os.fstat(directory))[:6] == expected)
                require(declaration.files.identity(os.fstat(retained))[:6] == expected)
                publication._names(directory, names | set(identities))
                for name, pinned in identities.items():
                    require(
                        declaration.files.identity(
                            os.stat(name, dir_fd=directory, follow_symlinks=False)
                        )[:6]
                        == pinned
                    )
                guard()

            check()
            for name in ("journal", "inbox"):
                os.mkdir(name, mode=0o700, dir_fd=directory)
                info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                require(stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o700)
                require((info.st_uid, info.st_gid) == self.owner[2:] and info.st_nlink == 2)
                identities[name] = declaration.files.identity(info)[:6]
                check()
            os.fsync(directory)
            check()
            return identities
        except BaseException as error:
            problem = error
            raise
        finally:
            if directory >= 0:
                closing, directory = directory, -1
                try:
                    os.close(closing)
                except BaseException as error:
                    if (
                        problem is None
                        or isinstance(problem, Exception)
                        or not isinstance(error, Exception)
                    ):
                        raise

    @contextmanager
    def idle_service(self, docker):
        """One passive service assembly, keeping startup custody until cleanup.

        Requires this owner's accepted template-derived baseline. Creates only
        new private journal/inbox directories and the original preparation
        record; any existing residue refuses and stays untouched. There is no
        implicit run(), notice, App dispatch, native startup or recording.

        Assembly fits one two-second window within the original offer. The
        yielded service keeps its own original deadline checks; this context
        does not repoll/extend acceptance across the service lifetime. It closes
        service then journal before releasing the startup owner's clock/plan.
        Successful exit leaves startup and declaration with the caller. Every
        failed path preserves files and attempts original cleanup once.
        No installed command currently selects this method.
        """
        import supplemental_recording_service_operator as operator

        acquired, cleanup, problem = False, [], None
        try:
            require(self.lock.acquire(blocking=False))
            acquired = True
            self._guard()
            require(self.accepted and not self.service_used and self._service_inputs is not None)
            self.service_used = True
            # Protect custody during construction as well as the yielded
            # lifetime; reentrant failure cannot close a clock being lent to
            # a partly constructed service before its own cleanup runs.
            self._service_active = True
            require(type(docker) is plans.ordinary.Docker and docker.path == "/var/run/docker.sock")
            end = time.monotonic() + publication.MAX_SECONDS
            original, plan, clock = self.original, self.original.plan, self.clock
            baseline, projected = self.baseline, self.projected

            def guard():
                require(time.monotonic() < end)
                self._guard()
                require(self.original is original and self.clock is clock)
                require(self.baseline is baseline and self.projected is projected)
                require(time.monotonic() < end)

            guard()
            identities = self._service_directories(original, guard)
            journal = operator.launch.bootstrap.Journal(plan.root / "journal")
            cleanup.append(journal.close)
            require(declaration.files.identity(os.fstat(journal.fd))[:6] == identities["journal"])
            require(not journal.entries and journal.machine is None)
            journal.append(plan.preparation(baseline, projected))
            guard()
            service = operator.IdleService(
                original, projected, journal, docker, clock_witness=clock
            )
            cleanup.append(service.close)

            def invalidate():
                # Deny more service actions immediately on custody failure,
                # but leave original descriptors alive for ordered cleanup.
                service.failed = True

            self._service_invalidate = invalidate
            require(
                declaration.files.identity(os.fstat(service.inbox.fd))[:6] == identities["inbox"]
            )
            guard()
            yield service
            # Pure original-object checks only, not a renewed offer read after
            # a service that may legitimately outlive the acceptance window.
            self._binding()
        except BaseException as error:
            problem = error
        finally:
            while cleanup:
                callback = cleanup.pop()
                try:
                    callback()
                except BaseException as error:
                    if (
                        problem is None
                        or isinstance(problem, Exception)
                        and not isinstance(error, Exception)
                    ):
                        problem = error
            if acquired:
                self._service_active = False
                self._service_invalidate = None
                self.lock.release()
        if problem is not None:
            self._fail(problem)

    def _fail(self, error):
        self.failed = True
        if self._service_active and self._service_invalidate is not None:
            self._service_invalidate()
        if self.owner == (os.getpid(), get_ident(), os.geteuid(), os.getegid()):
            try:
                self.close()
            except BaseException as cleanup:
                if isinstance(error, Exception) and not isinstance(cleanup, Exception):
                    raise cleanup
        if not isinstance(error, Exception):
            raise error
        raise UnconfirmedStartup(MESSAGE) from None

    def close(self):
        require(self.owner == (os.getpid(), get_ident(), os.geteuid(), os.getegid()))
        require(not self._service_active)
        if self.closed:
            return
        self.closed = True
        error = None
        while self._cleanup:
            callback = self._cleanup.pop()
            try:
                callback()
            except BaseException as problem:
                if (
                    error is None
                    or isinstance(error, Exception)
                    and not isinstance(problem, Exception)
                ):
                    error = problem
        if error is not None:
            self.failed = True
            if not isinstance(error, Exception):
                raise error
            raise UnconfirmedStartup(MESSAGE) from None


def startup_probe(root, expected_sha256):
    """Finite original-owner observation, not an installed recording service.

    Unlike the older passive read probe, this explicitly publishes a new
    synthetic-or-independently-provisioned case's claim and clock-bound plan.
    It may consume one independently submitted acceptance, but performs no
    Engine request, journal, operator notice, native launch or scanner action.
    All original handles stay alive during this observation, even after
    acceptance. It retires before the original offer's at-most-15-second bound,
    reserving the last two seconds rather than starting another bounded read at
    the expiry edge. The bound is never restarted or extended.
    A separate supervisor must bound blocked kernel I/O. No signal is masked.
    Exit75 proves neither acceptance nor readiness; files remain for inspection.
    """
    with declaration.Declaration(root, expected_sha256) as original:
        owner = Startup(original)
        try:
            owner.prepare()
            # Observation may end early; it cannot claim a successful final
            # custody read at expiry. Reserve one acceptance I/O budget instead
            # of repeatedly beginning fresh reads immediately before expiry.
            end = owner.offer.deadline - acceptance.MAX_SECONDS
            for _ in range(151):
                # Expiry only ends this non-authorizing observation. It is not
                # a successful custody check or a return of accepted handles.
                if time.monotonic() >= end:
                    return 75
                if owner.accepted:
                    owner.accepted_input()
                else:
                    owner.poll()
                remaining = end - time.monotonic()
                if remaining <= 0:
                    return 75
                time.sleep(min(0.1, remaining))
            raise UnconfirmedStartup(MESSAGE) from None
        finally:
            owner.close()


if __name__ == "__main__":
    try:
        root = Path(sys.argv[1])
        require(str(root) == sys.argv[1] and root.is_absolute() and ".." not in root.parts)
        code = startup_probe(root, sys.argv[2])
    except Exception:
        print(PROBE_MESSAGE, file=sys.stderr)
        raise SystemExit(75) from None
    raise SystemExit(code)
