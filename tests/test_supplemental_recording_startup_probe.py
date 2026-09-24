"""Passive startup target; real private files/namespace, explicitly simulated time."""

import ast
import io
import subprocess
import sys
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_service_input as intake

m = intake.m


@pytest.fixture
def case(tmp_path, monkeypatch):
    root = tmp_path / "private-probe"
    root.mkdir(mode=0o700)
    original = m.plans.clock.read()
    issued = original.boottime_ns / m.plans.clock.NS
    value = intake.fixtures.value() | dict(
        boot=original.boot,
        original_clock=asdict(original) | {"namespace": list(original.namespace)},
        deadlines=dict(
            issued_at=issued,
            ready_by=issued + 300,
            stop_by=issued + 510,
            recover_by=issued + m.plans.base.TOTAL_SECONDS,
        ),
    )
    raw = m.plans.base.encode(value)
    path = root / "plan.json"
    path.write_bytes(raw)
    path.chmod(0o600)
    # An explicit unit-test alias, not an installed case or trusted plan proof.
    monkeypatch.setattr(m.plans.Plan, "root", property(lambda _: root))
    plan = m.plans.load_bytes(raw, m.plans.base.checksum(value))
    state = SimpleNamespace(root=root, plan=plan, elapsed=0, sleeps=[], reads=0)

    def read():
        state.reads += 1
        state.elapsed += 0.001
        delta = round(state.elapsed * m.plans.clock.NS)
        return replace(
            original,
            before_ns=original.before_ns + delta,
            after_ns=original.after_ns + delta,
            boottime_ns=original.boottime_ns + delta,
        )

    def sleep(seconds):
        assert 0 < seconds <= 0.1
        state.sleeps.append(seconds)
        state.elapsed += seconds

    state.read, state.sleep = read, sleep
    monkeypatch.setattr(m.plans.clock, "read", read)
    monkeypatch.setattr(m.time, "sleep", sleep)
    return state


def invoke(case):
    return m.startup_probe(case.root, case.plan.sha256)


def test_probe_retains_original_plan_deadlines_no_receipts_and_no_descriptors(case):
    before_fds = intake.descriptors()
    before = {p.name: (p.read_bytes(), m.files.identity(p.stat())) for p in case.root.iterdir()}
    original = case.plan.raw, case.plan.lease, case.plan.deadlines
    assert invoke(case) == 75  # Never a readiness/recording/restoration success.
    assert 30 <= case.elapsed < 30.01
    assert 0 < len(case.sleeps) <= 301 and case.reads > len(case.sleeps)
    assert original == (case.plan.raw, case.plan.lease, case.plan.deadlines)
    assert before == {
        p.name: (p.read_bytes(), m.files.identity(p.stat())) for p in case.root.iterdir()
    }
    assert intake.descriptors() == before_fds


def test_late_start_never_gets_a_new_thirty_seconds(case):
    case.elapsed = 29
    assert invoke(case) == 75
    assert 30 <= case.elapsed < 30.01 and sum(case.sleeps) < 1


@pytest.mark.parametrize("elapsed", [30, 31, 301])
def test_already_expired_probe_refuses_before_sleep(case, elapsed):
    case.elapsed = elapsed
    before = intake.descriptors()
    with pytest.raises(m.UnconfirmedInput, match=m.MESSAGE):
        invoke(case)
    assert not case.sleeps and intake.descriptors() == before


def test_original_earlier_ready_deadline_wins(case):
    value = m.plans.json.loads(case.plan.raw)
    value["deadlines"]["ready_by"] = value["deadlines"]["issued_at"] + 0.25
    raw = m.plans.base.encode(value)
    (case.root / "plan.json").write_bytes(raw)
    case.plan = m.plans.load_bytes(raw, m.plans.base.checksum(value))
    assert invoke(case) == 75
    assert 0.249 <= case.elapsed < 0.26
    assert sum(case.sleeps) < 0.25


@pytest.mark.parametrize("fault", ["bytes", "replacement", "permissions", "plan_object"])
def test_change_after_first_sleep_is_not_adopted(case, monkeypatch, fault):
    before = intake.descriptors()
    observed = []
    real_recheck = m.CasePlan.recheck

    def remember(original):
        plan = real_recheck(original)
        observed.append(plan)
        return plan

    def mutate(seconds):
        case.sleep(seconds)
        path = case.root / "plan.json"
        if fault == "bytes":
            path.write_bytes(b"PRIVATE invalid plan")
        elif fault == "replacement":
            new = case.root / "replacement"
            new.write_bytes(case.plan.raw)
            new.chmod(0o600)
            new.replace(path)
        elif fault == "permissions":
            path.chmod(0o644)
        else:
            object.__setattr__(observed[-1].deadlines, "stop_by", 9999)

    monkeypatch.setattr(m.CasePlan, "recheck", remember)
    monkeypatch.setattr(m.time, "sleep", mutate)
    with pytest.raises(m.UnconfirmedInput, match=m.MESSAGE):
        invoke(case)
    assert len(case.sleeps) == 1 and intake.descriptors() == before


@pytest.mark.parametrize("fault", ["boot", "namespace", "reverse", "suspend", "read_error"])
def test_clock_fault_during_probe_closes_original_handles(case, monkeypatch, fault):
    before = intake.descriptors()

    def changed():
        value = case.read()
        if not case.sleeps:
            return value
        if fault == "boot":
            return replace(value, boot="b" * 32)
        if fault == "namespace":
            return replace(value, namespace=(0, 123))
        if fault == "reverse":
            return case.plan.original_clock
        if fault == "suspend":
            return replace(value, boottime_ns=value.boottime_ns + m.plans.clock.NS)
        raise OSError("PRIVATE clock contents")

    monkeypatch.setattr(m.plans.clock, "read", changed)
    with pytest.raises(m.plans.clock.UnconfirmedClock, match=m.plans.clock.MESSAGE):
        invoke(case)
    assert len(case.sleeps) == 1 and intake.descriptors() == before


@pytest.mark.parametrize("error", [KeyboardInterrupt, SystemExit, OSError])
def test_interrupted_probe_keeps_evidence_and_closes_owned_handles(case, monkeypatch, error):
    before = intake.descriptors()

    def interrupted(_):
        raise error("PRIVATE")

    monkeypatch.setattr(m.time, "sleep", interrupted)
    with pytest.raises(error):
        invoke(case)
    assert (case.root / "plan.json").read_bytes() == case.plan.raw
    assert intake.descriptors() == before


def test_poll_count_is_a_second_finite_bound(case, monkeypatch):
    monkeypatch.setattr(m.time, "sleep", lambda seconds: case.sleeps.append(seconds))
    before = intake.descriptors()
    with pytest.raises(m.UnconfirmedInput, match=m.MESSAGE):
        invoke(case)
    assert len(case.sleeps) == 301 and intake.descriptors() == before


def test_clock_constructor_failure_closes_retained_plan(case, monkeypatch):
    before = intake.descriptors()

    def fail(_):
        raise m.plans.clock.UnconfirmedClock(m.plans.clock.MESSAGE)

    monkeypatch.setattr(m.plans.clock, "ClockWitness", fail)
    with pytest.raises(m.plans.clock.UnconfirmedClock):
        invoke(case)
    assert not case.sleeps and intake.descriptors() == before


def foreign_plan(case, **updates):
    value = m.plans.json.loads(case.plan.raw)
    value["original_clock"].update(namespace=[0, 123], **updates)
    raw = m.plans.base.encode(value)
    (case.root / "plan.json").write_bytes(raw)
    case.plan = m.plans.load_bytes(raw, m.plans.base.checksum(value))


def test_foreign_plan_is_still_refused_by_default_before_any_sleep(case):
    foreign_plan(case)
    before = intake.descriptors()
    with pytest.raises(m.plans.clock.UnconfirmedClock):
        invoke(case)
    assert not case.sleeps and intake.descriptors() == before


def test_explicit_passive_zero_probe_never_relabels_own_samples(case, monkeypatch):
    foreign_plan(case)
    before = intake.descriptors()
    raw, lease = case.plan.raw, case.plan.lease
    actual_check = m.plans.Plan.check_clock

    def strict(plan, sample):
        assert sample.namespace == plan.original_clock.namespace
        return actual_check(plan, sample)

    monkeypatch.setattr(m.plans.Plan, "check_clock", strict)
    assert m.startup_probe(case.root, case.plan.sha256, zero_offset_probe=True) == 75
    assert 30 <= case.elapsed < 30.01 and intake.descriptors() == before
    assert case.plan.raw == raw and case.plan.lease == lease


@pytest.mark.parametrize("when", [1, 2, 4])
def test_zero_probe_offset_failure_refuses_and_closes_handles(case, monkeypatch, when):
    foreign_plan(case)
    actual, calls = m.time_domain._offsets, []

    def offsets(pid):
        calls.append(pid)
        if len(calls) == when:
            raise m.time_domain.UnconfirmedDomain(m.time_domain.MESSAGE)
        return actual(pid)

    monkeypatch.setattr(m.time_domain, "_offsets", offsets)
    before = intake.descriptors()
    with pytest.raises(m.time_domain.UnconfirmedDomain):
        m.startup_probe(case.root, case.plan.sha256, zero_offset_probe=True)
    assert len(calls) == when and intake.descriptors() == before


@pytest.mark.parametrize("enabled", [0, 1, "yes", None])
def test_probe_mode_requires_an_explicit_boolean(case, enabled):
    with pytest.raises(m.UnconfirmedInput):
        m.startup_probe(case.root, case.plan.sha256, zero_offset_probe=enabled)
    assert not case.sleeps


def test_zero_probe_has_local_cap_even_with_foreign_future_numeric_clock(case):
    value = m.plans.json.loads(case.plan.raw)
    for name in ("before_ns", "boottime_ns", "after_ns"):
        value["original_clock"][name] += 100 * m.plans.clock.NS
    value["original_clock"]["namespace"] = [0, 123]
    value["deadlines"] = {key: seconds + 100 for key, seconds in value["deadlines"].items()}
    raw = m.plans.base.encode(value)
    (case.root / "plan.json").write_bytes(raw)
    case.plan = m.plans.load_bytes(raw, m.plans.base.checksum(value))
    assert m.startup_probe(case.root, case.plan.sha256, zero_offset_probe=True) == 75
    assert 30 <= case.elapsed < 30.02  # Not 130s; no foreign-domain success claim.


def test_equal_but_replaced_decoded_plan_is_refused(case, monkeypatch):
    actual = m.CasePlan.recheck
    calls = []

    def changed(owner):
        plan = actual(owner)
        calls.append(plan)
        return m.plans.load_bytes(plan.raw, plan.sha256) if len(calls) > 2 else plan

    before = intake.descriptors()
    monkeypatch.setattr(m.CasePlan, "recheck", changed)
    with pytest.raises(m.UnconfirmedInput):
        invoke(case)
    assert not case.sleeps and intake.descriptors() == before


@pytest.mark.parametrize("flags", [[], ["-I"], ["-I", "-B"]])
@pytest.mark.parametrize("arguments", [[], ["/PRIVATE", "a" * 64], ["--help"]])
def test_unsealed_real_cli_is_sanitized_and_never_attempts_plan_intake(flags, arguments):
    result = subprocess.run(
        [sys.executable, *flags, m.__file__, *arguments],
        capture_output=True,
        text=True,
        timeout=5,
        cwd="/",
    )
    assert result.returncode == 64 and not result.stdout
    assert result.stderr == m.PROBE_MESSAGE + "\n"


def main_blocks():
    parsed = ast.parse(Path(m.__file__).read_text())
    return [
        item
        for item in parsed.body
        if isinstance(item, ast.If) and ast.unparse(item.test) == "__name__ == '__main__'"
    ]


def test_valid_bootstrap_adds_only_fixed_helper_import_directory():
    fake_sys = SimpleNamespace(
        argv=["probe", "/case", "a" * 64],
        flags=SimpleNamespace(isolated=1, dont_write_bytecode=1),
        stderr=io.StringIO(),
        path=["/stdlib"],
    )
    fake_os = SimpleNamespace(geteuid=lambda: 0, getegid=lambda: 0, getcwd=lambda: "/")
    scope = dict(
        __name__="__main__",
        __file__="/opt/sdsctl-recording-host/supplemental_recording_service_input.py",
        sys=fake_sys,
        os=fake_os,
        Path=Path,
        PROBE_MESSAGE=m.PROBE_MESSAGE,
    )
    code = compile(ast.Module(body=[main_blocks()[0]], type_ignores=[]), "<guard-test>", "exec")
    exec(code, scope)
    assert fake_sys.path == ["/opt/sdsctl-recording-host", "/stdlib"]
    assert not fake_sys.stderr.getvalue()


@pytest.mark.parametrize(
    "fault", ["argv", "isolated", "bytecode", "uid", "gid", "cwd", "path", "cwd_error"]
)
def test_bootstrap_guard_fails_before_private_imports(fault):
    fake_sys = SimpleNamespace(
        argv=["probe", "/case", "a" * 64],
        flags=SimpleNamespace(isolated=1, dont_write_bytecode=1),
        stderr=io.StringIO(),
        path=[],
    )
    fake_os = SimpleNamespace(geteuid=lambda: 0, getegid=lambda: 0, getcwd=lambda: "/")
    location = "/opt/sdsctl-recording-host/supplemental_recording_service_input.py"
    if fault == "argv":
        fake_sys.argv.append("extra")
    elif fault in ("isolated", "bytecode"):
        setattr(fake_sys.flags, "isolated" if fault == "isolated" else "dont_write_bytecode", 0)
    elif fault in ("uid", "gid"):
        setattr(fake_os, "geteuid" if fault == "uid" else "getegid", lambda: 1)
    elif fault == "cwd":
        fake_os.getcwd = lambda: "/PRIVATE"
    elif fault == "path":
        location = "/PRIVATE/supplemental_recording_service_input.py"
    else:

        def no_cwd():
            raise OSError("PRIVATE")

        fake_os.getcwd = no_cwd
    scope = dict(
        __name__="__main__",
        __file__=location,
        sys=fake_sys,
        os=fake_os,
        Path=Path,
        PROBE_MESSAGE=m.PROBE_MESSAGE,
    )
    code = compile(ast.Module(body=[main_blocks()[0]], type_ignores=[]), "<guard-test>", "exec")
    with pytest.raises(SystemExit) as error:
        exec(code, scope)
    assert error.value.code == 64 and not fake_sys.path
    assert fake_sys.stderr.getvalue() == m.PROBE_MESSAGE + "\n"


@pytest.mark.parametrize("root", ["relative", "/case/../other", "/case//path", "/case/"])
def test_main_refuses_noncanonical_root_before_probe(root):
    fake_sys = SimpleNamespace(argv=["probe", root, "a" * 64], stderr=io.StringIO())
    scope = dict(
        __name__="__main__",
        sys=fake_sys,
        Path=Path,
        require=m.require,
        PROBE_MESSAGE=m.PROBE_MESSAGE,
        startup_probe=lambda *_: pytest.fail("Invalid root reached probe"),
    )
    code = compile(ast.Module(body=[main_blocks()[-1]], type_ignores=[]), "<main-test>", "exec")
    with pytest.raises(SystemExit) as error:
        exec(code, scope)
    assert error.value.code == 75 and fake_sys.stderr.getvalue() == m.PROBE_MESSAGE + "\n"


def test_main_sanitizes_intake_exception():
    fake_sys = SimpleNamespace(argv=["probe", "/case", "a" * 64], stderr=io.StringIO())

    def fail(*_):
        raise OSError("PRIVATE filename or plan contents")

    scope = dict(
        __name__="__main__",
        sys=fake_sys,
        Path=Path,
        require=m.require,
        PROBE_MESSAGE=m.PROBE_MESSAGE,
        startup_probe=fail,
    )
    code = compile(ast.Module(body=[main_blocks()[-1]], type_ignores=[]), "<main-test>", "exec")
    with pytest.raises(SystemExit) as error:
        exec(code, scope)
    assert error.value.code == 75 and error.value.__suppress_context__
    assert fake_sys.stderr.getvalue() == m.PROBE_MESSAGE + "\n"
