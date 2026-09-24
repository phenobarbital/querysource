# TASK-773: `QS` residual stage + `QSURL_MAX_RESIDUAL_ROWS` cost guard

**Feature**: FEAT-152 — qsurl: HTSQL-style URL query dialect (chumsky 0.13 + PyO3)
**Spec**: `sdd/specs/qsurl-parser.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-765, TASK-772
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 8, AC12. The brainstorm resolved that the residual runs as a new stage in
`QS.query()`, on both the cache-hit path and the provider-fetch path, right before
`_output_format`. A configurable cap (`QSURL_MAX_RESIDUAL_ROWS`, default 50000) rejects
residual work over too many rows with `QSUrlError("cost")` **before** a DataFrame is built.
The cache key stays the pushdown checksum.

---

## Scope

- Add `QSURL_MAX_RESIDUAL_ROWS` to `querysource/conf.py`.
- Add the keyword-only `residual: ResidualPlan | None = None` argument to `QS.__init__`, stored as `self._residual`.
- Add `QS._apply_residual(result)` and call it at the two insertion points.
- Test both paths, the cost guard and the empty-result case with mocks (no database).

**NOT in scope**: building the plan (TASK-771); the handler (TASK-775); `MultiQS` / `QueryObject`.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/conf.py` | MODIFY | `QSURL_MAX_RESIDUAL_ROWS` setting |
| `querysource/queries/qs.py` | MODIFY | `residual` kwarg, `_apply_residual`, two call sites |
| `tests/qsurl/test_qs_residual.py` | CREATE | Cache-hit, provider, cost, empty tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from navconfig import config                           # verified: querysource/conf.py:5
from querysource.qsurl.plan import ResidualPlan        # TASK-765
from querysource.qsurl.errors import QSUrlError        # TASK-765
from querysource.qsurl import residual                 # TASK-772 (residual.apply)
from querysource.queries.qs import QS                  # verified: querysource/queries/qs.py:49
from querysource.exceptions import DataNotFound        # verified: querysource/exceptions.py:53 (already imported in qs.py:25)
```

### Existing Signatures to Use
```python
# querysource/conf.py — settings idiom (lines 284-295)
MULTIQS_MAX_CONCURRENT_THREADS = config.getint("MULTIQS_MAX_CONCURRENT_THREADS", fallback=4,)
EXCLUDED_QUERY_PARAMETERS: set = {                     # line 387 (insert the new setting ABOVE the comment block at 385)

# querysource/queries/qs.py
from .base import BaseQuery                            # line 38 (last import before `if TYPE_CHECKING:` at 40)
class QS(BaseQuery):                                   # line 49
    def __init__(self, slug: str = '', conditions: dict = None, request: web.Request = None,
                 loop: asyncio.AbstractEventLoop = None, *, tenant: str | None = None,
                 definition: "LoadedDefinition | None" = None,
                 principal: "QSPrincipal | None" = None,      # line 64
                 **kwargs):                                     # line 65
        super().__init__(slug, conditions=conditions, ..., **kwargs)   # 67-76 — kwargs forwarded to BaseQuery
        self.is_cached: bool = False                            # line 83
    async def query(self, output_format: str | None = None):   # line 428
        # cache hit, inside try/except/else (483-506):
        #     else:
        #         self._result = result                                              # line 505
        #         return await self._output_format(self._result, error)  # pylint: disable=W0150   # line 506
        # provider path, after `async with self.semaphore:` try/except/finally (511-580):
        #     if check_empty(result):                                                # line 582
        #         raise DataNotFound(f'{self._qs.__name__!s} Empty Result')          # 583-585
        #     self._result = result                                                  # line 586
    # self._logger (AbstractQuery, querysource/interfaces/queries.py:79)
```

### Does NOT Exist
- ~~`QS.residual`~~, ~~`QS._apply_residual`~~, ~~`QSURL_MAX_RESIDUAL_ROWS`~~ — created here.
- ~~a residual kwarg on `BaseQuery` / `AbstractQuery`~~ — pop it in `QS.__init__` BEFORE `super().__init__` so it is not stored in `self.kwargs`.
- ~~`except Exception` coverage at the insertion points~~ — the cache-hit call is in the try's `else:` clause (not covered by its `except`s) and the provider call is after the `async with` block; keep them there so `QSUrlError` is never turned into a cache miss or a 500.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/conf.py", "action": "MODIFY"},
    {"path": "querysource/queries/qs.py", "action": "MODIFY"},
    {"path": "tests/qsurl/test_qs_residual.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/queries/qs.py#QS",
    "sym:querysource/queries/qs.py#QS.query"
  ]
}
```

---

## Implementation Blueprint

### Steps (in order)
1. Add the setting to `conf.py` — *why*: navconfig is the only settings source.
2. Add the kwarg and attribute to `QS.__init__` — *why*: the handler passes the plan at construction.
3. Add `_apply_residual` and wire the two call sites — *why*: both result paths must honour the plan.
4. Import `QSURL_MAX_RESIDUAL_ROWS` inside `_apply_residual` from `..conf` at call time (`from .. import conf` then `conf.QSURL_MAX_RESIDUAL_ROWS`) — *why*: tests monkeypatch the module attribute.
5. Write the tests with a fake provider (`query()` returning rows) and a fake connection (`in_cache`/`from_cache`).

### `querysource/conf.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -cF 'EXCLUDED_QUERY_PARAMETERS: set = {' querysource/conf.py)
# BEFORE — insert above the comment block that precedes `EXCLUDED_QUERY_PARAMETERS: set = {` (verified: querysource/conf.py:387; comment at 385)
## qsurl (FEAT-152): rows a pushdown result may have before an in-memory residual plan runs.
QSURL_MAX_RESIDUAL_ROWS = config.getint("QSURL_MAX_RESIDUAL_ROWS", fallback=50000)
```

### `querysource/queries/qs.py` (MODIFY — signature)
```python
# occurrences: 1 (verified: grep -cF '            principal: "QSPrincipal | None" = None,' querysource/queries/qs.py)
# AFTER — insert below `            principal: "QSPrincipal | None" = None,` (verified: querysource/queries/qs.py:64)
            residual: "ResidualPlan | None" = None,
# and under `if TYPE_CHECKING:` (line 40) add:
    from ..qsurl.plan import ResidualPlan
```

### `querysource/queries/qs.py` (MODIFY — attribute)
```python
# occurrences: 1 (verified: grep -cF '        self.is_cached: bool = False' querysource/queries/qs.py)
# AFTER — insert below `        self.is_cached: bool = False` (verified: querysource/queries/qs.py:83)
        self._residual: "ResidualPlan | None" = residual
```
**Why**: `residual` is a named keyword-only parameter, so it never reaches `**kwargs` or `super().__init__`.

### `querysource/queries/qs.py` (MODIFY — method, add after `__repr__` at ~line 124)
```python
    def _apply_residual(self, result):
        """Return ``result`` with ``self._residual`` applied (untouched when there is no plan).

        Raises:
            QSUrlError: kind "cost" when ``len(result)`` exceeds ``QSURL_MAX_RESIDUAL_ROWS``
                (checked before any DataFrame is built); kind "lower" from ``residual.apply``.
            DataNotFound: when the plan leaves zero rows.
        """
        if self._residual is None or self._residual.is_empty():
            return result
        from .. import conf  # read at call time: tests monkeypatch conf.QSURL_MAX_RESIDUAL_ROWS
        from ..qsurl import residual
        from ..qsurl.errors import QSUrlError
        # FILL IN: n = len(result); if n > conf.QSURL_MAX_RESIDUAL_ROWS → raise QSUrlError("cost",
        #   f"residual stage over {n} rows exceeds QSURL_MAX_RESIDUAL_ROWS={conf.QSURL_MAX_RESIDUAL_ROWS}; push down a narrower filter")
        # FILL IN: out = residual.apply(result, self._residual); empty → raise DataNotFound("qsurl: empty result after residual filter")
        self._logger.debug("qsurl residual applied: %s rows in", len(result))
        return out
```
**Why**: lazy imports keep `qs.py`'s import graph unchanged for every non-qsurl caller.

### `querysource/queries/qs.py` (MODIFY — call sites)
```python
# CACHE HIT — occurrences: 2 for `self._result = result` → disambiguate with the next line:
#   `                    self._result = result`
#   `                    return await self._output_format(self._result, error)  # pylint: disable=W0150`   (verified: qs.py:505-506, occurrences 1)
# REPLACE line 505 with:
                    self._result = self._apply_residual(result)

# PROVIDER — occurrences: 1 (verified: grep -cF '            ## returning data:' querysource/queries/qs.py)
# BEFORE — insert above `            ## returning data:` (verified: querysource/queries/qs.py:593), i.e. AFTER the
# `## Saving into Cache:` block (588-592) so `save_cache(cache_key, result)` still stores the PUSHDOWN rows:
            self._result = self._apply_residual(self._result)
```
**Why**: the cache key is the pushdown checksum, so the cache must hold pushdown rows and the residual is re-applied on every hit (spec §2). Placing the call after `save_cache` guarantees that without touching the cache code; it is also outside the `try/except Exception → 500` block (511-580), so `QSUrlError` reaches the handler intact.

### `tests/qsurl/test_qs_residual.py` (CREATE)
```python
"""QS residual stage: both result paths, cost guard, empty result (spec AC12)."""
from __future__ import annotations

import pytest

from querysource import conf
from querysource.exceptions import DataNotFound
from querysource.qsurl import QSUrlError, ResidualPlan
from querysource.queries.qs import QS

ROWS = [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}, {"a": 3, "b": "x"}, {"a": 4, "b": "z"}]
PLAN = ResidualPlan(filter={"and": [{"column": "b", "expression": "==", "value": "x"}]})


def _qs(plan) -> QS:
    qs = QS(slug="s", residual=plan)
    # FILL IN: attach a fake provider (_qs) whose query() returns (ROWS, None), refresh() False,
    #   accepts() None, checksum() "c", __name__ "fake"; set is_cached False; stub event_log/connection.dispose;
    #   set qs._output_format to an async callable returning its first argument
    return qs

async def test_provider_path_applies_plan(): ...        # FILL IN: result == rows with b == "x"
async def test_cache_hit_applies_plan(): ...            # FILL IN: is_cached True, in_cache True, from_cache returns ROWS encoded
async def test_cost_guard(monkeypatch): ...             # FILL IN: monkeypatch.setattr(conf, "QSURL_MAX_RESIDUAL_ROWS", 3) → QSUrlError kind "cost"
async def test_empty_after_residual_raises(): ...       # FILL IN: plan matching nothing → DataNotFound
async def test_no_plan_is_a_noop(): ...                 # FILL IN: residual=None returns ROWS unchanged
def test_residual_not_in_kwargs():
    assert "residual" not in QS(slug="s", residual=PLAN).kwargs
```

### FILL IN checklist
- [ ] `_apply_residual` cost check and empty check.
- [ ] Test fakes and bodies.

---

## Acceptance Criteria

- [ ] Plan applied on both paths before `_output_format` (spec AC12).
- [ ] `len(rows) > QSURL_MAX_RESIDUAL_ROWS` → `QSUrlError("cost")` before a DataFrame is built.
- [ ] Empty after residual → `DataNotFound`.
- [ ] Cached payload is the pushdown result.
- [ ] `QS(...)` without `residual` behaves exactly as before: `pytest tests/e2e/test_qs_dry_run.py -q` green.
- [ ] `ruff check querysource/queries/qs.py querysource/conf.py tests/qsurl/test_qs_residual.py` clean.

---

## Validation Commands

- `pytest tests/qsurl/test_qs_residual.py -q`
- `pytest tests/e2e/test_qs_dry_run.py -q`

---

## Test Specification

See the `tests/qsurl/test_qs_residual.py` block above.

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at the path listed above for full context.
2. **Check dependencies** — verify every `Depends-on` task is `done` in `sdd/tasks/index/qsurl-parser.json`.
3. **Verify the Codebase Contract** before writing any code: re-run the `grep -c` for every MODIFY anchor; if a count changed, re-locate the anchor; if it is `0`, stop and report the drift.
4. **Update status** in `sdd/tasks/index/qsurl-parser.json` → `"in-progress"`.
5. **Implement** from the Implementation Blueprint; complete every `# FILL IN:`; never change a signature or path the blueprint fixes.
6. **Verify** every Acceptance Criterion and run the Validation Commands (`source .venv/bin/activate` first).
7. **Move this file** to `sdd/tasks/completed/TASK-773-qs-residual-stage.md` and set the index entry to `"done"`.
8. **Fill in the Completion Note** below.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**: What was implemented, any deviations from scope, issues encountered.

**Deviations from spec**: none | describe if any
