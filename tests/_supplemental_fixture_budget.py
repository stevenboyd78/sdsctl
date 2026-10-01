"""Recover the exact integer durations used to construct synthetic test plans."""


def integer_budget(limits):
    # (issued + integer) - issued can be one ulp BELOW the integer when the
    # absolute clock crosses a float exponent boundary. int() would truncate a
    # whole second. Require exact forward reconstruction, not a tolerance that
    # might silently reinterpret a genuinely fractional duration.
    issued = limits["issued_at"]
    result = {}
    for target, field in (("ready_by", "ready_seconds"), ("stop_by", "stop_seconds")):
        duration = round(limits[target] - issued)
        assert duration > 0 and issued + duration == limits[target]
        result[field] = duration
    return result
