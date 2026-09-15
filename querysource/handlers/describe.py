"""Read-only describe endpoints for stored query slugs (FEAT-148)."""
from __future__ import annotations

import asyncio
import re
from math import ceil
from typing import Literal

from aiohttp import web
from asyncdb.exceptions import DriverError, NoDataFound, ProviderError
from datamodel.exceptions import ValidationError
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from ..auth.slug_visibility import (
    DescribeStore,
    Principal,
    PrincipalKind,
    build_program_predicate,
    can_access,
    describe_grants,
    filter_visible,
    legacy_store,
    resolve_principal,
)
from ..conf import QS_DESCRIBE_COLUMNS_TIMEOUT, QS_DESCRIBE_MAX_SCAN
from ..exceptions import ParserError, SlugNotFound
from ..queries.describe import (
    RESERVED_PLACEHOLDERS,
    describe_slug,
    extract_placeholders,
)
from ..queries.qs import QS
from ..types.validators import pg_constants, pg_udfs, udf_keywords
from ..utils.vocabulary import build_vocabulary
from ._pagination import (
    PaginatedResponse,
    PaginationParams,
    _validate_bare_identifier,
    build_order_by,
    build_scan_sql,
    build_where_clause,
    compose_where,
)
from .abstract import AbstractHandler

SLUG_PATTERN = r"[A-Za-z0-9_.\-:]{1,255}"
VOCABULARY_LINK = "/api/v1/queries/vocabulary"
_PAGINATION_KEYS = frozenset({"page", "page_size", "sort", "search", "q", "fields"})


class ColumnInfo(BaseModel):
    """One output column; ``type`` is None when not introspectable."""

    name: str
    type: str | None = None


class ColumnsResponse(BaseModel):
    """Response of GET .../queries/{slug}/columns."""

    slug: str
    columns: list[ColumnInfo]
    columns_source: Literal["prepare", "declared", "unavailable"]
    warnings: list[str] = []


class QueryDescribe(AbstractHandler):
    """Read-only describe/columns/vocabulary endpoints for query slugs (FEAT-148)."""

    LIST_FIELDS: tuple[str, ...] = ("query_slug", "provider", "description", "program_slug", "updated_at")

    async def _principal(self, request: web.Request) -> Principal:
        """Resolve the caller; raise ``web.HTTPUnauthorized`` when there is none (AC4)."""
        session = await self._get_user_session(request)
        principal = await resolve_principal(request, session)
        if principal.kind is PrincipalKind.NONE:
            raise web.HTTPUnauthorized()
        return principal

    async def _store(self, request: web.Request) -> DescribeStore:
        """Legacy definitions store (tenant branch added by TASK-743)."""
        return legacy_store()

    async def _load_visible(
        self, request: web.Request, principal: Principal, store: DescribeStore, slug: str
    ):
        """Return the visible definition or raise a body-less ``web.HTTPNotFound`` (AC6/AC7)."""
        if not slug or not re.fullmatch(SLUG_PATTERN, slug):
            raise web.HTTPNotFound()
        predicate = build_program_predicate(principal, store, param_index=2)
        if predicate.deny_all:
            raise web.HTTPNotFound()
        
        # Validate identifiers
        _validate_bare_identifier(store.schema, "schema")
        _validate_bare_identifier(store.table, "table")
        
        # Build exists_sql
        exists_sql = f'SELECT 1 FROM "{store.schema}"."{store.table}" WHERE "query_slug" = $1'
        if predicate.sql:
            exists_sql += f" AND ({predicate.sql})"
        
        # Acquire connection and check existence
        async with await request.app['qs_connection'].acquire() as conn:
            row = await conn.fetch_one(exists_sql, slug, *predicate.args)
            if row is None:
                raise web.HTTPNotFound()
            
            # Load the model
            try:
                model = await store.loader(conn, slug)
            except (NoDataFound, ValidationError, SlugNotFound) as err:
                self.logger.warning("Failed to load slug %r: %s", slug, err)
                raise web.HTTPNotFound()
            
            # Check access
            if not await can_access(request, principal, slug, "slug:describe", "slug:execute"):
                raise web.HTTPNotFound()
            
            return model

    async def describe_list(self, request: web.Request) -> web.Response:
        """GET .../queries/describe — 200 | 204 | 400 | 401 (spec §2 List)."""
        principal = await self._principal(request)
        store = await self._store(request)
        qp = self.query_parameters(request)
        if "q" in qp and not qp.get("search"):
            qp["search"] = qp.pop("q")
        try:
            params = PaginationParams.from_query_string(qp)
        except (ValueError, PydanticValidationError) as err:
            return self.error(response={"message": f"Invalid pagination params: {err}"}, status=400)
        
        predicate = build_program_predicate(principal, store)
        if predicate.deny_all:
            headers = {
                "X-Total-Count": "0",
                "X-Page": "1",
                "X-Page-Size": str(params.page_size),
                "X-Total-Pages": "0",
            }
            return self.no_content(headers=headers)
        
        # Extract extra filters
        extra = {k: v for k, v in qp.items() if k not in _PAGINATION_KEYS}
        
        # Build fields list
        fields = list(self.LIST_FIELDS)
        if params.fields:
            fields = ["query_slug", *[f for f in params.fields if f != "query_slug"]]
        
        # Build SQL components — the program pre-filter predicate.sql (if any)
        # must be ANDed into the WHERE clause; build_where_clause() never uses
        # bound placeholders itself (values are inlined, safely quoted), so
        # composing it with predicate.sql (which may use $1) is safe and the
        # only placeholder(s) in the final SQL come from predicate.sql.
        try:
            where = compose_where(build_where_clause(params, extra), predicate.sql)
            order_by = build_order_by(params, nulls_last=True)
            sql = build_scan_sql(store.schema, store.table, fields, where, order_by, QS_DESCRIBE_MAX_SCAN + 1)
        except ValueError as err:
            return self.error(response={"message": f"Invalid filter/sort: {err}"}, status=400)
        
        # Execute query
        async with await request.app['qs_connection'].acquire() as conn:
            rows = await conn.fetch_all(sql, *predicate.args)
            rows = [dict(r) for r in rows or []]
        
        # Handle truncation
        truncated = len(rows) > QS_DESCRIBE_MAX_SCAN
        if truncated:
            rows = rows[:QS_DESCRIBE_MAX_SCAN]
            self.logger.warning(
                "describe_list truncated %d rows to %d", len(rows) + 1, QS_DESCRIBE_MAX_SCAN
            )
        
        # Filter visible slugs
        slugs = [r["query_slug"] for r in rows]
        allowed = await filter_visible(request, principal, slugs, "slug:list", "slug:execute")
        allowed_set = set(allowed)
        visible = [r for r in rows if r["query_slug"] in allowed_set]
        
        # Pagination
        total = len(visible)
        total_pages = ceil(total / params.page_size) if total else 0
        page_rows = visible[params.offset: params.offset + params.page_size]
        
        # Build headers
        headers = {
            "X-Total-Count": str(total),
            "X-Page": str(params.page),
            "X-Page-Size": str(params.page_size),
            "X-Total-Pages": str(total_pages),
        }
        if truncated:
            headers["X-Truncated"] = "true"
        
        # Return response
        if total == 0:
            return self.no_content(headers=headers)
        
        return self.json_response(
            PaginatedResponse(
                data=page_rows,
                meta={
                    "page": params.page,
                    "page_size": params.page_size,
                    "total": total,
                    "total_pages": total_pages,
                }
            ).model_dump(),
            headers=headers
        )

    async def describe(self, request: web.Request) -> web.Response:
        """GET .../queries/{slug}/describe — 200 | 401 | 404 (spec §2 Detail)."""
        principal = await self._principal(request)
        store = await self._store(request)
        slug = request.match_info.get("slug", "")
        model = await self._load_visible(request, principal, store, slug)
        grants = await describe_grants(request, principal, slug)
        payload = describe_slug(
            model, grants,
            columns_link=request.path.rsplit("/", 1)[0] + "/columns",
            vocabulary_link=VOCABULARY_LINK,
        )
        return self.json_response(payload)

    async def columns(self, request: web.Request) -> web.Response:
        """GET .../queries/{slug}/columns — 200 | 401 | 404 (prepare, never execute)."""
        principal = await self._principal(request)
        store = await self._store(request)
        slug = request.match_info.get("slug", "")
        model = await self._load_visible(request, principal, store, slug)
        try:
            options = await self.json_data(request) or {}
        except (TypeError, ValueError):
            options = {}
        conditions = {**options, **self.query_parameters(request)}
        warnings: list[str] = []
        columns: list[dict] = []
        source = "unavailable"
        provider = None
        qs = QS(slug=slug, conditions=conditions, request=request)
        try:
            await qs.build_provider()
            provider = qs.get_source()
            query_str = str(provider.get_query() or "")
            names, _ = extract_placeholders(query_str)
            unresolved = [n for n in (names or []) if n not in RESERVED_PLACEHOLDERS]
            if unresolved:
                warnings.append(f"prepare_skipped: unresolved placeholders {sorted(unresolved)}")
            else:
                try:
                    cols = await asyncio.wait_for(provider.describe_columns(), QS_DESCRIBE_COLUMNS_TIMEOUT)
                    if cols:
                        columns = cols
                        source = "prepare"
                except (ParserError, ProviderError, DriverError, asyncio.TimeoutError) as err:
                    warnings.append(f"prepare_failed: {type(err).__name__}")
        except SlugNotFound:
            raise web.HTTPNotFound()
        except (ParserError, ProviderError, DriverError) as err:
            warnings.append(f"provider_unavailable: {type(err).__name__}")
        finally:
            try:
                await qs.close()
            except Exception:  # pylint: disable=broad-except  # noqa: BLE001, S110
                pass

        if source != "prepare":
            definition = provider.get_definition() if provider is not None else model
            attrs = (definition.get("attributes") if isinstance(definition, dict) else getattr(definition, "attributes", None)) or {}
            declared = attrs.get("columns") or []
            if declared:
                columns = [{"name": str(c), "type": None} for c in declared]
                source = "declared"

        return self.json_response(
            ColumnsResponse(slug=slug, columns=columns, columns_source=source, warnings=warnings).model_dump()
        )

    async def vocabulary(self, request: web.Request) -> web.Response:
        """GET /api/v1/queries/vocabulary — 200 | 401."""
        await self._principal(request)
        return self.json_response(build_vocabulary(udf_keywords(), pg_constants(), pg_udfs()))

