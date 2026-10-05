#!/usr/bin/env python3
"""Read-only source inventory for the uninstalled App controller libraries.

This is a distinct closed bundle, not an active entrypoint or installed-image
qualification. Observed code is hashed, never imported. Legacy preparation
profiles and their command policies remain unchanged. Interpreter, dependencies,
input provenance, supervision and actual lifetime checks are separate gates.
The inventory itself is stdlib-only. Importing the controller/native libraries
also requires PySerial; hashing this bundle does not qualify that dependency.
"""

from __future__ import annotations

import supplemental_recording_host_source as source

KIND = "finite-recording-app-controller-host-source-v1"
MODULES = source.STARTUP_MODULES | frozenset(
    {
        "qualify_supplemental_recording_app",
        "qualify_supplemental_recording_app_active",
        "qualify_supplemental_recording_app_ready",
        "supplemental_recording_api",
        "supplemental_recording_app_begin",
        "supplemental_recording_app_candidate",
        "supplemental_recording_app_execution",
        "supplemental_recording_app_failure",
        "supplemental_recording_app_finalized",
        "supplemental_recording_app_host_source",
        "supplemental_recording_app_launch",
        "supplemental_recording_app_observation",
        "supplemental_recording_app_publish",
        "supplemental_recording_app_recording",
        "supplemental_recording_app_recovery",
        "supplemental_recording_app_service",
        "supplemental_recording_assembly",
        "supplemental_recording_construction",
        "supplemental_recording_launch_plan",
        "supplemental_recording_receipt_inventory",
        "supplemental_recording_schedule",
    }
)
ROOTS = source.STARTUP_ROOTS | frozenset(
    {
        "supplemental_recording_app_host_source",
        "supplemental_recording_app_service",
        "supplemental_recording_app_recording",
    }
)
HELPER_FILES = frozenset(name + ".py" for name in MODULES)


class Layout(source.Layout):
    """Explicit read-only App inventory; not auto-detected or legacy-compatible."""

    def _profile(self):
        source.require(type(self) is Layout)
        source.require(
            self.startup is False
            and self.permission_probe is False
            and self.service_preparation is False
        )
        return HELPER_FILES, KIND


if __name__ == "__main__":
    raise SystemExit("Read-only App controller source only; no App service enabled.")
