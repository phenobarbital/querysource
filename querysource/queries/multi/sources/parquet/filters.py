"""DNF filter translation for Parquet sources (FEAT-158).

Filters are DNF lists only: a flat list of ``[col, op, value]`` entries is an AND,
and a list of such lists is an OR of ANDs. Operators come from
:data:`ALLOWED_OPERATORS`. Nothing is ever parsed from a string or evaluated.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pyarrow.compute as pc

ALLOWED_OPERATORS: frozenset[str] = frozenset({
    "=", "==", "!=", "<", "<=", ">", ">=",
    "in", "not in", "is null", "is not null",
})
_NULL_OPERATORS = frozenset({"is null", "is not null"})
_LIST_OPERATORS = frozenset({"in", "not in"})


def _is_conjunction(filters: list) -> bool:
    """Return True when *filters* is a flat AND list (its first item is a predicate)."""
    return bool(filters) and isinstance(filters[0], (list, tuple)) and bool(filters[0]) and isinstance(filters[0][0], str)


def _predicate(entry: Any, position: str) -> "pc.Expression":
    """Convert one ``[col, op, value]`` (or ``[col, "is null"]``) entry.

    Raises:
        ValueError: malformed entry, unknown operator, non-list value for
            ``in``/``not in``, or a value given to a null operator.
    """
    import pyarrow.compute as pc  # noqa: PLC0415

    if not isinstance(entry, (list, tuple)) or len(entry) not in (2, 3) or not isinstance(entry[0], str):
        raise ValueError(f"{position}: malformed filter entry")

    column, raw_operator = entry[0], entry[1]
    if not isinstance(raw_operator, str):
        raise ValueError(f"{position}: invalid operator {raw_operator!r}")
    operator = " ".join(raw_operator.lower().split())
    if operator not in ALLOWED_OPERATORS:
        raise ValueError(f"{position}: unknown operator {raw_operator!r}")

    if operator in _NULL_OPERATORS:
        if len(entry) == 3 and entry[2] is not None:
            raise ValueError(f"{position}: operator {operator!r} does not accept a value")
        expression = pc.field(column).is_null()
        return expression if operator == "is null" else ~expression

    if len(entry) != 3:
        raise ValueError(f"{position}: operator {operator!r} requires a value")
    value = entry[2]
    if operator in _LIST_OPERATORS:
        if not isinstance(value, list):
            raise ValueError(f"{position}: operator {operator!r} requires a list value")
        expression = pc.field(column).isin(value)
        return expression if operator == "in" else ~expression

    field = pc.field(column)
    if operator in {"=", "=="}:
        return field == value
    if operator == "!=":
        return field != value
    if operator == "<":
        return field < value
    if operator == "<=":
        return field <= value
    if operator == ">":
        return field > value
    return field >= value


def build_filter_expression(filters: list | None) -> "pc.Expression | None":
    """Convert a DNF filter list into a pyarrow compute expression.

    Args:
        filters: None, a flat list of [col, op, value] (AND), or a list of such
            lists (OR of ANDs).

    Returns:
        The combined expression, or None when ``filters`` is None or empty.

    Raises:
        ValueError: on an unknown operator, a malformed entry, a non-list value
            for ``in``/``not in``, or a value given to ``is null``/``is not null``.
    """
    if not filters:
        return None
    if not isinstance(filters, list):
        raise ValueError("filters must be a list of [column, operator, value] entries")

    if _is_conjunction(filters):
        expression = _predicate(filters[0], "filters[0]")
        for index, entry in enumerate(filters[1:], start=1):
            expression = expression & _predicate(entry, f"filters[{index}]")
        return expression

    expression = None
    for group_index, group in enumerate(filters):
        if not isinstance(group, list):
            raise ValueError(f"filters[{group_index}]: malformed filter group")
        if not group:
            raise ValueError(f"filters[{group_index}]: empty filter group")
        group_expression = _predicate(group[0], f"filters[{group_index}][0]")
        for entry_index, entry in enumerate(group[1:], start=1):
            group_expression = group_expression & _predicate(entry, f"filters[{group_index}][{entry_index}]")
        expression = group_expression if expression is None else expression | group_expression
    return expression


def filter_columns(filters: list | None) -> set[str]:
    """Return every column name referenced by *filters* (empty set for None/[]).

    Used by ParquetSource to report unknown columns against the dataset schema
    before scanning. Assumes *filters* already passed build_filter_expression.
    """
    if not filters:
        return set()
    if _is_conjunction(filters):
        return {entry[0] for entry in filters}
    return {entry[0] for group in filters for entry in group}
