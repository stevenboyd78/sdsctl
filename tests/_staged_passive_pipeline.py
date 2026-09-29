"""Fixed TEST-ONLY staged outer entrypoint, never an installed launcher.

The parent stages reviewed checkout code and independently records its hashes
BEFORE this exec. A Python audit hook covers ordinary imports AND direct spec
loaders in this outer. Test scaffolding, interpreter/dependencies, child exec
provenance and Engine/runtime/publication facts are still synthetic. This is
not a security sandbox or production source-admission mechanism.
"""

import hashlib
import json
import os
import sys
from pathlib import Path

PREFIXES = ("supplemental_", "qualify_supplemental_", "accept_supplemental_")


class FixtureSourceRefused(ImportError):
    pass


def main():
    root, manifest, mode, target = sys.argv[1:]
    root = Path(root)
    expected = json.loads(Path(manifest).read_bytes())
    scripts, product = root / "scripts", root / "src/sds200"
    observed = set()

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

    sys.addaudithook(guard)
    # Keep the original checkout visible AFTER the staged roots to exercise
    # refusal, not merely make fallback impossible by hiding every alternative.
    sys.path[:0] = [str(root), str(root / "src"), str(scripts)]
    if mode == "probe":
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
