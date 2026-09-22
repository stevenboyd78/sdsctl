"""Real private journal, fake bounded I/O: never contacts HA or a scanner."""

import importlib.util
import sys
from dataclasses import asdict, replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "scripts"
# Load one shared policy identity for the executor's strict dataclass checks.
for name in ("supplemental_handoff_policy", "supplemental_handoff_executor"):
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, ROOT / (name + ".py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
p = sys.modules["supplemental_handoff_policy"]
e = sys.modules["supplemental_handoff_executor"]
CASE = "f1193f32983a4ce692b585fcdd058841"
BOOT = "aaaaaaaaaaaa4aaa8aaaaaaaaaaaaaaa"
NORMAL = p.App("a" * 64, "running", "b" * 64, True, False)
CANDIDATE = p.App("c" * 64, "stopped")
STOPPED = p.App("a" * 64, "stopped")
RUNNING = p.App("c" * 64, "running", "d" * 64, True, False)
RESTORED = replace(NORMAL, generation="e" * 64)


def sample(now=12, normal=NORMAL, candidate=CANDIDATE, **kwargs):
    return e.Sample(BOOT, now, p.Observation(now, normal, candidate, True, True, True, **kwargs))


class IO:
    def __init__(self, *values):
        self.values = list(values)
        self.sent = []
        self.failure = None

    def read(self):
        value = self.values.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    def send(self, command, case):
        self.sent.append((command, case))
        if self.failure:
            raise self.failure


@pytest.fixture
def journal(tmp_path):
    path = tmp_path / "case"
    path.mkdir(mode=0o700)
    with p.Journal(path) as j:
        first = sample(10)
        j.append(
            {
                "kind": "prepare",
                "case_id": CASE,
                "boot_id": BOOT,
                "now": 10,
                "observation": asdict(first.observation),
            }
        )
        j.append({"kind": "request", "boot_id": BOOT, "now": 11})
        yield j


def test_every_command_needs_a_new_intent_then_matching_fresh_observation(journal):
    pairs = [
        sample(),
        sample(12.1),
        sample(13, STOPPED),
        sample(13.1, STOPPED),
        sample(14, STOPPED, RUNNING),
    ]
    io = IO(*pairs)
    ex = e.Executor(journal, io.read, io.send)
    assert ex.poll().outcome == "dispatch_submitted"
    assert ex.poll().outcome == "dispatch_submitted"
    assert ex.poll().phase == "candidate_running"
    journal.append({"kind": "finish", "now": 15, "boot_id": BOOT})
    io.values += [
        sample(16, STOPPED, RUNNING),
        sample(16.1, STOPPED, RUNNING),
        sample(17, STOPPED),
        sample(17.1, STOPPED),
        sample(18, RESTORED),
    ]
    assert ex.poll().outcome == "dispatch_submitted"
    assert ex.poll().outcome == "dispatch_submitted"
    assert ex.poll().phase == "complete"
    assert ex.poll().outcome == "terminal"
    assert [(cmd[2], cmd[3]) for cmd, _ in io.sent] == [
        ("stop", p.NORMAL),
        ("start", p.CANDIDATE),
        ("stop", p.CANDIDATE),
        ("start", p.NORMAL),
    ]
    assert all(
        cmd[:2] == ("ha", "apps") and cmd[-1] == "--raw-json" and case == CASE
        for cmd, case in io.sent
    )


def test_intent_is_durable_before_dispatch_and_restart_never_resends(journal):
    io = IO(sample(), sample(12.1))

    def send(command, case):
        # A callback failure can happen after the remote operation already began.
        assert (journal.path / "0002.json").is_file()
        io.send(command, case)
        raise RuntimeError("private adapter error must not be returned")

    result = e.Executor(journal, io.read, send).poll()
    assert result.outcome == "dispatch_unconfirmed" and "private" not in repr(result)
    path = journal.path
    journal.close()
    with p.Journal(path) as recovered:
        io.values = [sample(13)]
        assert e.Executor(recovered, io.read, io.send).poll().outcome == "observed"
        assert len(io.sent) == 1


@pytest.mark.parametrize(
    "change",
    [
        lambda s: replace(s, boot_id="bbbbbbbbbbbb4bbb8bbbbbbbbbbbbbbb"),
        lambda s: sample(15),
        lambda s: sample(11.9),
        lambda s: replace(s, observation=replace(s.observation, jobs_idle=False)),
        lambda s: replace(s, observation=replace(s.observation, other_owners_stopped=False)),
        lambda s: replace(s, observation=replace(s.observation, core_running=False)),
        lambda s: sample(12.1, normal=replace(NORMAL, recording=True)),
        lambda s: sample(12.1, normal=replace(NORMAL, pin="f" * 64)),
        lambda s: sample(12.1, normal=replace(NORMAL, generation="f" * 64)),
        lambda s: sample(12.1, normal=replace(NORMAL, healthy=None)),
        lambda s: sample(12.1, candidate=RUNNING),
        lambda s: None,
        lambda s: TimeoutError("sensitive exception"),
    ],
)
def test_pre_dispatch_change_withholds_consumed_intent(journal, change):
    io = IO(sample(), change(sample(12.1)))
    ex = e.Executor(journal, io.read, io.send)
    assert ex.poll().outcome == "intent_withheld"
    assert journal.machine.state.phase == "stopping_normal" and len(journal.entries) == 3
    io.values = [sample(16)]
    assert ex.poll().outcome == "observed" and not io.sent


@pytest.mark.parametrize("failure", [TimeoutError("private"), ValueError("private"), None, {}])
def test_no_observation_does_not_create_intent(journal, failure):
    io = IO(failure)
    assert e.Executor(journal, io.read, io.send).poll().outcome == "observation_unavailable"
    assert len(journal.entries) == 2 and not io.sent


def test_persist_failure_never_dispatches(journal, monkeypatch):
    io = IO(sample(), sample(12.1))

    def fail(_event):
        raise OSError("disk unavailable")

    monkeypatch.setattr(journal, "append", fail)
    with pytest.raises(OSError):
        e.Executor(journal, io.read, io.send).poll()
    assert not io.sent and len(io.values) == 1


def test_reentrant_poll_refused_without_second_command(journal):
    io = IO(sample(), sample(12.1))

    def send(command, case):
        with pytest.raises(p.UnsafeHandoff):
            ex.poll()
        io.send(command, case)

    ex = e.Executor(journal, io.read, send)
    assert ex.poll().outcome == "dispatch_submitted" and len(io.sent) == 1


def test_unknown_dispatch_can_reconcile_but_not_assume_success(journal):
    io = IO(sample(), sample(12.1))
    io.failure = TimeoutError("reply lost")
    ex = e.Executor(journal, io.read, io.send)
    assert ex.poll() == e.Result("stopping_normal", "dispatch_unconfirmed")
    io.failure = None
    # The next independent observations establish exit before start is allowed.
    io.values = [sample(13, STOPPED), sample(13.1, STOPPED)]
    assert ex.poll() == e.Result("starting_candidate", "dispatch_submitted")
    assert [cmd[2] for cmd, _ in io.sent] == ["stop", "start"]


@pytest.mark.parametrize(
    "operation,slug",
    [
        ("rebuild", p.NORMAL),
        ("stop", "core"),
        ("start", p.CANDIDATE + ";true"),
        ("restart", p.CANDIDATE),
    ],
)
def test_only_fixed_cli_targets_and_operations_allowed(operation, slug):
    with pytest.raises(p.UnsafeHandoff):
        e.cli_arguments(p.Action(operation, slug, CASE, "a" * 64))


@pytest.mark.parametrize("now", [-1, True, float("nan"), float("inf")])
def test_invalid_sample_times_refused(now):
    with pytest.raises(p.UnsafeHandoff):
        e.Sample(BOOT, now, sample().observation)


def test_stale_sample_refused():
    with pytest.raises(p.UnsafeHandoff):
        e.Sample(BOOT, 15, sample().observation)


def test_crash_after_intent_before_dispatch_is_not_replayed(journal):
    io = IO(sample(), SystemExit("simulated abrupt exit"))
    with pytest.raises(SystemExit):
        e.Executor(journal, io.read, io.send).poll()
    path = journal.path
    journal.close()
    with p.Journal(path) as recovered:
        io.values = [sample(13)]
        result = e.Executor(recovered, io.read, io.send).poll()
        assert result.phase == "stopping_normal" and not io.sent


def test_replaced_journal_withholds_new_intent(journal):
    original = journal.path
    moved = original.with_name("preserved-case")
    io = IO(sample())

    def read():
        if io.values:
            return io.read()
        original.rename(moved)
        original.mkdir(mode=0o700)
        return sample(12.1)

    assert e.Executor(journal, read, io.send).poll().outcome == "intent_withheld"
    assert not io.sent
    assert (moved / "0002.json").is_file()
    assert list(original.iterdir()) == []


def test_withheld_intent_eventually_reaches_review_without_retry(journal):
    io = IO(sample(), TimeoutError())
    ex = e.Executor(journal, io.read, io.send)
    assert ex.poll().outcome == "intent_withheld"
    io.values = [sample(journal.machine.state.deadline)]
    assert ex.poll().phase == "review"
    assert ex.poll().outcome == "terminal"
    assert not io.sent
