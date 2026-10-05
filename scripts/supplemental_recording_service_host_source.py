#!/usr/bin/env python3
"""Read-only joint controller/observer inventory, not an active service.

This separate closed bundle includes the original App controller and independent
deadline, App/native/CLI custody and private evidence channel. Hash observed
files without importing them. No existing entrypoint/profile is enlarged and
neither a matching digest nor an import test supplies action consent, installed
runtime/dependency provenance, bounded execution or exclusive recovery custody.
"""

from __future__ import annotations

import supplemental_recording_app_host_source as controller

source = controller.source
KIND = "finite-recording-service-controller-observer-source-v1"
MODULES = controller.MODULES | frozenset(
    {
        "supplemental_recording_control",
        "supplemental_recording_service_app_custody",
        "supplemental_recording_service_cli_channel",
        "supplemental_recording_service_cli_custody",
        "supplemental_recording_service_deadline",
        "supplemental_recording_service_host_source",
        "supplemental_recording_service_native_custody",
    }
)
ROOTS = controller.ROOTS | frozenset(
    {
        "supplemental_recording_service_cli_channel",
        "supplemental_recording_service_host_source",
        "supplemental_recording_service_native_custody",
    }
)
HELPER_FILES = frozenset(name + ".py" for name in MODULES)


class Layout(source.Layout):
    """Explicit joint inventory, never a legacy profile or a command selector."""

    def _profile(self):
        source.require(type(self) is Layout)
        source.require(
            self.startup is False
            and self.permission_probe is False
            and self.service_preparation is False
        )
        return HELPER_FILES, KIND


if __name__ == "__main__":
    raise SystemExit("Read-only controller/observer source only; no active service enabled.")
