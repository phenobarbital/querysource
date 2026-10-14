"""Two-step Python API over QS (FEAT-175 M8)."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .models import CompiledQuery, SemanticQuery

if TYPE_CHECKING:
    from ..auth.principal import QSPrincipal


async def compile(  # noqa: A001
    resource: str,
    *,
    metrics: list[str],
    dimensions: list[str] | None = None,
    filters: list[dict | str] | None = None,
    time_grain: str | None = None,
    order_by: list[str] | None = None,
    limit: int | None = None,
    offset: int = 0,
    tenant: str | None = None,
    principal: QSPrincipal | None = None,
) -> CompiledQuery:
    """Compile a semantic request for slug ``resource`` without executing it."""
    from ..queries.qs import QS

    request = SemanticQuery(
        metrics=metrics,
        dimensions=dimensions or [],
        filters=filters or [],
        time_grain=time_grain,
        order_by=order_by or [],
        limit=limit,
        offset=offset,
    )
    qs = QS(slug=resource, conditions=request.model_dump(), tenant=tenant, principal=principal)
    try:
        await qs.build_provider()
        compiled = qs._qs.compiled.model_copy(deep=True)
        compiled.parameters["slug"] = resource
        return compiled
    finally:
        await qs.close()


async def execute(
    compiled: CompiledQuery,
    *,
    tenant: str | None = None,
    principal: QSPrincipal | None = None,
    output_format: str | None = None,
) -> Any:
    """Re-run the embedded request through QS and return the query result."""
    from ..queries.qs import QS

    slug = compiled.parameters["slug"]
    conditions = compiled.parameters["request"]
    qs = QS(slug=slug, conditions=conditions, tenant=tenant, principal=principal)
    try:
        await qs.build_provider()
        return await qs.query(output_format)
    finally:
        await qs.close()
