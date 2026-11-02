# TASK-897: Semantic models, compiler Protocol and exceptions

**Feature**: FEAT-175 — Querysource Semantic Layer (Ossie models compiled by Sidemantic)
**Spec**: `sdd/specs/semantic-layer.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 1. Creates the QuerySource-owned public contract of the semantic
layer: pydantic v2 request/resource/compile/catalog models (spec §2 Data
Models), the `SemanticCompiler` Protocol and the `SemanticError` hierarchy.
Every other FEAT-175 task imports from here. This module MUST NOT import
`sidemantic`, `duckdb` or `sqlglot` (AC-3).

---

## Scope

- Create the `querysource/semantic/` package (`__init__.py`, `models.py`, `protocol.py`, `exceptions.py`).
- Implement every model of spec §2 Data Models with the constraints of §3 M1 (`extra="forbid"` on `SemanticQuery`, `time_grain ∈ TIME_GRAINS`, `op ∈ FILTER_OPS`, `limit >= 1` when set, `offset >= 0`).
- Implement `SemanticQuery.from_conditions()` and `SemanticResource.from_yaml()`.
- Implement the exception hierarchy with the codes fixed in §3 M1.
- Create `tests/semantic/__init__.py`, the shared `tests/semantic/conftest.py` (spec §4 fixtures) and `tests/semantic/test_models.py`.

**NOT in scope**: filters rendering (TASK-900), loader (TASK-901), compiler (TASK-903), `compile`/`execute` re-exports in `__init__.py` (TASK-905), `pyproject.toml` (TASK-898).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/semantic/__init__.py` | CREATE | Package; re-exports models + exceptions |
| `querysource/semantic/models.py` | CREATE | pydantic v2 models |
| `querysource/semantic/protocol.py` | CREATE | `SemanticCompiler` Protocol |
| `querysource/semantic/exceptions.py` | CREATE | `SemanticError` hierarchy |
| `tests/semantic/__init__.py` | CREATE | empty package marker |
| `tests/semantic/conftest.py` | CREATE | shared fixtures (spec §4) |
| `tests/semantic/test_models.py` | CREATE | M1 unit tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.exceptions import QueryException      # verified: querysource/exceptions.py:6
from pydantic import BaseModel, ConfigDict, Field, field_validator, ValidationError  # pydantic 2.12.5 installed; already used in querysource/handlers/describe.py:12-13
import yaml                                            # pyyaml, transitive dependency (spec §7 External Dependencies)
```

### Existing Signatures to Use
```python
# querysource/exceptions.py:6
class QueryException(Exception):
    default_code: int = 500
    code: int = 500
    def __init__(self, message: str, code: int | None = None, **kwargs): ...
    # a missing/0 code falls back to the class's default_code
```

### Does NOT Exist
- ~~`querysource/semantic/`~~ — created by this task.
- ~~`QueryModel.kind`~~ — `kind: semantic` lives inside the YAML in `query_raw`.
- ~~`SemanticLayer.compile()` returning an object~~ — irrelevant here; `CompiledQuery` is ours.
- ~~`tests/semantic/`~~ — created by this task.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/semantic/__init__.py", "action": "CREATE"},
    {"path": "querysource/semantic/models.py", "action": "CREATE"},
    {"path": "querysource/semantic/protocol.py", "action": "CREATE"},
    {"path": "querysource/semantic/exceptions.py", "action": "CREATE"},
    {"path": "tests/semantic/__init__.py", "action": "CREATE"},
    {"path": "tests/semantic/conftest.py", "action": "CREATE"},
    {"path": "tests/semantic/test_models.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/exceptions.py#QueryException"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- pydantic v2 only (`model_config = ConfigDict(...)`, `field_validator`), never `datamodel` here (brainstorm Q2).
- Validation failures inside `from_conditions` / `from_yaml` must become `SemanticRequestError` / `SemanticResourceError` (400) — never a raw `pydantic.ValidationError` (AC-22).
- No import of sidemantic / sqlglot / duckdb anywhere in this task (AC-3).

### References in Codebase
- `querysource/exceptions.py` — `QueryException` pattern (`default_code`).
- `tests/e2e/conftest.py:125` — `definitions` fixture reused by `semantic_definitions`.

---

## Implementation Blueprint

### Steps (in order)
1. Write `exceptions.py` first — *why*: `models.py` raises these.
2. Write `models.py` with every class of spec §2, then `protocol.py` — *why*: the Protocol signature references the models.
3. Write `__init__.py` re-exports — *why*: later tasks import from `querysource.semantic`.
4. Write the test conftest and `test_models.py`; run validation — *why*: AC-19.

### `querysource/semantic/exceptions.py` (CREATE)
```python
"""Semantic-layer exceptions (FEAT-175). Every semantic failure derives from SemanticError."""
from __future__ import annotations

from ..exceptions import QueryException


class SemanticError(QueryException):
    """Base of every semantic-layer error; re-raised unchanged by QS.build_provider."""

    default_code: int = 500


class SemanticRequestError(SemanticError):
    """Invalid caller request: unknown field, bad operator/grain/limit, unparsable filter."""

    default_code: int = 400


class SemanticResourceError(SemanticError):
    """Malformed resource YAML / model document, path escape, or non-SQL datasource."""

    default_code: int = 400


class SemanticGovernanceError(SemanticError):
    """Invalid governance.row_filters entry (fail-closed configuration error)."""

    default_code: int = 500


class SemanticCompilerUnavailable(SemanticError):
    """The optional compiler is not installed."""

    default_code: int = 501

    def __init__(self, message: str | None = None, **kwargs) -> None:
        super().__init__(
            message or "Semantic compiler unavailable: install querysource[semantic]",
            code=501,
            **kwargs,
        )


class SemanticFieldDenied(SemanticError):
    """PBAC or Sidemantic security denied a metric/dimension; names the field."""

    default_code: int = 403

    def __init__(self, field: str, message: str | None = None, **kwargs) -> None:
        self.field = field
        super().__init__(message or f"Access denied to semantic field '{field}'", code=403, **kwargs)
```
**Why this shape**: codes are fixed by spec §3 M1. Pass `code=` explicitly where the constructor is
overridden because `QueryException.__init__` (exceptions.py:6) resolves `code` itself — FILL IN below.

### `querysource/semantic/models.py` (CREATE)
```python
"""Public semantic-layer contract (FEAT-175 §2 Data Models). pydantic v2; no compiler import."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .exceptions import SemanticRequestError, SemanticResourceError

FILTER_OPS: tuple[str, ...] = ("=", "!=", ">", ">=", "<", "<=", "in", "not in", "between",
                               "like", "ilike", "is null", "is not null")
TIME_GRAINS: tuple[str, ...] = ("day", "week", "month", "quarter", "year")
RESERVED_CONDITION_KEYS: tuple[str, ...] = ("refresh", "_output_options", "_csv_options",
                                            "_graph_options", "_report_options", "queryformat",
                                            "_download", "_filename")


class SemanticFilter(BaseModel):
    """Structured filter `{field, op, value}`."""

    model_config = ConfigDict(extra="forbid")
    field: str
    op: str
    value: Any | None = None

    @field_validator("op")
    @classmethod
    def _check_op(cls, value: str) -> str:
        """Normalize to lower case and require membership in FILTER_OPS."""
        # FILL IN: lower/strip, raise ValueError when not in FILTER_OPS — bounded by AC-13


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
    # FILL IN: field_validators — time_grain ∈ TIME_GRAINS; limit None or >= 1; offset >= 0 (spec §4 test_semantic_query_rejects_bad_grain_and_limit)

    @classmethod
    def from_conditions(cls, conditions: dict[str, Any] | None) -> "SemanticQuery":
        """Drop RESERVED_CONDITION_KEYS, validate the rest; ValidationError → SemanticRequestError(400)."""
        # FILL IN: copy dict, pop reserved keys, cls.model_validate(...) inside try/except ValidationError
```
*(continue in the same file — kept as a second block for size)*
```python
class SemanticDefinition(BaseModel):
    """`semantic:` block of the resource YAML."""

    model_config = ConfigDict(extra="forbid")
    format: Literal["ossie", "sidemantic"] = "ossie"
    compiler: Literal["sidemantic"] = "sidemantic"
    definition: str | dict[str, Any]
    scope: str | None = None


class GovernanceBlock(BaseModel):
    """Role-keyed row filters; 'default' applies to everybody."""

    model_config = ConfigDict(extra="forbid")
    row_filters: dict[str, list[SemanticFilter | str]] = Field(default_factory=dict)


class SemanticResource(BaseModel):
    """Parsed `query_raw` of a semantic slug."""

    name: str
    kind: Literal["semantic"]
    connection: str | None = None
    semantic: SemanticDefinition
    governance: GovernanceBlock = Field(default_factory=GovernanceBlock)

    @classmethod
    def from_yaml(cls, text: str) -> "SemanticResource":
        """yaml.safe_load + validate; any failure → SemanticResourceError(400)."""
        # FILL IN: safe_load (yaml.YAMLError), require a mapping, model_validate (ValidationError)


class SemanticModelRef(BaseModel):
    slug: str
    revision: str
    format: str
    scope: str | None = None
    source_path: Path
    model_version: str
    warnings: list[str] = Field(default_factory=list)


class CompilationTarget(BaseModel):
    connection: str | None = None
    driver: str
    dialect: str
    user_attributes: dict[str, Any] | None = None
    default_limit: int
    max_limit: int


class CompiledQuery(BaseModel):
    sql: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    connection: str | None = None
    dialect: str
    model_version: str
    metrics: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class CatalogField(BaseModel):
    name: str
    kind: Literal["metric", "dimension"]
    type: str | None = None
    description: str | None = None
    granularities: list[str] = Field(default_factory=list)
    granted: bool = True


class CatalogPayload(BaseModel):
    slug: str
    model_version: str
    dialect: str
    models: list[str]
    metrics: list[CatalogField]
    dimensions: list[CatalogField]
    row_filters_applied: list[str] = Field(default_factory=list)
```
**Why this shape**: verbatim from spec §2. `SemanticModelRef.warnings` is an addition so the loader
(TASK-901) can carry the "inline wins" warning to `CompiledQuery.warnings` (AC-7). Do not rename fields —
TASK-900..906 depend on them.

### `querysource/semantic/protocol.py` (CREATE)
```python
"""Compiler adapter contract (FEAT-175 M1)."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from .models import CatalogField, CompilationTarget, CompiledQuery, SemanticModelRef, SemanticQuery


@runtime_checkable
class SemanticCompiler(Protocol):
    """Implementations never leak their internal objects through CompiledQuery."""

    def compile(self, model: SemanticModelRef, request: SemanticQuery,
                target: CompilationTarget) -> CompiledQuery:
        """Compile `request` to literal SQL for `target.dialect`."""

    def catalog(self, model: SemanticModelRef, target: CompilationTarget) -> list[CatalogField]:
        """List metrics and dimensions of the model."""
```

### `querysource/semantic/__init__.py` (CREATE)
```python
"""QuerySource semantic layer (FEAT-175). Importing this package never imports sidemantic."""
from .exceptions import (SemanticCompilerUnavailable, SemanticError, SemanticFieldDenied,
                         SemanticGovernanceError, SemanticRequestError, SemanticResourceError)
from .models import (FILTER_OPS, TIME_GRAINS, CatalogField, CatalogPayload, CompilationTarget,
                     CompiledQuery, SemanticFilter, SemanticModelRef, SemanticQuery,
                     SemanticResource)
from .protocol import SemanticCompiler

__all__ = (
    'FILTER_OPS', 'TIME_GRAINS', 'CatalogField', 'CatalogPayload', 'CompilationTarget',
    'CompiledQuery', 'SemanticCompiler', 'SemanticCompilerUnavailable', 'SemanticError',
    'SemanticFieldDenied', 'SemanticFilter', 'SemanticGovernanceError', 'SemanticModelRef',
    'SemanticQuery', 'SemanticRequestError', 'SemanticResource', 'SemanticResourceError',
)
```
**Why**: TASK-905 appends `compile`/`execute` to this file; keep `__all__` a tuple so it can extend it.

### `tests/semantic/conftest.py` (CREATE)
```python
"""Shared fixtures for FEAT-175 semantic-layer tests."""
import pytest

INLINE_RESOURCE_YAML = """\
name: epson.sales
kind: semantic
semantic:
  format: sidemantic
  definition:
    # FILL IN: Sidemantic-native model 'orders' (table orders) with metrics revenue (sum amount),
    # order_count (count), dimensions region (categorical) and order_date (time, day granularity)
governance:
  row_filters:
    default: []
    analyst:
      - {field: region, op: in, value: ["ES", "PT"]}
"""


@pytest.fixture
def sidemantic_available():
    pytest.importorskip("sidemantic")


@pytest.fixture
def inline_resource_yaml() -> str:
    return INLINE_RESOURCE_YAML


@pytest.fixture
def semantic_definitions(definitions, inline_resource_yaml):
    """Reuses tests/e2e/conftest.py::definitions (FakeDefinitionRepository)."""
    definitions.add(query_slug="epson.sales", provider="semantic", query_raw=inline_resource_yaml)
    return definitions


@pytest.fixture
def fake_evaluator(monkeypatch):
    """Patch resolve_evaluator to (True, evaluator); evaluator denies resources in `deny`."""
    # FILL IN: build an object with a `deny: set[str]` attribute and an async check_access
    # compatible with querysource.auth.enforcement.evaluate; patch
    # querysource.auth.enforcement.resolve_evaluator AND the name imported in
    # querysource.semantic.governance (if already imported) to return (True, evaluator)
```
**Why**: spec §4 fixtures. `definitions` lives in `tests/e2e/conftest.py`; FILL IN: if it is not visible
from `tests/semantic/`, import it explicitly (`from tests.e2e.conftest import definitions  # noqa: F401`).

### FILL IN checklist
- [ ] `models.py::SemanticFilter._check_op` — op allow-list; bounded by AC-13
- [ ] `models.py::SemanticQuery` validators — grain/limit/offset; bounded by AC-14, AC-15
- [ ] `models.py::SemanticQuery.from_conditions` — strip reserved keys, 400 on unknown; bounded by S5
- [ ] `models.py::SemanticResource.from_yaml` — safe_load only, 400 on malformed; bounded by S9
- [ ] `exceptions.py` — confirm `code` attribute ends up as the class code for every subclass
- [ ] `conftest.py` — inline model body and `fake_evaluator`
- [ ] `test_models.py` — bodies of the tests below

---

## Acceptance Criteria

- [ ] `from querysource.semantic import SemanticQuery, CompiledQuery, SemanticError` works and imports no `sidemantic`/`sqlglot`/`duckdb` (`python -c "import querysource.semantic, sys; assert 'sidemantic' not in sys.modules and 'sqlglot' not in sys.modules"`).
- [ ] All models are pydantic v2 `BaseModel` (AC-9).
- [ ] `SemanticRequestError().code == 400`, `SemanticFieldDenied('x').code == 403`, `SemanticCompilerUnavailable().code == 501` and its message names `querysource[semantic]` (AC-3).
- [ ] `pytest tests/semantic/test_models.py -q` passes; `ruff check querysource/semantic tests/semantic` clean.

---

## Validation Commands
- `pytest tests/semantic/test_models.py -q`

---

## Test Specification

```python
# tests/semantic/test_models.py
import pytest
from querysource.semantic import (SemanticQuery, SemanticResource, SemanticRequestError,
                                  SemanticResourceError, SemanticFieldDenied, SemanticCompilerUnavailable)


def test_semantic_query_from_conditions_strips_reserved_keys():
    q = SemanticQuery.from_conditions({"metrics": ["revenue"], "refresh": True,
                                       "_output_options": {}, "queryformat": "json"})
    assert q.metrics == ["revenue"]
    with pytest.raises(SemanticRequestError):
        SemanticQuery.from_conditions({"metrics": ["revenue"], "sql": "select 1"})


def test_semantic_query_rejects_bad_grain_and_limit():
    for bad in ({"time_grain": "hour"}, {"limit": -1}, {"offset": -5}):
        with pytest.raises(SemanticRequestError):
            SemanticQuery.from_conditions(bad)


def test_semantic_resource_from_yaml_inline_and_path(inline_resource_yaml):
    assert isinstance(SemanticResource.from_yaml(inline_resource_yaml).semantic.definition, dict)
    # FILL IN: path form (definition: models/x.ossie.yaml) → str; kind: other → SemanticResourceError;
    # "::: not yaml" → SemanticResourceError


def test_error_codes():
    assert SemanticFieldDenied("margin").code == 403
    assert "querysource[semantic]" in str(SemanticCompilerUnavailable())
```

---

## Agent Instructions

1. Work in the feature worktree (`python -m scripts.sdd.ensure_worktree --slug semantic-layer --feature-id FEAT-175`).
2. Read the spec; verify the Codebase Contract; implement from the blueprint; complete every FILL IN.
3. Run the Validation Commands; commit only the listed files.
4. Close with `scripts/sdd/close_task.sh TASK-897 semantic-layer verified`; fill the Completion Note.

---

## Completion Note

**Completed by**: sdd-worker (coder seat gpt-5.6-terra, codex, 1 attempt, 269s)
**Date**: 2026-10-15
**Notes**: Merge-tier validation green (7 passed). Review recorded as coder-review:07a81ad43553588e494f43aa, no fix commits. Closed via close_task.sh (finalize_task needs a durable review EvidenceRef no available tool produces). Cython extensions were rebuilt inside the worktree (prebuilt .so were stale vs .pyx).

**Deviations from spec**: none
