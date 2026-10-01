# TASK-833: Dual-path tests for `@!` / `@$` and version bump

**Feature**: FEAT-179 — JSONB NOT (`@!`) and NOT-ALL (`@$`) filter operators
**Spec**: `sdd/specs/jsonb-not-or-operators.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-831, TASK-832
**Assigned-to**: unassigned

---

## Context

Implements spec Modules 3 and 4 (§3) and the §4 Test Specification. Every case
runs through both code paths (the Rust fast path and the Cython fallback) via
the existing `PATHS` parametrization, so the tests check that the two
implementations agree. The version bump follows the precedent set by commit
`8936386`.

---

## Scope

- Add the happy-path cases for `@!`/`@$` to `test_jsonb_condition`.
- Add the drop cases to `test_invalid_jsonb_filters_are_dropped` (empty,
  non-list, invalid JSON, mixed comparison/JSONB dict).
- Add escaping coverage for negated operands.
- Add `test_build_query_negation_is_grouped` with sqlglot AST assertions.
- Bump `querysource/version.py` to `5.1.5`.

**NOT in scope**: any change to `pgsql.pyx` or `pgsql_parser.rs`. If a test
shows the two paths disagree, report it on TASK-831 or TASK-832 rather than
patching the builders here.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `tests/test_pgsql_jsonb_filters.py` | MODIFY | new parametrized cases and one build_query test |
| `querysource/version.py` | MODIFY | `5.1.4` → `5.1.5` |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports (already present in the test module)
```python
import pytest
import sqlglot
from sqlglot import exp
from querysource.models import QueryObject
from querysource.parsers import pgsql
from querysource.parsers.pgsql import pgSQLParser
```

### Existing Signatures to Use
```python
# tests/test_pgsql_jsonb_filters.py
SQL = "SELECT * FROM t {where_cond}"
PATHS = [pytest.param("rust", marks=skipif(not pgsql.HAS_RUST)), "cython"]
def _make_parser(query: str, filter_: dict) -> pgSQLParser
async def _render(path: str, filter_: dict, query: str = SQL) -> str
def _where(sql: str) -> Optional[str]
async def test_jsonb_condition(path, filter_, expected)                 # line 118
async def test_invalid_jsonb_filters_are_dropped(path, filter_)         # line 146
async def test_build_query_any_of_is_grouped(use_rust, monkeypatch)     # line 195 — template
# querysource/version.py:9
__version__ = '5.1.4'
```

Expected-string conventions (from existing cases): JSON operands are compact
orjson, and a dict literal is written as `E'\\x7b...\\x7d'`. Example:
`{"course": "Pilates Studio"}` becomes
`E'\\x7b"course":"Pilates Studio"\\x7d'::jsonb`.

### Does NOT Exist
- ~~a `conftest.py` fixture for JSONB parsers~~ — use the module's own helpers.
- ~~`pgsql.jsonb_none_of_condition` callable from Python~~ — `cdef` helpers are not importable. Test through `_render`.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {
      "path": "tests/test_pgsql_jsonb_filters.py",
      "action": "MODIFY"
    },
    {
      "path": "querysource/version.py",
      "action": "MODIFY"
    }
  ],
  "contract_symbols": [
    "sym:tests/test_pgsql_jsonb_filters.py#test_jsonb_condition"
  ]
}
```

---

## Implementation Blueprint

### Steps (in order)
1. Confirm the Rust extension is rebuilt and loaded:
   `python -c "from querysource.parsers import pgsql; assert pgsql.HAS_RUST"`.
   *Why*: if it is not, the rust half of `PATHS` is skipped and parity is never
   checked (spec risk "stale Rust extension").
2. Add the parametrized cases.
3. Add the build_query test.
4. Bump the version, then run the Validation Commands.

### `tests/test_pgsql_jsonb_filters.py` (MODIFY) — happy-path cases
```python
# occurrences: 1 (verified: grep -c '    ({"tags": {"@>|": [["x"]]}}, """tags @> '\''["x"]'\''::jsonb"""),' tests/test_pgsql_jsonb_filters.py)
# AFTER — insert below that line (verified: tests/test_pgsql_jsonb_filters.py:112)
    # @! none-of: NOT of the OR-ed containment checks
    (
        {"attrs": {"@!": [{"status": "active"}, {"status": "pending"}]}},
        """NOT (attrs @> E'\\x7b"status":"active"\\x7d'::jsonb"""
        """ OR attrs @> E'\\x7b"status":"pending"\\x7d'::jsonb)""",
    ),
    ({"tags": {"@!": [["x"]]}}, """NOT (tags @> '["x"]'::jsonb)"""),
    # @$ not-all: OR of the negated containment checks
    (
        {"attrs": {"@$": [{"status": "active"}, {"status": "pending"}]}},
        """((NOT attrs @> E'\\x7b"status":"active"\\x7d'::jsonb)"""
        """ OR (NOT attrs @> E'\\x7b"status":"pending"\\x7d'::jsonb))""",
    ),
    ({"tags": {"@$": [["x"]]}}, """NOT (tags @> '["x"]'::jsonb)"""),
    # FILL IN: combined case — the ticket example on `graduation_details` with both
    #   "@!" and "@$" over [{"course": "Pilates Studio"}, {"course": "Pilates Mat"}];
    #   expected "(<none-of> AND <not-all>)" per spec §2 rendered-forms table.
    # FILL IN: one JSON-text operand case for @! (e.g. ['{"a": 1}', {"b": 2}]) mirroring line 109.
```
**Why**: the expected strings are the spec §2 table written out literally.
Both builders must produce them exactly.

### `tests/test_pgsql_jsonb_filters.py` (MODIFY) — drop cases
```python
# occurrences: 1 (verified: grep -c '    {"attrs": {"@>|": [{"a": 1}, "not json"]}},  # one invalid item drops all' tests/test_pgsql_jsonb_filters.py)
# AFTER — insert below that line (verified: tests/test_pgsql_jsonb_filters.py:144)
    {"attrs": {"@!": []}},  # empty list: dropped (no filter)
    {"attrs": {"@$": []}},  # empty list: dropped (no filter), not logical FALSE
    {"attrs": {"@!": {"a": 1}}},  # needs a list of operands
    {"attrs": {"@$": '[{"a": 1}]'}},  # JSON text is not a list of operands
    {"attrs": {"@!": [{"a": 1}, "not json"]}},  # one invalid item drops all
    {"attrs": {"@!": [{"a": 1}], "@$": []}},  # one bad group drops the whole dict
    {"attrs": {"@!": [{"a": 1}], ">=": 5}},  # mixed comparison/JSONB tokens
```

### `tests/test_pgsql_jsonb_filters.py` (MODIFY) — build_query grouping
```python
# occurrences: 1 (verified: grep -c 'async def test_build_query_any_of_is_grouped(use_rust: bool, monkeypatch) -> None:' tests/test_pgsql_jsonb_filters.py)
# AFTER — append at end of file, below test_build_query_any_of_is_grouped (verified: tests/test_pgsql_jsonb_filters.py:195-213)


@pytest.mark.parametrize("use_rust", [
    pytest.param(
        True, marks=pytest.mark.skipif(not pgsql.HAS_RUST, reason="qs_parsers not built")
    ),
    False,
])
async def test_build_query_negation_is_grouped(use_rust: bool, monkeypatch) -> None:
    """``@!``/``@$`` groups stay parenthesized next to the other AND-ed conditions."""
    monkeypatch.setattr(pgsql, "HAS_RUST", use_rust)
    operands = [{"course": "Pilates Studio"}, {"course": "Pilates Mat"}]
    filter_ = {
        "country": "United States",
        "graduation_details": {"@!": operands, "@$": operands},
    }
    sql = await _make_parser(SQL, filter_).build_query(querylimit=10)
    assert "{" not in sql and "}" not in sql
    tree = sqlglot.parse_one(sql, read="postgres")
    where = tree.args["where"].this
    assert isinstance(where, exp.And)
    # FILL IN: assert the right side is a Paren wrapping an exp.And whose left is
    #   exp.Not (over a Paren/Or) and whose right is a Paren over exp.Or of exp.Not
    #   terms. Bounded by spec §4 / S6. Check the actual sqlglot tree shape once and
    #   assert it; do not loosen it to string matching.
    # FILL IN: escaping — one more case whose operand contains a quote, braces and a
    #   backslash (e.g. {"name": "x'; --", "path": "a\\b{c}"}) under "@!" goes through
    #   build_query and sqlglot.parse_one without error (spec S8).
```

### `querysource/version.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c "__version__ = '5.1.4'" querysource/version.py)
# REPLACE line (verified: querysource/version.py:9)
__version__ = '5.1.5'
```

### FILL IN checklist
- [ ] combined `@!` + `@$` happy-path case (spec §2 table).
- [ ] JSON-text operand case for `@!`.
- [ ] AST nesting assertions in `test_build_query_negation_is_grouped` (S6).
- [ ] Escaping case through `build_query` for a negated operand (S8).

---

## Acceptance Criteria

- [ ] All new and existing cases pass on both `rust` and `cython` paths. The rust path must run, not be skipped.
- [ ] Existing expected strings are unchanged.
- [ ] `ruff check tests/test_pgsql_jsonb_filters.py querysource/version.py` is clean.
- [ ] `querysource/version.py` is `5.1.5`.

---

## Validation Commands

- `pytest tests/test_pgsql_jsonb_filters.py -q`

---

## Test Specification

See the blueprint blocks above. They are the test scaffold.

---

## Agent Instructions

1. Work in the feature worktree (`python -m scripts.sdd.ensure_worktree --slug jsonb-not-or-operators --feature-id FEAT-179`).
2. Check that TASK-831 and TASK-832 are `"done"` in `sdd/tasks/index/jsonb-not-or-operators.json`, and that both extensions are rebuilt in your environment (`make build-inplace`, `make build-rust`).
3. Set the status to `"in-progress"`, implement, and run the Validation Commands.
4. Commit only the two listed files.
5. Close with `scripts/sdd/close_task.sh TASK-833 jsonb-not-or-operators verified`.

---

## Completion Note

Seat: gpt-5.6-luna · Backend: codex · Model: gpt-5.6-luna · Attempts: 2 (attempt 1 on gpt-5.6-terra: empty_delivery) · Duration: 454.4s · Tokens: n/a

Added dual-path (Cython + Rust) cases for `@!`/`@$` to `tests/test_pgsql_jsonb_filters.py` and bumped `querysource/version.py` to 5.1.5. Verified after building both extensions in-worktree (`setup.py build_ext --inplace`; `maturin build` with the .so copied into the gitignored `querysource/qs_parsers/`): `pytest tests/test_pgsql_jsonb_filters.py` → 100 passed. No corrections needed (review feedback recorded).
