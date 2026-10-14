"""Semantic catalog endpoint (FEAT-175 M9)."""
from __future__ import annotations

import hashlib

from aiohttp import web

from ..auth._resource_types import ResourceType
from ..semantic import governance
from ..semantic.compilers import get_compiler
from ..semantic.dialects import dialect_for_driver
from ..semantic.exceptions import SemanticCompilerUnavailable, SemanticError
from ..semantic.loader import get_loader
from ..semantic.models import CatalogField, CatalogPayload, CompilationTarget, SemanticResource
from .describe import QueryDescribe


class SemanticCatalog(QueryDescribe):
    """Read-only catalog for semantic slugs."""

    async def catalog(self, request: web.Request) -> web.Response:
        """Return a governed semantic catalog."""
        slug = request.match_info["slug"]
        principal = await self._principal(request)
        store = await self._store(request)
        loaded = await self._load_visible(request, principal, store, slug)
        model = getattr(loaded, "runtime", loaded)
        if getattr(model, "provider", None) != "semantic":
            raise web.HTTPNotFound()
        await self._enforce_pbac(request, ResourceType.SEMANTIC, slug, "semantic:execute")

        try:
            resource = SemanticResource.from_yaml(model.query_raw)
        except SemanticError as err:
            self.logger.warning("Invalid semantic resource %r: %s", slug, err)
            raise web.HTTPNotFound() from None

        revision = getattr(loaded, "revision", None) or hashlib.sha256(model.query_raw.encode()).hexdigest()
        model_ref = get_loader().resolve(slug, revision, resource)
        driver = getattr(model, "dwh_driver", None) or getattr(model, "driver", None) or "pg"
        dialect, _warning = dialect_for_driver(driver)
        from ..conf import QS_SEMANTIC_DEFAULT_LIMIT, QS_SEMANTIC_MAX_LIMIT

        target = CompilationTarget(
            connection=resource.connection,
            driver=driver,
            dialect=dialect,
            default_limit=QS_SEMANTIC_DEFAULT_LIMIT,
            max_limit=QS_SEMANTIC_MAX_LIMIT,
        )
        try:
            fields = get_compiler(resource.semantic.compiler).catalog(model_ref, target)
        except SemanticCompilerUnavailable as err:
            raise web.HTTPNotImplemented(text=str(err)) from None

        qs_principal = governance.principal_from_session(await self._get_user_session(request))
        governed: list[CatalogField] = []
        for field in fields:
            decision = await governance.check(
                request,
                qs_principal,
                f"{slug}:{field.kind}:{field.name.rsplit('.', 1)[-1]}",
                "semantic:use",
                logger=self.logger,
            )
            if decision.allowed:
                governed.append(field)
            else:
                governed.append(CatalogField(name=field.name, kind=field.kind, granted=False))

        models: list[str] = []
        for field in governed:
            model_name = field.name.split(".", 1)[0]
            if model_name not in models:
                models.append(model_name)
        metrics = [field for field in governed if field.kind == "metric"]
        dimensions = [field for field in governed if field.kind == "dimension"]
        _filters, matched_roles = governance.governance_filters(resource, qs_principal)
        payload = CatalogPayload(
            slug=slug,
            model_version=model_ref.model_version,
            dialect=dialect,
            models=models,
            metrics=metrics,
            dimensions=dimensions,
            row_filters_applied=matched_roles,
        )
        return self.json_response(payload.model_dump())
