"""Filter value grammar shared by the query parsers (FEAT-165).

Key suffixes, comparison dicts, typed filter formats and ``[NOT] BETWEEN``
parsing. Pure Python so both the Cython parsers and tests import it directly.
"""
from __future__ import annotations

import re
import string
from dataclasses import dataclass

from ..exceptions import ParserError
from ..types.validators import is_udf, unquote_literal
from ..utils.functions import to_udf

KEY_SUFFIX_CHARS: str = "|!~#@:"
COMPARISON_OPERATORS: tuple[str, ...] = (">=", "<=", "<>", "!=", "<", ">")
TYPED_FILTER_FORMATS: frozenset[str] = frozenset(
    {"array", "numrange", "int4range", "int8range", "tsrange", "tstzrange", "daterange"}
)

_BOUND = r"(?:'(?:[^']|'')*'|[A-Za-z0-9_:./+-]+)"
_BETWEEN_PREFIX = re.compile(r"^\s*(?:NOT\s+)?BETWEEN\s", re.IGNORECASE)
_BETWEEN_CLAUSE = re.compile(
    rf"^\s*(?P<not>NOT\s+)?BETWEEN\s+(?P<lo>{_BOUND})\s+AND\s+(?P<hi>{_BOUND})\s*$",
    re.IGNORECASE,
)
_NUMBER = re.compile(r"^-?\d+(?:\.\d+)?$")


@dataclass(frozen=True)
class BetweenClause:
    """A validated, normalised ``[NOT] BETWEEN`` clause."""

    negated: bool
    low: str
    high: str

    def render(self) -> str:
        """Return the normalised SQL ``[NOT] BETWEEN`` expression."""
        prefix = "NOT BETWEEN" if self.negated else "BETWEEN"
        return f"{prefix} {self.low} AND {self.high}"


def base_key(key: str) -> str:
    """Return ``key`` without trailing suffix characters (``KEY_SUFFIX_CHARS``)."""
    return key.rstrip(KEY_SUFFIX_CHARS)


_BRACE_FIELD = re.compile(r"(?<!\{)\{([^{}]+)\}(?!\})")


def template_fields(template: str) -> frozenset[str]:
    """Return the base field names a ``str.format_map`` template substitutes.

    Conversions (``!s``), format specs (``:>10``), attribute (``.x``) and index
    (``[0]``) parts are stripped, and escaped braces (``{{name}}``) are ignored.
    A template that is not a valid format string falls back to the
    brace-delimited names it contains, so detection never raises.

    Args:
        template: The raw query template.

    Returns:
        The set of placeholder base names.
    """
    try:
        names = [
            field for _, field, _, _ in string.Formatter().parse(template)
            if field
        ]
    except ValueError:
        names = [
            re.split(r"[!:]", field, maxsplit=1)[0]
            for field in _BRACE_FIELD.findall(template)
        ]
    return frozenset(
        base for base in (re.split(r"[.\[]", name, maxsplit=1)[0].strip() for name in names)
        if base
    )


def is_comparison_dict(value: dict) -> bool:
    """Return whether ``value`` is non-empty and uses only comparison operators."""
    return bool(value) and all(key in COMPARISON_OPERATORS for key in value)


def _quote(text: str) -> str:
    """Quote ``text`` as a SQL string literal (single quotes doubled)."""
    return "'" + text.replace("'", "''") + "'"


__all__ = [
    "BetweenClause",
    "COMPARISON_OPERATORS",
    "KEY_SUFFIX_CHARS",
    "TYPED_FILTER_FORMATS",
    "base_key",
    "is_comparison_dict",
    "parse_between",
    "template_fields",
    "unquote_literal",
]


def _invalid_between(key: str) -> ParserError:
    """Build the stable error used for invalid ``BETWEEN`` clauses."""
    return ParserError(f"invalid BETWEEN clause for '{key}'")


def _normalise_bound(key: str, bound: str) -> str:
    """Return ``bound`` as a safe SQL literal (see spec §2 B.1).

    Raises:
        ParserError: the bound is outside the grammar.
    """
    if any(marker in bound for marker in (";", "--", "/*")):
        raise _invalid_between(key)
    if _NUMBER.fullmatch(bound):
        return bound
    if bound.startswith("'") and bound.endswith("'"):
        return _quote(bound[1:-1].replace("''", "'"))
    if re.fullmatch(r"[A-Za-z0-9_:./+-]+", bound):
        if is_udf(bound.upper()):
            return _quote(str(to_udf(bound)))
        return _quote(bound)
    raise _invalid_between(key)


def parse_between(key: str, value: str) -> BetweenClause | None:
    """Parse ``[NOT] BETWEEN <lo> AND <hi>`` (case-insensitive, whole string).

    A ``!`` anywhere in ``key``'s suffix also negates. Returns None when ``value`` does not
    start with ``BETWEEN``/``NOT BETWEEN``.

    Raises:
        ParserError: the value starts like a BETWEEN clause but is malformed.
    """
    if not isinstance(value, str) or not _BETWEEN_PREFIX.match(value):
        return None
    match = _BETWEEN_CLAUSE.match(value)
    if match is None:
        raise _invalid_between(key)
    # BETWEEN collapses the key to base_key(), so a "!" anywhere in the
    # stripped suffix (not only the last character) must negate.
    key_negated = "!" in key[len(base_key(key)):]
    return BetweenClause(
        negated=bool(match.group("not")) or key_negated,
        low=_normalise_bound(key, match.group("lo")),
        high=_normalise_bound(key, match.group("hi")),
    )
