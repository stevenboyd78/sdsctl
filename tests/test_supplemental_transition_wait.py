"""Research-only withholding; saved structural facts, no hardware/network I/O.

The CrerB7 capture retained nine known tags, not the other two raw records.
Fixtures reconstruct only those known tags and do not invent their values.
"""

import json
from dataclasses import replace

import pytest

from sds200.daemon_display_read_research import DisplayReadKind, _selection
from sds200.exceptions import CommandRejectedError, CommandTimeoutError
from sds200.xml_protocol import ScannerInfoParser

from .test_supplemental_research_launcher import FIRMWARE, launcher
from .test_supplemental_research_launcher import trial as trial

CORE = ("System", "Department", "Site", "SiteFrequency", "TGID", "Property", "DualWatch")


def transition(*, omit=None, extra="", mode="Scan Mode", screen="trunk_scan"):
    records = "".join(f"<{tag}/>" for tag in (*CORE, "MonitorList", "OverWrite") if tag != omit)
    return ScannerInfoParser().parse(
        "PSI", f'<ScannerInfo Mode="{mode}" V_Screen="{screen}">{records}{extra}</ScannerInfo>'
    )


@pytest.fixture
def waiting(trial):
    window, *_ = trial
    window.transition_wait = True
    return trial


@pytest.mark.parametrize("trial", [(True, True)], indirect=True)
def test_exact_transition_withholds_until_two_fresh_matching_observations(waiting):
    window, scanner, cache, clock, psi = waiting
    assert window.arm()
    psi()
    assert cache.poll_once()
    clock.now = 10.6
    psi(transition())
    assert not window.closed and not window.allow_poll()
    before = list(scanner.reads)
    cache.poll_once()  # Scope gate also refuses a worker already scheduled.
    assert scanner.reads == before
    clock.now = 10.8
    psi()
    assert not window.allow_poll()
    clock.now = 11.0
    psi()
    assert window.allow_poll()
    # A refused cache attempt retains pacing; withholding cannot bypass it.
    clock.now = 12
    psi()
    assert cache.poll_once() and len(scanner.reads) == 2
    evidence = window.report()["transition_wait"]
    assert evidence["episodes"] == evidence["recoveries"] == 1
    assert not evidence["waiting"] and window.scan_rejection is None
    assert evidence["first_observation"]["violations"] == ["mode_mismatch"]
    assert window.timing_report()["transition_wait"] == evidence
    evidence["first_observation"]["violations"].clear()
    assert window.report()["transition_wait"]["first_observation"]["violations"]


@pytest.mark.parametrize("trial", [(True, True)], indirect=True)
@pytest.mark.parametrize("missing", CORE)
def test_missing_core_records_remain_terminal(waiting, missing):
    window, scanner, _, _, psi = waiting
    assert window.arm()
    psi()
    psi(transition(omit=missing))
    assert window.closed and window.failure == "scan_context_changed"
    assert not scanner.reads


@pytest.mark.parametrize("trial", [(True, True)], indirect=True)
@pytest.mark.parametrize(
    "tag", sorted(launcher.EXCLUDED_SCAN_TAGS | {"ConvFrequency", "System", "TGID"})
)
def test_excluded_and_duplicate_records_are_never_paused(waiting, tag):
    window, scanner, _, _, psi = waiting
    assert window.arm()
    psi()
    psi(transition(extra=f"<{tag}/>"))
    assert window.closed and not scanner.reads


@pytest.mark.parametrize("trial", [(True, True)], indirect=True)
@pytest.mark.parametrize("mode", ["Scan Hold", "Close Call", "PRIVATE", "", "Scan Mode "])
def test_no_held_close_call_unknown_or_nonexact_exception(waiting, mode):
    window, scanner, _, _, psi = waiting
    assert window.arm()
    psi()
    psi(transition(mode=mode))
    assert window.closed and not scanner.reads
    assert "PRIVATE" not in json.dumps(window.report())


@pytest.mark.parametrize("trial", [(True, True)], indirect=True)
def test_disabled_policy_remains_terminal_and_selector_never_admits_transition(trial):
    window, _, _, _, psi = trial
    assert window.arm()
    psi()
    info = transition()
    assert all(_selection(info, kind) is None for kind in DisplayReadKind)
    psi(info)
    assert window.closed and window.failure == "scan_context_changed"


@pytest.mark.parametrize("trial", [(True, True)], indirect=True)
@pytest.mark.parametrize("stage", ["before_arm", "no_prior_psi", "inflight", "active_poll"])
def test_ambiguous_start_or_read_race_stays_terminal(waiting, stage):
    window, scanner, _, _, psi = waiting
    if stage != "before_arm":
        assert window.arm()
    if stage != "no_prior_psi":
        psi()
    window.inflight = int(stage == "inflight")
    window.timing_poll_active = stage == "active_poll"
    psi(transition())
    assert window.closed and not scanner.reads
    psi()
    assert not window.allow_poll()
    with pytest.raises(ValueError, match="rearmed"):
        window.arm()


@pytest.mark.parametrize("trial", [(True, True)], indirect=True)
@pytest.mark.parametrize("action", ["silence", "repeat", "stutter", "late_recovery"])
def test_transition_deadline_never_renews_and_no_post_read_success(waiting, action):
    window, scanner, _, clock, psi = waiting
    assert window.arm()
    psi()
    clock.now = 10.5
    psi(transition())
    if action in {"repeat", "stutter"}:
        clock.now = 11
        if action == "stutter":
            psi()
        psi(transition())
    clock.now = 12.001
    if action == "late_recovery":
        psi()
    assert not window.allow_poll()
    assert window.closed and window.failure == "scan_transition_timeout"
    assert window.report()["status"] == "qualification_unconfirmed"
    assert not scanner.reads


@pytest.mark.parametrize("trial", [(True, True)], indirect=True)
def test_first_wait_does_not_replace_first_terminal_fault(waiting):
    window, _, _, clock, psi = waiting
    assert window.arm()
    psi()
    psi(transition())
    first = window.report()["transition_wait"]["first_observation"]
    window.observe_psi(None)
    rejection = window.report()["scan_rejection"]
    clock.now += 0.2
    psi()
    psi(transition())
    assert window.report()["scan_rejection"] == rejection
    assert window.report()["transition_wait"]["first_observation"] == first
    assert window.failure == "scan_context_changed" and not window.allow_poll()


@pytest.mark.parametrize("trial", [(True, True)], indirect=True)
@pytest.mark.parametrize("error", [CommandRejectedError, CommandTimeoutError])
def test_existing_read_fault_cannot_be_hidden_by_withholding(waiting, error):
    window, scanner, cache, clock, psi = waiting
    assert window.arm()
    psi()

    def fail(_command):
        raise error("PRIVATE")

    scanner.reply = fail
    assert cache.poll_once()
    clock.now += 0.1
    psi(transition())
    assert window.closed and window.failure == "read_unconfirmed"
    assert window.report()["read_failure"] is not None
    psi()
    psi()
    assert not window.allow_poll() and len(scanner.reads) == 1


@pytest.mark.parametrize("trial", [(True, True)], indirect=True)
def test_sixty_reads_with_bounded_withholding_preserve_original_limits(waiting):
    window, scanner, cache, clock, psi = waiting
    assert window.arm()
    for index in range(60):
        clock.now = 10 + (index // 2) * 2 + (index % 2) * 0.5
        psi()
        assert cache.poll_once()
        if index % 10 == 0:
            clock.now += 0.05
            psi(transition())
            assert not window.allow_poll()
            clock.now += 0.05
            psi()
            assert not window.allow_poll()
            clock.now += 0.05
            psi()
            assert window.allow_poll()
    psi()
    psi()
    assert window.report()["status"] == "replies_and_psi_observed"
    assert window.report()["transition_wait"]["recoveries"] == 6
    assert len(scanner.reads) == 60 and not window.allow_poll()
    assert window.started == 10 and window.window_seconds == 64
    assert window.max_psi_gap <= 2 and not window.timing_overflow
    assert "PRIVATE" not in json.dumps(window.report())


@pytest.mark.parametrize("trial", [(True, True)], indirect=True)
def test_other_family_and_non_psi_are_not_transition_exceptions(waiting):
    window, _, _, _, psi = waiting
    assert window.arm()
    psi()
    window.observe_psi(replace(transition(), command="GSI"))
    assert window.closed


@pytest.mark.parametrize("trial", [(True, True)], indirect=True)
def test_fixed_overall_deadline_is_not_extended_by_wait_or_recovery(waiting):
    window, scanner, _, clock, psi = waiting
    assert window.arm()
    # Qualified PSI without reads reaches the end of the original window.
    for when in range(11, 74):
        clock.now = when
        psi()
    clock.now = 73.2
    psi(transition())
    clock.now = 73.4
    psi()
    clock.now = 74
    psi()
    assert not window.allow_poll() and not scanner.reads
    assert window.started == 10 and window.window_seconds == 64


@pytest.mark.parametrize("trial", [(True, True)], indirect=True)
def test_late_transition_cannot_reuse_final_post_read_success(waiting):
    window, _, _, _, psi = waiting
    assert window.arm()
    psi()
    window.opportunities = 60
    window.post_read_psi = 2
    psi(transition())
    assert window.post_read_psi == 0
    assert window.report()["status"] == "qualification_unconfirmed"
    psi()
    assert window.post_read_psi == 0
    psi()
    assert window.post_read_psi == 1


@pytest.mark.parametrize("trial", [(True, True)], indirect=True)
def test_transition_during_actual_read_finishes_once_but_cannot_resume(waiting):
    window, scanner, cache, clock, psi = waiting
    assert window.arm()
    psi()
    original = scanner.reply

    def overlap(command):
        psi(transition())
        return original(command)

    scanner.reply = overlap
    assert cache.poll_once()
    assert window.closed and window.failure == "scan_context_changed"
    clock.now += 1
    psi()
    psi()
    cache.poll_once()
    assert len(scanner.reads) == 1 and not window.allow_poll()


@pytest.mark.parametrize("trial", [(True, True)], indirect=True)
def test_disconnect_while_withheld_remains_terminal(waiting):
    window, scanner, _, _, psi = waiting
    assert window.arm()
    psi()
    psi(transition())
    window.observe_connection(False)
    window.observe_connection(True)
    psi()
    psi()
    assert not window.allow_poll() and not scanner.reads
    assert window.failure == "connection_changed"


@pytest.mark.parametrize("trial", [(True, True)], indirect=True)
def test_diagnostic_exception_cannot_allow_a_paused_read(waiting, monkeypatch):
    window, scanner, _, _, psi = waiting
    assert window.arm()
    psi()

    def fail(_info):
        raise RuntimeError("PRIVATE")

    monkeypatch.setattr(launcher, "scan_context_shape", fail)
    psi(transition())
    assert window.closed and not scanner.reads
    assert window.scan_rejection["violations"] == ["diagnostic_unavailable"]
    assert "PRIVATE" not in json.dumps(window.report())


@pytest.mark.parametrize("value", [None, 0, 1, "true", True])
def test_new_policy_requires_explicit_continuity_timing_and_boolean(tmp_path, value):
    with pytest.raises(ValueError, match="Transition"):
        launcher.ReadWindow(object(), FIRMWARE, transition_wait=value)
    with pytest.raises(ValueError, match="Transition"):
        launcher.SupplementalTrigger(tmp_path / "case", FIRMWARE, transition_wait=value)


def test_valid_policy_is_explicit_and_pinned_in_readiness(tmp_path):
    window = launcher.ReadWindow(
        object(), FIRMWARE, continuity=True, timing=True, transition_wait=True
    )
    trigger = launcher.SupplementalTrigger(
        tmp_path / "case", FIRMWARE, continuity=True, timing=True, transition_wait=True
    )
    trigger.write("policy.json", {})
    policy = json.loads((tmp_path / "case/policy.json").read_text())
    assert window.transition_wait and not window.allow_poll()
    assert policy["scan_transition_wait_enabled"] is True
    assert policy["scan_transition_recovery_psi"] == 2
    assert policy["read_kind"] == "shared-clock-favorites-transition-wait"
    assert policy["max_opportunities"] == 60 and policy["window_seconds"] == 64
    assert policy["max_psi_gap_seconds_allowed"] == 2 and policy["timing_event_limit"] == 512


@pytest.mark.parametrize("trial", [(True, True)], indirect=True)
@pytest.mark.parametrize(
    "info",
    [
        transition(extra="<Private/><Private/>"),
        transition(mode="Trunk Scan", screen="conventional_scan"),
        transition(screen="cc_searching"),
        None,
    ],
)
def test_other_unknown_or_cross_family_contexts_stay_terminal(waiting, info):
    window, scanner, _, _, psi = waiting
    assert window.arm()
    psi()
    window.observe_psi(info)
    assert window.closed and not scanner.reads


@pytest.mark.parametrize("trial", [(True, True)], indirect=True)
def test_unknown_single_record_is_not_exported_or_used_for_eligibility(waiting):
    window, _, _, _, psi = waiting
    assert window.arm()
    psi()
    psi(transition(extra='<PrivateTag Name="SECRET"/>'))
    report = window.report()
    assert not window.closed and not window.allow_poll()
    assert "PrivateTag" not in json.dumps(report) and "SECRET" not in json.dumps(report)


@pytest.mark.parametrize("trial", [(True, True)], indirect=True)
def test_transition_chatter_hits_existing_metadata_limit_and_stays_closed(waiting):
    window, scanner, _, _, psi = waiting
    assert window.arm()
    psi()
    for _ in range(600):
        psi(transition())
        psi()
        psi()
    assert window.closed and window.timing_overflow
    assert len(window.timing_events) == 512
    assert window.failure == "timing_overflow" and not scanner.reads
    assert window.transition_episodes <= 256


@pytest.mark.parametrize("trial", [(True, True)], indirect=True)
def test_trigger_cannot_silently_enable_a_different_window_policy(tmp_path, trial):
    window, scanner, *_ = trial
    trigger = launcher.SupplementalTrigger(
        tmp_path / "case", FIRMWARE, continuity=True, timing=True, transition_wait=True
    )
    trigger.window = window
    with pytest.raises(ValueError, match="exact supplemental reader owner"):
        trigger.invoke(window.runtime)
    assert not window.attempted and not scanner.reads
