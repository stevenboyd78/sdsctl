"""Pure supervised Config.Env/startup comparisons; fake credentials only.

These tests do not authenticate an image/container/process or installed HA App.
"""

import copy

import pytest

from . import test_supplemental_recording_execution as execution
from . import test_supplemental_recording_runtime as runtime

m = runtime.m
TOKEN = "PRIVATE-FAKE-CREDENTIAL-" + "x" * 90
HOSTNAME = "sdsctl-environment-fixture"
TIMEZONE = "America/Denver"


@pytest.fixture
def image():
    return [
        "PATH=/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "PYTHON_VERSION=3.14.7",
        "PYTHON_SHA256=" + "a" * 64,
        "PYTHONDONTWRITEBYTECODE=1",
        "PYTHONUNBUFFERED=1",
    ]


@pytest.fixture
def configured(image):
    return [*image, "TZ=" + TIMEZONE, "SUPERVISOR_TOKEN=" + TOKEN, "HASSIO_TOKEN=" + TOKEN]


def config_pin(values, image, **overrides):
    kwargs = dict(image_environment_sha256=m.environment(image), timezone=TIMEZONE)
    kwargs.update(overrides)
    return m.supervised_environment(values, **kwargs)


def process_bytes(configured, *, fixed_exec):
    parsed = dict(value.split("=", 1) for value in configured)
    parsed.update(HOME="/root", HOSTNAME=HOSTNAME)
    if fixed_exec:
        parsed["PATH"] = m.FIXED_EXEC_PATH
    return b"\0".join((key + "=" + value).encode("ascii") for key, value in parsed.items()) + b"\0"


def process_pin(raw, configured, image, *, fixed_exec=True, **overrides):
    kwargs = dict(
        configured=configured,
        configured_sha256=config_pin(configured, image),
        image_environment_sha256=m.environment(image),
        timezone=TIMEZONE,
        hostname=HOSTNAME,
        fixed_exec=fixed_exec,
    )
    kwargs.update(overrides)
    return m.supervised_process_environment(raw, **kwargs)


def replace_value(values, key, value):
    return [key + "=" + value if entry.startswith(key + "=") else entry for entry in values]


def test_explicit_profile_does_not_change_old_image_profile(configured, image, capsys):
    before = copy.deepcopy((image, configured))
    result = config_pin(configured, image)
    assert len(result) == 64 and TOKEN not in result
    assert result == config_pin(list(reversed(configured)), image)
    assert (image, configured) == before
    runtime.denied(lambda: m.environment(configured))
    runtime.denied(lambda: config_pin(image, image))
    assert m.environment(image) == m.checksum(
        {
            "schema": 1,
            "kind": m.KIND + "-environment",
            "values": dict(entry.split("=", 1) for entry in image),
        }
    )
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("key", ["SUPERVISOR_TOKEN", "HASSIO_TOKEN"])
def test_each_opaque_token_is_pinned_without_requiring_equality(configured, image, key):
    changed = replace_value(configured, key, "DIFFERENT-FAKE-" + "y" * 60)
    assert config_pin(changed, image) != config_pin(configured, image)
    raw = process_bytes(changed, fixed_exec=True)
    runtime.denied(lambda: process_pin(raw, configured, image))
    runtime.denied(
        lambda: process_pin(raw, changed, image, configured_sha256=config_pin(configured, image))
    )


@pytest.mark.parametrize(
    "key,value",
    [
        ("PATH", "/usr/local/bin:/usr/bin:/bin"),
        ("PYTHON_VERSION", "3.14.8"),
        ("PYTHON_SHA256", "b" * 64),
        ("PYTHONDONTWRITEBYTECODE", "0"),
        ("PYTHONUNBUFFERED", "0"),
    ],
)
def test_all_image_values_must_match_independent_pin(configured, image, key, value):
    runtime.denied(lambda: config_pin(replace_value(configured, key, value), image))


@pytest.mark.parametrize(
    "fault",
    ["extra", "replacement", "duplicate", "missing", "object", "tuple", "null", "boolean"],
)
def test_config_closed_shape_before_any_filtering(configured, image, fault):
    values = list(configured)
    if fault == "extra":
        values.append("LD_PRELOAD=PRIVATE")
    elif fault == "replacement":
        values[-1] = "LD_PRELOAD=PRIVATE"
    elif fault == "duplicate":
        values[-1] = values[-2]
    elif fault == "missing":
        values.pop()
    elif fault == "object":
        values = dict(entry.split("=", 1) for entry in values)
    elif fault == "tuple":
        values = tuple(values)
    elif fault == "null":
        values = None
    else:
        values[0] = True
    runtime.denied(lambda: config_pin(values, image))


@pytest.mark.parametrize("key", ["LD_PRELOAD", "PYTHONPATH", "HOME", "HOSTNAME", "GCONV_PATH"])
def test_config_does_not_accept_runtime_or_loader_additions(configured, image, key):
    runtime.denied(lambda: config_pin([*configured, key + "=PRIVATE"], image))


@pytest.mark.parametrize(
    "value", ["", "x" * 31, "x" * 513, "x" * 32 + " ", "x" * 32 + "\n", "x" * 32 + "é"]
)
@pytest.mark.parametrize("key", ["SUPERVISOR_TOKEN", "HASSIO_TOKEN"])
def test_credentials_are_bounded_opaque_ascii(configured, image, key, value):
    runtime.denied(lambda: config_pin(replace_value(configured, key, value), image))


@pytest.mark.parametrize("value", ["x" * 32, "x" * 512, "a+/=._~-" * 10])
def test_credential_shape_is_not_a_token_format_guess(configured, image, value):
    assert config_pin(replace_value(configured, "SUPERVISOR_TOKEN", value), image)


@pytest.mark.parametrize("timezone", ["UTC", "America/Argentina/Buenos_Aires", "Etc/GMT+7"])
def test_explicit_pinned_timezone_names(configured, image, timezone):
    assert config_pin(replace_value(configured, "TZ", timezone), image, timezone=timezone)


@pytest.mark.parametrize(
    "timezone",
    [
        "",
        "/etc/localtime",
        ":America/Denver",
        "../zone",
        "US//Mountain",
        "A/../B",
        "A ",
        "A" * 65,
        True,
        None,
    ],
)
def test_timezone_is_not_arbitrary_file_or_program(configured, image, timezone):
    runtime.denied(lambda: config_pin(configured, image, timezone=timezone))


def test_different_valid_timezone_is_not_silently_accepted(configured, image):
    runtime.denied(lambda: config_pin(configured, image, timezone="UTC"))
    runtime.denied(lambda: config_pin(replace_value(configured, "TZ", "UTC"), image))


@pytest.mark.parametrize("pin", [None, True, "a" * 63, "A" * 64, "b" * 64])
def test_expected_image_pin_is_required(configured, image, pin):
    runtime.denied(lambda: config_pin(configured, image, image_environment_sha256=pin))


@pytest.mark.parametrize("fixed_exec", [False, True])
def test_exact_process_bytes_compare_without_mutating_or_exposing_inputs(
    configured, image, fixed_exec, capsys
):
    raw = process_bytes(configured, fixed_exec=fixed_exec)
    before = copy.deepcopy((image, configured, raw))
    result = process_pin(raw, configured, image, fixed_exec=fixed_exec)
    reordered = b"\0".join(reversed(raw[:-1].split(b"\0"))) + b"\0"
    assert process_pin(reordered, configured, image, fixed_exec=fixed_exec) == result
    assert len(result) == 64 and TOKEN not in result
    assert (image, configured, raw) == before
    assert capsys.readouterr() == ("", "")


def test_fixed_exec_path_matches_all_existing_closed_command_types():
    command = execution.COMMAND
    for kind in (command, execution.m.ProbeCommand(*command.__dict__.values())):
        assert kind.create_body()["Env"] == ["PATH=" + m.FIXED_EXEC_PATH]
    web = execution.m.WebCommand(*command.__dict__.values(), request_sha256="a" * 64)
    assert web.create_body()["Env"] == ["PATH=" + m.FIXED_EXEC_PATH]


@pytest.mark.parametrize(
    "fault",
    [
        "missing_nul",
        "double_nul",
        "duplicate",
        "missing",
        "extra",
        "replacement",
        "unicode",
        "newline",
        "oversize",
        "string",
        "bytearray",
        "null",
    ],
)
def test_process_bytes_have_exact_closed_shape(configured, image, fault):
    raw = process_bytes(configured, fixed_exec=True)
    parts = raw[:-1].split(b"\0")
    if fault == "missing_nul":
        raw = raw[:-1]
    elif fault == "double_nul":
        raw += b"\0"
    elif fault == "duplicate":
        raw = b"\0".join([*parts[:-1], parts[0]]) + b"\0"
    elif fault == "missing":
        raw = b"\0".join(parts[:-1]) + b"\0"
    elif fault == "extra":
        raw += b"LD_PRELOAD=PRIVATE\0"
    elif fault == "replacement":
        raw = b"\0".join([*parts[:-1], b"PYTHONPATH=PRIVATE"]) + b"\0"
    elif fault == "unicode":
        raw = raw.replace(HOSTNAME.encode(), b"\xff")
    elif fault == "newline":
        raw = raw.replace(HOSTNAME.encode(), b"PRIVATE\n")
    elif fault == "oversize":
        raw = b"x" * 16384 + b"\0"
    elif fault == "string":
        raw = raw.decode("ascii")
    elif fault == "bytearray":
        raw = bytearray(raw)
    else:
        raw = None
    runtime.denied(lambda: process_pin(raw, configured, image))


@pytest.mark.parametrize("fixed_exec", [False, True])
@pytest.mark.parametrize(
    "key,value",
    [
        ("HOME", "/tmp"),
        ("HOSTNAME", "other"),
        ("TZ", "UTC"),
        ("PATH", "/usr/local/bin"),
        ("PYTHON_VERSION", "3.14.8"),
    ],
)
def test_process_never_accepts_an_altered_derived_or_inherited_value(
    configured, image, fixed_exec, key, value
):
    raw = process_bytes(configured, fixed_exec=fixed_exec)
    parts = [item.decode("ascii") for item in raw[:-1].split(b"\0")]
    raw = "\0".join(replace_value(parts, key, value)).encode("ascii") + b"\0"
    runtime.denied(lambda: process_pin(raw, configured, image, fixed_exec=fixed_exec))


@pytest.mark.parametrize("fixed_exec", [False, True])
def test_init_and_exec_path_profiles_cannot_be_interchanged(configured, image, fixed_exec):
    raw = process_bytes(configured, fixed_exec=not fixed_exec)
    runtime.denied(lambda: process_pin(raw, configured, image, fixed_exec=fixed_exec))


@pytest.mark.parametrize("fixed_exec", [None, 0, 1, "true"])
def test_process_profile_requires_explicit_boolean(configured, image, fixed_exec):
    raw = process_bytes(configured, fixed_exec=True)
    runtime.denied(lambda: process_pin(raw, configured, image, fixed_exec=fixed_exec))


@pytest.mark.parametrize("hostname", [None, True, "", "other", "-bad", "bad-", "a/b", "a" * 65])
def test_hostname_requires_exact_independent_pin(configured, image, hostname):
    raw = process_bytes(configured, fixed_exec=True)
    runtime.denied(lambda: process_pin(raw, configured, image, hostname=hostname))


@pytest.mark.parametrize("configured_sha256", [None, True, "x", "a" * 64])
def test_process_requires_original_config_pin(configured, image, configured_sha256):
    raw = process_bytes(configured, fixed_exec=True)
    runtime.denied(lambda: process_pin(raw, configured, image, configured_sha256=configured_sha256))


def test_fault_message_never_exposes_values(configured, image, monkeypatch, capsys):
    def failed(*args, **kwargs):
        raise ValueError(TOKEN)

    monkeypatch.setattr(m, "checksum", failed)
    runtime.denied(
        lambda: m.supervised_environment(
            configured, image_environment_sha256="a" * 64, timezone=TIMEZONE
        )
    )
    assert capsys.readouterr() == ("", "")
