"""Emitted native format fed to the real parser on private local fixtures.

Only the host plan/projection envelope is synthetic here. This tests format
compatibility, not accepted host provenance, a live generation or a launch.
"""

import hashlib
from dataclasses import replace
from types import SimpleNamespace

import pytest

from sds200.network_audio import NetworkAudioTransport

from . import test_supplemental_recording_app_launch as publication_tests
from . import test_supplemental_recording_launch_plan as plan_tests

m = publication_tests.m
tree, configured, cached, prepared = (
    plan_tests.tree,
    plan_tests.configured,
    plan_tests.cached,
    plan_tests.prepared,
)


@pytest.mark.parametrize("rtsp_port", [554, 8554])
def test_emitted_description_is_accepted_by_the_real_native_parser(prepared, rtsp_port):
    s = prepared
    root = s.spec.sockets.parent
    specification = replace(s.spec, rtsp_port=rtsp_port)
    endpoint = NetworkAudioTransport(specification.host, rtsp_port=rtsp_port).endpoint
    manifest = m.native.protected.manifest_bytes(
        s.stored.baseline,
        s.stored.writer,
        hashlib.sha256(endpoint.encode()).hexdigest(),
    )
    stored = m.native.protected._decode(manifest)
    (root / "baseline/baseline.json").write_bytes(manifest)
    deployment = s.path.parent.parent / "deployment.toml"
    profile_sha256, _ = m.native.cached.profile_files(deployment, s.tree.root)
    projected = SimpleNamespace(
        native=stored,
        layout=SimpleNamespace(deployment=deployment, data=root.parent),
    )

    def check(original):
        assert original is projected

    plan = SimpleNamespace(
        native_root=root,
        firmware=specification.firmware,
        check_projection=check,
        native_baseline_sha256=stored.manifest_sha256,
        projection_sha256="c" * 64,
        candidate_runtime=SimpleNamespace(source="b" * 64),
        sha256="d" * 64,
    )
    raw = m._description(
        SimpleNamespace(projected=projected),
        SimpleNamespace(plan=plan, generation="a" * 64),
        specification,
        profile_sha256,
    )
    s.path.write_bytes(raw)
    parsed = m.native.load(
        s.path,
        expected_sha256=hashlib.sha256(raw).hexdigest(),
        expected_source_sha256="b" * 64,
    )
    assert parsed.specification == specification and parsed.stored == stored
    assert parsed.generation == "a" * 64 and parsed.profile_sha256 == profile_sha256
    assert parsed.projection_sha256 == plan.projection_sha256
    assert parsed.host_plan_sha256 == plan.sha256
    assert not tuple(specification.sockets.iterdir())
    assert not tuple(specification.receipts.iterdir())
