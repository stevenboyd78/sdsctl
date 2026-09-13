"""The public facade must not tax short-lived, focused native commands."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import sds200


def _fresh(code: str) -> None:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(Path(sds200.__file__).resolve().parent.parent)
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_import_version_and_discovery_do_not_load_public_implementation() -> None:
    _fresh("""
        import sys
        import sds200
        assert isinstance(sds200.__version__, str)
        assert len(sds200.__all__) == len(set(sds200.__all__)) == 1004
        assert set(sds200.__all__) <= set(dir(sds200))
        assert dir(sds200) == sorted(set(dir(sds200)))
        assert not any(name.startswith('sds200.') for name in sys.modules)
        for name in ('no_such_export', '__missing__'):
            assert not hasattr(sds200, name)
            try:
                getattr(sds200, name)
            except AttributeError as error:
                assert 'sds200' in str(error) and name in str(error)
            else:
                raise AssertionError('Unknown export accepted')
        assert not any(name.startswith('sds200.') for name in sys.modules)
    """)


def test_native_reader_import_does_not_load_unrelated_public_features() -> None:
    _fresh("""
        import sys
        from sds200 import browser_device_continuation_current as current
        assert callable(current.inspect_stopped_continuation)
        for name in ('_public', 'audio', 'broadcastify', 'favorites_editor_external_execution',
                     'radioreference', 'tui', 'web_dashboard'):
            assert 'sds200.' + name not in sys.modules, name
    """)


def test_every_export_resolves_to_its_original_module_object() -> None:
    _fresh("""
        import ast
        import importlib
        from pathlib import Path
        import sds200
        source = Path(sds200.__file__).with_name('_public.py').read_text()
        imports = [node for node in ast.parse(source).body if isinstance(node, ast.ImportFrom)]
        expected = {alias.asname or alias.name: (node.module, alias.name)
                    for node in imports for alias in node.names}
        assert len(expected) == 1004
        assert set(expected) == set(sds200.__all__)
        for name, (module, original) in expected.items():
            actual = getattr(sds200, name)
            assert actual is getattr(importlib.import_module('.' + module, 'sds200'), original)
            assert vars(sds200)[name] is actual
        assert importlib.import_module('sds200._public').__all__ == sds200.__all__
        namespace = {}
        exec('from sds200 import *', namespace)
        assert set(namespace) - {'__builtins__'} == set(expected)
        assert all(namespace[name] is getattr(sds200, name) for name in expected)
    """)


def test_submodule_first_and_concurrent_public_imports_preserve_identity() -> None:
    _fresh("""
        from concurrent.futures import ThreadPoolExecutor
        import pickle
        import sys
        import sds200
        from sds200.scanner import ScannerCapabilities
        assert 'sds200._public' not in sys.modules
        with ThreadPoolExecutor(max_workers=8) as pool:
            values = list(pool.map(lambda _: sds200.ScannerCapabilities, range(32)))
        assert all(value is ScannerCapabilities for value in values)
        assert pickle.loads(pickle.dumps(ScannerCapabilities)) is ScannerCapabilities
        assert sds200.ScannerCapabilities.__module__ == 'sds200.scanner'
    """)


def test_failed_deferred_import_does_not_publish_partial_exports() -> None:
    _fresh("""
        import sds200
        original = sds200._import_module
        def unavailable(*args):
            raise ImportError('deliberately unavailable')
        sds200._import_module = unavailable
        try:
            sds200.ScannerModel
        except ImportError:
            pass
        else:
            raise AssertionError('Import failure swallowed')
        assert not set(sds200.__all__) & vars(sds200).keys()
        sds200._import_module = original
        from sds200.scanner import ScannerModel
        assert sds200.ScannerModel is ScannerModel
    """)
