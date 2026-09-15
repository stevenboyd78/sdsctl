"""Exercise real pytest capture teardown across consecutive CLI-style tests."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def test_package_logging_is_isolated_across_capture_lifetimes(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    shutil.copyfile(root / "tests" / "conftest.py", tmp_path / "conftest.py")
    test_file = tmp_path / "test_capture_lifecycle.py"
    test_file.write_text(
        '''import io
import logging
from sds200.logging_config import configure_logging

logger = logging.getLogger("sds200")
original = None
retained = None

def setup_module():
    global original, retained
    original = (tuple(logger.handlers), logger.level, logger.propagate, logger.disabled)
    retained = logging.StreamHandler(io.StringIO())
    logger.addHandler(retained)
    logger.setLevel(logging.INFO)
    logger.propagate = True
    logger.disabled = False

def test_first_configures_logging_to_captured_stderr(capsys):
    configure_logging(level_name="DEBUG")
    logger.warning("first capture is live")
    assert "first capture is live" in capsys.readouterr().err

def test_second_has_no_closed_capture_handler(capsys):
    assert not any(getattr(handler.stream, "closed", False)
                   for handler in logger.handlers if hasattr(handler, "stream"))
    assert logger.level == logging.INFO
    assert logger.propagate is True
    logger.warning("second capture is live")
    assert "Logging error" not in capsys.readouterr().err

def teardown_module():
    assert retained in logger.handlers
    assert not retained._closed
    assert logger.level == logging.INFO
    assert logger.propagate is True
    logger.removeHandler(retained)
    retained.close()
    handlers, level, propagate, disabled = original
    logger.handlers[:] = handlers
    logger.setLevel(level)
    logger.propagate = propagate
    logger.disabled = disabled
''',
        encoding="utf-8",
    )
    environment = dict(os.environ, PYTHONPATH=str(root / "src"))
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(test_file)],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 passed" in result.stdout
    assert "Logging error" not in result.stderr
