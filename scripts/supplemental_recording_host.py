#!/usr/bin/env python3
"""Recording-aware host evidence join; offline, not an installed service plan.

The candidate has an explicitly different seal. Complete changing recording
evidence accompanies every static pin; the normal App keeps its original entire
inventory. This does not authenticate native start/stop returns, dispatch App
commands, publish checkpoints or infer process exit.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from threading import Lock

import supplemental_handoff_observer as ordinary
import supplemental_handoff_protected as static
import supplemental_recording_protected as recording
from supplemental_handoff_policy import CANDIDATE, NORMAL, checksum, require
from supplemental_recording_handoff import Contract, Files, Observation
from supplemental_recording_recovery import Sample


@dataclass(frozen=True)
class CandidateSeal:
    version: str
    image: str
    settings: str
    files: static.StaticFiles
    contract: Contract

    @property
    def slug(self):
        return CANDIDATE

    def __post_init__(self):
        require(type(self.files) is static.StaticFiles and type(self.contract) is Contract)
        ordinary.seal_identity(self.version, self.image, self.settings)

    @property
    def pin(self):
        return checksum({"kind": "finite-recording-candidate-v1", **asdict(self)})


@dataclass(frozen=True)
class CandidateEvidence:
    static: static.StaticFiles
    recording: recording.Collected

    def __post_init__(self):
        require(type(self.static) is static.StaticFiles)
        require(type(self.recording) is recording.Collected)
        require(type(self.recording.files) is Files)


@dataclass(frozen=True)
class Capture:
    """Trusted stage/identity inputs, not a network-controlled collection request.

    A future host reconciler must obtain the start expectation, previous progress
    and acknowledgment from its independently bound durable evidence. Neither an
    idle-looking API flag nor a stopped.json file supplies that authority.
    """

    stage: str
    expected: recording.evidence.RecordingExpectation | None = None
    previous: recording.monitor.Observation | None = None
    stopped: dict | None = None
    acknowledgment: recording.Acknowledgment | None = None

    def __post_init__(self):
        require(type(self.stage) is str)
        require(self.stage in ("pristine", "active", "finalizing", "retained", "finalized"))
        if self.stage == "pristine":
            require(
                all(
                    v is None
                    for v in (self.expected, self.previous, self.stopped, self.acknowledgment)
                )
            )
        else:
            require(type(self.expected) is recording.evidence.RecordingExpectation)
            require(self.previous is None or type(self.previous) is recording.monitor.Observation)
            if self.stage == "finalized":
                require(
                    type(self.stopped) is dict
                    and type(self.acknowledgment) is recording.Acknowledgment
                )
            else:
                require(self.stopped is None and self.acknowledgment is None)


class FilesCollector:
    """Actual fixed host-path checks plus every old/new recording file.

    The selected layouts/mounts and immutable image package hashes must already
    be sealed independently. This class cannot make caller-supplied hashes true.
    It neither replaces old inventories nor hides a changing root inside an old
    ProtectedFiles.recordings field.
    """

    def __init__(
        self,
        normal: static.ProtectedLayout,
        candidate: static.ProtectedLayout,
        collector: recording.Collector,
        capture: Callable[[], Capture],
    ):
        require(type(normal) is static.ProtectedLayout and normal.slug == NORMAL)
        require(type(candidate) is static.ProtectedLayout and candidate.slug == CANDIDATE)
        require(type(collector) is recording.Collector and callable(capture))
        require(collector.stored.baseline.root == candidate.recordings)
        require(not normal.recordings.is_relative_to(candidate.recordings))
        require(not candidate.recordings.is_relative_to(normal.recordings))
        # Candidate recording must not mutate any normal or candidate profile,
        # context, or App-data tree. Disjointness is checked in both directions.
        for layout in (normal, candidate):
            for path in (layout.context, layout.data, *layout.profile_paths):
                require(not path.is_relative_to(candidate.recordings))
                require(not candidate.recordings.is_relative_to(path))
        self.normal, self.candidate, self.collector, self.capture = (
            normal,
            candidate,
            collector,
            capture,
        )

    def __call__(self, slug, container):
        require(slug in (NORMAL, CANDIDATE))
        if slug == NORMAL:
            return static.collect(self.normal, container)
        fixed = static._collect_static(self.candidate, container)
        request = self.capture()
        require(type(request) is Capture)
        if request.stage == "pristine":
            result = self.collector.pristine()
        elif request.stage in ("active", "finalizing"):
            result = self.collector.active(
                request.expected,
                finalizing=request.stage == "finalizing",
                previous=request.previous,
            )
        elif request.stage == "retained":
            result = self.collector.retained(request.expected, previous=request.previous)
        else:
            result = self.collector.finalized(
                request.expected,
                previous=request.previous,
                stopped=request.stopped,
                acknowledgment=request.acknowledgment,
            )
        return CandidateEvidence(fixed, result)


class HostObserver(ordinary.HostObserver):
    """Use the full existing Docker/Supervisor checks with a recording-aware seal.

    Cached native flags are never replaced with idle. Exact init-exit and tracked
    CLI completion remain separate policy gates. All collector inputs must be
    qualified and source-bound; this join does not authenticate an asserted hash.
    """

    def __init__(self, *args, **kwargs):
        # Recording requires the independently guarded RTP publication. The
        # legacy reader-only network cannot be an implicit downgrade here.
        kwargs.setdefault("network", ordinary.AUDIO_NETWORK)
        require(kwargs["network"] == ordinary.AUDIO_NETWORK)
        self._lock = Lock()
        self._files = None
        super().__init__(*args, **kwargs)

    @staticmethod
    def validate_seals(seals):
        require(type(seals) is tuple and len(seals) == 2)
        require(sum(type(s) is ordinary.AppSeal and s.slug == NORMAL for s in seals) == 1)
        require(sum(type(s) is CandidateSeal for s in seals) == 1)

    def file_pin(self, slug, config, files):
        if slug == NORMAL:
            return super().file_pin(slug, config, files)
        require(type(files) is CandidateEvidence)
        seal = self.seals[CANDIDATE]
        require(files.recording.files.contract_sha256 == seal.contract.sha256)
        self._files = files.recording.files
        return CandidateSeal(
            config.version, seal.image, config.settings_sha256, files.static, seal.contract
        ).pin

    def sample(self, boot, now, began, observed, other_stopped, jobs_idle):
        require(self._files is not None)
        candidate = observed[CANDIDATE]
        if candidate.state == "running" and self._files.generation is not None:
            require(candidate.generation == self._files.generation)
        return Sample(
            boot,
            now,
            Observation(
                began, observed[NORMAL], candidate, other_stopped, jobs_idle, True, self._files
            ),
        )

    def read(self):
        require(self._lock.acquire(blocking=False))
        self._files = None
        try:
            return super().read()
        finally:
            self._files = None
            self._lock.release()


if __name__ == "__main__":
    raise SystemExit("Offline recording-aware host evidence only; no live plan is enabled.")
