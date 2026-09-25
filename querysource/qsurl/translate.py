"""Split a qsurl IR into pushdown ``conditions`` and an in-memory ``ResidualPlan``."""
from __future__ import annotations

import re

from . import capabilities as caps
from .errors import QSUrlError
from .plan import ResidualPlan

IDENT_RE: re.Pattern[str] = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_CMP = ("<", "<=", ">", ">=")
_TEXT_PATTERNS: dict[str, tuple[str, str, str]] = {
    # expression: (operator, prefix, suffix)
    "startswith": ("ILIKE", "", "%"),
    "contains": ("ILIKE", "%", "%"),
    "endswith": ("ILIKE", "%", ""),
    "not_contains": ("NOT ILIKE", "%", "%"),
}


def like_escape(value: str) -> str:
    """Escape ``\\``, ``%`` and ``_`` for use inside an ILIKE pattern (quoting is the builder's job)."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _leaf_pushdown(leaf: dict, capabilities: frozenset[str]) -> tuple[str, object] | None:
    """Return ``(key, value)`` for a pushable leaf, or None when it must stay residual."""
    col = leaf.get("column")
    if not isinstance(col, str):
        # dict (fn) or dotted columns are covered by the functions/navigation
        # "unsupported" guard before this function ever runs.
        return None
    expr = leaf["expression"]
    value = leaf.get("value")

    if expr == "==":
        if isinstance(value, list):
            if caps.IN_LIST not in capabilities:
                return None
            return col, list(value)
        if caps.FILTER not in capabilities:
            return None
        return col, value

    if expr == "!=":
        if isinstance(value, list):
            if caps.IN_LIST not in capabilities:
                return None
            return f"{col}!", list(value)
        if caps.FILTER not in capabilities:
            return None
        return f"{col}!", value

    if expr in _CMP:
        if caps.FILTER not in capabilities:
            return None
        return col, {expr: value}

    if expr == "is_null":
        if caps.NULL_CHECK not in capabilities:
            return None
        return col, "null"

    if expr == "not_null":
        if caps.NULL_CHECK not in capabilities:
            return None
        return col, "!null"

    if expr in _TEXT_PATTERNS:
        if caps.TEXT_MATCH not in capabilities or not isinstance(value, str):
            return None
        op, prefix, suffix = _TEXT_PATTERNS[expr]
        return col, {op: f"{prefix}{like_escape(value)}{suffix}"}

    # "regex" and anything unrecognised is never pushed down.
    return None


def _split_filter(node: dict | None, capabilities: frozenset[str]) -> tuple[dict, dict | None]:
    """Return ``(pushed_filter, residual_filter)`` per rule 3 (duplicate keys stay residual)."""
    if node is None:
        return {}, None
    if "or" in node:
        # A root `or` (and any `or`/`not` subtree) is never a pushdown candidate:
        # it goes to the residual plan whole, preserving order.
        return {}, node

    children = node.get("and", [])
    pushed: dict = {}
    residual: list = []
    for child in children:
        if "expression" in child:
            result = _leaf_pushdown(child, capabilities)
            if result is not None:
                key, value = result
                if key not in pushed:
                    pushed[key] = value
                    continue
            # unpushable leaf, or duplicate pushdown key: stays residual.
            residual.append(child)
        else:
            # a nested and/or/not subtree: never a direct-leaf pushdown candidate.
            residual.append(child)

    residual_filter = {"and": residual} if residual else None
    return pushed, residual_filter


def _referenced_columns(node: dict | None) -> list[str]:
    """Plain-string columns referenced anywhere in ``node``, in first-seen order."""
    result: list[str] = []
    if node is None:
        return result

    def _walk(n: dict) -> None:
        if "and" in n:
            for child in n["and"]:
                _walk(child)
        elif "or" in n:
            for child in n["or"]:
                _walk(child)
        elif "not" in n:
            _walk(n["not"])
        else:
            col = n.get("column")
            if isinstance(col, str) and col not in result:
                result.append(col)

    _walk(node)
    return result


def _check_identifier(value: str) -> None:
    """Raise QSUrlError("lower", ...) when ``value`` is not a safe identifier."""
    if not IDENT_RE.match(value):
        raise QSUrlError("lower", f"invalid identifier: `{value}`")


def split(ir: dict, capabilities: frozenset[str], *, residual_scan: bool = True) -> tuple[dict, ResidualPlan]:
    """Split the IR into the parser ``conditions`` dict and the in-memory ResidualPlan.

    Args:
        ir: qsurl IR dict (``querysource.qsurl.parse`` output).
        capabilities: the executing provider's ``capabilities``.
        residual_scan: the executing provider's ``residual_scan`` flag.

    Returns:
        ``(conditions, plan)``; ``conditions`` only uses ``fields``, ``filter``,
        ``ordering``, ``_limit``, ``_offset``.

    Raises:
        QSUrlError: kind "unsupported" (functions/navigation), "lower" (bad identifier),
            or "cost" (residual-only filter with ``residual_scan=False``).
    """
    needed = set(ir.get("requires", []))
    for cap in caps.ALL:
        if cap in needed and cap in caps.UNSUPPORTED_PHASE1:
            raise QSUrlError("unsupported", f"capability `{cap}` is not supported by any provider in this release")

    fields = ir.get("fields") or []
    requested: list[str] = []
    alias_names: set[str] = set()
    for f in fields:
        if isinstance(f, dict):
            col = f.get("column")
            alias = f.get("alias")
            if not isinstance(col, str):
                # dotted/fn columns are covered by the navigation/functions guard above.
                continue
            _check_identifier(col)
            if alias is not None:
                _check_identifier(alias)
                alias_names.add(alias)
            requested.append(col)
        else:
            _check_identifier(f)
            requested.append(f)

    sort = ir.get("sort") or []
    for s in sort:
        _check_identifier(s["column"])

    for col in _referenced_columns(ir.get("filter")):
        _check_identifier(col)

    # Rule 3: filter.
    pushed_filter, residual_filter = _split_filter(ir.get("filter"), capabilities)
    conditions: dict = {}
    if pushed_filter:
        conditions["filter"] = pushed_filter

    # Rule 5: sort.
    def _is_alias_only(key: str) -> bool:
        return key in alias_names and key not in requested

    sort_pushable = (
        caps.SORT in capabilities
        and bool(sort)
        and all(not _is_alias_only(s["column"]) for s in sort)
    )
    if sort and sort_pushable:
        ordering = [
            s["column"] if s.get("order") != "desc" else f"{s['column']} DESC"
            for s in sort
        ]
        conditions["ordering"] = ordering
        plan_sort: tuple[tuple[str, bool], ...] = ()
    elif sort:
        plan_sort = tuple((s["column"], s.get("order") == "desc") for s in sort)
    else:
        plan_sort = ()

    # Rule 7: distinct is always residual.
    plan_distinct = bool(ir.get("distinct", False))

    # Rule 6: window (limit/offset) pushed only when the filter is fully pushed,
    # there is no distinct, and sort is either absent or pushed.
    limit = ir.get("limit")
    offset = ir.get("offset")
    window_pushable = (
        residual_filter is None
        and not plan_distinct
        and (not sort or sort_pushable)
    )
    plan_limit: int | None = None
    plan_offset: int | None = None
    if window_pushable:
        if limit is not None and caps.LIMIT in capabilities:
            conditions["_limit"] = limit
        else:
            plan_limit = limit
        if offset is not None and caps.OFFSET in capabilities:
            conditions["_offset"] = offset
        else:
            plan_offset = offset
        # A pushed limit with a residual offset (or vice-versa) is never emitted:
        # if either half could not be pushed, both go to the plan.
        if plan_limit is not None or plan_offset is not None:
            if "_limit" in conditions:
                del conditions["_limit"]
                plan_limit = limit
            if "_offset" in conditions:
                del conditions["_offset"]
                plan_offset = offset
    else:
        plan_limit = limit
        plan_offset = offset

    # Rule 4: fields, aliases and projection. Decided last: alias pushdown needs to
    # know whether the plan is otherwise empty.
    plan_otherwise_empty = (
        residual_filter is None
        and plan_sort == ()
        and not plan_distinct
        and plan_limit is None
        and plan_offset is None
    )
    plan_rename: list[tuple[str, str]] = []
    field_list: list[str] = []
    if fields:
        if caps.ALIAS in capabilities and plan_otherwise_empty:
            for f in fields:
                if isinstance(f, dict):
                    field_list.append(f"{f['column']} AS {f['alias']}")
                else:
                    field_list.append(f)
        else:
            for f in fields:
                if isinstance(f, dict):
                    field_list.append(f["column"])
                    plan_rename.append((f["column"], f["alias"]))
                else:
                    field_list.append(f)

        missing = [
            col
            for col in (*_referenced_columns(residual_filter), *(k for k, _ in plan_sort))
            if col not in requested
        ]
        plan_project: tuple[str, ...] = ()
        if missing:
            conditions["fields"] = requested + missing
            plan_project = tuple(requested)
        else:
            conditions["fields"] = field_list
    else:
        plan_project = ()

    plan = ResidualPlan(
        filter=residual_filter,
        sort=plan_sort,
        project=plan_project,
        distinct=plan_distinct,
        offset=plan_offset,
        limit=plan_limit,
        rename=tuple(plan_rename),
    )

    if not residual_scan and plan.filter is not None and not conditions.get("filter"):
        raise QSUrlError(
            "cost",
            "residual-only filter on a provider that forbids scans (residual_scan=False); "
            "push down at least one condition",
        )

    return conditions, plan
