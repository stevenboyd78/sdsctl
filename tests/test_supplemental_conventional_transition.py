"""OGtd1n redacted structural regression; no raw capture or hardware I/O.

The eighth raw record was not retained. Fixtures reconstruct the seven known
tags and exact documented watch values only, not private attributes or values.
"""

import json
from dataclasses import replace

import pytest

from sds200.daemon_display_read_research import DisplayReadKind, _selection
from sds200.exceptions import CommandTimeoutError
from sds200.xml_protocol import ScannerInfoParser

from .test_supplemental_research_launcher import launcher
from .test_supplemental_research_launcher import trial as trial
from .test_supplemental_transition_wait import transition as trunk_transition

CORE = (
    "System",
    "Department",
    "ConvFrequency",
    "Property",
    "DualWatch",
    "MonitorList",
    "OverWrite",
)


def transition(
    *,
    omit=None,
    extra="",
    mode="Trunk Scan",
    screen="conventional_scan",
    pri="Off",
    cc="Off",
    wx="Priority",
):
    records = "".join(
        (f'<DualWatch PRI="{pri}" CC="{cc}" WX="{wx}"/>' if tag == "DualWatch" else f"<{tag}/>")
        for tag in CORE
        if tag != omit
    )
    return ScannerInfoParser().parse(
        "PSI", f'<ScannerInfo Mode="{mode}" V_Screen="{screen}">{records}{extra}</ScannerInfo>'
    )


@pytest.fixture
def waiting(trial):
    window, *_ = trial
    window.transition_wait = True
    return trial


@pytest.mark.parametrize("trial", [(True, True)], indirect=True)
def test_observed_shape_is_only_withheld_and_requires_two_fresh_valid_psi(waiting):
    window, scanner, cache, clock, psi = waiting
    info = transition()
    for kind in (DisplayReadKind.CLOCK, DisplayReadKind.FAVORITES):
        assert _selection(info, kind) is None
    assert window.arm()
    psi()
    assert cache.poll_once()
    clock.now += 0.1
    psi(info)
    assert not window.closed and not window.allow_poll()
    cache.poll_once()
    assert len(scanner.reads) == 1
    original_deadline = window.transition_deadline
    clock.now += 0.1
    psi()
    assert not window.allow_poll()
    clock.now += 0.1
    psi(info)  # A second mixed report breaks the recovery sequence.
    assert window.transition_deadline == original_deadline
    clock.now += 0.1
    psi()
    assert not window.allow_poll()
    clock.now += 0.1
    psi()
    assert window.allow_poll()
    assert window.transition_recoveries == 1
    assert window.report()["transition_wait"]["first_observation"]["dual_watch"] == {
        "record_status": "single",
        "PRI": "Off",
        "CC": "Off",
        "WX": "Priority",
    }
    assert window.report()["status"] == "qualification_unconfirmed"


@pytest.mark.parametrize("trial", [(True, True)], indirect=True)
@pytest.mark.parametrize(
    "info",
    [
        *(transition(omit=tag) for tag in CORE),
        *(
            transition(extra=f"<{tag}/>")
            for tag in sorted(
                launcher.EXCLUDED_SCAN_TAGS | set(CORE) | {"Site", "SiteFrequency", "TGID"}
            )
        ),
        *(
            transition(mode=mode)
            for mode in ("Trunk Scan Hold", "Close Call", "Trunk Scan ", "PRIVATE", "")
        ),
        transition(screen="trunk_scan"),
        transition(pri="DND"),
        transition(cc="DND"),
        transition(wx="Off"),
        transition(wx="Priority "),
        transition(pri=""),
        transition(extra="<Private/><Private/>"),
        transition(extra="<Private/><Other/>"),
        replace(transition(), command="GSI"),
    ],
)
def test_unobserved_ambiguous_and_other_family_shapes_remain_terminal(waiting, info):
    window, scanner, _, _, psi = waiting
    assert window.arm()
    psi()
    psi(info)
    assert window.closed and window.failure == "scan_context_changed"
    assert not scanner.reads and not window.allow_poll()


@pytest.mark.parametrize("trial", [(True, True)], indirect=True)
@pytest.mark.parametrize("stage", ["before_arm", "no_psi", "inflight", "active_poll", "policy_off"])
def test_new_shape_cannot_bypass_startup_inflight_or_opt_in_gates(waiting, stage):
    window, scanner, _, _, psi = waiting
    if stage != "before_arm":
        assert window.arm()
    if stage != "no_psi":
        psi()
    window.inflight = int(stage == "inflight")
    window.timing_poll_active = stage == "active_poll"
    if stage == "policy_off":
        window.transition_wait = False
    psi(transition())
    assert window.closed and not scanner.reads
    with pytest.raises(ValueError, match="rearmed"):
        window.arm()


@pytest.mark.parametrize("trial", [(True, True)], indirect=True)
@pytest.mark.parametrize("action", ["silence", "repeat", "stutter", "family_change"])
def test_new_shape_does_not_extend_original_gap_deadline(waiting, action):
    window, scanner, _, clock, psi = waiting
    assert window.arm()
    psi()
    clock.now += 0.1
    psi(transition())
    clock.now = 11
    if action == "stutter":
        psi()
    if action != "silence":
        psi(trunk_transition() if action == "family_change" else transition())
    clock.now = 12.001
    assert not window.allow_poll()
    assert window.failure == "scan_transition_timeout" and not scanner.reads


@pytest.mark.parametrize("trial", [(True, True)], indirect=True)
@pytest.mark.parametrize("reason", ["disconnect", "read_failure", "inflight_transition"])
def test_terminal_faults_remain_terminal_for_new_shape(waiting, reason):
    window, scanner, cache, _, psi = waiting
    assert window.arm()
    psi()
    if reason == "read_failure":

        def fail(_command):
            raise CommandTimeoutError("PRIVATE")

        scanner.reply = fail
        assert cache.poll_once()
    elif reason == "inflight_transition":
        original = scanner.reply

        def reply(command):
            psi(transition())
            return original(command)

        scanner.reply = reply
        assert cache.poll_once()
    psi(transition())
    if reason == "disconnect":
        window.observe_connection(False)
    psi()
    psi()
    assert window.closed and not window.allow_poll()
    assert "PRIVATE" not in json.dumps(window.report())


@pytest.mark.parametrize("trial", [(True, True)], indirect=True)
def test_both_transition_shapes_preserve_sixty_read_and_overall_time_limits(waiting):
    window, scanner, cache, clock, psi = waiting
    assert window.arm()
    for index in range(60):
        clock.now = 10 + (index // 2) * 2 + (index % 2) * 0.5
        psi()
        assert cache.poll_once()
        if index % 10 == 0:
            clock.now += 0.05
            psi(transition() if index % 20 else trunk_transition())
            assert not window.allow_poll()
            clock.now += 0.05
            psi()
            assert not window.allow_poll()
            clock.now += 0.05
            psi()
            assert window.allow_poll()
    psi()
    psi()
    assert len(scanner.reads) == 60 and not window.allow_poll()
    assert window.report()["status"] == "replies_and_psi_observed"
    assert window.transition_recoveries == 6
    assert window.started == 10 and window.window_seconds == 64


@pytest.mark.parametrize("trial", [(True, True)], indirect=True)
def test_new_shape_cannot_extend_overall_deadline_with_a_fresh_psi(waiting):
    window, scanner, _, clock, psi = waiting
    assert window.arm()
    clock.now = 73.5
    psi()
    clock.now = 73.6
    psi(transition())
    assert not window.allow_poll() and not window.closed
    assert window.transition_deadline == 75.5
    clock.now = 74.001  # Original 64 seconds wins over the later gap deadline.
    assert not window.allow_poll()
    psi()
    psi()
    assert window.transition_deadline is None
    assert not window.allow_poll() and not scanner.reads
    assert window.report()["status"] == "qualification_unconfirmed"


def test_unretained_single_tag_and_values_are_not_exported():
    info = transition(extra='<PrivateTag Name="SECRET"/>')
    assert launcher.is_withheld_transition(info)
    for kind in (DisplayReadKind.CLOCK, DisplayReadKind.FAVORITES):
        assert _selection(info, kind) is None
    report = json.dumps(launcher.scan_context_shape(info))
    assert "PrivateTag" not in report and "SECRET" not in report
