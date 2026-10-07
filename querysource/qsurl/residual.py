"""In-memory qsurl residual stage: apply a ResidualPlan without string eval."""
from __future__ import annotations

import logging
import operator
import re

import pandas as pd

try:
    import pyarrow as pa
    import pyarrow.compute as pc
except ImportError:  # pragma: no cover - pyarrow ships with the `parquet` extra
    pa = None
    pc = None

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

# Ledger issue:2241b8e60919 (FEAT-152 review, fixed by FEAT-164): the `regex`
# / `iregex` leaves run an attacker-controlled pattern against every row,
# synchronously, on the handler's event-loop thread. They are evaluated ONLY
# with RE2 (`pyarrow.compute.match_substring_regex`, linear time) in
# `_re2_contains` — never through `Series.str.contains(regex=True)`, because
# pandas silently falls back to Python's backtracking `re` whenever a pattern
# has lookaround or a backreference (`(?=a)(a|a)+$` blocked for 11 s on one
# 27-char row). Without pyarrow the leaves fail closed. The length cap and
# nested-quantifier screen below stay as defence in depth and keep parity
# with the parser-level regex guard (FEAT-180).
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


def _re2_contains(col: pd.Series, pattern: str, ignore_case: bool) -> pd.Series:
    """Search ``pattern`` in ``col`` with RE2 only (linear time, never Python ``re``).

    Args:
        col: column to match; non-string values are matched on their string form.
        pattern: user-supplied regex (RE2 syntax).
        ignore_case: True for ``iregex``.

    Returns:
        Boolean mask aligned to ``col.index``; nulls never match.

    Raises:
        QSUrlError: kind "lower" when pyarrow is unavailable or RE2 rejects the
            pattern (lookaround, backreferences, malformed syntax).
    """
    if pc is None:
        raise QSUrlError("lower", "regex filters require pyarrow (RE2 engine), which is not installed")
    arrow = pa.array(col.astype(pd.StringDtype("pyarrow")))
    try:
        matched = pc.match_substring_regex(arrow, pattern=pattern, ignore_case=ignore_case)
    except pa.ArrowInvalid as err:
        raise QSUrlError("lower", f"invalid regex `{pattern}`: {err}") from err
    return pd.Series(matched.fill_null(False).to_numpy(zero_copy_only=False), index=col.index, dtype=bool)


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

    if expr in ("contains", "not_contains", "icontains", "not_icontains"):
        mask = col.astype("string").str.contains(
            re.escape(value), case=not expr.endswith("icontains"), na=False, regex=True
        )
        return ~mask if expr.startswith("not_") else mask

    if expr in ("startswith", "endswith"):
        text = col.astype("string")
        return text.str.startswith(value, na=False) if expr == "startswith" else text.str.endswith(value, na=False)
    if expr in ("istartswith", "iendswith"):
        text = col.astype("string").str.lower()
        if expr == "istartswith":
            return text.str.startswith(value.lower(), na=False)
        return text.str.endswith(value.lower(), na=False)

    if expr in ("regex", "iregex"):
        _check_regex_safety(value)
        return _re2_contains(col, value, ignore_case=(expr == "iregex"))

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
