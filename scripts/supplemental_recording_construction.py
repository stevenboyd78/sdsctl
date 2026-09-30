#!/usr/bin/env python3
"""Explicit native construction for the finite recording candidate; offline only.

This is NOT an ordinary CLI hook and does not consume ordinary daemon arguments,
environment defaults or destination/reload/MQTT/remote configurations. It builds
only the declared finite services. Construction starts no sockets or threads.
There is no CLI, operator protocol or guardian here: those remain launch gates.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from ipaddress import IPv4Address
from pathlib import Path
from types import SimpleNamespace

import supplemental_recording_protected as protected
from supplemental_recording_evidence import template

from sds200.daemon_supplemental_acquisition import SupplementalAcquisitionPolicy
from sds200.scanner_display_configuration import ScannerDisplayConfiguration

MESSAGE = "Finite candidate construction is unconfirmed; preserve the case."


class UnconfirmedConstruction(ValueError):
    """Never leak a profile path, target or arbitrary construction exception."""


def require(value):
    if not value:
        raise UnconfirmedConstruction(MESSAGE)


@dataclass(frozen=True)
class Specification:
    """Exact future source-plan inputs, not an authenticated launch permission.

    Non-default ports support isolated loopback qualification. An installed host
    plan must independently seal/validate the real port mappings and addresses.
    No hostname resolution happens during validation or construction.
    """

    host: str
    control_port: int
    rtsp_port: int
    rtp_bind_address: str
    rtp_bind_port: int
    sockets: Path
    receipts: Path
    firmware: str
    read_window_seconds: float
    max_read_attempts: int
    ready_timeout: float

    def __post_init__(self):
        try:
            require(type(self.host) is str and 0 < len(self.host) <= 253)
            require(
                all(
                    re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
                    for label in self.host.split(".")
                )
            )
            # Dotted numeric input must be an actual IPv4 address, not a DNS
            # fallback for a malformed address. The native transports use IPv4.
            if all(c in "0123456789." for c in self.host):
                require(str(IPv4Address(self.host)) == self.host)
            for port in (self.control_port, self.rtsp_port):
                require(type(port) is int and 1 <= port <= 65535)
            require(type(self.rtp_bind_port) is int and 0 <= self.rtp_bind_port <= 65535)
            require(type(self.rtp_bind_address) is str)
            require(str(IPv4Address(self.rtp_bind_address)) == self.rtp_bind_address)
            if self.rtp_bind_port == 0:
                require(IPv4Address(self.host).is_loopback)
                require(IPv4Address(self.rtp_bind_address).is_loopback)
            for path in (self.sockets, self.receipts):
                require(type(path) is type(Path()))
                protected._path(str(path))
            require(len(os.fsencode(self.sockets / "recordings.sock")) < 104)
            require(type(self.ready_timeout) in (int, float) and math.isfinite(self.ready_timeout))
            require(0 < self.ready_timeout <= 600)
            self.policy()
        except Exception:
            raise UnconfirmedConstruction(MESSAGE) from None

    def policy(self):
        return SupplementalAcquisitionPolicy(
            self.firmware, self.read_window_seconds, self.max_read_attempts
        )


def _disjoint(left, right):
    require(not left.is_relative_to(right) and not right.is_relative_to(left))


def _empty_private(path):
    with protected._private_directory(path, exclusive=False) as fd, os.scandir(fd) as items:
        require(next(items, None) is None)


def _services():
    """Import construction-only services only for an actual construction call.

    A read-only launch-plan/probe validator needs Specification, not an API,
    event server, recording manager or lifecycle assembly. Imports do not create
    any services; the fixed native source-origin checks still cover this graph.
    """
    from supplemental_recording_api import FiniteRecordingApi
    from supplemental_recording_assembly import NativeRecordingAssembly

    from sds200.audio import AudioStream
    from sds200.audio_sinks import AudioFanoutSession, PcmSinkRouter
    from sds200.daemon_display_profile import DaemonDisplayProfile
    from sds200.daemon_event_server import DaemonEventServer
    from sds200.daemon_event_stream import DaemonEventStream
    from sds200.daemon_ipc import DaemonSocketListener, resolve_daemon_socket_location
    from sds200.daemon_pcmu_server import DaemonPcmuServer
    from sds200.daemon_process import DaemonProcess
    from sds200.daemon_recording import DaemonRecordingManager
    from sds200.daemon_recording_file_server import DaemonRecordingFileServer
    from sds200.daemon_runtime import DaemonRuntime
    from sds200.daemon_server import DaemonApiServer
    from sds200.daemon_supplemental_acquisition import DaemonSupplementalAcquisition
    from sds200.network import UdpTransport
    from sds200.network_audio import NetworkAudioTransport
    from sds200.pcmu_stream import PcmuStream
    from sds200.radio import SDS200
    from sds200.scanner_display_supplemental_transport import SupplementalDeliveryService

    return SimpleNamespace(
        FiniteRecordingApi=FiniteRecordingApi,
        NativeRecordingAssembly=NativeRecordingAssembly,
        AudioStream=AudioStream,
        AudioFanoutSession=AudioFanoutSession,
        PcmSinkRouter=PcmSinkRouter,
        DaemonDisplayProfile=DaemonDisplayProfile,
        DaemonEventServer=DaemonEventServer,
        DaemonEventStream=DaemonEventStream,
        DaemonSocketListener=DaemonSocketListener,
        resolve_daemon_socket_location=resolve_daemon_socket_location,
        DaemonPcmuServer=DaemonPcmuServer,
        DaemonProcess=DaemonProcess,
        DaemonRecordingManager=DaemonRecordingManager,
        DaemonRecordingFileServer=DaemonRecordingFileServer,
        DaemonRuntime=DaemonRuntime,
        DaemonApiServer=DaemonApiServer,
        DaemonSupplementalAcquisition=DaemonSupplementalAcquisition,
        UdpTransport=UdpTransport,
        NetworkAudioTransport=NetworkAudioTransport,
        PcmuStream=PcmuStream,
        SDS200=SDS200,
        SupplementalDeliveryService=SupplementalDeliveryService,
    )


@contextmanager
def construct(
    specification: Specification,
    stored: protected.StoredBaseline,
    configuration: ScannerDisplayConfiguration,
    *,
    generation: str,
):
    """Build exact native objects; yield a passive, single-run assembly.

    The caller supplies independently pinned native-namespace baseline/profile
    inputs and generation. These values do not authenticate themselves. Before
    run, context cleanup closes only never-started objects. Once run is attempted,
    ownership belongs to the native lifecycle: an uncertain/blocked shutdown is
    not retried here and requires independent exact-process recovery.
    """
    cleanup = ExitStack()
    trial = None
    try:
        require(type(specification) is Specification)
        # Revalidate frozen values too: no altered internal/trusted object can
        # bypass the boundary merely by retaining its Python type.
        specification.__post_init__()
        require(type(configuration) is ScannerDisplayConfiguration)
        protected.evidence.digest(generation)
        collector = protected.Collector(stored)
        require(stored.writer.uid == os.geteuid() and stored.writer.gid == os.getegid())
        # Intent durability and dispatch consume time before native begin. An
        # exact-fit schedule is infeasible; the later native guard still checks
        # the ACTUAL remaining budget, even when static headroom exists.
        require(
            3 + specification.read_window_seconds + 10 < stored.contract.maximum_recording_seconds
        )
        root = stored.baseline.root
        configuration.require_separate_recordings(root)
        for location in (specification.sockets, specification.receipts):
            for other in (root, configuration.source_path, configuration.state_directory):
                _disjoint(location, other)
            _empty_private(location)
        _disjoint(specification.sockets, specification.receipts)
        collector.pristine()  # Complete original inventory; never recapture it.
        services = _services()

        scanner = services.SDS200.from_transport(
            services.UdpTransport(
                specification.host, remote_port=specification.control_port, reconnect=False
            )
        )
        cleanup.callback(scanner.close)
        configuration.require_scanner_target(scanner.endpoint)
        profile = services.DaemonDisplayProfile(configuration, lambda: scanner.endpoint)
        source = services.NetworkAudioTransport(
            specification.host,
            rtsp_port=specification.rtsp_port,
            local_host=specification.rtp_bind_address,
            local_port=specification.rtp_bind_port,
        )
        cleanup.callback(source.stop)
        require(
            hashlib.sha256(source.endpoint.encode()).hexdigest()
            == stored.contract.audio_endpoint_sha256
        )
        router = services.PcmSinkRouter(name="finite-recording-pcm")
        audio = services.AudioFanoutSession(services.AudioStream(source), (router,))
        # This finite one-attempt candidate assigns recovery to its independent
        # outer owner.  Keep the ordinary daemon PSI poller out of the same
        # nonblocking acquisition-admission boundary.
        runtime = services.DaemonRuntime(scanner, audio, router, psi_auto_recover=False)
        manager = services.DaemonRecordingManager(
            runtime, root, template=template(stored.baseline.case)
        )
        cleanup.callback(manager.close)
        api = services.FiniteRecordingApi(runtime, recording_manager=manager)
        api.display_profile = profile
        acquisition = services.DaemonSupplementalAcquisition(
            runtime, profile, specification.policy()
        )
        cleanup.callback(acquisition.close)
        delivery = services.SupplementalDeliveryService(acquisition.frames, acquisition=acquisition)
        api.display_frames, api.supplemental_display = acquisition.frames, delivery

        def listener(name):
            return services.DaemonSocketListener(
                services.resolve_daemon_socket_location(specification.sockets / name)
            )

        server = services.DaemonApiServer(listener("api.sock"), api)
        events = services.DaemonEventStream(runtime, recording_manager=manager)
        cleanup.callback(events.close)
        event_server = services.DaemonEventServer(listener("events.sock"), events)
        files = services.DaemonRecordingFileServer(listener("recordings.sock"), manager)
        packets = services.PcmuStream(source)
        cleanup.callback(packets.close)
        pcmu = services.DaemonPcmuServer(listener("pcmu.sock"), packets)
        process = services.DaemonProcess(
            runtime,
            recording_manager=manager,
            api_server=server,
            event_server=event_server,
            recording_file_server=files,
            pcmu_server=pcmu,
        )
        trial = services.NativeRecordingAssembly(
            process,
            api,
            acquisition,
            delivery,
            stored.baseline,
            specification.receipts,
            stored.writer,
            generation=generation,
            audio_endpoint_sha256=stored.contract.audio_endpoint_sha256,
            ready_timeout=specification.ready_timeout,
        )
        yield trial
        if trial._attempted:
            require(trial.cleanup_complete)
    except Exception:
        if trial is not None:
            # Do not mislabel an exception from the caller's context body as
            # construction failure. Native lifecycle failures are independently
            # sanitized by the assembly and still keep ownership of cleanup.
            raise
        raise UnconfirmedConstruction(MESSAGE) from None
    finally:
        if trial is not None and trial._attempted:
            # Native run already owns cleanup. Never repeat an uncertain stop
            # or finalize through a second lifecycle on context unwinding.
            cleanup.pop_all()
        else:
            try:
                cleanup.close()
            except Exception:
                raise UnconfirmedConstruction(MESSAGE) from None


if __name__ == "__main__":
    raise SystemExit("Offline construction only; no finite candidate launch is enabled.")
