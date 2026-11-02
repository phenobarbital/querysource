"""Sidemantic-backed SemanticCompiler (FEAT-175 M4). The ONLY module importing sidemantic (lazily)."""
from __future__ import annotations

import functools
import logging
import re
from typing import Any

from ..exceptions import (
    SemanticCompilerUnavailable,
    SemanticFieldDenied,
    SemanticRequestError,
    SemanticResourceError,
)
from ..filters import FilterValidator
from ..loader import ModelLoader
from ..models import (
    TIME_GRAINS,
    CatalogField,
    CompilationTarget,
    CompiledQuery,
    SemanticModelRef,
    SemanticQuery,
)

_GRAIN_SEP = "__"
_ORDER_BY = re.compile(r"(?P<field>[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)(?:\s+(?P<dir>asc|desc))?", re.IGNORECASE)


def _sidemantic() -> Any:
    """Import sidemantic lazily, mapping a missing install to a 501 error."""
    try:
        import sidemantic  # noqa: PLC0415
    except ImportError as exc:
        raise SemanticCompilerUnavailable() from exc
    return sidemantic


def _security_error() -> type[Exception]:
    """Return sidemantic's ``SecurityError`` (``sidemantic.core.semantic_layer``)."""
    _sidemantic()
    from sidemantic.core.semantic_layer import SecurityError  # noqa: PLC0415

    return SecurityError


@functools.lru_cache(maxsize=1)
def compile_only_adapter_class() -> type:
    """Build ``CompileOnlyAdapter`` lazily (its base class lives in sidemantic)."""
    _sidemantic()
    from sidemantic.db.base import BaseDatabaseAdapter  # noqa: PLC0415

    class CompileOnlyAdapter(BaseDatabaseAdapter):
        """Never executes; ``dialect`` returns the target dialect."""

        def __init__(self, dialect: str) -> None:
            self._dialect = dialect

        @property
        def dialect(self) -> str:
            """Return the SQL dialect to compile for."""
            return self._dialect

        @property
        def raw_connection(self) -> Any:
            """There is no underlying connection."""
            return None

        def _refuse(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("CompileOnlyAdapter: execution is disabled (compile-only)")

        execute = executemany = fetchone = fetch_record_batch = get_tables = get_columns = _refuse

        def close(self) -> None:
            """Nothing to close."""
            return None

    return CompileOnlyAdapter


class SidemanticCompiler:
    """SemanticCompiler implementation backed by Sidemantic's compile-only layer."""

    def __init__(self, loader: ModelLoader) -> None:
        """Bind the compiler to a model loader (also the layer cache).

        Raises:
            SemanticCompilerUnavailable: If sidemantic is not installed.
        """
        self.logger = logging.getLogger(__name__)
        self._loader = loader
        _sidemantic()

    def _layer(self, model: SemanticModelRef, target: CompilationTarget) -> Any:
        """Return a cached ``SemanticLayer`` or build one with a compile-only adapter."""
        key = (model.model_version, target.dialect, model.format, model.scope)
        layer = self._loader.cached(key)
        if layer is not None:
            return layer
        from sidemantic import SemanticLayer  # noqa: PLC0415
        from sidemantic.loaders import load_from_file  # noqa: PLC0415

        adapter = compile_only_adapter_class()(target.dialect)
        options: dict[str, Any] = {}
        if model.format == "ossie":
            options = {
                "ossie_scope_id": model.scope,
                "ossie_adapter_options": {"target_dialect": target.dialect},
            }
        try:
            layer = SemanticLayer(
                connection=adapter,
                dialect=target.dialect,
                auto_register=False,
                default_limit=target.default_limit,
                max_limit=target.max_limit,
            )
            load_from_file(layer, model.source_path, **options)
        except Exception as exc:  # noqa: BLE001 - any loader failure is a resource problem
            self.logger.warning("Semantic model %s failed to load: %s", model.slug, exc)
            raise SemanticResourceError(f"Unable to load semantic model '{model.slug}'") from exc
        self._loader.store(key, layer)
        return layer

    def _fields(self, layer: Any) -> tuple[dict[str, str | None], dict[str, str | None]]:
        """Return ``(metrics, dimensions)`` as ``{qualified_name: type}`` maps."""
        metrics: dict[str, str | None] = {}
        dimensions: dict[str, str | None] = {}
        for model_name in layer.list_models():
            model = layer.get_model(model_name)
            for metric in model.metrics or []:
                metrics[f"{model_name}.{metric.name}"] = getattr(metric, "type", None)
            for dim in model.dimensions or []:
                dimensions[f"{model_name}.{dim.name}"] = getattr(dim, "type", None)
        return metrics, dimensions

    @staticmethod
    def _split_grain(name: str) -> tuple[str, str | None]:
        """Split ``dim__month`` into ``("dim", "month")`` when the suffix is a known grain."""
        base, sep, grain = name.rpartition(_GRAIN_SEP)
        if sep and grain in TIME_GRAINS:
            return base, grain
        return name, None

    def qualify(self, layer: Any, names: list[str], *, kind: str) -> list[str]:
        """Qualify ``'net_sales'`` to ``'orders.net_sales'`` when the suffix is unique.

        Args:
            layer: The Sidemantic layer.
            names: Requested names (qualified or bare); dimensions may carry a ``__grain`` suffix.
            kind: ``"metric"`` or ``"dimension"``.

        Raises:
            SemanticRequestError: On unknown or ambiguous names (400).
        """
        metrics, dimensions = self._fields(layer)
        known = metrics if kind == "metric" else dimensions
        qualified: list[str] = []
        for name in names:
            base, grain = self._split_grain(name) if kind == "dimension" else (name, None)
            if base in known:
                resolved = base
            else:
                matches = sorted(item for item in known if item.endswith(f".{base}"))
                if not matches:
                    raise SemanticRequestError(
                        f"Unknown {kind} {name!r}; available: {', '.join(sorted(known)) or '(none)'}"
                    )
                if len(matches) > 1:
                    raise SemanticRequestError(
                        f"Ambiguous {kind} {name!r}; use one of: {', '.join(matches)}"
                    )
                resolved = matches[0]
            qualified.append(f"{resolved}{_GRAIN_SEP}{grain}" if grain else resolved)
        return qualified

    def _apply_time_grain(self, layer: Any, dimensions: list[str], grain: str | None) -> list[str]:
        """Rewrite time dimensions to ``<dim>__<grain>``; explicit suffixes win."""
        if grain is None:
            return dimensions
        _, dim_types = self._fields(layer)
        rewritten: list[str] = []
        found_time = False
        for dim in dimensions:
            base, explicit = self._split_grain(dim)
            if dim_types.get(base) == "time":
                found_time = True
                rewritten.append(dim if explicit else f"{base}{_GRAIN_SEP}{grain}")
            else:
                rewritten.append(dim)
        if not found_time:
            raise SemanticRequestError(f"time_grain {grain!r} requires a time dimension in the request")
        return rewritten

    def _qualify_order_by(self, layer: Any, order_by: list[str]) -> list[str]:
        """Validate and qualify ``"field[__grain] [asc|desc]"`` entries; reject anything else.

        Raises:
            SemanticRequestError: If an entry is not a known metric/dimension with an
                optional direction (400). Free-form SQL never reaches the compiler.
        """
        metrics, dimensions = self._fields(layer)
        known = {**metrics, **dimensions}
        result: list[str] = []
        for entry in order_by:
            match = _ORDER_BY.fullmatch(entry.strip())
            if match is None:
                raise SemanticRequestError(
                    f"Invalid order_by entry {entry!r}; use '<field> [asc|desc]'"
                )
            head, direction = match.group("field"), (match.group("dir") or "").lower()
            base, grain = self._split_grain(head)
            if base not in known:
                matches = sorted(item for item in known if item.endswith(f".{base}"))
                if len(matches) != 1:
                    raise SemanticRequestError(
                        f"Unknown or ambiguous order_by field {head!r}; available: {', '.join(sorted(known))}"
                    )
                base = matches[0]
            qualified = f"{base}{_GRAIN_SEP}{grain}" if grain else base
            result.append(f"{qualified} {direction}".strip())
        return result

    def compile(
        self, model: SemanticModelRef, request: SemanticQuery, target: CompilationTarget
    ) -> CompiledQuery:
        """Qualify, grain, cap the limit, render filters, compile and map errors.

        Raises:
            SemanticRequestError: Invalid request (400).
            SemanticFieldDenied: Sidemantic security denied the query (403).
            SemanticResourceError: The model document could not be loaded (400).
        """
        from ...conf import QS_SEMANTIC_MAX_FILTERS  # noqa: PLC0415

        warnings = list(model.warnings)
        layer = self._layer(model, target)
        metrics = self.qualify(layer, request.metrics, kind="metric")
        dimensions = self.qualify(layer, request.dimensions, kind="dimension")
        dimensions = self._apply_time_grain(layer, dimensions, request.time_grain)

        limit = request.limit if request.limit is not None else target.default_limit
        if limit > target.max_limit:
            warnings.append(f"limit {limit} exceeds the maximum; capped to {target.max_limit}")
            limit = target.max_limit

        catalog_dims = {f.name: f for f in self.catalog(model, target) if f.kind == "dimension"}
        validator = FilterValidator(catalog_dims, dialect=target.dialect, max_filters=QS_SEMANTIC_MAX_FILTERS)
        filters = validator.render(request.filters)
        order_by = self._qualify_order_by(layer, request.order_by)

        try:
            sql = layer.compile(
                metrics=metrics or None,
                dimensions=dimensions or None,
                filters=filters or None,
                order_by=order_by or None,
                limit=limit,
                offset=request.offset or None,
                dialect=target.dialect,
                user_attributes=target.user_attributes,
            )
        except _security_error() as exc:
            self.logger.warning("Semantic security denied query on %s: %s", model.slug, exc)
            raise SemanticFieldDenied("security") from exc
        except (ValueError, KeyError) as exc:
            raise SemanticRequestError(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            if type(exc).__module__.startswith("sqlglot"):
                raise SemanticRequestError(f"Unable to compile the request: {exc}") from exc
            raise

        return CompiledQuery(
            sql=sql,
            parameters={"request": request.model_dump()},
            connection=target.connection,
            dialect=target.dialect,
            model_version=model.model_version,
            metrics=metrics,
            dimensions=dimensions,
            warnings=warnings,
        )

    def catalog(self, model: SemanticModelRef, target: CompilationTarget) -> list[CatalogField]:
        """List every metric and dimension of the model with its type and granularities."""
        layer = self._layer(model, target)
        fields: list[CatalogField] = []
        for model_name in layer.list_models():
            sem_model = layer.get_model(model_name)
            for metric in sem_model.metrics or []:
                fields.append(
                    CatalogField(
                        name=f"{model_name}.{metric.name}",
                        kind="metric",
                        type=getattr(metric, "agg", None) or getattr(metric, "type", None),
                        description=getattr(metric, "description", None),
                    )
                )
            for dim in sem_model.dimensions or []:
                is_time = getattr(dim, "type", None) == "time"
                fields.append(
                    CatalogField(
                        name=f"{model_name}.{dim.name}",
                        kind="dimension",
                        type=getattr(dim, "type", None),
                        description=getattr(dim, "description", None),
                        granularities=list(TIME_GRAINS) if is_time else [],
                    )
                )
        return fields
