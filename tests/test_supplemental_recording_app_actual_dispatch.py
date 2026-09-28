"""Original request/dispatch/service joins real native recording and owned exits.

The two initial fixed CLI commands and one restoration command use synthetic
App/Engine metadata, not a live Docker endpoint. Real native recording, original
journal/ledger and owned normal/candidate init pidfds are joined in one service
lifetime. Independent observer Link custody and installed qualification remain
separate. No device or preserved case is accessed.
"""

import pytest

from . import test_supplemental_recording_app_actual_recovery as recovery
from . import test_supplemental_recording_app_driver_dispatch as dispatch

service = recovery.service
bwrap, staged, mapped = service.bwrap, service.staged, service.mapped
layout, image_umask, supervised = service.layout, service.image_umask, service.supervised
image, configured = service.image, service.configured
candidate, app, native = service.candidate, service.app, service.native
driver_case, dispatched = service.service_case, dispatch.dispatched
pytestmark = service.pytestmark


@pytest.fixture
def launch_case(native, monkeypatch, mapped):
    native.initial_dispatch = True
    yield from service.launch_case.__wrapped__(native, monkeypatch, mapped)


def test_original_request_and_dispatches_join_actual_native_lifetime(
    dispatched, mapped, staged, monkeypatch
):
    recovery.run_recovery(dispatched, mapped, staged, monkeypatch, initial=True)
