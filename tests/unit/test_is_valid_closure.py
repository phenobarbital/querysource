"""FEAT-167 TASK-874 — is_valid() generic branch is a closed decision list (AC1, AC3, AC5)."""
from __future__ import annotations

from pathlib import Path

import pytest

from querysource.types.validators import is_valid

_SRC = Path(__file__).resolve().parents[2] / "querysource" / "types" / "validators.pyx"


@pytest.mark.parametrize("name", ["DBHOST", "DBPWD", "DBUSER", "REDIS_HOST", "APP_NAME"])
def test_config_key_is_a_plain_literal(name: str) -> None:
    """A value equal to a navconfig key is quoted as text, never substituted (AC1)."""
    assert is_valid("u", name) == f"'{name}'"
    assert is_valid("d", name, "date") == f"'{name}'"


@pytest.mark.parametrize("name", ["midnight", "now", "days_ago", "uri_exists", "print", "fdate"])
def test_function_name_is_a_plain_literal(name: str) -> None:
    """Non-keyword function names are literals (AC2)."""
    assert is_valid("c", name) == f"'{name}'"


def test_keywords_still_resolve() -> None:
    """Allowlisted relative-date keywords resolve to quoted values (AC2)."""
    for keyword in ("today", "TODAY", "fdom", "ldopm"):
        resolved = is_valid("d", keyword)
        assert resolved != f"'{keyword}'"
        assert resolved.startswith("'") and resolved.endswith("'")


@pytest.mark.parametrize("value", ["pg_sleep(5)", "(select 1)", "lower('x')", "Acme (US)", "x) OR (1=1)"])
def test_parenthesis_value_is_quoted(value: str) -> None:
    """A '(' no longer bypasses quoting (AC3, §8 Q3)."""
    assert is_valid("k", value) == "'" + value.replace("'", "''") + "'"
    assert is_valid("k", value, noquote=True) == value


def test_pg_udf_member_stays_raw() -> None:
    """The default PG_UDF member is the only raw function pass-through."""
    assert is_valid("k", "now()") == "now()"


def test_source_has_no_config_or_print() -> None:
    """AC1/AC5 static guards."""
    src = _SRC.read_text(encoding="utf-8")
    assert "from navconfig import config" not in src
    assert "get_config_var" not in src
    assert "print(" not in src
