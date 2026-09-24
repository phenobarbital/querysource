"""Closed capability vocabulary for qsurl (matches the Rust ``Feature`` enum).

The tuple order of ``ALL`` is the Rust declaration order, which is the order the
IR's ``requires`` list uses. Never sort it alphabetically.
"""
from __future__ import annotations

SELECT = "select"
ALIAS = "alias"
FILTER = "filter"
OR = "or"
NOT = "not"
IN_LIST = "in_list"
NULL_CHECK = "null_check"
TEXT_MATCH = "text_match"
REGEX = "regex"
FUNCTIONS = "functions"
NAVIGATION = "navigation"
SORT = "sort"
LIMIT = "limit"
OFFSET = "offset"
DISTINCT = "distinct"

ALL: tuple[str, ...] = (
    SELECT, ALIAS, FILTER, OR, NOT, IN_LIST, NULL_CHECK, TEXT_MATCH, REGEX,
    FUNCTIONS, NAVIGATION, SORT, LIMIT, OFFSET, DISTINCT,
)
BASE: frozenset[str] = frozenset({SELECT, FILTER, IN_LIST, NULL_CHECK})
UNSUPPORTED_PHASE1: frozenset[str] = frozenset({FUNCTIONS, NAVIGATION})


def validate(caps: frozenset[str]) -> frozenset[str]:
    """Return ``caps`` unchanged after checking every token is in ``ALL``.

    Args:
        caps: capability tokens to validate.

    Returns:
        The same ``caps`` set, unchanged.

    Raises:
        ValueError: naming the unknown token(s).
    """
    unknown = caps - set(ALL)
    if unknown:
        raise ValueError(f"unknown qsurl capabilities: {sorted(unknown)}")
    return caps
