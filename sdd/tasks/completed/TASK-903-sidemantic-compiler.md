# TASK-903: Sidemantic compiler (compile-only adapter)

**Feature**: FEAT-175 — Querysource Semantic Layer (Ossie models compiled by Sidemantic)
**Spec**: `sdd/specs/semantic-layer.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: L (4-8h)
**Depends-on**: TASK-897, TASK-898, TASK-900, TASK-901
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 4 (NOT delegation-eligible: Sidemantic's `describe_models()` shape and the
`SecurityError` import path must be verified against the installed 0.12.x wheel). This is the
**only** module that imports `sidemantic`, lazily. It builds a `SemanticLayer` bound to a
`CompileOnlyAdapter` (no DuckDB connection, AC-4), loads documents with public `load_from_file`
(AC-8), qualifies names, applies `time_grain`, caps `limit`, renders filters via
`FilterValidator` (TASK-900), compiles with `user_attributes`, and maps errors.

---

## Scope

- `querysource/semantic/compilers/__init__.py` with `get_compiler(name='sidemantic')` factory (lazy).
- `querysource/semantic/compilers/sidemantic.py`: `CompileOnlyAdapter`, `SidemanticCompiler` (`_layer`, `qualify`, `compile`, `catalog`).
- Tests `tests/semantic/test_sidemantic_compiler.py` (sidemantic-dependent ones use `sidemantic_available`).

**NOT in scope**: PBAC (TASK-902), governance filter collection (provider, TASK-904).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/semantic/compilers/__init__.py` | CREATE | lazy factory |
| `querysource/semantic/compilers/sidemantic.py` | CREATE | adapter + compiler |
| `tests/semantic/test_sidemantic_compiler.py` | CREATE | compiler tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.semantic.models import (CatalogField, CompilationTarget, CompiledQuery,
                                         SemanticModelRef, SemanticQuery, TIME_GRAINS)   # TASK-897
from querysource.semantic.exceptions import (SemanticCompilerUnavailable, SemanticFieldDenied,
                                             SemanticRequestError, SemanticResourceError)  # TASK-897
from querysource.semantic.loader import ModelLoader, get_loader          # TASK-901
from querysource.semantic.filters import FilterValidator                 # TASK-900
# External — sidemantic 0.12.0 (spec §6, verified upstream; RE-VERIFY against the installed wheel):
from sidemantic import SemanticLayer                                     # sidemantic/core/semantic_layer.py:70
from sidemantic.loaders import load_from_file                            # sidemantic/loaders.py:693
from sidemantic.db.base import BaseDatabaseAdapter                       # sidemantic/db/base.py:176
```

### Existing Signatures to Use
```python
# sidemantic (upstream, spec §6)
# SemanticLayer.__init__(connection: str | BaseDatabaseAdapter = "duckdb:///:memory:", dialect=None, auto_register=True,
#                        ..., default_limit=None, max_limit=None, ...)            # :76-93 ; adapter used as-is (:230-233)
# SemanticLayer.compile(metrics=None, dimensions=None, filters=None, segments=None, order_by=None, limit=None,
#                       offset=None, dialect=None, ..., user_attributes=None, ...) -> str      # :1143-1163
# SemanticLayer.describe_models(model_names=None) -> dict ; get_model(name) ; list_models() -> list[str]   # :925,:1912,:1934
# load_from_file(layer, file, *, strict=True, ossie_scope_id=None, ossie_adapter_options=None) -> None     # loaders.py:693
# BaseDatabaseAdapter abstract: execute, executemany, fetchone, fetch_record_batch, get_tables, get_columns, close,
#                               dialect (property), raw_connection (property)     # db/base.py:208-296
# compile(user_attributes=None) on a model with `security:` raises SecurityError (deny-by-default)
```

### Does NOT Exist
- ~~`SemanticLayer.compile()` returning an object~~ — returns `str`.
- ~~`OssieAdapter.parse(text)`~~ — path-based only; inline goes through the loader's temp file.
- ~~`sidemantic.interchange` internals~~ — never import (AC-8).
- ~~`sidemantic.db.unavailable.UnavailableDatabaseAdapter` as our adapter~~ — ship `CompileOnlyAdapter`.
- ~~`SecurityError` path assumed~~ — FILL IN: locate it in the installed wheel (`python -c "import sidemantic, pkgutil; ..."` / grep site-packages) and record the path in the Completion Note.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/semantic/compilers/__init__.py", "action": "CREATE"},
    {"path": "querysource/semantic/compilers/sidemantic.py", "action": "CREATE"},
    {"path": "tests/semantic/test_sidemantic_compiler.py", "action": "CREATE"}
  ],
  "contract_symbols": []
}
```

---

## Implementation Notes

### Key Constraints
- `import sidemantic` (pulls duckdb) happens only inside functions of `compilers/sidemantic.py`; `ImportError` → `SemanticCompilerUnavailable` (AC-3). `CompileOnlyAdapter` must therefore be defined lazily (class factory inside a function, cached) because its base class comes from sidemantic.
- `CompileOnlyAdapter` execution methods raise `RuntimeError("compile-only")`; `dialect` returns the target dialect.
- Layer cache key `(model.model_version, target.dialect, model.format, model.scope)` via `ModelLoader.cached/store`.
- `qualify`: unique suffix match across all models; zero → 400 listing available names; many → 400 listing candidates (AC-14).
- `time_grain`: for each requested dimension of time type without `__` suffix → `<dim>__<grain>`; explicit suffix wins; grain with no time dimension → 400 (AC-14).
- `limit`: None → `target.default_limit`; `> target.max_limit` → cap + warning (AC-15).
- `CompiledQuery.parameters = {"request": request.model_dump(), "roles": [...]}` — never bind values (AC-24). The provider fills `roles`; the compiler sets `request` only.
- Error mapping: SecurityError → `SemanticFieldDenied("security")`; ValueError/KeyError from unknown fields → `SemanticRequestError`; model load errors → `SemanticResourceError`.

---

## Implementation Blueprint

### Steps (in order)
1. Install check: `python -c "import sidemantic; print(sidemantic.__version__)"`; locate `SecurityError` and inspect `describe_models()` output on a toy model — *why*: the two unverified points of M4.
2. Write `compilers/__init__.py`, then the adapter factory, then `SidemanticCompiler`.
3. Write tests; sidemantic tests skip without the extra.

### `querysource/semantic/compilers/__init__.py` (CREATE)
```python
"""Semantic compilers (FEAT-175). Importing this package does not import sidemantic."""
from __future__ import annotations

from ..protocol import SemanticCompiler


def get_compiler(name: str = "sidemantic") -> SemanticCompiler:
    """Return the compiler implementation; raises SemanticCompilerUnavailable when not installed."""
    from ..exceptions import SemanticResourceError
    if name != "sidemantic":
        raise SemanticResourceError(f"Unknown semantic compiler: {name}")
    from .sidemantic import SidemanticCompiler
    from ..loader import get_loader
    return SidemanticCompiler(get_loader())
```

### `querysource/semantic/compilers/sidemantic.py` (CREATE)
```python
"""Sidemantic-backed SemanticCompiler (FEAT-175 M4). The ONLY module importing sidemantic (lazily)."""
from __future__ import annotations

import functools
import logging
from typing import Any

from ..exceptions import (SemanticCompilerUnavailable, SemanticFieldDenied, SemanticRequestError,
                          SemanticResourceError)
from ..filters import FilterValidator
from ..loader import ModelLoader
from ..models import (TIME_GRAINS, CatalogField, CompilationTarget, CompiledQuery, SemanticModelRef,
                      SemanticQuery)


def _sidemantic() -> Any:
    try:
        import sidemantic  # noqa: PLC0415
    except ImportError as exc:
        raise SemanticCompilerUnavailable() from exc
    return sidemantic


@functools.lru_cache(maxsize=1)
def compile_only_adapter_class() -> type:
    """Build CompileOnlyAdapter lazily (its base class lives in sidemantic)."""
    _sidemantic()
    from sidemantic.db.base import BaseDatabaseAdapter  # noqa: PLC0415

    class CompileOnlyAdapter(BaseDatabaseAdapter):
        """Never executes; `dialect` returns the target dialect."""

        def __init__(self, dialect: str) -> None:
            self._dialect = dialect

        @property
        def dialect(self) -> str:
            return self._dialect

        @property
        def raw_connection(self) -> Any:
            return None

        def _refuse(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("CompileOnlyAdapter: execution is disabled (compile-only)")

        execute = executemany = fetchone = fetch_record_batch = get_tables = get_columns = _refuse

        def close(self) -> None:
            return None

    return CompileOnlyAdapter


class SidemanticCompiler:
    """SemanticCompiler implementation."""

    def __init__(self, loader: ModelLoader) -> None:
        self.logger = logging.getLogger(__name__)
        self._loader = loader
        _sidemantic()

    def _layer(self, model: SemanticModelRef, target: CompilationTarget) -> Any:
        """Cache hit or build SemanticLayer(connection=CompileOnlyAdapter) + load_from_file."""
        # FILL IN: key=(model.model_version, target.dialect, model.format, model.scope); loader.cached/store;
        # SemanticLayer(connection=adapter, dialect=..., auto_register=False, default_limit=..., max_limit=...);
        # load_from_file(layer, model.source_path, ossie_scope_id=model.scope,
        #                ossie_adapter_options={'target_dialect': target.dialect}); errors → SemanticResourceError
```
```python
    # (continued, same class)
    def qualify(self, layer: Any, names: list[str], *, kind: str) -> list[str]:
        """'net_sales' → 'orders.net_sales' when unique; else SemanticRequestError listing candidates."""
        # FILL IN: build {qualified} sets for kind from layer.list_models()/get_model(); AC-14

    def _apply_time_grain(self, layer: Any, dimensions: list[str], grain: str | None) -> list[str]:
        # FILL IN: AC-14 rewrite rules

    def compile(self, model: SemanticModelRef, request: SemanticQuery,
                target: CompilationTarget) -> CompiledQuery:
        """Qualify, grain, cap limit, render filters, compile with user_attributes, map errors."""
        warnings = list(model.warnings)
        layer = self._layer(model, target)
        # FILL IN: metrics/dims qualify; dims grain; limit default/cap (+warning); dimensions dict for
        # FilterValidator from self.catalog(); filters = FilterValidator(...).render(request.filters);
        # sql = layer.compile(metrics=, dimensions=, filters=, order_by=, limit=, offset=, dialect=target.dialect,
        #                     user_attributes=target.user_attributes); exception mapping per Key Constraints;
        # return CompiledQuery(sql=sql, parameters={"request": request.model_dump()}, connection=target.connection,
        #                      dialect=target.dialect, model_version=model.model_version, metrics=..., dimensions=...,
        #                      warnings=warnings)

    def catalog(self, model: SemanticModelRef, target: CompilationTarget) -> list[CatalogField]:
        """From layer.describe_models() + get_model(name); dimensions carry type/granularities."""
        # FILL IN: shape verified in Step 1
```
**Why**: the max-filters bound comes from `conf.QS_SEMANTIC_MAX_FILTERS` — FILL IN: read it lazily
inside `compile()` (`from ...conf import QS_SEMANTIC_MAX_FILTERS`) so the compiler stays settings-free at import.

### FILL IN checklist
- [ ] Record `SecurityError` module path and `describe_models()` shape (Completion Note)
- [ ] `_layer`, `qualify`, `_apply_time_grain`, `compile`, `catalog`
- [ ] Verify the `BaseDatabaseAdapter` abstract member list against the wheel (properties vs methods)
- [ ] Test bodies

---

## Acceptance Criteria

- [ ] `test_compile_only_adapter_never_executes`: with `duckdb.connect` patched to raise, building a layer and compiling succeeds (AC-4).
- [ ] `sidemantic` import patched to fail → `SemanticCompilerUnavailable` (501) (AC-3).
- [ ] Qualification, time-grain rewrite, ambiguous/unknown names → 400 (AC-14); limit capped with warning (AC-15).
- [ ] Secured model with `user_attributes=None` → `SemanticFieldDenied` (AC-11).
- [ ] Compiled SQL for the conftest model contains `SUM(`.

---

## Validation Commands
- `pytest tests/semantic/test_sidemantic_compiler.py -q`

---

## Test Specification

```python
# tests/semantic/test_sidemantic_compiler.py
import builtins
import pytest
from querysource.semantic.exceptions import SemanticCompilerUnavailable, SemanticRequestError, SemanticFieldDenied


def test_compiler_unavailable_501(monkeypatch):
    real_import = builtins.__import__
    def fake(name, *a, **k):
        if name.startswith("sidemantic"):
            raise ImportError(name)
        return real_import(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", fake)
    from querysource.semantic.compilers import sidemantic as mod
    with pytest.raises(SemanticCompilerUnavailable):
        mod._sidemantic()


def test_compile_only_adapter_never_executes(sidemantic_available, monkeypatch):
    # FILL IN: monkeypatch duckdb.connect to raise; compile the conftest inline model; adapter.execute raises


def test_compiler_qualifies_and_rejects_ambiguous(sidemantic_available, tmp_path):
    # FILL IN


def test_compiler_time_grain_rewrite(sidemantic_available, tmp_path):
    # FILL IN: order_date + month → orders.order_date__month; explicit __day wins


def test_compiler_security_error_maps_to_403(sidemantic_available, tmp_path):
    # FILL IN
```

---

## Agent Instructions

1. Work in the feature worktree; TASK-897, 898, 900, 901 done; `uv pip install -e ".[semantic]"` applied.
2. Do Step 1 verification FIRST and update this task's contract if the wheel differs.
3. Implement; run validation; commit only listed files.
4. Close with `scripts/sdd/close_task.sh TASK-903 semantic-layer verified`.

---

## Completion Note

**Completed by**: sdd-worker directly (Sonnet 5.5; Nova seats failed on expired credentials, user directed codex/sonnet)
**Date**: 2026-10-15
**Notes**: Verified against sidemantic 0.12.0 wheel (installed to a scratch --target dir; the shared venv lacks the `semantic` extra, so the 6 sidemantic-dependent tests skip under the sandboxed merge-tier run and were run green separately: tests/semantic 28 passed).
- `SecurityError` path: `sidemantic.core.semantic_layer.SecurityError`.
- `describe_models()` returns `{'models': [{name, table, dimensions:[{name,type,granularity?}], metrics:[{name,agg,sql}]}], ...}`; catalog is built from `list_models()` + `get_model()` instead (typed objects).
- Native metric keys are `agg`/`sql` (not `type`/`expression`): the TASK-897 conftest fixture was invalid and was fixed (commit a5a6511, feedback coder-feedback:c398de538cb2fd32c3c52daa, review fix on TASK-897).
- Security `access` expressions must be Jinja (`{{ ... }}`) or bool; a plain SQL expression raises SecurityError at compile time.
- Compiled SQL for an unqualified filter field is rewritten into the model CTE by Sidemantic; filters are passed as strings from FilterValidator.
**Deviations from spec**: none (tests/semantic/conftest.py touched as a TASK-897 review fix, separate commit).
