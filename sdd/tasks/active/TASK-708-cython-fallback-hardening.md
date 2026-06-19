# TASK-708: Harden Cython `filter_conditions` fallback (HAS_RUST=False path)

**Feature**: FEAT-103 — Malforming Query-Slug Issue (SQLi & Datasource Credential Exposure)
**Spec**: `sdd/specs/malforming-queryslug-issue.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-704
**Assigned-to**: unassigned

---

## Context

When the Rust extension is unavailable (`HAS_RUST=False`), the Cython
`filter_conditions` fallback builds the WHERE clause with raw f-strings, leaving the
condition **key**, **operator**, and **dict/list/BETWEEN values** unescaped
(`sql.pyx:127/139/162/192`, `pgsql.pyx:65/74/86/208/212`). Even though Rust is the
production path, the fallback must be safe (defense-in-depth, and some environments
ship without the wheel). Implements spec §3 Module 5.

---

## Scope

- In each Cython parser's `filter_conditions` fallback, escape/validate every
  interpolation site:
  - keys → identifier-quote (or reject non-identifiers);
  - operators → allowlist;
  - all value branches (scalar, list/IN, BETWEEN, dict-comparison, bool) →
    `Entity.quoteString`/`Entity.escapeString` or the Rust `validators` (via `_rs`)
    when available; reject SQL-shaped tokens.
- Apply to: `sql.pyx`, `pgsql.pyx`, `sqlserver.pyx`, `cql.pyx`, `bigquery.pyx`,
  `sosql.pyx`.
- Recompile the `.pyx` modules (build step) and add a unit test that forces the
  fallback (monkeypatch `HAS_RUST=False` / `self.filter` path) and asserts safety.

**NOT in scope**: the Rust path (TASK-705), `raw_query` providers (TASK-707). Keep the
`filter_conditions` signature and the Rust fast-path branch unchanged.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/parsers/sql.pyx` | MODIFY | Escape key/op/value sites in fallback (122-198) |
| `querysource/parsers/pgsql.pyx` | MODIFY | Same (65/74/86/204-212) |
| `querysource/parsers/sqlserver.pyx` | MODIFY | Same (147/155) |
| `querysource/parsers/cql.pyx` | MODIFY | Same (70/82/121) |
| `querysource/parsers/bigquery.pyx` | MODIFY | Same (165) |
| `querysource/parsers/sosql.pyx` | MODIFY | Same (148/156) |
| `tests/unit/test_cython_filter_fallback.py` | CREATE | Force fallback + injection test |

---

## Codebase Contract (Anti-Hallucination)

> Verified 2026-06-19 on `dev`.

### Existing Signatures to Use
```python
# querysource/parsers/sql.pyx
async def filter_conditions(self, sql):                # line 113
    if HAS_RUST and self.filter:                       # line 118 — Rust fast path (KEEP)
        return _rs.filter_conditions(sql, dict(self.filter), dict(self.cond_definition))
    # --- Cython fallback (HARDEN below) ---
    key = f'"{key}"'                                   # line 127 (only when numeric key today)
    where_cond.append(f"{key} {op} {v}")               # line 139  ← raw op + value
    where_cond.append(f"({key} {value})")              # line 162-168 (BETWEEN) ← raw
    where_cond.append(f"{key}={Entity.quoteString(value)}")  # line 188/192 (already escaped)
    where_cond.append(f"{key} = {value}")              # line 192 (bool) ← raw

# Entity escaping helpers already used in these parsers:
# Entity.quoteString(value), Entity.escapeString(value)   (from python-datamodel)
```

### Does NOT Exist
- ~~a Cython `sanitize` helper~~ — use `Entity.quoteString`/`escapeString` or `_rs`
  validators; do not invent a new helper name without adding it.
- ~~removing the Rust fast-path~~ — keep `if HAS_RUST and self.filter: return _rs...`.

---

## Implementation Notes

### Key Constraints
- Follow `.claude/rules/cython-development.md`: `cdef`/`cpdef`, static typing, prefer
  `cimport`. Recompile after edits.
- Behavioral parity with the hardened Rust path (TASK-705) — the fallback and Rust
  path should reject/escape the same inputs.
- Default-deny on unknown operators / non-identifier keys.

### References in Codebase
- `querysource/parsers/pgsql.pyx:204` — `f"{key} {op} {Entity.quoteString(v)}"` is an
  example of the already-escaped form to extend to all branches.

---

## Acceptance Criteria

- [ ] With `HAS_RUST` forced False, malicious key/operator/list/BETWEEN values are
      escaped or rejected (no raw interpolation).
- [ ] Legitimate filters produce the same SQL as before (regression).
- [ ] `.pyx` modules recompile cleanly; existing parser tests pass.
- [ ] `tests/unit/test_cython_filter_fallback.py` passes.

---

## Test Specification

```python
# tests/unit/test_cython_filter_fallback.py
import pytest
# Force the Cython fallback (HAS_RUST monkeypatched False) and build a filter
# with a malicious key/value; assert the rendered SQL contains no UNION/-- breakout
# and that benign filters render unchanged.
```

---

## Agent Instructions

1. Confirm TASK-704 in `completed/` (validators available for parity).
2. Update index → `in-progress`; `source .venv/bin/activate`.
3. Implement; recompile `.pyx` (project build). Keep the Rust fast-path branch.
4. Run parser tests + the new fallback test.
5. Move to `completed/`, update index, fill Completion Note.

---

## Completion Note

**Completed by**: <id>
**Date**: YYYY-MM-DD
**Notes**:
**Deviations from spec**: none
