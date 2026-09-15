from __future__ import annotations

import logging
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

import pytest

from sds200 import cli
from sds200.configuration import (
    ENVIRONMENT_CONFIGURATION_VARIABLES,
    ApplicationConfiguration,
    ConfigurationPaths,
    ResolvedApplicationConfiguration,
    resolve_configuration_paths,
)


@pytest.fixture(autouse=True)
def isolate_package_logging() -> Iterator[None]:
    """Do not carry CLI handlers bound to a test's captured stderr into the next.

    Detach existing handlers before configure_logging can close them; restore
    them afterwards without disturbing pytest's root/caplog handlers.
    """
    logger = logging.getLogger("sds200")
    handlers = tuple(logger.handlers)
    filters = tuple(logger.filters)
    level, propagate, disabled = logger.level, logger.propagate, logger.disabled
    for handler in handlers:
        logger.removeHandler(handler)
    try:
        yield
    finally:
        for handler in tuple(logger.handlers):
            logger.removeHandler(handler)
            if handler not in handlers:
                handler.close()
        for handler in handlers:
            logger.addHandler(handler)
        logger.filters[:] = filters
        logger.setLevel(level)
        logger.propagate = propagate
        logger.disabled = disabled


@pytest.fixture
def host_timezone(monkeypatch):
    """Select and then restore the process's real local timezone for TUI tests."""

    @contextmanager
    def selected(zone):
        if not hasattr(time, "tzset"):
            pytest.skip("Selecting the host timezone requires time.tzset")
        with monkeypatch.context() as patch:
            patch.setenv("TZ", zone)
            time.tzset()
            try:
                yield
            finally:
                patch.undo()
                time.tzset()

    return selected


@pytest.fixture
def local_timezone_utc(host_timezone):
    with host_timezone("UTC"):
        yield


@pytest.fixture(autouse=True)
def isolate_cli_application_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep CLI tests independent of configuration installed on the host."""

    for _, variable in ENVIRONMENT_CONFIGURATION_VARIABLES:
        monkeypatch.delenv(variable, raising=False)

    isolated_paths = resolve_configuration_paths(
        environ={},
        home=tmp_path / "configuration-home",
        system_config_dir=tmp_path / "configuration-etc" / "sdsctl",
    )
    original_loader = cli.load_application_configuration

    def load_isolated_application_configuration(
        *,
        paths: ConfigurationPaths | None = None,
        environ: Mapping[str, str] | None = None,
        command_line_values: Mapping[str, object] | None = None,
        defaults: ApplicationConfiguration | None = None,
    ) -> ResolvedApplicationConfiguration:
        return original_loader(
            paths=isolated_paths if paths is None else paths,
            environ=environ,
            command_line_values=command_line_values,
            defaults=defaults,
        )

    monkeypatch.setattr(
        cli,
        "load_application_configuration",
        load_isolated_application_configuration,
    )
