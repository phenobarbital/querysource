# TASK-776: End-to-end dry-run tests for qsurl (parse → split → QS)

**Feature**: FEAT-152 — qsurl: HTSQL-style URL query dialect (chumsky 0.13 + PyO3)
**Spec**: `sdd/specs/qsurl-parser.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-769, TASK-770, TASK-771, TASK-773
**Assigned-to**: unassigned

---

## Context

Spec §4 Integration Tests, AC5, AC11, AC12. Unit tests prove each piece; this task proves
the pieces compose against the **real** dialect parsers without a database, using the
existing dry-run harness (`tests/e2e/conftest.py`: fake Redis, in-memory definitions).
It pins the exact SQL a pushdown-only qsurl renders on PostgreSQL, the `ILIKE` pushdown,
the residual path for a root `or`, and the Cassandra cost rejection.

---

## Scope

Create `tests/e2e/test_qsurl_dry_run.py` with:
- `test_pushdown_only_pg_sql` — `hisense_stores{store_id,name}?state_code='CA'&opened>=2024-01-01:sort(-name):top(50)` on a `provider="pg"` definition renders the exact pinned SQL and an empty plan.
- `test_text_match_pushdown_pg` — `?city~'san'` renders `city ILIKE '%san%'`.
- `test_or_filter_is_residual_on_pg` — root `or`: no WHERE from qsurl; `QS._apply_residual` on stubbed rows returns the right subset.
- `test_cassandra_residual_only_is_cost` — `translate.split(ir, cassandraProvider.capabilities, residual_scan=cassandraProvider.residual_scan)` for `?city~'san'` → `QSUrlError(kind="cost")`.

**NOT in scope**: the HTTP layer (TASK-775 tests it); a live database.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `tests/e2e/test_qsurl_dry_run.py` | CREATE | Composition tests on the dry-run harness |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
import sqlglot                                                   # verified: tests/e2e/test_qs_dry_run.py:15
from querysource.queries.qs import QS                            # verified: tests/e2e/test_qs_dry_run.py:24
from querysource.providers.pg import pgProvider                  # verified: querysource/providers/pg.py:14
from querysource.providers.cassandra import cassandraProvider    # verified: querysource/providers/cassandra.py:26
from querysource.qsurl import QSUrlError, parse                  # TASK-765
from querysource.qsurl.translate import split                    # TASK-771
```

### Existing Signatures to Use
```python
# tests/e2e/conftest.py
@pytest.fixture(autouse=True) def fake_redis(monkeypatch) -> type[_FakeRedis]         # 94-98
@pytest.fixture(autouse=True) def real_provider_resolution(monkeypatch) -> None       # 101-111
@pytest.fixture def definitions(monkeypatch) -> FakeDefinitionRepository             # 114-127
class FakeDefinitionRepository:
    def add(self, **fields: object) -> QueryModel      # QueryModel fields: query_slug, provider, query_raw, fields, conditions, ordering, is_raw

# tests/e2e/test_qs_dry_run.py — helper pattern
async def dry_run(**kwargs) -> str:
    qs = QS(**kwargs)
    sql, error = await qs.dry_run()
    assert error is None and isinstance(sql, str)
    assert_valid_sql(sql)          # sqlglot.parse(sql, read="postgres") → exactly one statement
    return sql
# Observed rendering shapes (test_qs_dry_run.py:49-73, 142-151):
#   "SELECT region, amount FROM public.sales  WHERE year='2024' ORDER BY region"
#   "SELECT * FROM t  WHERE amount >= '100' AND name IS NOT NULL AND d IN ('2024-01-01','2024-01-31')"
#   "SELECT * FROM t  ORDER BY id LIMIT 5 OFFSET 10"

# querysource/queries/qs.py (TASK-773): QS(..., residual=ResidualPlan|None); QS._apply_residual(result)
# provider class attributes (TASK-770): pgProvider.capabilities, cassandraProvider.capabilities / .residual_scan
```

### Does NOT Exist
- ~~a live-DB fixture for qsurl~~ — dry-run only.
- ~~`QS.dry_run()` applying the residual~~ — dry-run renders SQL only; test the residual via `_apply_residual` directly.
- ~~`provider="pg"` guaranteed to resolve to `pgProvider` in the harness~~ — FILL IN: confirm which `provider` value the harness maps to `pgProvider` (check `QS.build_provider` / an existing e2e case); pin it in the test.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "tests/e2e/test_qsurl_dry_run.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/queries/qs.py#QS"
  ]
}
```

---

## Implementation Blueprint

### Steps (in order)
1. Register a `hisense_stores` definition via the `definitions` fixture with `query_raw="SELECT {fields} FROM public.stores {where_cond}"` — *why*: exercises fields, filter, ordering and limit placeholders.
2. For each test: `ir = parse(src)`; `conditions, plan = split(ir, <provider>.capabilities, residual_scan=<provider>.residual_scan)`; `QS(slug=..., conditions=conditions, residual=plan).dry_run()` — *why*: this is exactly what the handler composes.
3. Pin the exact rendered SQL string after running it once, and also assert `sqlglot` parses it — *why*: AC5 requires a pinned render.

### `tests/e2e/test_qsurl_dry_run.py` (CREATE)
```python
"""qsurl end-to-end on the dry-run harness: parse → split → QS (FEAT-152)."""
from __future__ import annotations

import pytest
import sqlglot

from querysource.providers.cassandra import cassandraProvider
from querysource.providers.pg import pgProvider
from querysource.qsurl import QSUrlError, parse
from querysource.qsurl.translate import split
from querysource.queries.qs import QS

PG_PROVIDER = "pg"   # FILL IN: confirm the harness maps this to pgProvider


@pytest.fixture(autouse=True)
def _catalog(definitions) -> None:
    definitions.add(
        query_slug="hisense_stores",
        provider=PG_PROVIDER,
        query_raw="SELECT {fields} FROM public.stores {where_cond}",
    )


async def _render(src: str) -> tuple[str, object]:
    ir = parse(src)
    conditions, plan = split(ir, pgProvider.capabilities, residual_scan=pgProvider.residual_scan)
    sql, error = await QS(slug=ir["slug"], conditions=conditions, residual=plan).dry_run()
    assert error is None
    assert len(sqlglot.parse(sql, read="postgres")) == 1
    return sql, plan


async def test_pushdown_only_pg_sql():
    sql, plan = await _render("hisense_stores{store_id,name}?state_code='CA'&opened>=2024-01-01:sort(-name):top(50)")
    assert plan.is_empty()
    # FILL IN: assert sql == "<exact rendered SQL>" (SELECT store_id, name … WHERE state_code='CA' AND opened >= '2024-01-01' ORDER BY name DESC LIMIT 50)

async def test_text_match_pushdown_pg(): ...          # FILL IN: "city ILIKE '%san%'" in sql, plan empty
async def test_or_filter_is_residual_on_pg(): ...     # FILL IN: no qsurl WHERE; QS(..., residual=plan)._apply_residual(rows) subset
def test_cassandra_residual_only_is_cost():
    ir = parse("stores?city~'san'")
    with pytest.raises(QSUrlError) as exc:
        split(ir, cassandraProvider.capabilities, residual_scan=cassandraProvider.residual_scan)
    assert exc.value.kind == "cost"
```

### FILL IN checklist
- [ ] Confirm the `provider` value for `pgProvider` in the harness.
- [ ] Pin the exact SQL for the first two tests.
- [ ] `test_or_filter_is_residual_on_pg` body.

---

## Acceptance Criteria

- [ ] The pushdown-only render is pinned exactly and is valid PostgreSQL (AC5).
- [ ] `ILIKE` pushdown appears in the rendered SQL (AC11).
- [ ] Cassandra residual-only → `cost` before any query (AC12).
- [ ] `pytest tests/e2e/test_qs_dry_run.py -q` still green (harness untouched).

---

## Validation Commands

- `pytest tests/e2e/test_qsurl_dry_run.py -q`
- `pytest tests/e2e/test_qs_dry_run.py -q`

---

## Test Specification

See the `tests/e2e/test_qsurl_dry_run.py` block above.

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at the path listed above for full context.
2. **Check dependencies** — verify every `Depends-on` task is `done` in `sdd/tasks/index/qsurl-parser.json`.
3. **Verify the Codebase Contract** before writing any code: re-run the `grep -c` for every MODIFY anchor; if a count changed, re-locate the anchor; if it is `0`, stop and report the drift.
4. **Update status** in `sdd/tasks/index/qsurl-parser.json` → `"in-progress"`.
5. **Implement** from the Implementation Blueprint; complete every `# FILL IN:`; never change a signature or path the blueprint fixes.
6. **Verify** every Acceptance Criterion and run the Validation Commands (`source .venv/bin/activate` first).
7. **Move this file** to `sdd/tasks/completed/TASK-776-qsurl-e2e-dry-run.md` and set the index entry to `"done"`.
8. **Fill in the Completion Note** below.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**: What was implemented, any deviations from scope, issues encountered.

**Deviations from spec**: none | describe if any
