"""Semantic provider (FEAT-175): compile a SemanticQuery and run it on the model's datasource."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, Union

from aiohttp import web
from asyncdb.exceptions import DriverError, NoDataFound, ProviderError

from ..auth.request_gate import get_request_session
from ..conf import QS_SEMANTIC_DEFAULT_LIMIT, QS_SEMANTIC_MAX_FILTERS, QS_SEMANTIC_MAX_LIMIT
from ..connections import QueryConnection
from ..exceptions import QueryException
from ..models import QueryModel
from ..semantic import governance
from ..semantic.compilers import get_compiler
from ..semantic.dialects import dialect_for_driver
from ..semantic.exceptions import SemanticError, SemanticGovernanceError, SemanticResourceError
from ..semantic.filters import FilterValidator
from ..semantic.loader import get_loader
from ..semantic.models import CompilationTarget, CompiledQuery, SemanticQuery, SemanticResource
from ..utils.functions import get_hash
from .abstract import BaseProvider


class semanticProvider(BaseProvider):
    """Compile a SemanticQuery against the slug's semantic model and run the SQL on its datasource."""

    __parser__ = None

    def __init__(
        self,
        slug: str = "",
        query: Any = None,
        qstype: str = "",
        connection: Callable = None,
        definition: Union[QueryModel, dict] = None,
        conditions: dict = None,
        request: web.Request = None,
        **kwargs: Any,
    ) -> None:
        """Parse the semantic resource and structured request before execution."""
        self._principal = kwargs.pop("principal", None)
        super().__init__(
            slug=slug,
            query=query,
            qstype=qstype,
            connection=connection,
            definition=definition,
            conditions=conditions,
            request=request,
            **kwargs,
        )
        self.resource: SemanticResource = SemanticResource.from_yaml(self._query or "")
        self.request_model: SemanticQuery = SemanticQuery.from_conditions(self._conditions)
        self.compiled: CompiledQuery | None = None
        self._datasource_name: str | None = None

    async def _resolve_principal(self) -> Any:
        """Return the explicit principal or derive one from the HTTP session."""
        if self._principal is not None:
            return self._principal
        if self._request is None:
            return None
        session = await get_request_session(self._request, logger=self._logger)
        return governance.principal_from_session(session)

    async def _resolve_datasource(self) -> tuple[str, str]:
        """Resolve the resource datasource or the configured default PostgreSQL connection."""
        manager = QueryConnection(loop=self._loop)
        if self.resource.connection:
            session = None
            app = None
            if self._request is not None:
                app = self._request.app
                session = await get_request_session(self._request, logger=self._logger)
            source, connection = await manager.datasource(self.resource.connection, session=session, app=app)
            self._connection = connection
            self._datasource_name = self.resource.connection
            self._driver = source.driver
            return self._datasource_name, source.driver

        self._datasource_name = "default"
        driver = manager.default_driver_name
        self._connection = manager.default_connection(driver=driver, dsn=manager.default_dsn)
        self._driver = driver
        return self._datasource_name, driver

    async def prepare_connection(self) -> None:
        """Resolve access, model, filters and compile the request in the required order."""
        principal = await self._resolve_principal()
        name, driver = await self._resolve_datasource()
        await governance.enforce_datasource(self._request, principal, name, driver, logger=self._logger)
        dialect, warning = dialect_for_driver(driver)
        revision = (
            getattr(self._definition, "revision", None)
            or self.kwargs.get("revision")
            or get_hash(self._query)
        )
        self._resource_revision = revision
        try:
            model = await asyncio.to_thread(get_loader().resolve, self._slug, revision, self.resource)
            await governance.enforce_query(self._request, principal, self._slug, self.request_model, logger=self._logger)
            filters, roles = governance.governance_filters(self.resource, principal)
            target = CompilationTarget(
                connection=self.resource.connection,
                driver=driver,
                dialect=dialect,
                user_attributes=governance.user_attributes(principal),
                default_limit=QS_SEMANTIC_DEFAULT_LIMIT,
                max_limit=QS_SEMANTIC_MAX_LIMIT,
            )
            compiler = get_compiler(self.resource.semantic.compiler)
            catalog = {field.name: field for field in compiler.catalog(model, target) if field.kind == "dimension"}
            try:
                FilterValidator(catalog, dialect=dialect, max_filters=QS_SEMANTIC_MAX_FILTERS).render(filters)
            except SemanticError as exc:
                role = ", ".join(roles) if roles else "default"
                self._logger.error("Invalid governance filter for slug=%s role=%s: %s", self._slug, role, exc)
                raise SemanticGovernanceError("Invalid semantic governance filter") from exc
            merged = self.request_model.model_copy(update={"filters": [*self.request_model.filters, *filters]})
            self.compiled = compiler.compile(model, merged, target)
        except (SemanticError, QueryException):
            raise
        except Exception as exc:  # configuration boundaries are normalized for QS callers
            self._logger.exception("Unable to prepare semantic resource %s: %s", self._slug, exc)
            raise SemanticResourceError(f"Unable to prepare semantic resource '{self._slug}'") from exc

        if warning:
            self.compiled.warnings.append(warning)
        self.compiled.parameters["request"] = self.request_model.model_dump()
        self.compiled.parameters["roles"] = roles
        self._query = self.compiled.sql

    async def dry_run(self) -> tuple[str, None]:
        """Return the compiled literal SQL without executing it."""
        return self.compiled.sql, None

    async def query(self) -> list:
        """Execute the compiled literal SQL using the standard provider result contract."""
        error = None
        try:
            async with await self._connection.connection() as conn:
                result, error = await conn.query(self.compiled.sql)
            if error:
                return [result, error]
            if result:
                self._result = result
                return [self._result, error]
            raise self.NotFound("Semantic: Empty Result")
        except NoDataFound as exc:
            raise self.NotFound(f"Semantic: Empty Result: {exc}") from exc
        except (ProviderError, DriverError) as exc:
            raise QueryException(f"Query Error: {exc}") from exc
        except QueryException:
            raise
        except Exception as exc:
            self._logger.exception(exc, stack_info=False)
            raise self.Error("Semantic query: Uncaught Error", exception=exc, code=406) from exc

    def checksum(self) -> str:
        """Return a cache checksum incorporating the compiled semantic model identity."""
        if self.compiled is None:
            return super().checksum()
        compiled = self.compiled
        return get_hash(f"{compiled.sql}|{compiled.model_version}|{compiled.dialect}|{compiled.connection}")

    @staticmethod
    def _output_name(name: str) -> str:
        """Convert a compiler-qualified output name to its SQL alias."""
        return name.rsplit(".", maxsplit=1)[-1]

    async def columns(self) -> list[str]:
        """Return output aliases for compiled metrics and dimensions."""
        if self.compiled is None:
            return []
        self._columns = [self._output_name(name) for name in [*self.compiled.metrics, *self.compiled.dimensions]]
        return self._columns

    async def describe_columns(self) -> list[dict]:
        """Return output aliases paired with types from the compiler catalog."""
        if self.compiled is None:
            return []
        target = CompilationTarget(
            connection=self.resource.connection,
            driver=self._driver,
            dialect=self.compiled.dialect,
            default_limit=QS_SEMANTIC_DEFAULT_LIMIT,
            max_limit=QS_SEMANTIC_MAX_LIMIT,
        )
        model = await asyncio.to_thread(get_loader().resolve, self._slug, self._resource_revision, self.resource)
        catalog = {field.name: field.type for field in get_compiler(self.resource.semantic.compiler).catalog(model, target)}
        return [
            {"name": self._output_name(name), "type": catalog.get(name.split("__", maxsplit=1)[0])}
            for name in [*self.compiled.metrics, *self.compiled.dimensions]
        ]

    async def close(self) -> None:
        """Release the provider's datasource reference."""
        self._connection = None
