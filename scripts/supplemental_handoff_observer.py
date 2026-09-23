#!/usr/bin/env python3
"""Strict, read-only evidence assembly for the private finite App handoff.

No service, automatic request, filesystem mutation or scanner probe. Source and
protected-file collection must be supplied by the separately qualified collector;
these objects do not turn an asserted hash into independently collected evidence.
Docker/CLI access is administrative and must never be exposed through a web API.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from contextlib import suppress
from dataclasses import asdict, dataclass
from typing import Any, cast

from supplemental_handoff_executor import Sample
from supplemental_handoff_host import (
    AUDIO_NETWORK,
    CLI,
    CORE,
    READER_NETWORK,
    READS,
    Docker,
    app_configuration,
    audio_container_network,
    docker_output,
    execution_state,
    generation,
    network_policy,
    require_host_rtp_idle,
    rtp_port_owners,
    supervisor_data,
    supervisor_jobs_idle,
)
from supplemental_handoff_policy import (
    CANDIDATE,
    NORMAL,
    App,
    Observation,
    checksum,
    clock,
    digest,
    identifier,
    require,
)


def image_id(value: Any) -> None:
    require(type(value) is str and value.startswith("sha256:"))
    digest(value[7:])


def seal_identity(version, image, settings) -> None:
    require(type(version) is str and re.fullmatch(r"[A-Za-z0-9._-]{1,128}", version) is not None)
    image_id(image)
    digest(settings)


@dataclass(frozen=True)
class ProtectedFiles:
    context: str
    package: str
    profile: str
    recordings: str

    def __post_init__(self) -> None:
        for value in asdict(self).values():
            digest(value)


@dataclass(frozen=True)
class AppSeal:
    """Expected independently verified identities, not installed-manifest claims."""

    slug: str
    version: str
    image: str
    settings: str
    files: ProtectedFiles

    def __post_init__(self) -> None:
        require(self.slug in (NORMAL, CANDIDATE))
        seal_identity(self.version, self.image, self.settings)
        require(type(self.files) is ProtectedFiles)

    @property
    def pin(self) -> str:
        return checksum(asdict(self))


@dataclass(frozen=True)
class NativeState:
    """Collected cached state for exactly one verified container incarnation.

    The collector checks profile/target/model/firmware, PSI connection, finite
    candidate guardian/case and supplemental capability without demand or probes.
    None means readiness/recording is unconfirmed, not healthy or recording idle.
    """

    generation: str
    healthy: bool | None
    recording: bool | None

    def __post_init__(self) -> None:
        digest(self.generation)
        for value in (self.healthy, self.recording):
            require(value is None or type(value) is bool)


class SupervisorReads:
    """Fixed read-only CLI calls; each new execution is attempted once.

    Read failures never imply command completion or App state. There is no method
    to start an old execution. The caller supplies bounded transport and elapsed
    time; an outer service deadline still covers kernel stalls. No options or raw
    private error text is logged by this component.
    """

    def __init__(self, docker: Docker, *, image: str, incarnation: str):
        image_id(image)
        digest(incarnation)
        self.docker, self.image, self.incarnation = docker, image, incarnation

    def read(self, key: str) -> bytes:
        require(key in READS)
        command = READS[key]
        before = self.docker.container(CLI)
        require(generation(before, name=CLI, image=self.image) == self.incarnation)
        execution = self.docker.create_execution(before["Id"], command, attach=True)
        require(
            execution_state(
                self.docker.inspect_execution(execution),
                execution_id=execution,
                container_id=before["Id"],
                command=command,
            )
            == "created"
        )
        require(
            generation(self.docker.container(CLI), name=CLI, image=self.image) == self.incarnation
        )
        raw = self.docker.start_execution(execution, attach=True)
        after = self.docker.inspect_execution(execution)
        require(
            execution_state(
                after, execution_id=execution, container_id=before["Id"], command=command
            )
            == "not_running"
            and after["ExitCode"] == 0
        )
        require(
            generation(self.docker.container(CLI), name=CLI, image=self.image) == self.incarnation
        )
        return docker_output(raw)


def installed_apps(raw: bytes) -> dict[str, tuple[str, str]]:
    """Decode the observed CLI wire key (addons), not its user-facing Apps label."""
    values = supervisor_data(raw).get("addons")
    require(type(values) is list and 0 < len(values) <= 256)
    result: dict[str, tuple[str, str]] = {}
    for item in cast(list[Any], values):
        require(type(item) is dict)
        slug, version, state = (item.get(k) for k in ("slug", "version", "state"))
        require(type(slug) is str and re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,127}", slug) is not None)
        require(slug not in result)
        require(
            type(version) is str and re.fullmatch(r"[A-Za-z0-9._-]{1,128}", version) is not None
        )
        require(state in ("started", "stopped", None))
        result[slug] = (version, state or "unknown")
    return result


def container_index(values: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    require(type(values) is list and len(values) <= 256)
    result: dict[str, dict[str, Any]] = {}
    ids: set[str] = set()
    for value in values:
        require(type(value) is dict)
        digest(value.get("Id"))
        require(value["Id"] not in ids)
        ids.add(value["Id"])
        names = value.get("Names")
        require(type(names) is list and len(names) == 1)
        name = cast(list[Any], names)[0]
        require(
            type(name) is str
            and re.fullmatch(r"/[A-Za-z0-9][A-Za-z0-9_.-]{0,255}", name) is not None
        )
        require(name not in result)
        image_id(value.get("ImageID"))
        require(
            value.get("State")
            in ("created", "running", "paused", "restarting", "removing", "exited", "dead")
        )
        result[name] = {k: value[k] for k in ("Id", "ImageID", "State")}
    return result


def manual_container(value: dict[str, Any]) -> None:
    """A scanner owner must not independently restart outside the journal."""
    config = value.get("HostConfig")
    require(type(config) is dict)
    config = cast(dict[str, Any], config)
    policy = config.get("RestartPolicy")
    require(type(policy) is dict and policy.get("Name") == "no")
    policy = cast(dict[str, Any], policy)
    require(type(policy.get("MaximumRetryCount")) is int and policy["MaximumRetryCount"] == 0)


def retained_exit(value: dict[str, Any], *, name: str, image: str, cid: str) -> str:
    """Docker exit evidence only; RecoverySession still requires its pidfd receipt.

    A consumed candidate cannot qualify as a fresh case simply because it exited.
    Initial service preparation must separately require an absent candidate and
    absent case state. This path supports its natural finite-guardian shutdown.
    """
    manual_container(value)
    require(value.get("Id") == cid and value.get("Name") == "/" + name)
    require(value.get("Image") == image)
    state = value.get("State")
    require(type(state) is dict and state.get("Status") == "exited")
    state = cast(dict[str, Any], state)
    require(all(state.get(k) is False for k in ("Running", "Paused", "Restarting", "Dead")))
    require(type(state.get("Pid")) is int and state["Pid"] == 0)
    require(type(state.get("ExitCode")) is int and type(state.get("OOMKilled")) is bool)
    require(type(state.get("Error")) is str)
    for key in ("StartedAt", "FinishedAt"):
        require(
            type(state.get(key)) is str
            and re.fullmatch(r"20\d\d-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,9})?Z", state[key])
            is not None
        )
    return checksum({"id": cid, "image": image, "name": name, "state": state})


class HostObserver:
    """Fresh joined evidence; process receipts remain RecoverySession's gate.

    Seals and inventory expectations must be independently reconstructed, then
    held outside both Apps. collect_files must freshly verify all four protected
    trees and installed package (or immutable image when stopped), without repair.
    read_native uses cached local IPC only, no supplemental demand/scanner probe.
    This class does not claim atomicity; the executor takes a second full sample
    before dispatch and checks its preconditions. Every collection has a 2s bound.
    """

    @staticmethod
    def validate_seals(seals) -> None:
        require(type(seals) is tuple and len(seals) == 2 and all(type(s) is AppSeal for s in seals))
        require({s.slug for s in seals} == {NORMAL, CANDIDATE})

    def file_pin(self, slug, config, files) -> str:
        require(type(files) is ProtectedFiles)
        return AppSeal(
            slug, config.version, self.seals[slug].image, config.settings_sha256, files
        ).pin

    def sample(self, boot, now, began, observed, other_stopped, jobs_idle) -> Sample:
        return Sample(
            boot,
            now,
            Observation(
                began, observed[NORMAL], observed[CANDIDATE], other_stopped, jobs_idle, True
            ),
        )

    def __init__(
        self,
        docker: Docker,
        supervisor: SupervisorReads,
        *,
        seals: tuple[AppSeal, AppSeal],
        installed_versions: dict[str, str],
        other_scanner_apps: frozenset[str],
        core_image: str,
        core_generation: str,
        core_version: str,
        read_clock: Callable[[], tuple[str, float]],
        collect_files: Callable[[str, dict[str, Any] | None], ProtectedFiles],
        read_native: Callable[[str, str], NativeState],
        network: str = READER_NETWORK,
    ):
        network_policy(network)
        self.network = network
        self.validate_seals(seals)
        require(type(installed_versions) is dict and 2 <= len(installed_versions) <= 256)
        for slug, version in installed_versions.items():
            require(
                type(slug) is str and re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,127}", slug) is not None
            )
            require(
                type(version) is str and re.fullmatch(r"[A-Za-z0-9._-]{1,128}", version) is not None
            )
        require(
            type(other_scanner_apps) is frozenset
            and other_scanner_apps <= installed_versions.keys()
        )
        require(not other_scanner_apps & {NORMAL, CANDIDATE})
        # The reviewed inventory also lists unrelated Apps. Never allow a known
        # SDS App to be accidentally omitted from the additional-owner gate.
        require(
            {slug for slug in installed_versions if "sds200" in slug or "sdsctl" in slug}
            - {NORMAL, CANDIDATE}
            <= other_scanner_apps
        )
        for seal in seals:
            require(installed_versions.get(seal.slug) == seal.version)
        image_id(core_image)
        digest(core_generation)
        require(
            type(core_version) is str
            and re.fullmatch(r"[A-Za-z0-9._-]{1,128}", core_version) is not None
        )
        self.docker, self.supervisor, self.seals = docker, supervisor, {s.slug: s for s in seals}
        self.versions, self.other = dict(installed_versions), other_scanner_apps
        self.core_image, self.core_generation, self.core_version = (
            core_image,
            core_generation,
            core_version,
        )
        self.read_clock, self.collect_files, self.read_native = (
            read_clock,
            collect_files,
            read_native,
        )

    def read(self) -> Sample:
        boot, began = self.read_clock()
        identifier(boot)
        clock(began)
        apps = installed_apps(self.supervisor.read("apps"))
        require({slug: value[0] for slug, value in apps.items()} == self.versions)
        listed = self.docker.containers()
        before = container_index(listed)
        port_owners = self._audio_network(listed)
        # Refuse unlisted orphan App containers rather than silently ignoring an
        # older naming convention or a possible additional scanner owner.
        for name in before:
            for prefix in ("/app_", "/addon_"):
                if name.startswith(prefix):
                    require(prefix == "/app_" and name[len(prefix) :] in apps)
        other_stopped = all(
            apps[slug][1] == "stopped" and "/app_" + slug not in before for slug in self.other
        )
        jobs_idle = supervisor_jobs_idle(self.supervisor.read("jobs"))
        core = self.docker.container(CORE)
        core_id = generation(core, name=CORE, image=self.core_image)
        require(core_id == self.core_generation)
        require(supervisor_data(self.supervisor.read("core")).get("version") == self.core_version)
        observed: dict[str, App] = {}
        exited: dict[str, tuple[str, str]] = {}
        for slug, key in ((NORMAL, "normal"), (CANDIDATE, "candidate")):
            seal = self.seals[slug]
            config = app_configuration(self.supervisor.read(key), slug=slug, network=self.network)
            require((config.version, config.supervisor_state) == apps[slug])
            image = self.docker.image(seal.image)
            require(image.get("Id") == seal.image and image.get("Os") == "linux")
            require(image.get("Architecture") in ("amd64", "arm64"))
            listing = before.get("/app_" + slug)
            container = None
            incarnation = None
            if listing is not None:
                require(listing["ImageID"] == seal.image)
                if listing["State"] == "running" and config.supervisor_state == "started":
                    container = self.docker.container("app_" + slug)
                    require(container.get("Id") == listing["Id"])
                    manual_container(container)
                    if self.network == AUDIO_NETWORK:
                        audio_container_network(container, candidate=slug == CANDIDATE)
                    incarnation = generation(container, name="app_" + slug, image=seal.image)
                elif listing["State"] == "exited" and config.supervisor_state == "stopped":
                    proof = retained_exit(
                        self.docker.container("app_" + slug),
                        name="app_" + slug,
                        image=seal.image,
                        cid=listing["Id"],
                    )
                    exited[slug] = (listing["Id"], proof)
            files = self.collect_files(slug, container)
            # Report actual fingerprints to policy; a changed pin ends the case
            # instead of hiding a known mismatch as a transient read failure.
            pin = self.file_pin(slug, config, files)
            if incarnation is not None:
                # Do not execute/import a collector inside an App whose code,
                # options or protected inputs no longer match the reviewed seal.
                # The changed pin still reaches policy and ends the case.
                native = NativeState(incarnation, None, None)
                if pin == seal.pin:
                    # A starting/exiting daemon may not answer IPC yet. Keep
                    # the verified container incarnation visible so recovery
                    # can bind its init pidfd early. This cannot authorize a
                    # healthy/recording-idle transition.
                    with suppress(Exception):
                        native = self.read_native(slug, incarnation)
                require(type(native) is NativeState and native.generation == incarnation)
                current = self.docker.container("app_" + slug)
                manual_container(current)
                if self.network == AUDIO_NETWORK:
                    audio_container_network(current, candidate=slug == CANDIDATE)
                require(generation(current, name="app_" + slug, image=seal.image) == incarnation)
                observed[slug] = App(pin, "running", incarnation, native.healthy, native.recording)
            elif (listing is None or slug in exited) and config.supervisor_state == "stopped":
                observed[slug] = App(pin, "stopped")
            else:
                observed[slug] = App(pin, "unknown")
        require(installed_apps(self.supervisor.read("apps")) == apps)
        listed = self.docker.containers()
        require(container_index(listed) == before)
        require(self._audio_network(listed) == port_owners)
        for slug, (cid, proof) in exited.items():
            require(
                retained_exit(
                    self.docker.container("app_" + slug),
                    name="app_" + slug,
                    image=self.seals[slug].image,
                    cid=cid,
                )
                == proof
            )
        require(
            generation(self.docker.container(CORE), name=CORE, image=self.core_image) == core_id
        )
        jobs_idle = supervisor_jobs_idle(self.supervisor.read("jobs")) and jobs_idle
        end_boot, now = self.read_clock()
        require(end_boot == boot)
        clock(now)
        require(0 <= now - began <= 2)
        return self.sample(boot, now, began, observed, other_stopped, jobs_idle)

    def _audio_network(self, listed: list[dict[str, Any]]) -> tuple[str, ...]:
        if self.network == READER_NETWORK:
            return ()
        owners = rtp_port_owners(listed)
        candidate = next((v for v in listed if v.get("Names") == ["/app_" + CANDIDATE]), None)
        running = candidate is not None and candidate.get("State") == "running"
        require(bool(owners) == running)
        if not running:
            require_host_rtp_idle()
        return owners


if __name__ == "__main__":
    raise SystemExit("Read-only observation components; no handoff or service was started.")
