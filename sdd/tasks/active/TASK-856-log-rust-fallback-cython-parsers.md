# TASK-856: Log swallowed Rust fast-path failures in the SQL, PostgreSQL and SQL Server parsers

**Feature**: FEAT-163 — Log swallowed Rust fast-path failures in the Cython parsers
**Spec**: `sdd/specs/fixgroup-06e4d35ca133.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: S (< 2h)
**Depends-on**: none
**Assigned-to**: unassigned
**discovered_from**: issue:dc4ca6c5475c

---

## Context

Promoted by `/sdd-fix` from ledger issue `issue:dc4ca6c5475c` (tech_debt, minor,
"Partial-match Rust errors are swallowed by `except Exception` in Cython parsers
(sql.pyx silent)", discovered from `spec:FEAT-180`). Three Cython parsers delegate
`filter_conditions` to the Rust extension and, when the Rust call raises (FEAT-180
`PyValueError` for an invalid partial-matching operand, a stale extension, a panic
surfaced as an exception), fall back to the Cython implementation with a bare
`except Exception: pass` — `sql.pyx:149-150`, `pgsql.pyx:339-340`,
`sqlserver.pyx:99-100`. `bigquery.pyx:154-157` already logs the same fallback at
WARNING. This task implements spec §3 M1 (the only module): add the log line to the
three silent sites and one `caplog` test per parser.

---

## Scope

- Replace the three bare `except Exception: pass` blocks with
  `except Exception as exc:` + `self.logger.warning("Rust <fn> failed, falling back to Cython: %s", exc)`,
  `<fn>` being `filter_conditions` / `pgsql_filter_conditions` / `mssql_filter_conditions`.
- Append `test_sql_rust_failure_is_logged`, `test_pgsql_rust_failure_is_logged` and
  `test_mssql_rust_failure_is_logged` to the three existing FEAT-180 test modules: stub
  `_rs` with an object whose Rust entry point raises `RuntimeError("boom")`, force
  `HAS_RUST = True`, capture the parser logger with `caplog`, assert the message and
  that the Cython fallback still rendered the expected WHERE body.
- Rebuild the Cython extensions in place (`make build-inplace`) before running the tests.

**NOT in scope**: narrowing `except Exception` (spec §7 D1); re-raising Rust validation
errors instead of falling back; the `group_by` / `order_by` / `pgsql_unnest_*` /
`bq_process_fields` fast paths; `bigquery.pyx` (already logs); any change to rendered SQL.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/parsers/sql.pyx` | MODIFY | log the swallowed exception at `sql.pyx:149-150` |
| `querysource/parsers/pgsql.pyx` | MODIFY | log the swallowed exception at `pgsql.pyx:339-340` |
| `querysource/parsers/sqlserver.pyx` | MODIFY | log the swallowed exception at `sqlserver.pyx:99-100` |
| `tests/test_sql_partial_matching.py` | MODIFY | append `test_sql_rust_failure_is_logged` |
| `tests/test_pgsql_partial_matching.py` | MODIFY | append `test_pgsql_rust_failure_is_logged` |
| `tests/test_mssql_partial_matching.py` | MODIFY | append `test_mssql_rust_failure_is_logged` |

---

## Codebase Contract (Anti-Hallucination)

> **CRITICAL**: This section contains VERIFIED code references from the actual codebase.
> The implementing agent MUST use these exact imports, class names, and method signatures.
> **DO NOT** invent, guess, or assume any import, attribute, or method not listed here.
> If you need something not listed, VERIFY it exists first with `grep` or `read`.

### Verified Imports
```python
import logging as stdlib_logging                       # verified: tests/test_sql_partial_matching.py:4 (already imported there)
import logging                                         # stdlib; tests/test_pgsql_partial_matching.py and tests/test_mssql_partial_matching.py do NOT import it yet — add it
from querysource.parsers import sql as sqlmod          # verified: tests/test_sql_partial_matching.py:22 (sqlmod.HAS_RUST, sqlmod._rs)
from querysource.parsers import pgsql                  # verified: tests/test_pgsql_partial_matching.py:8
from querysource.parsers import sqlserver as mssqlmod  # verified: tests/test_mssql_partial_matching.py:10
```
```cython
from querysource.qs_parsers import _qs_parsers as _rs  # verified: sql.pyx:22, pgsql.pyx:24, sqlserver.pyx:20 — module-level Python global, patchable via monkeypatch.setattr(<module>, "_rs", ...)
```

### Existing Signatures to Use
```cython
# querysource/parsers/abstract.pyx
self._name_ = type(self).__name__                               # line 40
self.logger = logging.getLogger(f'QS.Parser.{self._name_}')     # line 41 → "QS.Parser.SQLParser", "QS.Parser.pgSQLParser", "QS.Parser.msSQLParser"

# querysource/parsers/sql.pyx
async def filter_conditions(self, sql):                          # line 141; try: return _rs.filter_conditions(sql, dict(self.filter), dict(self.cond_definition)) at 147-148
            except Exception:                                    # line 149
                pass  # fall through to the Cython implementation (raises ParserError on invalid operands)   # line 150 (1 occurrence)

# querysource/parsers/pgsql.pyx
async def filter_conditions(self, sql):                          # line 333; try: return _rs.pgsql_filter_conditions(sql, self.filter, cond_def) at 336-338
            except Exception:                                    # line 339
                pass  # fall through to Cython implementation    # line 340 (1 occurrence in this file)
        return await self._filter_conditions_cy(sql)             # line 341

# querysource/parsers/sqlserver.pyx
async def filter_conditions(self, sql):                          # line 91; try: return _rs.mssql_filter_conditions(sql, self.filter, cond_def) at 96-98
            except Exception:                                    # line 99
                pass  # fall through to Cython implementation    # line 100 (1 occurrence in this file)
        return await self._filter_conditions_cy(sql)             # line 101

# querysource/parsers/bigquery.pyx — PATTERN SOURCE (do not modify)
            except Exception as exc:                             # line 154
                self.logger.warning(
                    "Rust bq_filter_conditions failed, falling back to Cython: %s", exc
                )                                                # lines 155-157
```
```python
# tests/test_sql_partial_matching.py
SQL = "SELECT * FROM t {where_cond}"                             # line 26
def _make_parser(filter_: dict[str, Any]) -> SQLParser           # line 65; sets cond_definition={} and filter
def _where_body(sql: str) -> str | None                          # line 81
async def test_sql_rust_error_falls_through(monkeypatch)         # line 123; RustFailure stub + monkeypatch.setattr(sqlmod, "_rs", RustFailure()) at 133 — copy this shape

# tests/test_pgsql_partial_matching.py
SQL = "SELECT * FROM t {where_cond}"                             # line 11
def _make_parser(filter_: dict) -> pgSQLParser                   # line 57
def _where_body(sql: str) -> str | None                          # exists (used at line 99)
# Cython rendering of {"n": {"startswith": "andre"}} → "n LIKE 'andre%'" (CORPUS line 38: full_name LIKE 'andre%')

# tests/test_mssql_partial_matching.py
SQL = "SELECT * FROM t {where_cond}"                             # line 13
def _make_parser(filter_: dict[str, Any]) -> msSQLParser         # line 59
def _where_body(sql: str) -> str | None                          # exists (used in the file's tests)
# Cython rendering of {"n": {"startswith": "andre"}} → "n LIKE 'andre%' ESCAPE '!'" (CORPUS via _FAMILIES line 14)
```

### Does NOT Exist
- ~~`self.logger` as a `cdef` attribute / in a `.pxd`~~ — plain Python attribute from `AbstractParser.__init__`; no `.pxd` edit.
- ~~`_filter_conditions_cy` in `sql.pyx`~~ — generic SQL's Cython fallback is inline after the `try`; only pgsql/sqlserver/bigquery have `_filter_conditions_cy`.
- ~~a shared `_rust_fallback()` helper in `abstract.pyx`~~ — do not create one; three inline sites.
- ~~`tests/__init__.py`~~ — tests are top-level modules.
- ~~`pytest.LogCaptureFixture` needing an import~~ — `caplog` is a built-in pytest fixture; annotate as `pytest.LogCaptureFixture`.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/parsers/sql.pyx", "action": "MODIFY"},
    {"path": "querysource/parsers/pgsql.pyx", "action": "MODIFY"},
    {"path": "querysource/parsers/sqlserver.pyx", "action": "MODIFY"},
    {"path": "tests/test_sql_partial_matching.py", "action": "MODIFY"},
    {"path": "tests/test_pgsql_partial_matching.py", "action": "MODIFY"},
    {"path": "tests/test_mssql_partial_matching.py", "action": "MODIFY"}
  ],
  "contract_symbols": [
    "sym:querysource/parsers/sql.pyx#SQLParser.filter_conditions",
    "sym:querysource/parsers/pgsql.pyx#pgSQLParser.filter_conditions",
    "sym:querysource/parsers/sqlserver.pyx#msSQLParser.filter_conditions",
    "sym:querysource/parsers/abstract.pyx#AbstractParser.__init__",
    "sym:tests/test_sql_partial_matching.py#_make_parser",
    "sym:tests/test_pgsql_partial_matching.py#_make_parser",
    "sym:tests/test_mssql_partial_matching.py#_make_parser"
  ]
}
```

---

## Implementation Notes

### Pattern to Follow
```cython
# querysource/parsers/bigquery.pyx:154-157
            except Exception as exc:
                self.logger.warning(
                    "Rust bq_filter_conditions failed, falling back to Cython: %s", exc
                )
```

### Key Constraints
- Keep `except Exception` (spec §7 D1) — only add `as exc` and the warning; the fallback must still absorb every Rust exception.
- `%s` lazy formatting, no f-strings (D3).
- Message template is fixed: `"Rust <fn> failed, falling back to Cython: %s"` so one grep finds all four parsers (D2).
- Tests stub `_rs`; never call the real extension (D4) — they must run and pass on every machine, never skip.
- Run `make build-inplace` after editing `.pyx` files; Python imports the compiled `.so` from the source tree.
- `test_sql_rust_error_falls_through` must keep passing unchanged (G2 regression guard).

### References in Codebase
- `querysource/parsers/bigquery.pyx:148-158` — the logging fallback to mirror
- `tests/test_sql_partial_matching.py:123-136` — `RustFailure` stub pattern
- `tests/test_error_formatter.py:50-61` — `caplog.at_level(..., logger=...)` usage

---

## Implementation Blueprint

### Steps (in order)
1. Edit the three `.pyx` except blocks exactly as the blocks below show — *why*: the message must match BigQuery's template so log consumers grep one pattern (D2).
2. Run `make build-inplace` from the worktree root — *why*: pytest imports the compiled extension, not the `.pyx`.
3. Append the three tests (one per module) — *why*: AC2 requires a never-skipping test that proves the warning fires and the fallback still renders.
4. Run the Validation Commands; then `ruff check` on the three test files — *why*: AC2–AC4.

### `querysource/parsers/sql.pyx` (MODIFY)
```cython
# occurrences: 1 (verified: grep -c 'pass  # fall through to the Cython implementation (raises ParserError on invalid operands)' querysource/parsers/sql.pyx)
# REPLACE — the two lines `            except Exception:` + `                pass  # fall through to the Cython implementation (raises ParserError on invalid operands)` (verified: querysource/parsers/sql.pyx:149-150) with:
            except Exception as exc:
                # fall through to the Cython implementation (raises ParserError on invalid operands)
                self.logger.warning(
                    "Rust filter_conditions failed, falling back to Cython: %s", exc
                )
```
**Why**: the inline Cython fallback begins right after the `try` block; keep the comment so the control flow stays documented.

### `querysource/parsers/pgsql.pyx` (MODIFY)
```cython
# occurrences: 1 (verified: grep -c 'pass  # fall through to Cython implementation' querysource/parsers/pgsql.pyx)
# REPLACE — the two lines `            except Exception:` + `                pass  # fall through to Cython implementation` (verified: querysource/parsers/pgsql.pyx:339-340) with:
            except Exception as exc:
                self.logger.warning(
                    "Rust pgsql_filter_conditions failed, falling back to Cython: %s", exc
                )
```
**Why**: `return await self._filter_conditions_cy(sql)` on the next line is the fallback; nothing else moves.

### `querysource/parsers/sqlserver.pyx` (MODIFY)
```cython
# occurrences: 1 (verified: grep -c 'pass  # fall through to Cython implementation' querysource/parsers/sqlserver.pyx)
# REPLACE — the two lines `            except Exception:` + `                pass  # fall through to Cython implementation` (verified: querysource/parsers/sqlserver.pyx:99-100) with:
            except Exception as exc:
                self.logger.warning(
                    "Rust mssql_filter_conditions failed, falling back to Cython: %s", exc
                )
```
**Why**: same shape as pgsql; `_filter_conditions_cy` follows.

### `tests/test_sql_partial_matching.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c 'async def test_sql_rust_error_falls_through' tests/test_sql_partial_matching.py)
# AFTER — append below the end of `test_sql_rust_error_falls_through` (verified: tests/test_sql_partial_matching.py:123-136; append at EOF is equivalent)
async def test_sql_rust_failure_is_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """An unexpected Rust failure is logged at WARNING before the Cython fallback renders."""
    class RustFailure:
        """Stand in for a Rust extension that fails unexpectedly."""

        @staticmethod
        def filter_conditions(*args: object) -> str:
            raise RuntimeError("boom")

    monkeypatch.setattr(sqlmod, "_rs", RustFailure())
    monkeypatch.setattr(sqlmod, "HAS_RUST", True)
    with caplog.at_level(stdlib_logging.WARNING, logger="QS.Parser.SQLParser"):
        rendered = await _make_parser({"n": {"startswith": "andre"}}).filter_conditions(SQL)
    assert _where_body(rendered) == "n LIKE 'andre%' ESCAPE '!'"
    assert "Rust filter_conditions failed, falling back to Cython: boom" in caplog.text
```
**Why**: mirrors `test_sql_rust_error_falls_through` but with a non-validation error and a valid operand, so the assertion covers both the log line and the still-working fallback.

### `tests/test_pgsql_partial_matching.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c '^import pytest' tests/test_pgsql_partial_matching.py)
# AFTER — add `import logging` above `import pytest` (verified: tests/test_pgsql_partial_matching.py:4); then append at EOF:
async def test_pgsql_rust_failure_is_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """An unexpected Rust failure is logged at WARNING before the Cython fallback renders."""
    class RustFailure:
        """Stand in for a Rust extension that fails unexpectedly."""

        @staticmethod
        def pgsql_filter_conditions(*args: object) -> str:
            raise RuntimeError("boom")

    monkeypatch.setattr(pgsql, "_rs", RustFailure())
    monkeypatch.setattr(pgsql, "HAS_RUST", True)
    with caplog.at_level(logging.WARNING, logger="QS.Parser.pgSQLParser"):
        rendered = await _make_parser({"n": {"startswith": "andre"}}).filter_conditions(SQL)
    assert _where_body(rendered) == "n LIKE 'andre%'"
    assert "Rust pgsql_filter_conditions failed, falling back to Cython: boom" in caplog.text
```
**Why**: `filter_conditions` (not `_filter_conditions_cy`) is called so the Rust try/except is exercised.

### `tests/test_mssql_partial_matching.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c '^import pytest' tests/test_mssql_partial_matching.py)
# AFTER — add `import logging` above `from typing import Any` (verified: tests/test_mssql_partial_matching.py:4); then append at EOF:
async def test_mssql_rust_failure_is_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """An unexpected Rust failure is logged at WARNING before the Cython fallback renders."""
    class RustFailure:
        """Stand in for a Rust extension that fails unexpectedly."""

        @staticmethod
        def mssql_filter_conditions(*args: object) -> str:
            raise RuntimeError("boom")

    monkeypatch.setattr(mssqlmod, "_rs", RustFailure())
    monkeypatch.setattr(mssqlmod, "HAS_RUST", True)
    with caplog.at_level(logging.WARNING, logger="QS.Parser.msSQLParser"):
        rendered = await _make_parser({"n": {"startswith": "andre"}}).filter_conditions(SQL)
    assert _where_body(rendered) == "n LIKE 'andre%' ESCAPE '!'"
    assert "Rust mssql_filter_conditions failed, falling back to Cython: boom" in caplog.text
```
**Why**: same shape; the expected body is the SQL Server `ESCAPE '!'` form from this file's CORPUS.

### FILL IN checklist
- [ ] none — every block is complete; only the import placement in the two test files is positional (keep imports sorted: `logging` before `pytest`, ruff I001).

---

## Acceptance Criteria

- [ ] AC1. No bare `except Exception: pass` remains in `filter_conditions` of `sql.pyx`, `pgsql.pyx`, `sqlserver.pyx`; each logs `"Rust <fn> failed, falling back to Cython: %s"`.
- [ ] AC2. `pytest tests/test_sql_partial_matching.py tests/test_pgsql_partial_matching.py tests/test_mssql_partial_matching.py -q` passes after `make build-inplace`; the three new tests run (not skipped).
- [ ] AC3. `pytest tests/test_bigquery_partial_matching.py tests/test_partial_matching_conformance.py -q` stays green.
- [ ] AC4. `ruff check tests/test_sql_partial_matching.py tests/test_pgsql_partial_matching.py tests/test_mssql_partial_matching.py` reports nothing new.

---

## Validation Commands

- `pytest tests/test_sql_partial_matching.py -q`
- `pytest tests/test_pgsql_partial_matching.py -q`
- `pytest tests/test_mssql_partial_matching.py -q`
- `pytest tests/test_bigquery_partial_matching.py -q`
- `pytest tests/test_partial_matching_conformance.py -q`

---

## Test Specification

See the three test blocks in the Implementation Blueprint — they are the complete tests.

---

## Agent Instructions

When you pick up this task:

1. **Work in the feature worktree** — never on `base_branch`
   (`python -m scripts.sdd.ensure_worktree --slug fixgroup-06e4d35ca133 --feature-id FEAT-163`)
2. **Read the spec** at the path listed above for full context
3. **Check dependencies** — none
4. **Verify the Codebase Contract** — before writing ANY code, re-run the `grep -c` lines in the blueprint
5. **Update status** in `sdd/tasks/index/fixgroup-06e4d35ca133.json` → `"in-progress"` (set `started_at`) and commit only that index file
6. **Implement** — write the blueprint blocks; run `make build-inplace`
7. **Verify** — run the Validation Commands
8. **Commit the code** — stage only the six files this task lists
9. **Close the task** with `scripts/sdd/close_task.sh TASK-856 fixgroup-06e4d35ca133 verified`
10. **Fill in the Completion Note** below, then commit the staged SDD state

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**: What was implemented, any deviations from scope, issues encountered.

**Deviations from spec**: none | describe if any
