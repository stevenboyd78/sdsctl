#!/usr/bin/env python3
"""Read-only inventory for retained peer handoff libraries, never a launcher.

The closed controller/observer inventory predates the private input, listener,
descriptor delivery and original writer intake joins. This separately tagged
inventory names their full static import closure without enlarging that older
profile. Observed files are hashed, never imported or used as an allowlist.

A distinct handoff expectations kind and an explicit collector opt-in can now
select this read-only comparison. Older declarations, collector defaults,
permissions and commands remain unchanged. A matching digest cannot authenticate
its own input pin, designate an active entrypoint, qualify the outer owner/platform
bound, or grant App actions or exclusive recovery custody. Those are separate gates.
"""

from __future__ import annotations

import supplemental_recording_service_host_source as service

source = service.source
KIND = "finite-recording-peer-handoff-library-source-v1"
MODULES = service.MODULES | frozenset(
    {
        "qualify_supplemental_recording_peer_runtime",
        "supplemental_recording_peer_bootstrap",
        "supplemental_recording_peer_connection",
        "supplemental_recording_peer_delivery",
        "supplemental_recording_peer_host_source",
        "supplemental_recording_peer_inputs",
        "supplemental_recording_peer_listener",
        "supplemental_recording_peer_termination",
        "supplemental_recording_service_runtime_expectations",
        "supplemental_recording_watchdog",
        "supplemental_recording_writer_channel",
    }
)
ROOTS = service.ROOTS | frozenset(
    {
        "supplemental_recording_peer_delivery",
        "supplemental_recording_peer_host_source",
        "supplemental_recording_writer_channel",
    }
)
HELPER_FILES = frozenset(name + ".py" for name in MODULES)


class Layout(source.Layout):
    """An explicitly selected inventory; no discovery, execution or admission."""

    def _profile(self):
        source.require(type(self) is Layout)
        source.require(
            self.startup is False
            and self.permission_probe is False
            and self.service_preparation is False
        )
        return HELPER_FILES, KIND


if __name__ == "__main__":
    raise SystemExit("Read-only peer handoff library source only; no active launch enabled.")
