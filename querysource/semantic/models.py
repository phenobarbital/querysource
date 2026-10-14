"""Public semantic-layer contract (FEAT-175 §2 Data Models)."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .exceptions import SemanticRequestError, SemanticResourceError

FILTER_OPS: tuple[str, ...] = (
    "=",
    "!=",
    ">",
    ">=",
    "<",
    "<=",
    "in",
    "not in",
    "between",
    "like",
    "ilike",
    "is null",
    "is not null",
)
TIME_GRAINS: tuple[str, ...] = ("day", "week", "month", "quarter", "year")
RESERVED_CONDITION_KEYS: tuple[str, ...] = (
    "refresh",
    "_output_options",
    "_csv_options",
    "_graph_options",
    "_report_options",
    "queryformat",
    "_download",
    "_filename",
)


class SemanticFilter(BaseModel):
    """Structured filter ``{field, op, value}``."""

    model_config = ConfigDict(extra="forbid")
    field: str
    op: str
    value: Any | None = None

    @field_validator("op")
    @classmethod
    def _check_op(cls, value: str) -> str:
        """Normalize to lower case and require membership in FILTER_OPS."""
        normalized = value.strip().lower()
        if normalized not in FILTER_OPS:
            raise ValueError(f"Unsupported semantic filter operator: {value!r}")
        return normalized


class SemanticQuery(BaseModel):
    """Structured request; unknown keys rejected; time_grain in TIME_GRAINS."""

    model_config = ConfigDict(extra="forbid")
    metrics: list[str] = Field(default_factory=list, description="Catalog metric names")
    dimensions: list[str] = Field(default_factory=list)
    filters: list[SemanticFilter | str] = Field(default_factory=list)
    time_grain: str | None = None
    order_by: list[str] = Field(default_factory=list)
    limit: int | None = None
    offset: int = 0

    @field_validator("time_grain")
    @classmethod
    def _check_time_grain(cls, value: str | None) -> str | None:
        """Require the optional grain to be one of the supported values."""
        if value is not None and value not in TIME_GRAINS:
            raise ValueError(f"Unsupported semantic time grain: {value!r}")
        return value

    @field_validator("limit")
    @classmethod
    def _check_limit(cls, value: int | None) -> int | None:
        """Require a supplied row limit to be positive."""
        if value is not None and value < 1:
            raise ValueError("Semantic query limit must be at least 1")
        return value

    @field_validator("offset")
    @classmethod
    def _check_offset(cls, value: int) -> int:
        """Require the result offset to be non-negative."""
        if value < 0:
            raise ValueError("Semantic query offset must be non-negative")
        return value

    @classmethod
    def from_conditions(cls, conditions: dict[str, Any] | None) -> "SemanticQuery":
        """Drop transport keys and translate validation errors to a request error."""
        values = dict(conditions or {})
        for key in RESERVED_CONDITION_KEYS:
            values.pop(key, None)
        try:
            return cls.model_validate(values)
        except ValidationError as exc:
            raise SemanticRequestError(str(exc)) from exc


class SemanticDefinition(BaseModel):
    """``semantic:`` block of the resource YAML."""

    model_config = ConfigDict(extra="forbid")
    format: Literal["ossie", "sidemantic"] = "ossie"
    compiler: Literal["sidemantic"] = "sidemantic"
    definition: str | dict[str, Any]
    scope: str | None = None


class GovernanceBlock(BaseModel):
    """Role-keyed row filters; ``default`` applies to everybody."""

    model_config = ConfigDict(extra="forbid")
    row_filters: dict[str, list[SemanticFilter | str]] = Field(default_factory=dict)


class SemanticResource(BaseModel):
    """Parsed ``query_raw`` of a semantic slug."""

    name: str
    kind: Literal["semantic"]
    connection: str | None = None
    semantic: SemanticDefinition
    governance: GovernanceBlock = Field(default_factory=GovernanceBlock)

    @classmethod
    def from_yaml(cls, text: str) -> "SemanticResource":
        """Safely parse a resource document and translate every failure to 400."""
        try:
            document = yaml.safe_load(text)
            if not isinstance(document, dict):
                raise ValueError("Semantic resource YAML must contain a mapping")
            return cls.model_validate(document)
        except (yaml.YAMLError, ValidationError, TypeError, ValueError) as exc:
            raise SemanticResourceError(str(exc)) from exc


class SemanticModelRef(BaseModel):
    """Resolved semantic document supplied to a compiler."""

    slug: str
    revision: str
    format: str
    scope: str | None = None
    source_path: Path
    model_version: str
    warnings: list[str] = Field(default_factory=list)


class CompilationTarget(BaseModel):
    """Datasource-specific compilation settings."""

    connection: str | None = None
    driver: str
    dialect: str
    user_attributes: dict[str, Any] | None = None
    default_limit: int
    max_limit: int


class CompiledQuery(BaseModel):
    """Literal SQL and metadata emitted by a semantic compiler."""

    sql: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    connection: str | None = None
    dialect: str
    model_version: str
    metrics: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class CatalogField(BaseModel):
    """A governed metric or dimension available from a semantic catalog."""

    name: str
    kind: Literal["metric", "dimension"]
    type: str | None = None
    description: str | None = None
    granularities: list[str] = Field(default_factory=list)
    granted: bool = True


class CatalogPayload(BaseModel):
    """Serializable semantic catalog response."""

    slug: str
    model_version: str
    dialect: str
    models: list[str]
    metrics: list[CatalogField]
    dimensions: list[CatalogField]
    row_filters_applied: list[str] = Field(default_factory=list)
