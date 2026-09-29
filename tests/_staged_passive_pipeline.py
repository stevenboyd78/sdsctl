"""Fixed TEST-ONLY staged outer entrypoint, never an installed launcher.

The parent stages reviewed checkout code and independently records its hashes
BEFORE this exec. A Python audit hook covers ordinary imports AND direct spec
loaders in this outer and both original peer processes. Test scaffolding,
interpreter/dependencies, installed command and Engine/runtime/publication
provenance are still synthetic or unqualified. This is
not a security sandbox or production source-admission mechanism.
"""

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

PREFIXES = ("supplemental_", "qualify_supplemental_", "accept_supplemental_")


class FixtureSourceRefused(ImportError):
    pass


def install_source_guard(root, manifest, *, role=None):
    """Same test-only exec check in each separately exec'd original peer.

    A per-process private append log survives native termination; atexit would
    miss a killed observer. It is source evidence, not a cleanup/exit receipt.
    The manifest and bootstrap remain independently staged FIXTURE inputs.
    """
    root = Path(root)
    expected = json.loads(Path(manifest).read_bytes())
    scripts, product = root / "scripts", root / "src/sds200"
    observed = set()
    report = None
    if role is not None:
        assert role in ("writer", "observer")
        descriptor, report = tempfile.mkstemp(prefix=role + "-", dir=root.parent / "peer-sources")
        os.close(descriptor)

    def guard(event, args):
        if event != "exec":
            return
        path = Path(args[0].co_filename)
        private = path.name.startswith(PREFIXES) and path.suffix == ".py"
        is_product = "sds200" in path.parts and path.suffix == ".py"
        if not private and not is_product:
            return
        allowed = scripts / path.name if private else path
        if (
            not path.is_absolute()
            or path != allowed
            or (is_product and not path.is_relative_to(product))
            or path.resolve(strict=True) != path
            or expected.get(str(path)) != hashlib.sha256(path.read_bytes()).hexdigest()
        ):
            raise FixtureSourceRefused("Staged passive fixture source refused")
        if private:
            observed.add(path.stem)
            if report is not None:
                # Open/close per event: no retained descriptor is inherited by
                # peers or counted as a command-owned channel. Names only.
                descriptor = os.open(
                    report, os.O_WRONLY | os.O_APPEND | os.O_CLOEXEC | os.O_NOFOLLOW
                )
                try:
                    record = (path.stem + "\n").encode("ascii")
                    assert os.write(descriptor, record) == len(record)
                finally:
                    os.close(descriptor)

    sys.addaudithook(guard)
    return observed


def main():
    root, manifest, mode, target = sys.argv[1:]
    root = Path(root)
    role = mode.removeprefix("probe-") if mode in ("probe-writer", "probe-observer") else None
    if role is not None:
        (root.parent / "peer-sources").mkdir(mode=0o700)
    observed = install_source_guard(root, manifest, role=role)
    # Keep the original checkout visible AFTER the staged roots to exercise
    # refusal, not merely make fallback impossible by hiding every alternative.
    sys.path[:0] = [str(root), str(root / "src"), str(root / "scripts")]
    if mode in ("probe", "probe-writer", "probe-observer"):
        import importlib.util

        # Direct spec loaders bypass MetaPathFinder. The exec guard must stop
        # this before any candidate code executes, with a fixed private message.
        spec = importlib.util.spec_from_file_location("fixture_private_probe", target)
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except FixtureSourceRefused:
            print('{"source_refused":true}')
            return 75
        raise AssertionError("Unconfirmed private source executed")

    assert mode == "pipeline"
    sys.path.extend([target, str(Path(target) / "scripts"), str(Path(target) / "src")])
    os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    import pytest

    from tests import test_supplemental_recording_qualified_peer_command as command

    # Explicit test-only bootstrap selection, before either child's first
    # private import. No inherited environment or production selector involved.
    command.STAGED_SOURCE_GUARD = (str(root), manifest)
    (root.parent / "peer-sources").mkdir(mode=0o700)

    node = (
        root / "tests/test_supplemental_observer_pipeline.py"
    ).as_posix() + "::test_original_observer_needs_no_plan_handoff_or_completion_stdin"
    result = pytest.main(
        [node, "-q", "-s", "-p", "no:cacheprovider", "--basetemp", str(root.parent / "run")]
    )
    # Counts alone cannot prove equal inventories. Return the exact closed name
    # set; neither names nor hashes represent installed runtime qualification.
    print(json.dumps(dict(staged_outer_result=int(result), modules=sorted(observed))))
    return int(result)


if __name__ == "__main__":
    raise SystemExit(main())
