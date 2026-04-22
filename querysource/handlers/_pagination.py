"""Pagination helpers for the Query Slug management list endpoint.

Pure functions + Pydantic models. No aiohttp, no DB calls — this module is
unit-testable in isolation.

This module provides:
    * Constants (default / max page size, default sort field & direction).
    * Allowlists derived from :class:`querysource.models.QueryModel` so that
      column identifiers interpolated into raw SQL can never come from
      user-controlled strings.
    * Pydantic v2 models :class:`PaginationParams`, :class:`PaginationMeta`
      and :class:`PaginatedResponse`.
    * Pure SQL-builder helpers: :func:`build_where_clause`,
      :func:`build_order_by`, :func:`build_count_sql`, :func:`build_page_sql`.

All builders validate every column identifier against a fixed allowlist and
route scalar values through :class:`querysource.types.validators.Entity`
(``toSQL`` + ``quoteString``) — the same helpers used by
``QueryManager.get_query_insert``. This matches the project's existing
SQL-safety pattern and provides defence-in-depth against injection.

See ``sdd/specs/querysource-slug-list-pagination.spec.md`` §3 Modules 1-2.
"""
from __future__ import annotations

import logging
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from ..models import QueryModel
from ..types.validators import Entity


logger = logging.getLogger(__name__)


SortDirection = Literal["asc", "desc"]

DEFAULT_PAGE_SIZE: int = 50
MAX_PAGE_SIZE: int = 200
DEFAULT_SORT_FIELD: str = "updated_at"
DEFAULT_SORT_DIRECTION: SortDirection = "desc"

# Columns allowlisted for WHERE / filter kwargs — every key of the live
# ``QueryModel.columns()`` dict. Frozen so the set is hashable and immutable.
_MODEL_COLUMNS: dict = QueryModel.columns(QueryModel)
FILTERABLE_COLUMNS: frozenset[str] = frozenset(_MODEL_COLUMNS.keys())

# Scalar columns that the caller is allowed to sort on. jsonb / array columns
# are deliberately excluded (see spec §7 "Known Risks / Gotchas").
SORTABLE_COLUMNS: frozenset[str] = frozenset(
    {
        "query_slug",
        "description",
        "program_slug",
        "provider",
        "is_cached",
        "created_at",
        "updated_at",
    }
)

# Columns matched by the ``search`` query-string param with ``ILIKE '%term%'``.
SEARCHABLE_COLUMNS: tuple[str, ...] = (
    "query_slug",
    "description",
    "program_slug",
    "source",
)


class PaginationParams(BaseModel):
    """Validated pagination / sort / search parameters for the slug list endpoint.

    Attributes:
        page: 1-based page number (``>= 1``).
        page_size: Rows per page. Clamped to ``[1, MAX_PAGE_SIZE]``.
        sort_field: Column to sort on. Must be in :data:`SORTABLE_COLUMNS`.
        sort_direction: ``"asc"`` or ``"desc"``.
        search: Optional free-text search term (max 255 chars). Applied via
            ``ILIKE '%term%'`` across :data:`SEARCHABLE_COLUMNS`.
        fields: Optional list of column names to project. Each element must be
            in :data:`FILTERABLE_COLUMNS`.
    """

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)
    sort_field: str = Field(default=DEFAULT_SORT_FIELD)
    sort_direction: SortDirection = Field(default=DEFAULT_SORT_DIRECTION)
    search: Optional[str] = Field(default=None, max_length=255)
    fields: Optional[list[str]] = Field(default=None)

    @field_validator("sort_field")
    @classmethod
    def _validate_sort_field(cls, v: str) -> str:
        """Reject sort fields that are not in :data:`SORTABLE_COLUMNS`."""
        if v not in SORTABLE_COLUMNS:
            raise ValueError(
                f"sort field not allowed: {v!r}. "
                f"Allowed: {sorted(SORTABLE_COLUMNS)}"
            )
        return v

    @field_validator("fields")
    @classmethod
    def _validate_fields(cls, v: Optional[list[str]]) -> Optional[list[str]]:
        """Reject any field not in :data:`FILTERABLE_COLUMNS`."""
        if v is None:
            return v
        unknown = [c for c in v if c not in FILTERABLE_COLUMNS]
        if unknown:
            raise ValueError(f"unknown field(s): {unknown}")
        return v

    @property
    def offset(self) -> int:
        """SQL OFFSET computed from ``(page - 1) * page_size``."""
        return (self.page - 1) * self.page_size

    @classmethod
    def from_query_string(cls, qs: dict) -> "PaginationParams":
        """Parse a flat query-string dict into :class:`PaginationParams`.

        Understood keys:
            * ``page`` (int)
            * ``page_size`` (int)
            * ``sort`` — either ``"<field>"`` or ``"<field>:<asc|desc>"``
            * ``search`` (str)
            * ``fields`` — either a comma-separated string or a list of strings

        Unknown keys are simply ignored here — they are handled as filter
        kwargs by ``build_where_clause``.

        Args:
            qs: Flat query-string mapping (string → string, typically).

        Returns:
            A validated :class:`PaginationParams` instance.

        Raises:
            pydantic.ValidationError: On unsafe or out-of-range values.
            ValueError: On malformed ``sort`` direction.
        """
        data: dict[str, Any] = {}

        if "page" in qs:
            data["page"] = int(qs["page"])
        if "page_size" in qs:
            data["page_size"] = int(qs["page_size"])

        if "sort" in qs and qs["sort"]:
            sort_value = str(qs["sort"]).strip()
            if ":" in sort_value:
                field_part, _, dir_part = sort_value.partition(":")
                field_part = field_part.strip()
                dir_part = dir_part.strip().lower()
                if dir_part not in ("asc", "desc"):
                    raise ValueError(
                        f"invalid sort direction: {dir_part!r}; "
                        "expected 'asc' or 'desc'"
                    )
                data["sort_field"] = field_part
                data["sort_direction"] = dir_part
            else:
                data["sort_field"] = sort_value

        if "search" in qs and qs["search"] not in (None, ""):
            data["search"] = str(qs["search"])

        if "fields" in qs and qs["fields"] not in (None, ""):
            raw = qs["fields"]
            if isinstance(raw, (list, tuple)):
                data["fields"] = [str(x).strip() for x in raw if str(x).strip()]
            else:
                data["fields"] = [
                    s.strip() for s in str(raw).split(",") if s.strip()
                ]

        return cls(**data)


class PaginationMeta(BaseModel):
    """Metadata block returned inside :class:`PaginatedResponse`."""

    page: int
    page_size: int
    total: int
    total_pages: int


class PaginatedResponse(BaseModel):
    """Response envelope for paginated list endpoints."""

    data: list[dict]
    meta: PaginationMeta


# ---------------------------------------------------------------------------
# SQL builders
# ---------------------------------------------------------------------------


def _quote_ident(name: str) -> str:
    """Return ``"<name>"`` after allowlist validation.

    Only identifiers already checked against :data:`FILTERABLE_COLUMNS` reach
    this point — this helper simply adds double quotes so column names that
    happen to collide with Postgres reserved words are still safe.
    """
    if name not in FILTERABLE_COLUMNS:
        raise ValueError(f"column not in allowlist: {name!r}")
    return f'"{name}"'


def _coerce_value(col_name: str, value: Any) -> str:
    """Run ``value`` through ``Entity.toSQL`` + ``Entity.quoteString``.

    Mirrors the pattern used by ``QueryManager.get_query_insert`` at
    ``querysource/handlers/manager.py:40-49``. The column's datamodel field is
    used to look up the Python type + db_type before conversion.

    Args:
        col_name: Name of the column (already allowlisted).
        value: Raw value to coerce.

    Returns:
        A SQL literal (quoted where appropriate) safe to splice into a WHERE
        clause.
    """
    field = _MODEL_COLUMNS.get(col_name)
    if field is None:
        # Should never happen — caller must pre-check the allowlist.
        raise ValueError(f"column not in model: {col_name!r}")
    _type = field.type
    try:
        _dbtype = field.db_type()
    except Exception:  # pragma: no cover - defensive
        _dbtype = None
    sql_val = Entity.toSQL(value, _type, dbtype=_dbtype)
    if sql_val == "NULL":
        return "NULL"
    return Entity.quoteString(str(sql_val), no_dblquoting=False)


def build_where_clause(
    params: PaginationParams,
    extra_filters: dict,
) -> str:
    """Return a SQL WHERE clause (including the leading ``WHERE``) or ``""``.

    The clause merges:
        * Equality predicates for every key in ``extra_filters`` that is in
          :data:`FILTERABLE_COLUMNS` (unknown keys are silently dropped — they
          can never reach the SQL string).
        * An ``ILIKE '%term%'`` predicate over :data:`SEARCHABLE_COLUMNS`
          when ``params.search`` is set (combined with ``OR`` inside a
          parenthesised group).

    Args:
        params: Pre-validated :class:`PaginationParams`.
        extra_filters: Additional filter kwargs (typically the leftover
            query-string params).

    Returns:
        The WHERE clause (possibly empty).
    """
    predicates: list[str] = []

    for key, value in extra_filters.items():
        if key not in FILTERABLE_COLUMNS:
            # Silently drop unknown keys — they never touch SQL.
            logger.debug("Dropping unknown filter key: %r", key)
            continue
        coerced = _coerce_value(key, value)
        if coerced == "NULL":
            predicates.append(f"{_quote_ident(key)} IS NULL")
        else:
            predicates.append(f"{_quote_ident(key)} = {coerced}")

    if params.search:
        term = params.search.replace("\\", "\\\\").replace("'", "''")
        like_literal = f"'%{term}%'"
        or_terms = [
            f"{_quote_ident(col)}::text ILIKE {like_literal}"
            for col in SEARCHABLE_COLUMNS
            if col in FILTERABLE_COLUMNS
        ]
        if or_terms:
            predicates.append("(" + " OR ".join(or_terms) + ")")

    if not predicates:
        return ""
    return "WHERE " + " AND ".join(predicates)


def build_order_by(params: PaginationParams) -> str:
    """Return ``ORDER BY "<col>" <DIR>`` with a validated column name.

    Defence in depth: even though :class:`PaginationParams` already rejects
    unknown sort columns, we re-check here before interpolating.

    Args:
        params: Pre-validated :class:`PaginationParams`.

    Returns:
        The ``ORDER BY`` clause.

    Raises:
        ValueError: If the sort field is not in :data:`SORTABLE_COLUMNS`.
    """
    if params.sort_field not in SORTABLE_COLUMNS:
        raise ValueError(
            f"sort field not allowed: {params.sort_field!r}"
        )
    direction = "ASC" if params.sort_direction == "asc" else "DESC"
    return f'ORDER BY "{params.sort_field}" {direction}'


def build_count_sql(schema: str, table: str, where: str) -> str:
    """Return ``SELECT COUNT(*) FROM "<schema>"."<table>" <where>``.

    Args:
        schema: Postgres schema name. Must be a bare identifier.
        table: Table name. Must be a bare identifier.
        where: WHERE clause as produced by :func:`build_where_clause`. May be
            empty.

    Returns:
        A complete ``SELECT COUNT(*)`` statement.
    """
    _validate_bare_identifier(schema, "schema")
    _validate_bare_identifier(table, "table")
    base = f'SELECT COUNT(*) FROM "{schema}"."{table}"'
    if where:
        return f"{base} {where}"
    return base


def build_page_sql(
    schema: str,
    table: str,
    fields: list[str],
    where: str,
    order_by: str,
    limit: int,
    offset: int,
) -> str:
    """Return a paged SELECT statement.

    Args:
        schema: Postgres schema name. Must be a bare identifier.
        table: Table name. Must be a bare identifier.
        fields: List of columns to project. Each must be in
            :data:`FILTERABLE_COLUMNS`. ``[]`` projects all columns (``*``).
        where: Clause from :func:`build_where_clause` (may be empty).
        order_by: Clause from :func:`build_order_by`.
        limit: Non-negative integer used as ``LIMIT``.
        offset: Non-negative integer used as ``OFFSET``.

    Returns:
        A complete ``SELECT`` statement safe to pass to ``conn.fetch``.

    Raises:
        ValueError: On unknown columns, non-bare identifiers, or negative
            ``limit`` / ``offset``.
    """
    _validate_bare_identifier(schema, "schema")
    _validate_bare_identifier(table, "table")
    if not isinstance(limit, int) or limit < 0:
        raise ValueError(f"limit must be a non-negative int, got {limit!r}")
    if not isinstance(offset, int) or offset < 0:
        raise ValueError(f"offset must be a non-negative int, got {offset!r}")

    if fields:
        unknown = [c for c in fields if c not in FILTERABLE_COLUMNS]
        if unknown:
            raise ValueError(f"unknown projection column(s): {unknown}")
        select_list = ", ".join(f'"{c}"' for c in fields)
    else:
        select_list = "*"

    parts = [f'SELECT {select_list} FROM "{schema}"."{table}"']
    if where:
        parts.append(where)
    if order_by:
        parts.append(order_by)
    parts.append(f"LIMIT {limit} OFFSET {offset}")
    return " ".join(parts)


def _validate_bare_identifier(value: str, kind: str) -> None:
    """Ensure ``value`` contains only ``[A-Za-z0-9_]`` characters.

    Schema and table names come from ``QueryModel.Meta`` (static config), but
    we still validate to avoid accidental future misuse.
    """
    if not value or not all(
        c.isalnum() or c == "_" for c in value
    ) or value[0].isdigit():
        raise ValueError(f"invalid {kind} identifier: {value!r}")
