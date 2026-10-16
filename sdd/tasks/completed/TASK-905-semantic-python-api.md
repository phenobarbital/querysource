# TASK-905: Python API — `querysource.semantic.compile()` / `execute()`

**Feature**: FEAT-175 — Querysource Semantic Layer (Ossie models compiled by Sidemantic)
**Spec**: `sdd/specs/semantic-layer.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-904
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 8 and §2 "New Public Interfaces". Two-step library API for ai-parrot: `compile()`
returns the `CompiledQuery` without executing; `execute()` re-runs the embedded request through
`QS` (never executes `compiled.sql` handed in by a caller). Open question Q12 is resolved by this
design (re-run through QS works in- and cross-process).

---

## Scope

- Create `querysource/semantic/api.py` with `compile()` / `execute()`.
- Re-export both from `querysource/semantic/__init__.py`.
- Tests `tests/semantic/test_api.py`.

**NOT in scope**: HTTP surface (TASK-906).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/semantic/api.py` | CREATE | `compile` / `execute` |
| `querysource/semantic/__init__.py` | MODIFY | re-export |
| `tests/semantic/test_api.py` | CREATE | API tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.queries.qs import QS                         # verified: qs.py:50
from querysource.auth.principal import QSPrincipal            # verified: principal.py:21
from querysource.semantic.models import CompiledQuery, SemanticQuery   # TASK-897
```

### Existing Signatures to Use
```python
# querysource/queries/qs.py
class QS(BaseQuery):
    def __init__(self, slug='', conditions=None, request=None, loop=None, *, tenant=None,
                 definition=None, principal=None, ...)          # :56
    async def build_provider(self)                               # :186 — sets self._qs (provider)
    async def query(self, output_format: str | None = None)      # :457
    async def close(self)                                        # FILL IN: verify name/signature in qs.py / interfaces/queries.py
# semanticProvider.compiled: CompiledQuery                       # TASK-904
```

### Does NOT Exist
- ~~`querysource.compile()` / `querysource.execute()`~~ top-level — the API lives in `querysource.semantic`.
- ~~Executing `compiled.sql` directly~~ — forbidden; always through `QS`.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/semantic/api.py", "action": "CREATE"},
    {"path": "querysource/semantic/__init__.py", "action": "MODIFY"},
    {"path": "tests/semantic/test_api.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/queries/qs.py#QS"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- `compile()` builds `SemanticQuery(...)` first (validation 400s before any I/O) and passes `model_dump()` as `conditions`.
- `compile()` must `close` the QS in `finally`.
- `execute()` needs the slug: store it in `CompiledQuery.parameters["slug"]` — FILL IN: add it in `compile()` (api-level, not in the provider) and read it back here.

---

## Implementation Blueprint

### Steps (in order)
1. Write `api.py`; 2. append re-exports to `__init__.py`; 3. tests with `semantic_definitions`.

### `querysource/semantic/api.py` (CREATE)
```python
"""Two-step Python API over QS (FEAT-175 M8)."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .models import CompiledQuery, SemanticQuery

if TYPE_CHECKING:
    from ..auth.principal import QSPrincipal


async def compile(resource: str, *, metrics: list[str], dimensions: list[str] | None = None,  # noqa: A001
                  filters: list[dict | str] | None = None, time_grain: str | None = None,
                  order_by: list[str] | None = None, limit: int | None = None, offset: int = 0,
                  tenant: str | None = None, principal: "QSPrincipal | None" = None) -> CompiledQuery:
    """Compile a semantic request for slug `resource` without executing it."""
    from ..queries.qs import QS
    request = SemanticQuery(metrics=metrics, dimensions=dimensions or [], filters=filters or [],
                            time_grain=time_grain, order_by=order_by or [], limit=limit, offset=offset)
    qs = QS(slug=resource, conditions=request.model_dump(), tenant=tenant, principal=principal)
    # FILL IN: try: await qs.build_provider(); compiled = qs._qs.compiled.model_copy(deep=True);
    # compiled.parameters["slug"] = resource; return compiled; finally: close qs


async def execute(compiled: CompiledQuery, *, tenant: str | None = None,
                  principal: "QSPrincipal | None" = None, output_format: str | None = None) -> Any:
    """Re-run the embedded request through QS and return qs.query(output_format)."""
    # FILL IN: slug = compiled.parameters["slug"]; conditions = compiled.parameters["request"];
    # QS(slug=..., conditions=..., tenant=..., principal=...); await build_provider(); return await qs.query(output_format)
```
**Why**: `compile` shadows the builtin deliberately (spec-fixed public name); hence `# noqa: A001`.

### `querysource/semantic/__init__.py` (MODIFY)
```python
# occurrences: 1 (verified after TASK-897: grep -c '^from .protocol import SemanticCompiler' querysource/semantic/__init__.py)
# AFTER — insert below `from .protocol import SemanticCompiler`
from .api import compile, execute  # noqa: A004
```
```python
# AFTER the `__all__ = (...)` tuple:
__all__ = (*__all__, 'compile', 'execute')
```

### FILL IN checklist
- [ ] `compile` body / `execute` body; QS close API name

---

## Acceptance Criteria

- [ ] `await querysource.semantic.compile("epson.sales", metrics=["revenue"])` returns a `CompiledQuery` with `model_version`, and no DB query is executed (AC-6).
- [ ] `import querysource.semantic` still imports no sidemantic.

---

## Validation Commands
- `pytest tests/semantic/test_api.py -q`

---

## Test Specification

```python
# tests/semantic/test_api.py
import pytest
from querysource import semantic


async def test_api_compile_returns_compiled_query(sidemantic_available, semantic_definitions, monkeypatch):
    # FILL IN: patch the asyncdb connection query to fail if called; compiled = await semantic.compile(...);
    # assert compiled.model_version and compiled.parameters["slug"] == "epson.sales"
```

---

## Agent Instructions

1. Work in the feature worktree; TASK-904 done.
2. Implement; validate; commit only listed files.
3. Close with `scripts/sdd/close_task.sh TASK-905 semantic-layer verified`.

---

## Completion Note

**Completed by**: sdd-worker (coder seat gpt-5.6-terra, codex, 1 attempt, 50s)
**Date**: 2026-10-15
**Notes**: Review fix 3c758c4 repaired a test guard patching a non-existent AsyncDB.connection (coder-feedback:e607cac961a1211ff1c8f2d1). Merge-tier green (28 passed, 14 skipped without the sidemantic extra; passes with scratch install). Review recorded via coder_record_review. Three tests/e2e failures reproduce on the base commit 322fe27 and are pre-existing. Closed via close_task.sh.

**Deviations from spec**: none
