# TASK-878: End-to-end dialect renders, fallback stub, and dialect docs

**Feature**: FEAT-167 — WHERE value hardening — `is_valid()` closure and idempotent literal quoting
**Spec**: `sdd/specs/where-value-hardening.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-873, TASK-876, TASK-877
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 4 / goal G6; spec AC1–AC7. The unit tasks prove each
function; this task proves the composed pipeline — the real
`set_options()` + `build_query()` path through `pgSQLParser` (Rust and
Cython), `SQLParser`, `CQLParser` and `BigQueryParser` — for the three
closed gaps and the apostrophe round-trip, updates the pure-Python `Entity`
stub in `tests/unit/test_cython_filter_fallback.py:54-60` to the idempotent
shape, and rewrites `docs/QUERYSOURCE_DIALECT.md` §13.1/§13.2 to describe the
enforced contract (no "other zero-argument helpers", three new keyword rows,
`(` values are literals).

---

## Scope

- Create `tests/unit/test_where_value_hardening.py` (harness copied from
  `tests/test_dialect_filter_preprocessing.py:13-38`).
- Update `_qs_quote_string` in `tests/unit/test_cython_filter_fallback.py`.
- Rewrite `docs/QUERYSOURCE_DIALECT.md` §13.1 (lines 696-729) and §13.2
  (lines 732-745).
- Run the full spec AC7 suite list plus `tests/` once (blast radius of
  `Entity.quoteString`, spec §7).

**NOT in scope**: any production code — if an e2e render fails, open a
ledger issue against the owning task instead of patching here.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `tests/unit/test_where_value_hardening.py` | CREATE | e2e renders per dialect |
| `tests/unit/test_cython_filter_fallback.py` | MODIFY | idempotent `_qs_quote_string` stub (`:54-60`) |
| `docs/QUERYSOURCE_DIALECT.md` | MODIFY | §13.1 (`:696-729`), §13.2 (`:732-745`) |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
import querysource.parsers.pgsql as pgsql                 # verified: tests/e2e/test_qsurl_dry_run.py:33 (module has HAS_RUST at :26-28)
from querysource.parsers.pgsql import pgSQLParser         # verified: tests/test_dialect_filter_preprocessing.py:9
from querysource.parsers.sql import SQLParser             # verified: querysource/parsers/sql.pyx (class SQLParser)
from querysource.parsers.cql import CQLParser             # verified: querysource/parsers/cql.pyx
from querysource.parsers.bigquery import BigQueryParser   # verified: querysource/parsers/bigquery.pyx
from querysource.models import QueryObject                # verified: tests/test_dialect_filter_preprocessing.py:7
from querysource.types.validators import Entity, is_valid, udf_keywords  # verified: validators.pyx:489, :625, :219
from querysource.qs_parsers import _qs_parsers as rs      # verified: tests/unit/test_udf_keyword_resolution.py:15
```

### Existing Signatures to Use
```python
# tests/test_dialect_filter_preprocessing.py:13-38 — harness to copy verbatim
class _Connection: async __aenter__ → None; async __aexit__ → None
class _Redis: async def connection(self) -> _Connection
async def preprocess(conditions: dict, query: str = SQL) -> pgSQLParser:
    parser = pgSQLParser(definition=None, conditions=QueryObject(**conditions), query=query)
    parser._redis = _Redis(); await parser.set_options(); return parser
# then: await parser.build_query() -> str  (e.g. "SELECT * FROM public.t  WHERE name='O''Brien'")
# Verified at spec time: the same constructor/call shape works for SQLParser, CQLParser, BigQueryParser.
# Force the Cython path: monkeypatch.setattr(pgsql, "HAS_RUST", False)   # tests/e2e/test_qsurl_dry_run.py docstring

# Expected renders (spec AC3/AC4) — the `WHERE` fragment:
#   pgsql (Rust & Cython), SQLParser, CQLParser:  {"name": "O'Brien"}            → name='O''Brien'   (cql: `name = 'O''Brien'`)
#                                                  {"name": {">": "O'Brien"}}     → name > 'O''Brien'  (cql renders comparison dicts differently — assert only equality/IN there)
#                                                  {"name": ["O'Brien", "x"]}     → name IN ('O''Brien','x')
#                                                  {"name": "pg_sleep(5)"}        → name='pg_sleep(5)'
#                                                  {"amount": {">": "x) OR (1=1"}} → amount > 'x) OR (1=1'
#                                                  {"username": "DBPWD"}          → username='DBPWD'
#                                                  {"created": "midnight"}        → created='midnight'
#   BigQueryParser:                               {"name": "O'Brien"}            → name="O'Brien"
#                                                  {"amount": {">": "x) OR (1=1"}} → amount > "x) OR (1=1"

# tests/unit/test_cython_filter_fallback.py
def _qs_quote_string(value) -> str:      # line 54-60 — pure-Python stand-in used only when the extension cannot be imported
    # today: None/'None' → "''"; null/NULL passthrough; replace("'", "''") and wrap — NO unwrap step

# docs/QUERYSOURCE_DIALECT.md
### 13.1 Value functions (date keywords)        # line 696; table rows TODAY..LDOM at 705-711; the
#   paragraph "The list can be changed per deployment ... Other zero-argument helpers of the same module can be named the same way, for example `fdow` / `ldow` ... or `ldopm`" at 713-716 — REWRITTEN
### 13.2 Database constants and functions       # line 732; table 734-737; paragraph 739-745 ("In a `filter` they are rendered as quoted literals") — EXTENDED
```

### Does NOT Exist
- ~~`parser.render()` / `parser.sql`~~ — the rendered query comes from `await parser.build_query()`.
- ~~a `definitions` fixture for these parsers~~ — `definition=None` works (preprocessing harness); the `definitions` fixture in `tests/e2e/` is for `QS.dry_run`, not needed here.
- ~~`InfluxParser`/`msSQLParser` in this test~~ — their constructors need a source/definition (verified at spec time: `AttributeError`/pickle stub); out of scope.
- ~~`docs/FILTER_OPERATORS.md` changes~~ — spec AC6 says unchanged.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "tests/unit/test_where_value_hardening.py", "action": "CREATE"},
    {"path": "tests/unit/test_cython_filter_fallback.py", "action": "MODIFY"},
    {"path": "docs/QUERYSOURCE_DIALECT.md", "action": "MODIFY"}
  ],
  "contract_symbols": [
    "sym:querysource/parsers/pgsql.pyx#pgSQLParser",
    "sym:querysource/types/validators.pyx#Entity",
    "sym:querysource/types/validators.pyx#is_valid"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- Build **both** extensions in the worktree before running anything:
  `python setup.py build_ext --inplace && make build-rust && make stage-rust`
  (memories `sdd-fix-worktree-tests`, `rust-extension-stage-rust`).
- Assert on the `WHERE` fragment (`query.split("WHERE", 1)[1].strip()`), not
  the whole string — leading-space differences are irrelevant.
- The parsers log at NOTICE/DEBUG; that is noise, not failure.
- Docs: keep §13.1's table format; add `FDOW`, `LDOW`, `LDOPM` rows after
  `LDOM`; delete the "Other zero-argument helpers…" sentence; state that
  only `UDF_LIST` names resolve and that `UDF_LIST` (env) is the only
  extension mechanism. §13.2: add that any other value — including one
  containing `(` — is sent as a quoted literal, and that a value already
  wrapped in single quotes is unwrapped once (idempotent quoting).

### References in Codebase
- `tests/test_dialect_filter_preprocessing.py` — harness and assertion style.
- `tests/e2e/test_qsurl_dry_run.py` — `monkeypatch.setattr(pgsql, "HAS_RUST", False)` pattern.

---

## Implementation Blueprint

### Steps (in order)
1. Rebuild both extensions — *why*: the merged TASK-872..877 code is invisible otherwise.
2. Create the e2e test file from the block below and complete the FILL INs — *why*: AC1–AC4 are defined as rendered SQL, not unit values.
3. Update `_qs_quote_string` — *why*: the stub must simulate the idempotent contract or the fallback suite diverges from the compiled behaviour.
4. Rewrite docs §13.1/§13.2 — *why*: AC6; the old text documents gap 2 as a feature.
5. Run the Validation Commands, then `pytest tests/ -q -x` once for blast radius; `ruff check` the two test files.

### `tests/unit/test_where_value_hardening.py` (CREATE)
```python
"""FEAT-167 TASK-878 — WHERE value hardening, end-to-end through set_options()/build_query()."""
from __future__ import annotations

import pytest

import querysource.parsers.pgsql as pgsql
from querysource.models import QueryObject
from querysource.parsers.bigquery import BigQueryParser
from querysource.parsers.cql import CQLParser
from querysource.parsers.pgsql import pgSQLParser
from querysource.parsers.sql import SQLParser
from querysource.qs_parsers import _qs_parsers as rs
from querysource.types.validators import Entity

SQL = "SELECT * FROM public.t {where_cond}"


class _Connection:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *args: object) -> None:
        return None


class _Redis:
    async def connection(self) -> _Connection:
        return _Connection()


async def where(parser_cls, flt: dict) -> str:
    """Render ``flt`` through the real pipeline and return the WHERE fragment."""
    parser = parser_cls(definition=None, conditions=QueryObject(filter=flt), query=SQL)
    parser._redis = _Redis()
    await parser.set_options()
    return (await parser.build_query()).split("WHERE", 1)[1].strip()


@pytest.fixture(params=["pg-rust", "pg-cython", "sql", "cql"])
def sq_parser(request, monkeypatch):
    """Single-quote dialects; pgsql in both engines."""
    if request.param == "pg-cython":
        monkeypatch.setattr(pgsql, "HAS_RUST", False)
    return {"pg-rust": pgSQLParser, "pg-cython": pgSQLParser, "sql": SQLParser, "cql": CQLParser}[request.param]


async def test_config_key_is_literal(sq_parser) -> None:          # AC1
    assert "'DBPWD'" in await where(sq_parser, {"username": "DBPWD"})
    assert "'12345678'" not in await where(sq_parser, {"username": "DBPWD"})


async def test_function_name_is_literal(sq_parser) -> None:       # AC2
    assert "'midnight'" in await where(sq_parser, {"created": "midnight"})


async def test_parenthesis_is_literal(sq_parser) -> None:         # AC3
    assert "'pg_sleep(5)'" in await where(sq_parser, {"name": "pg_sleep(5)"})
    # FILL IN: comparison dict {"amount": {">": "x) OR (1=1"}} → "amount > 'x) OR (1=1'" for pg/sql (cql renders dicts differently: skip there) — bounded by AC3


async def test_apostrophe_roundtrip(sq_parser) -> None:           # AC4
    assert "'O''Brien'" in await where(sq_parser, {"name": "O'Brien"})
    assert "'O''''Brien'" not in await where(sq_parser, {"name": "O'Brien"})
    assert "'OBrien'" not in await where(sq_parser, {"name": "O'Brien"})
    assert "IN ('O''Brien','x')" in await where(sq_parser, {"name": ["O'Brien", "x"]})
    # FILL IN: comparison dict and "!O'Brien" negation renders for pg/sql — bounded by AC4


async def test_bigquery_renders() -> None:                        # AC3/AC4 BigQuery
    assert await where(BigQueryParser, {"name": "O'Brien"}) == 'name="O\'Brien"'
    assert await where(BigQueryParser, {"amount": {">": "x) OR (1=1"}}) == 'amount > "x) OR (1=1"'


@pytest.mark.parametrize("raw", ["O'Brien", "plain", "it''s", "50%", "x) OR (1=1"])
def test_rust_cython_quote_parity(raw: str) -> None:              # AC4 both engines
    assert rs.quote_string(raw, True) == Entity.quoteString(raw)
    assert rs.quote_string(Entity.quoteString(raw), True) == Entity.quoteString(raw)
```
**Why this shape**: the fixture runs every assertion under four single-quote renderers so one table of expectations covers Rust and Cython; BigQuery is separate because it uses double quotes. The negative assertions (`'12345678'`, `'O''''Brien'`, `'OBrien'`) are the exact pre-fix outputs recorded in spec §1.

### `tests/unit/test_cython_filter_fallback.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c 'def _qs_quote_string(value) -> str:' tests/unit/test_cython_filter_fallback.py)
# REPLACE the function (lines 54-60) with:
def _qs_quote_string(value) -> str:
    """Pure-Python equivalent of Entity.quoteString (idempotent, FEAT-167)."""
    if value is None or value == 'None':
        return "''"
    if value in ('null', 'NULL'):
        return str(value)
    v = str(value)
    if len(v) >= 2 and v[0] == v[-1] == "'" and "'" not in v[1:-1].replace("''", ""):
        v = v[1:-1].replace("''", "'")
    return "'" + v.replace("'", "''") + "'"
```
**Why**: the stub only runs when the extension is missing, but it must then model the same contract or the fallback suite lies.

### `docs/QUERYSOURCE_DIALECT.md` (MODIFY)
```markdown
<!-- occurrences: 1 (verified: grep -c '| `LDOM` | last day of the current month (`YYYY-MM-DD`) |' docs/QUERYSOURCE_DIALECT.md) -->
<!-- AFTER — insert below the LDOM row (verified: QUERYSOURCE_DIALECT.md:711): -->
| `FDOW` | first day (Monday) of the current week (`YYYY-MM-DD`) |
| `LDOW` | last day (Sunday) of the current week (`YYYY-MM-DD`) |
| `LDOPM` | last day of the previous month (`YYYY-MM-DD`) |

<!-- REPLACE lines 698-700 ("A string value that names a built-in function from ... with no arguments**.") and
     lines 713-716 ("The list can be changed per deployment ... or `ldopm` (last day of the previous month).") with: -->
A string value equal (case-insensitively) to one of the keywords below is
**replaced by the keyword's value**. Only these keywords are resolved: any
other text — including the name of some other helper function — is sent to
the database as a quoted literal.

The list can be changed per deployment with the `UDF_LIST` environment
variable (comma separated). That variable is the only way to make an
additional zero-argument helper of `querysource/utils/functions.pyx`
resolvable; nothing else is executed by name.

<!-- §13.2 — AFTER the paragraph ending "use a date keyword (13.1) instead." (verified: QUERYSOURCE_DIALECT.md:744), append: -->
Any other value is a **literal**: a value that merely contains parentheses
(`Acme (US)`, `pg_sleep(5)`) is quoted like any text, never treated as a
function call, and a value equal to a configuration variable name is sent as
that text, never substituted. Quoting is idempotent — a value already wrapped
in single quotes with its inner quotes doubled (`'O''Brien'`) is unwrapped once
and re-quoted, so `O'Brien` always reaches the database as `'O''Brien'`.
```
**Why**: AC6; the deleted sentence is the one that documented gap 2 as a feature.

### FILL IN checklist
- [ ] `test_where_value_hardening.py::test_parenthesis_is_literal` — comparison-dict assertion, skipped for cql; bounded by AC3
- [ ] `test_where_value_hardening.py::test_apostrophe_roundtrip` — comparison dict + `!` negation; bounded by AC4
- [ ] `docs/QUERYSOURCE_DIALECT.md` — keep the surrounding table/paragraph formatting intact

---

## Acceptance Criteria

- [ ] Spec AC1, AC2, AC3, AC4 hold as rendered SQL under pgsql Rust, pgsql Cython, `SQLParser`, `CQLParser` and `BigQueryParser`.
- [ ] Spec AC6: §13.1 has no "other zero-argument helpers" sentence and has the three new rows; §13.2 states the literal rule.
- [ ] Spec AC7 suite list green; `pytest tests/ -q` shows no new failures attributable to `Entity.quoteString` callers.
- [ ] `ruff check tests/unit/test_where_value_hardening.py tests/unit/test_cython_filter_fallback.py` clean.

---

## Validation Commands

- `pytest tests/unit/test_where_value_hardening.py -q`
- `pytest tests/unit/test_cython_filter_fallback.py -q`
- `pytest tests/test_dialect_filter_preprocessing.py -q`
- `pytest tests/qsurl/test_pg_ilike.py -q`
- `pytest tests/unit/test_udf_keyword_resolution.py -q`
- `pytest tests/unit/test_vocabulary.py -q`
- `pytest tests/test_validators_array_type.py -q`

---

## Test Specification

See the `tests/unit/test_where_value_hardening.py` blueprint block above.

---

## Agent Instructions

When you pick up this task:

1. **Work in the feature worktree** — never on `base_branch`
   (`python -m scripts.sdd.ensure_worktree --slug where-value-hardening --feature-id FEAT-167`)
2. **Read the spec** at the path listed above for full context
3. **Check dependencies** — TASK-873, TASK-876, TASK-877 must be `"done"` in `sdd/tasks/index/where-value-hardening.json`
4. **Verify the Codebase Contract** — before writing ANY code
5. **Update status** in the per-spec index → `"in-progress"` and commit only that index file
6. **Implement** from the Implementation Blueprint; rebuild BOTH extensions first
7. **Verify** — run the Validation Commands
8. **Commit the code** — stage only the files this task lists
9. **Close the task** with `scripts/sdd/close_task.sh TASK-878 where-value-hardening verified`
10. **Fill in the Completion Note** below, then commit the staged SDD state

---

## Completion Note

Implemented by native seat sonnet, 1 attempt, merged.
- Created `tests/unit/test_where_value_hardening.py` (22 tests: pgsql Rust, pgsql Cython, SQLParser, CQLParser, BigQueryParser).
- Modified `tests/unit/test_cython_filter_fallback.py` (idempotent `_qs_quote_string` stub) and `docs/QUERYSOURCE_DIALECT.md` §13.1/§13.2.
- Deviation: `"!O'Brien"` is not negation syntax; roundtrip test asserts single doubling only.
- Orchestrator follow-up commit (outside task scope, caused by this feature): `tests/test_getfunc_allowlist.py` dropped `is_udf` from the re-exported-names list; `tests/e2e/test_qs_dry_run.py` injection test now expects `'x''; DROP TABLE t; --'`.
- Full `pytest tests/` by coder: 10 failed — 2 fixed above; 8 are missing optional deps (parquet gcs/s3), unrelated to FEAT-167.
- Closed via close_task.sh (no durable review EvidenceRef for finalize_task).
