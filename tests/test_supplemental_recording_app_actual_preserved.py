"""Actual recording files after explicit host abandonment, not successful completion.

The native fixture finishes normally but drops completion/exit returns; the
original service explicitly abandons after its confirmed start. Actual independent
worker/init exits and unchanged files are required for the preserved route.
App/CLI/health facts remain synthetic. No installed or live-device acceptance.
"""

import pytest

from . import test_supplemental_recording_app_actual_pristine as closed

bwrap, staged, mapped = closed.bwrap, closed.staged, closed.mapped
layout, image_umask, supervised = closed.layout, closed.image_umask, closed.supervised
image, configured = closed.image, closed.configured
candidate, app, native = closed.candidate, closed.app, closed.native
launch_case, driver_case, dispatched = closed.launch_case, closed.driver_case, closed.dispatched
pytestmark = closed.pytestmark


@pytest.mark.parametrize("fault", [None, "live_init", "new_file"])
def test_original_service_retains_actual_recording_without_claiming_success(
    dispatched, mapped, staged, monkeypatch, fault
):
    closed.run_closed(dispatched, mapped, staged, monkeypatch, fault=fault, preserved=True)
