# TASK-738: Typed columns provider hook (`describe_columns`)

**Feature**: FEAT-148 — Describe Query-Slug REST Endpoints
**Spec**: `sdd/specs/describe-queryslug.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-734
**Assigned-to**: unassigned

---

## Context

This task implements spec §3 **Module 5**.

**The problem.** `pgProvider.columns()` prepares the statement and reads `stmt.get_attributes()`, but keeps only `a.name` and discards the type.

**The fix.** Add an **additive** method, `describe_columns()`, that returns `[{"name", "type"}]`. `columns()` stays byte-identical, so `HEAD` and `PATCH /api/v2/services/queries/{slug}` keep their payloads (spec AC16).

**Why it depends on TASK-734.** TASK-734 also edits `querysource/providers/abstract.py`. Running them sequentially avoids a conflicting edit to the same class.

---

## Scope

- Add `BaseProvider.describe_columns()`, the default built from `columns()`.
- Add a `pgProvider.describe_columns()` override that prepares the statement (no execution) and keeps `attribute.type.name`.
- Write `tests/unit/test_provider_describe_columns.py`.

**NOT in scope**:
- Modifying `columns()` on any provider.
- The `/columns` HTTP route (TASK-741).
- Row sampling.
- Other providers' typed overrides.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/providers/abstract.py` | MODIFY | default `describe_columns()` |
| `querysource/providers/pg.py` | MODIFY | typed override |
| `tests/unit/test_provider_describe_columns.py` | CREATE | tests with mocked connection |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.providers.abstract import BaseProvider   # verified: querysource/providers/abstract.py:23
from querysource.providers.pg import pgProvider           # verified: querysource/providers/pg.py:14
from querysource.exceptions import ParserError            # verified: querysource/exceptions.py:70 (already imported in pg.py:8)
```

### Existing Signatures to Use
```python
# querysource/providers/abstract.py
class BaseProvider(ABC):                                      # :23
    async def columns(self):                                  # :190 (occurrences: 1)
        """Return the columns (fields) involved on the query (when possible)."""
        if self._qs:                                          # NOTE: providers may lack _qs → AttributeError
            self._columns = await self._qs.columns()
        return self._columns
    async def dry_run(self):                                  # :197 (occurrences: 1) — insert describe_columns just before this

# querysource/providers/pg.py
class pgProvider(sqlProvider):                                # :14
    async def columns(self):                                  # :44
        if self._query:
            try:
                async with await self._connection.connection() as conn:   # :49
                    stmt, _ = await conn.prepare(self._query)             # :50
                    self._columns = [a.name for a in stmt.get_attributes()]  # :51
            except AttributeError as ex:
                raise ParserError(f"Invalid Query or Column for query: {self._query}") from ex  # :52-55
        return self._columns                                  # :56 (occurrences: 1)
# asyncpg Attribute: .name (str), .type (Type namedtuple with .name, e.g. 'int4', 'date', 'text')
```

### Does NOT Exist
- ~~`BaseProvider.describe_columns()` / `pgProvider.describe_columns()`~~: added by this task.
- ~~`BaseProvider.columns()` raising `NotImplementedError`~~: it delegates to `self._qs` and can raise `AttributeError` when `_qs` is missing.
- ~~Typed column discovery for non-pg providers~~: out of scope. They use the untyped default.

---

## Implementation Notes

### Key Constraints
- **`BaseProvider.describe_columns` (the default):**
  - `cols = await self.columns()`; catch `AttributeError`/`NotImplementedError` and return `[]`.
  - Normalize each item: a `str` → `{"name": item, "type": None}`; a `dict` with `"name"` → `{"name": str(item["name"]), "type": item.get("type")}`; anything else → `{"name": str(item), "type": None}`.
  - `None` or empty → `[]`.
- **`pgProvider.describe_columns`:**
  - If `not self._query`, return `[]`.
  - Otherwise use the same connection and prepare pattern as `columns()`, returning `[{"name": a.name, "type": getattr(getattr(a, "type", None), "name", None)} for a in stmt.get_attributes()]`.
  - On `AttributeError`, raise `ParserError` with the same message style as `columns()`.
  - Do **not** assign `self._columns`, so `columns()` state is unaffected.
- **Never execute.** Only `prepare()` + `get_attributes()`.

---

## Implementation Blueprint

### Steps (in order)
1. Add the default method to `BaseProvider` before `dry_run` — *why*: it is inherited by every provider.
2. Add the pg override after `columns()` — *why*: pg is the typed path.
3. Write the tests and run `pytest tests/unit/test_provider_describe_columns.py tests/unit/test_provider_raw_query_validated.py -q` — *why*: AC15/AC16.

### `querysource/providers/abstract.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c "    async def dry_run(self):" querysource/providers/abstract.py)
# BEFORE — insert above `    async def dry_run(self):` (verified :197)
    async def describe_columns(self) -> list[dict]:
        """Return output columns as ``[{'name': str, 'type': Optional[str]}]``.

        Default: untyped names derived from :meth:`columns`. Never executes the query.
        Returns ``[]`` when column discovery is unsupported or yields nothing.
        """
        try:
            cols = await self.columns()
        except (AttributeError, NotImplementedError):
            return []
        # FILL IN: normalise str / dict-with-name / other → {'name', 'type'} — bounded by AC15
        return []

```

### `querysource/providers/pg.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c "        return self._columns" querysource/providers/pg.py)
# AFTER — insert below `        return self._columns` (verified :56)

    async def describe_columns(self) -> list[dict]:
        """Prepare (never execute) the query and return typed columns.

        Returns:
            ``[{'name': a.name, 'type': a.type.name}]`` from the prepared statement,
            or ``[]`` when there is no rendered query.

        Raises:
            ParserError: When the statement cannot be described (same as :meth:`columns`).
        """
        if not self._query:
            return []
        # FILL IN: mirror columns() connection/prepare; build typed dicts; do not touch self._columns — bounded by AC15/AC16
        return []
```
**Why**: this is additive only. `columns()` feeds the `X-Columns` header on `HEAD /api/v2/services/queries/{slug}` and must not change shape.

### `tests/unit/test_provider_describe_columns.py` (CREATE)
```python
"""FEAT-148 TASK-738 — provider describe_columns hook."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from querysource.providers.abstract import BaseProvider
from querysource.providers.pg import pgProvider


async def test_base_describe_columns_from_names():
    # FILL IN: provider = BaseProvider.__new__(<concrete subclass>) or MagicMock(spec=...) with columns AsyncMock(['a','b'])
    #          → [{'name':'a','type':None},{'name':'b','type':None}]
    ...


async def test_base_describe_columns_attribute_error_returns_empty():
    ...


async def test_pg_describe_columns_types_and_columns_unchanged():
    # FILL IN: pgProvider.__new__(pgProvider); set _query, _columns=[]; _connection.connection() async CM yielding conn
    #          whose prepare returns (stmt, None); stmt.get_attributes() → [SimpleNamespace(name='d', type=SimpleNamespace(name='date'))]
    #          assert typed output AND provider._columns still []
    ...


async def test_pg_describe_columns_empty_query():
    ...
```

### FILL IN checklist
- [ ] `BaseProvider.describe_columns` normalization.
- [ ] `pgProvider.describe_columns` prepare and typed mapping, with the `AttributeError` → `ParserError` mapping.
- [ ] Test bodies, including the async context-manager mock.

---

## Acceptance Criteria

- [ ] `pytest tests/unit/test_provider_describe_columns.py -q` passes.
- [ ] Spec AC16: `columns()` on `BaseProvider` and `pgProvider` is unchanged, and `git diff` shows no edits inside either `columns()` body.
- [ ] No `execute`/`fetch` call is added. Only `prepare` and `get_attributes`.
- [ ] `ruff check querysource/providers/abstract.py querysource/providers/pg.py tests/unit/test_provider_describe_columns.py` is clean.

---

## Test Specification

See the blueprint test file above.

---

## Agent Instructions

1. **Read the spec** (§3 Module 5, AC15, AC16).
2. **Check dependencies**: TASK-734 completed (same file, `providers/abstract.py`).
3. **Verify the Codebase Contract** anchors.
4. **Update status** → `"in-progress"`.
5. **Implement** from the blueprint.
6. **Verify** the acceptance criteria.
7. **Move this file** to `sdd/tasks/completed/`.
8. **Update the index** → `"done"`.
9. **Fill in the Completion Note**.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**:

**Deviations from spec**: none | describe if any
