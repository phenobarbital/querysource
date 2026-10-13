"""FEAT-167 TASK-875 — Rust allowlists: exact PG_UDF, env parity, 10-name default (AC3, AC11)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from querysource.qs_parsers import _qs_parsers as rs

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("value", ["pg_sleep(5)", "(select 1)", "lower('x')", "x) OR (1=1"])
def test_rust_is_valid_parenthesis_quoted(value: str) -> None:
    """A '(' no longer bypasses quoting on the raw-query path (AC3)."""
    assert not rs.is_pg_function(value)
    assert rs.is_valid("k", value, None, False) == "'" + value.replace("'", "''") + "'"


def test_rust_pg_udf_default() -> None:
    assert rs.is_pg_function("now()")
    assert rs.is_valid("k", "now()", None, False) == "now()"


def test_rust_default_udf_list_has_week_helpers() -> None:
    assert rs.is_udf("LDOPM") and rs.is_udf("fdow") and rs.is_udf("ldow")
    assert not rs.is_udf("midnight")


def test_rust_env_override_parity() -> None:
    """UDF_LIST / PG_UDF env overrides reach the Rust extension (AC11)."""
    env = os.environ.copy()
    env["UDF_LIST"] = "today, fdom"
    env["PG_UDF"] = "now(),my_fn()"
    env["PG_CONSTANTS"] = "current_date, my_const"
    code = (
        "from querysource.qs_parsers import _qs_parsers as rs; "
        "from querysource.types.validators import udf_keywords, pg_udfs; "
        "print(rs.is_udf('FDOM'), rs.is_udf('LDOM'), rs.is_pg_function('my_fn()'), "
        "rs.is_valid('x', 'my_fn()', None, False)); print(udf_keywords()); print(pg_udfs()); "
        "print(rs.is_pgconstant('my_const'), rs.is_pgconstant('CURRENT_TIMESTAMP'))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        cwd=_PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip().splitlines()
    assert out[-4] == "True False True my_fn()"
    assert out[-3] == "['TODAY', 'FDOM']"
    assert out[-2] == "['now()', 'my_fn()']"
    assert out[-1] == "True False"  # PG_CONSTANTS env replaces the default list
