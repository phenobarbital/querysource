"""FEAT-148 TASK-734 — relative-date keyword resolution parity (Rust raw path vs Cython)."""
import os
import subprocess
import sys

import pytest
from querysource.types.validators import (
    is_valid,
    pg_constants,
    pg_udfs,
    resolve_udf_conditions,
    udf_keywords,
)

from querysource.qs_parsers import _qs_parsers as rs

HINTS = [None, "date", "DATE", "datetime", "timestamp"]
INTEGER_KEYWORDS = {"CURRENT_YEAR", "CURRENT_MONTH"}


def _rust_render(value, hint):
    cd = {"x": hint} if hint else {}
    resolved = resolve_udf_conditions({"x": value}, cd)
    return rs.safe_format_map_validated("{x}", resolved, cd)


@pytest.mark.parametrize("hint", HINTS)
@pytest.mark.parametrize("keyword", udf_keywords() + ["fdom"])
def test_rust_cython_parity_all_keywords(keyword, hint):
    """Cython is_valid() and the pre-resolved Rust path must agree.

    Exception: untyped CURRENT_YEAR / CURRENT_MONTH — Cython quotes the
    integer result (`"'2026'"`), Rust does not (`'2026'`). Compare after
    stripping one level of single quotes for that specific combination
    (spec AC1).
    """
    cy = is_valid("x", keyword, hint)
    r = _rust_render(keyword, hint)
    if keyword.upper() in INTEGER_KEYWORDS and hint is None:
        assert cy.strip("'") == r.strip("'")
    else:
        assert cy == r


def test_resolve_udf_leaves_string_literal_integer_hints():
    """Hints outside {date, datetime, timestamp} must not be resolved."""
    for hint in ("string", "literal", "integer"):
        resolved = resolve_udf_conditions({"x": "TODAY"}, {"x": hint})
        assert resolved["x"] == "TODAY"
    # No hint at all is resolved (None is treated as "resolve").
    resolved = resolve_udf_conditions({"x": "TODAY"}, {})
    assert resolved["x"] != "TODAY"


def test_resolve_udf_does_not_mutate_input():
    """The input dict must be left untouched; non-str values pass through."""
    original = {"x": "TODAY", "y": 5, "z": None}
    snapshot = dict(original)
    resolved = resolve_udf_conditions(original, {"x": "date"})
    assert original == snapshot
    assert resolved is not original
    assert resolved["y"] == 5
    assert resolved["z"] is None
    assert resolved["x"] != "TODAY"


def test_accessors_return_copies():
    """Each accessor call must return an independent list copy."""
    first = udf_keywords()
    first.append("MUTATED")
    second = udf_keywords()
    assert "MUTATED" not in second

    assert pg_constants() == ["CURRENT_DATE", "CURRENT_TIMESTAMP"]
    assert pg_udfs() == ["now()"]

    pgc = pg_constants()
    pgc.append("MUTATED")
    assert "MUTATED" not in pg_constants()


def test_udf_env_override_comma_separated():
    """UDF_LIST / PG_UDF overrides are comma-separated env vars, parsed at import."""
    env = os.environ.copy()
    env["UDF_LIST"] = "today, fdom"
    env["PG_UDF"] = "now(),current_timestamp"
    code = (
        "from querysource.types.validators import udf_keywords, pg_udfs; "
        "print(udf_keywords()); print(pg_udfs())"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    lines = result.stdout.strip().splitlines()
    assert lines[0] == "['TODAY', 'FDOM']"
    assert lines[1] == "['now()', 'current_timestamp']"
