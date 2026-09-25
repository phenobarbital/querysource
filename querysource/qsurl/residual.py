"""In-memory qsurl residual stage: apply a ResidualPlan without string eval."""
from __future__ import annotations

import logging
import operator
import re

import pandas as pd

from .errors import QSUrlError
from .plan import ResidualPlan

_logger = logging.getLogger(__name__)
_OPS = {
    "==": operator.eq,
    "!=": operator.ne,
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
}

# Ledger issue:2241b8e60919 (code review, FEAT-152): the `regex` leaf runs an
# attacker-controlled pattern against every row via `str.contains(...,
# regex=True)`, synchronously, on the handler's event-loop thread — an
# unbounded ReDoS vector. The spec (sdd/specs/qsurl-parser.spec.md §3 Module
# 7) mandates this exact pandas call, so the mitigation is additive: reject
# overly long patterns and the classic "nested quantifier" shape
# (`(x+)+`, `(x*)+`, ...) that causes catastrophic backtracking in practice,
# before the pattern ever reaches `str.contains`. This is a best-effort
# static screen, not a proof of linear-time matching — a pattern-length cap
# plus this heuristic bounds the most common real-world risk without a new
# dependency or a signal/thread-based timeout around a vectorised,
# whole-column pandas call (which cannot be interrupted per-row anyway).
_MAX_REGEX_PATTERN_LENGTH = 200
_NESTED_QUANTIFIER_RE = re.compile(r"\([^()]*[+*][^()]*\)[+*]")


def _check_regex_safety(pattern: str) -> None:
    """Reject a `regex` leaf pattern likely to cause catastrophic backtracking.

    Raises:
        QSUrlError: kind "lower" when the pattern is too long or has the
            classic nested-quantifier shape (`(x+)+`, `(x*)+`, ...).
    """
    if len(pattern) > _MAX_REGEX_PATTERN_LENGTH:
        raise QSUrlError(
            "lower",
            f"regex pattern too long ({len(pattern)} > {_MAX_REGEX_PATTERN_LENGTH} chars)",
        )
    if _NESTED_QUANTIFIER_RE.search(pattern):
        raise QSUrlError(
            "lower",
            f"regex pattern `{pattern}` has a nested quantifier that risks catastrophic backtracking",
        )


def _column(df: pd.DataFrame, name: str) -> pd.Series:
    """Return ``df[name]`` or raise ``QSUrlError("lower")`` when it is missing."""
    if name not in df.columns:
        raise QSUrlError("lower", f"column `{name}` not in result")
    return df[name]


def _utc(value: str) -> pd.Timestamp:
    """Parse an ISO date/datetime literal as a UTC timestamp."""
    ts = pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def _leaf_mask(df: pd.DataFrame, leaf: dict) -> pd.Series:
    """Boolean mask for one IR leaf (see the table in TASK-772)."""
    col = _column(df, leaf["column"])
    expr = leaf["expression"]
    value = leaf.get("value")
    dtype = leaf.get("dtype")

    if expr == "==" and isinstance(value, list):
        return col.isin(value)
    if expr == "!=" and isinstance(value, list):
        return ~col.isin(value)

    if expr in _OPS:
        if dtype in ("date", "datetime"):
            left = pd.to_datetime(col, utc=True, errors="coerce")
            right = _utc(value)
            return _OPS[expr](left, right)
        return _OPS[expr](col, value)

    if expr == "is_null":
        return col.isnull() | (col == "")
    if expr == "not_null":
        return ~(col.isnull() | (col == ""))

    if expr == "contains":
        return col.astype("string").str.contains(re.escape(value), case=False, na=False, regex=True)
    if expr == "not_contains":
        return ~col.astype("string").str.contains(re.escape(value), case=False, na=False, regex=True)

    if expr == "startswith":
        return col.astype("string").str.lower().str.startswith(value.lower(), na=False)
    if expr == "endswith":
        return col.astype("string").str.lower().str.endswith(value.lower(), na=False)

    if expr == "regex":
        _check_regex_safety(value)
        try:
            return col.astype("string").str.contains(value, case=True, na=False, regex=True)
        except re.error as err:
            raise QSUrlError("lower", f"invalid regex `{value}`: {err}") from err

    raise QSUrlError("lower", f"unsupported expression `{expr}`")


def evaluate(df: pd.DataFrame, node: dict) -> pd.Series:
    """Boolean mask for an IR filter node or leaf over ``df`` (recursive; no eval)."""
    if "and" in node:
        mask = pd.Series(True, index=df.index)
        for child in node["and"]:
            mask = mask & evaluate(df, child)
        return mask
    if "or" in node:
        mask = pd.Series(False, index=df.index)
        for child in node["or"]:
            mask = mask | evaluate(df, child)
        return mask
    if "not" in node:
        return ~evaluate(df, node["not"])
    return _leaf_mask(df, node)


def apply(rows: pd.DataFrame | list, plan: ResidualPlan) -> pd.DataFrame | list:
    """Apply ``plan`` in the order filter -> sort -> project -> distinct -> offset -> limit -> rename.

    A list input (records or asyncdb Records) is materialised with
    ``pd.DataFrame([dict(r) for r in rows])`` and returned as ``list[dict]``; a DataFrame
    is returned as a DataFrame. An empty plan returns ``rows`` untouched.

    Args:
        rows: provider result set, either a ``pd.DataFrame`` or a list of mappings.
        plan: the ``ResidualPlan`` to apply.

    Returns:
        The filtered/sorted/projected result, in the same shape (DataFrame or list) as ``rows``.

    Raises:
        QSUrlError: kind "lower" on unknown columns or an invalid regex.
    """
    if plan.is_empty():
        return rows

    as_list = not isinstance(rows, pd.DataFrame)
    df = pd.DataFrame([dict(r) for r in rows]) if as_list else rows

    if plan.filter is not None:
        mask = evaluate(df, plan.filter)
        df = df[mask]

    if plan.sort:
        columns = [col for col, _descending in plan.sort]
        ascending = [not descending for _col, descending in plan.sort]
        for col in columns:
            _column(df, col)
        df = df.sort_values(by=columns, ascending=ascending)

    if plan.project:
        for col in plan.project:
            _column(df, col)
        df = df[list(plan.project)]

    if plan.distinct:
        df = df.drop_duplicates()

    if plan.offset is not None:
        df = df.iloc[plan.offset:]

    if plan.limit is not None:
        df = df.iloc[: plan.limit]

    if plan.rename:
        for col, _alias in plan.rename:
            _column(df, col)
        df = df.rename(columns=dict(plan.rename))

    df = df.reset_index(drop=True)
    return df.to_dict("records") if as_list else df
