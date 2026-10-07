---
# SDD flow type and base branch (FEAT-145).
type: feature
base_branch: dev
projects: [cython-parsers]
tags: [parsers, partial-matching, logging, observability, sdd-fix]
---

# Feature Specification: Log swallowed Rust fast-path failures in the Cython parsers

**Feature ID**: FEAT-163
**Date**: 2026-10-07
**Author**: agent:sdd-fix (on behalf of Jesus Lara)
**Status**: approved
**Target version**: 5.2.3
**Origin**: `/sdd-fix issue:dc4ca6c5475c` — ledger group `fixgroup:06e4d35ca133`
(tech_debt, minor, discovered from `spec:FEAT-180`). Parent FEAT-180 is
complete (`completed_at` set), so the fix gets its own feature id.

---

## 1. Motivation & Business Requirements

### Problem Statement
Three Cython parsers delegate WHERE-building to the Rust extension and, when
the Rust call raises, fall back to the Cython implementation **silently**:

| Parser | Rust call | Fallback site | Behaviour today |
|---|---|---|---|
| `querysource/parsers/sql.pyx` | `_rs.filter_conditions` | `sql.pyx:149-150` | `except Exception: pass` |
| `querysource/parsers/pgsql.pyx` | `_rs.pgsql_filter_conditions` | `pgsql.pyx:339-340` | `except Exception: pass` |
| `querysource/parsers/sqlserver.pyx` | `_rs.mssql_filter_conditions` | `sqlserver.pyx:99-100` | `except Exception: pass` |
| `querysource/parsers/bigquery.pyx` | `_rs.bq_filter_conditions` | `bigquery.pyx:154-157` | `except Exception as exc: self.logger.warning(...)` |

FEAT-180 made the Rust path raise `PyValueError` for invalid partial-matching
operands (`rust/src/partial_match.rs:93-137`, `rust/src/sql_parser.rs:207`),
and any *unexpected* Rust failure (a panic surfaced as `PanicException`, a
stale extension missing a symbol, a type mismatch) takes the same silent
route. Operators cannot tell from logs whether a query was rendered by Rust
or by the slower Cython path, nor why — which is what ledger issue
`issue:dc4ca6c5475c` reports ("Partial-match Rust errors are swallowed by
`except Exception` in Cython parsers (sql.pyx silent)"). The BigQuery parser
already logs the fallback; the other three do not.

### Goals
- G1. Every Rust fast-path fallback in `filter_conditions` of the SQL,
  PostgreSQL and SQL Server parsers logs a WARNING naming the Rust function
  and the exception, mirroring `bigquery.pyx:155-157`.
- G2. Rendered SQL is byte-for-byte unchanged on both paths: all existing
  FEAT-180 partial-matching suites stay green, including
  `test_sql_rust_error_falls_through` (the fallback still raises
  `ParserError` for invalid operands).
- G3. The new warning is covered by one test per parser that stubs `_rs`
  with a failing object and asserts the message through `caplog`.

### Non-Goals (explicitly out of scope)
- Narrowing `except Exception` to specific exception types (the fallback
  must still catch panics and stale-extension errors; see §7 D1).
- Re-raising Rust validation errors instead of falling back (the Cython
  path re-validates and raises `ParserError` itself; changing that would
  alter the error messages the FEAT-180 suites assert on).
- The other Rust fast paths in these files (`group_by`, `order_by`,
  `pgsql_unnest_plan`, `pgsql_unnest_wrap`, `bq_process_fields`) — they are
  either unguarded or already log.
- `bigquery.pyx` (already logs).

---

## 2. Architectural Design

### Overview
Replace the three bare `except Exception: pass` blocks with
`except Exception as exc:` + `self.logger.warning(...)`, using the exact
message shape BigQuery uses so log consumers can grep one pattern:

```
Rust <rust_fn> failed, falling back to Cython: <exc>
```

`self.logger` is `logging.getLogger(f'QS.Parser.{type(self).__name__}')`
(`abstract.pyx:40-41`), so the logger names are `QS.Parser.SQLParser`,
`QS.Parser.pgSQLParser` and `QS.Parser.msSQLParser`.

### Component Diagram
```
<Dialect>Parser.filter_conditions(sql)
  ├─ HAS_RUST and self.filter ──► _rs.<dialect>_filter_conditions(...)  ──► return
  │                                   │ raises
  │                                   ▼
  │                           self.logger.warning("Rust <fn> failed, falling back to Cython: %s", exc)   ← NEW
  └─ Cython fallback (_filter_conditions_cy / inline FEAT-103 path) ──► return / ParserError
```

### Integration Points
| Existing Component | Integration Type | Notes |
|---|---|---|
| `SQLParser.filter_conditions` (`sql.pyx:141`) | modifies | log before the inline Cython fallback |
| `pgSQLParser.filter_conditions` (`pgsql.pyx:333`) | modifies | log before `_filter_conditions_cy` |
| `msSQLParser.filter_conditions` (`sqlserver.pyx:91`) | modifies | log before `_filter_conditions_cy` |
| `AbstractParser.logger` (`abstract.pyx:41`) | uses | existing per-parser logger |
| `tests/test_sql_partial_matching.py`, `tests/test_pgsql_partial_matching.py`, `tests/test_mssql_partial_matching.py` | extends | one caplog test each |

### Data Models
None.

### New Public Interfaces
None.

---

## 3. Module Breakdown

#### Delegation-eligible modules
| Module | Eligible? | Decided patterns / exact contracts | Why not (if no) |
|---|---|---|---|
| M1: Cython fallback logging + tests | yes | message format, logger names, stub pattern and anchors are all fixed below | — |

### Module 1: Cython fallback logging and caplog tests
- **Path**: `querysource/parsers/sql.pyx`, `querysource/parsers/pgsql.pyx`,
  `querysource/parsers/sqlserver.pyx`, `tests/test_sql_partial_matching.py`,
  `tests/test_pgsql_partial_matching.py`, `tests/test_mssql_partial_matching.py`
- **Responsibility**: log the swallowed exception at WARNING in the three
  parsers; add one test per parser proving the warning is emitted and the
  Cython fallback still renders.
- **Depends on**: nothing new.
- **Interface Skeleton**:
  ```cython
  # querysource/parsers/sql.pyx  (replaces sql.pyx:149-150)
            except Exception as exc:
                self.logger.warning(
                    "Rust filter_conditions failed, falling back to Cython: %s", exc
                )

  # querysource/parsers/pgsql.pyx  (replaces pgsql.pyx:339-340)
            except Exception as exc:
                self.logger.warning(
                    "Rust pgsql_filter_conditions failed, falling back to Cython: %s", exc
                )

  # querysource/parsers/sqlserver.pyx  (replaces sqlserver.pyx:99-100)
            except Exception as exc:
                self.logger.warning(
                    "Rust mssql_filter_conditions failed, falling back to Cython: %s", exc
                )
  ```
  ```python
  # tests/test_sql_partial_matching.py (append)
  async def test_sql_rust_failure_is_logged(
      monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
  ) -> None:
      """An unexpected Rust failure is logged at WARNING before the Cython fallback renders."""
      class RustFailure:
          @staticmethod
          def filter_conditions(*args: object) -> str:
              raise RuntimeError("boom")
      monkeypatch.setattr(sqlmod, "_rs", RustFailure())
      monkeypatch.setattr(sqlmod, "HAS_RUST", True)
      with caplog.at_level(stdlib_logging.WARNING, logger="QS.Parser.SQLParser"):
          rendered = await _make_parser({"n": {"startswith": "andre"}}).filter_conditions(SQL)
      assert _where_body(rendered) == "n LIKE 'andre%' ESCAPE '!'"
      assert "Rust filter_conditions failed, falling back to Cython: boom" in caplog.text
  # tests/test_pgsql_partial_matching.py / tests/test_mssql_partial_matching.py: same shape with
  # pgsql._rs.pgsql_filter_conditions / mssqlmod._rs.mssql_filter_conditions, loggers
  # "QS.Parser.pgSQLParser" / "QS.Parser.msSQLParser", expected bodies
  # "n LIKE 'andre%'" / "n LIKE 'andre%' ESCAPE '!'".
  ```

---

## 4. Test Specification

### Unit Tests
| Test | Module | Description |
|---|---|---|
| `test_sql_rust_failure_is_logged` | M1 | `_rs` stub raises `RuntimeError("boom")`; WARNING on `QS.Parser.SQLParser` contains `Rust filter_conditions failed, falling back to Cython: boom`; Cython result rendered |
| `test_pgsql_rust_failure_is_logged` | M1 | same for `pgsql_filter_conditions` / `QS.Parser.pgSQLParser` |
| `test_mssql_rust_failure_is_logged` | M1 | same for `mssql_filter_conditions` / `QS.Parser.msSQLParser` |
| existing `test_sql_rust_error_falls_through` | M1 | still raises `ParserError("... requires a string operand")` |

### Integration Tests
| Test | Description |
|---|---|
| `tests/test_sql_partial_matching.py`, `tests/test_pgsql_partial_matching.py`, `tests/test_mssql_partial_matching.py`, `tests/test_bigquery_partial_matching.py`, `tests/test_partial_matching_conformance.py` | FEAT-180 corpus stays green on both paths |

### Test Data / Fixtures
```python
FILTER = {"n": {"startswith": "andre"}}      # valid on every dialect, renders on the Cython path
```

---

## 5. Acceptance Criteria

- [ ] AC1. `sql.pyx`, `pgsql.pyx` and `sqlserver.pyx` contain no bare `except Exception: pass` in `filter_conditions`; each fallback calls `self.logger.warning("Rust <fn> failed, falling back to Cython: %s", exc)` with `<fn>` = `filter_conditions` / `pgsql_filter_conditions` / `mssql_filter_conditions`.
- [ ] AC2. `pytest tests/test_sql_partial_matching.py tests/test_pgsql_partial_matching.py tests/test_mssql_partial_matching.py -q` passes after `make build-inplace` (the three new tests run on the Cython path and never skip).
- [ ] AC3. `pytest tests/test_bigquery_partial_matching.py tests/test_partial_matching_conformance.py -q` stays green (no SQL output change).
- [ ] AC4. `ruff check` on the three modified test files reports nothing new.

---

## 6. Codebase Contract

### Verified Imports
```python
import logging as stdlib_logging                       # verified: tests/test_sql_partial_matching.py:4
from querysource.parsers import sql as sqlmod          # verified: tests/test_sql_partial_matching.py:22 (sqlmod.HAS_RUST, sqlmod._rs)
from querysource.parsers import pgsql                  # verified: tests/test_pgsql_partial_matching.py:8
from querysource.parsers import sqlserver as mssqlmod  # verified: tests/test_mssql_partial_matching.py:10
```
```cython
from querysource.qs_parsers import _qs_parsers as _rs  # verified: sql.pyx:22, pgsql.pyx:24, sqlserver.pyx:20 (module-level, patchable)
```

### Existing Class Signatures
```cython
# querysource/parsers/abstract.pyx
self._name_ = type(self).__name__                                     # line 40
self.logger = logging.getLogger(f'QS.Parser.{self._name_}')           # line 41

# querysource/parsers/sql.pyx
async def filter_conditions(self, sql):                               # line 141; Rust try at 147-150, inline Cython fallback follows
# querysource/parsers/pgsql.pyx
async def filter_conditions(self, sql):                               # line 333; Rust try at 336-340; returns await self._filter_conditions_cy(sql) at 341
# querysource/parsers/sqlserver.pyx
async def filter_conditions(self, sql):                               # line 91; Rust try at 96-100; returns await self._filter_conditions_cy(sql) at 101
# querysource/parsers/bigquery.pyx — pattern source
            except Exception as exc:
                self.logger.warning(
                    "Rust bq_filter_conditions failed, falling back to Cython: %s", exc
                )                                                     # lines 154-157
```
```python
# tests/test_sql_partial_matching.py
def _make_parser(filter_: dict[str, Any]) -> SQLParser       # line 65
def _where_body(sql: str) -> str | None                       # line 81
async def test_sql_rust_error_falls_through(monkeypatch)      # line 121; stubs sqlmod._rs with RustFailure (pattern to copy)
# tests/test_pgsql_partial_matching.py
def _make_parser(filter_: dict) -> pgSQLParser                # line 57
# tests/test_mssql_partial_matching.py
def _make_parser(filter_: dict[str, Any]) -> msSQLParser      # line 59
```

### Integration Points
| New Component | Connects To | Via | Verified At |
|---|---|---|---|
| warning in `SQLParser.filter_conditions` | `self.logger` | `AbstractParser.__init__` | `abstract.pyx:41` |
| `test_*_rust_failure_is_logged` | `monkeypatch.setattr(<mod>, "_rs", stub)` | module-level `_rs` global | `tests/test_sql_partial_matching.py:133` |

### Does NOT Exist (Anti-Hallucination)
- ~~`self.logger` as a `cdef` attribute~~ — it is a plain Python attribute set in `AbstractParser.__init__`; no `.pxd` change is needed.
- ~~a shared `_rust_fallback(...)` helper in `abstract.pyx`~~ — each parser inlines its own try/except; keep it that way (three sites, one line each).
- ~~`_filter_conditions_cy` in `sql.pyx`~~ — the generic SQL parser's Cython fallback is inline in `filter_conditions` after the `try` block; only pgsql/sqlserver/bigquery have `_filter_conditions_cy`.
- ~~`tests/__init__.py`~~ — test modules are top-level.

### Edit Sites (Blueprint Anchors)
Verified against: `5bf294ff` (dev, 2026-10-07)

| File | Action | Verbatim anchor line | Verified at | Occurrences |
|---|---|---|---|---|
| `querysource/parsers/sql.pyx` | MODIFY (replace this line and the `except Exception:` above it) | `                pass  # fall through to the Cython implementation (raises ParserError on invalid operands)` | `sql.pyx:150` | 1 |
| `querysource/parsers/pgsql.pyx` | MODIFY (replace this line and the `except Exception:` above it) | `                pass  # fall through to Cython implementation` | `pgsql.pyx:340` | 1 |
| `querysource/parsers/sqlserver.pyx` | MODIFY (replace this line and the `except Exception:` above it) | `                pass  # fall through to Cython implementation` | `sqlserver.pyx:100` | 1 |
| `tests/test_sql_partial_matching.py` | MODIFY (append after) | `async def test_sql_rust_error_falls_through(monkeypatch: pytest.MonkeyPatch) -> None:` | `test_sql_partial_matching.py:121` | 1 |
| `tests/test_pgsql_partial_matching.py` | MODIFY (append at EOF) | — | — | — |
| `tests/test_mssql_partial_matching.py` | MODIFY (append at EOF) | — | — | — |

---

## 7. Implementation Notes & Constraints

### Decisions
- **D1 — keep `except Exception`, add the log.** The fallback must still
  absorb `pyo3_runtime.PanicException` (a `BaseException` subclass is *not*
  caught — that is deliberate and unchanged), `AttributeError` from a stale
  extension and `ValueError` from the FEAT-180 validators; the issue is the
  silence, not the breadth. Narrowing would change which failures reach the
  caller and is out of scope.
- **D2 — WARNING, same shape as BigQuery.** `bigquery.pyx:155-157` already
  logs `"Rust bq_filter_conditions failed, falling back to Cython: %s"`; the
  three new messages use the same template with their own Rust function name
  so one grep (`failed, falling back to Cython`) finds every fallback.
- **D3 — `%s` lazy formatting, not f-strings** (matches BigQuery and the
  logging convention in `codebase-conventions.md`).
- **D4 — tests stub `_rs`, never call the real extension**, so they pass with
  or without a fresh `_qs_parsers` build and never skip (AC2).

### Patterns to Follow
- `bigquery.pyx:154-157` for the except block.
- `tests/test_sql_partial_matching.py:121-134` (`RustFailure` stub +
  `monkeypatch.setattr(sqlmod, "_rs", ...)`) for the test shape.
- `tests/test_error_formatter.py:53` for `caplog.at_level(..., logger=...)`.

### Known Risks / Gotchas
- `.pyx` changes need `make build-inplace` (Cython) before pytest sees them;
  the extension in the source tree is what Python imports.
- `navconfig.logging` may install handlers on import; `caplog.at_level` with
  the explicit logger name is what makes the assertion deterministic.
- `test_sql_rust_error_falls_through` must keep passing unchanged — it is
  the regression guard for G2.

### External Dependencies
None.

---

## 8. Open Questions
None.
