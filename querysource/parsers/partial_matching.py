"""Partial-matching operators for ``where_cond`` / ``filter`` dict values (FEAT-180).

One table is the contract for every WHERE builder (Cython and Rust twin in
``rust/src/partial_match.rs``). Builders never list operator names: they look the
name up here and render the entry for their dialect (spec §2 rendered forms).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from ..exceptions import ParserError

CONTAINS_MIN_LENGTH: int = 3
MAX_REGEX_PATTERN_LENGTH: int = 200
LIKE_ESCAPE_CHAR_BANG: str = "!"
NESTED_QUANTIFIER_RE: re.Pattern[str] = re.compile(r"\([^()]*[+*][^()]*\)[+*]")


@dataclass(frozen=True, slots=True)
class PartialMatchOp:
    """One row of the operator table (spec §2 Data Models)."""

    name: str
    kind: str
    negated: bool
    insensitive: bool
    prefix: str
    suffix: str
    escape: bool
    min_length: int


def _op(
    name: str,
    kind: str,
    negated: bool,
    insensitive: bool,
    prefix: str,
    suffix: str,
    escape: bool,
    min_length: int,
) -> PartialMatchOp:
    return PartialMatchOp(name, kind, negated, insensitive, prefix, suffix, escape, min_length)


PARTIAL_MATCH_OPERATORS: dict[str, PartialMatchOp] = {o.name: o for o in (
    _op("like", "like", False, False, "", "", False, 0),
    _op("not_like", "like", True, False, "", "", False, 0),
    _op("ilike", "like", False, True, "", "", False, 0),
    _op("not_ilike", "like", True, True, "", "", False, 0),
    _op("startswith", "like", False, False, "", "%", True, 0),
    _op("not_startswith", "like", True, False, "", "%", True, 0),
    _op("istartswith", "like", False, True, "", "%", True, 0),
    _op("not_istartswith", "like", True, True, "", "%", True, 0),
    _op("endswith", "like", False, False, "%", "", True, 0),
    _op("not_endswith", "like", True, False, "%", "", True, 0),
    _op("iendswith", "like", False, True, "%", "", True, 0),
    _op("not_iendswith", "like", True, True, "%", "", True, 0),
    _op("contains", "like", False, False, "%", "%", True, CONTAINS_MIN_LENGTH),
    _op("not_contains", "like", True, False, "%", "%", True, CONTAINS_MIN_LENGTH),
    _op("icontains", "like", False, True, "%", "%", True, CONTAINS_MIN_LENGTH),
    _op("not_icontains", "like", True, True, "%", "%", True, CONTAINS_MIN_LENGTH),
    _op("regex", "regex", False, False, "", "", False, 0),
    _op("not_regex", "regex", True, False, "", "", False, 0),
    _op("iregex", "regex", False, True, "", "", False, 0),
    _op("not_iregex", "regex", True, True, "", "", False, 0),
)}


def like_escape(value: str) -> str:
    """Escape ``\\``, ``%`` and ``_`` with a backslash (PostgreSQL / BigQuery LIKE)."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def like_escape_bang(value: str) -> str:
    """Escape ``!``, ``%``, ``_`` and ``[`` with ``!`` (generic SQL / SQL Server, ``ESCAPE '!'``)."""
    return value.replace("!", "!!").replace("%", "!%").replace("_", "!_").replace("[", "![")


def build_like_pattern(op: PartialMatchOp, value: str, *, escaper: Callable[[str], str]) -> str:
    """Return ``prefix + (escaper(value) if op.escape else value) + suffix``. Never quotes."""
    return f"{op.prefix}{escaper(value) if op.escape else value}{op.suffix}"


def sql_like_literal(pattern: str) -> str:
    """Quote a pattern for the generic SQL parser (MySQL-safe: doubles ``\\`` and ``'``)."""
    return "'" + pattern.replace("\\", "\\\\").replace("'", "''") + "'"


def mssql_like_literal(pattern: str) -> str:
    """Quote a pattern for SQL Server (T-SQL has no backslash escapes: doubles ``'`` only)."""
    return "'" + pattern.replace("'", "''") + "'"


def bq_like_literal(pattern: str) -> str:
    """Quote a pattern as a BigQuery double-quoted literal (escapes ``\\`` then ``\"``)."""
    return '"' + pattern.replace("\\", "\\\\").replace('"', '\\"') + '"'


def validate_partial_match(key: str, op: str, value: object, *, supports_regex: bool) -> PartialMatchOp:
    """Validate one ``(op, operand)`` pair and return its table entry.

    Messages (part of the contract, mirrored in Rust):
        "unknown partial-matching operator '<op>' on '<key>'"
        "<op> on '<key>' requires a string operand"
        "<op> on '<key>' requires at least 3 characters (got <n>)"
        "<op> on '<key>': regex operators are not supported by this query parser"
        "<op> on '<key>': empty regex pattern"
        "<op> on '<key>': regex pattern too long (<n> > 200 chars)"
        "<op> on '<key>': regex pattern has a nested quantifier"

    Raises:
        ParserError: on any rule above, checked in that order.
    """
    entry = PARTIAL_MATCH_OPERATORS.get(op)
    if entry is None:
        raise ParserError(f"unknown partial-matching operator '{op}' on '{key}'")
    if not isinstance(value, str):
        raise ParserError(f"{op} on '{key}' requires a string operand")
    if len(value) < entry.min_length:
        raise ParserError(f"{op} on '{key}' requires at least 3 characters (got {len(value)})")
    if entry.kind == "regex" and not supports_regex:
        raise ParserError(f"{op} on '{key}': regex operators are not supported by this query parser")
    if entry.kind == "regex" and not value:
        raise ParserError(f"{op} on '{key}': empty regex pattern")
    if entry.kind == "regex" and len(value) > MAX_REGEX_PATTERN_LENGTH:
        raise ParserError(f"{op} on '{key}': regex pattern too long ({len(value)} > 200 chars)")
    if entry.kind == "regex" and NESTED_QUANTIFIER_RE.search(value):
        raise ParserError(f"{op} on '{key}': regex pattern has a nested quantifier")
    return entry


def validate_partial_match_dict(key: str, value: dict, *, supports_regex: bool) -> PartialMatchOp | None:
    """Validate a dict operand that may contain one partial-matching operator.

    Returns None when no key of ``value`` is a table operator. Raises ParserError when
    a table operator is combined with another key; otherwise validates its single pair.
    """
    table_keys = [op for op in value if op in PARTIAL_MATCH_OPERATORS]
    if not table_keys:
        return None
    if len(value) != 1:
        raise ParserError(
            f"one operator per field: '{key}' combines a partial-matching operator with other keys"
        )
    op = table_keys[0]
    return validate_partial_match(key, op, value[op], supports_regex=supports_regex)
