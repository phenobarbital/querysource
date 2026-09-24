# TASK-746: Parse `refresh` and `paged` in `AbstractParser` with `to_flag`

**Feature**: FEAT-149 — Falsy Refresh — flag-condition coercion for `refresh` and `paged`
**Spec**: `sdd/specs/falsy-refresh.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-744
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 3. Two parser defects:

1. `_query_refresh_sync` uses `strtobool`, so it disagrees with the provider for `''`
   (parser `False`, agreed semantics `True`).
2. `_offset_pagination_sync` calls `strtobool(paged)` without catching `ValueError`.
   Under Cython 3 the `cdef void` propagates it, so `paged=maybe` / `paged=` makes
   `await set_options()` **raise** and the query fails (verified 2026-09-24, spec §1 table).

---

## Scope

- Change the import at `parsers/abstract.pyx:15` to `from ..types import to_flag`
  (`strtobool` and `is_boolean` have no other uses in this file once both blocks change).
- `_query_refresh_sync`: `self.refresh = to_flag(refresh)`; `ValueError` → `self.logger.warning(...)` + `False`.
- `_offset_pagination_sync`: `self._paged = to_flag(paged)`; `ValueError` → warning + `False`.
  The `page` pop that follows (lines 220-223) must still run.
- `make build-inplace`.

**NOT in scope**: `.pxd` changes; `distinct` (`abstract.pyx:387`) or other conditions;
provider (TASK-745); new tests (TASK-747).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/parsers/abstract.pyx` | MODIFY | import `to_flag`; rewrite the `refresh` and `paged` coercion blocks |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
# inside querysource/parsers/abstract.pyx
from ..types import strtobool, is_boolean     # verified: abstract.pyx:15 (only 4 references in file: 15, 184, 212, 215)
from ..types import to_flag                   # created by TASK-744 (querysource/types/__init__.py:2)
```

### Existing Signatures to Use
```python
# querysource/parsers/abstract.pxd
cdef public object logger                     # line 11
cdef public bint refresh                      # line 23
cdef bint _paged                              # line 42
cdef void _query_refresh_sync(self)           # line 67
cdef void _offset_pagination_sync(self)       # line 70

# querysource/parsers/abstract.pyx
    self.logger = logging.getLogger(f'QS.Parser.{self._name_}')   # line 40
    cdef void _query_refresh_sync(self):                            # line 177
        cdef object refresh
        try:
            refresh = self.conditions.pop('refresh', False)         # 180
            if isinstance(refresh, bool):                           # 181
                self.refresh = refresh                              # 182
            else:
                self.refresh = strtobool(str(refresh))              # 184
        except (KeyError, AttributeError, ValueError):              # 185
            self.refresh = False                                    # 186

    cdef void _offset_pagination_sync(self):                        # line 204
        cdef object paged
        # _offset pop                                               # 206-209
        try:
            paged = self.conditions.pop('paged', False)             # 211
            if is_boolean(paged):                                   # 212
                self._paged = paged
            elif isinstance(paged, str):
                self._paged = strtobool(paged)                      # 215
            else:
                self._paged = False
        except (KeyError, AttributeError):                          # 218
            self._paged = False
        try:
            self._page_ = self.conditions.pop('page', 0)            # 221
        except (KeyError, AttributeError):
            self._page_ = 0
```
`self.conditions` is a `QueryObject` (`define_conditions`, abstract.pyx:96-102); its
`pop` returns `None` for a missing key, and `to_flag(None)` is `False`.

### Does NOT Exist
- ~~`noexcept` on these methods~~ — not declared; any uncaught exception propagates out of `set_options()`.
- ~~Python access to `_paged`~~ — non-public `cdef bint`.
- ~~`self._logger`~~ on the parser — it is `self.logger` (pxd:11).
- ~~`validators.pxd`~~ — `to_flag` is imported, not `cimport`ed.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {
      "path": "querysource/parsers/abstract.pyx",
      "action": "MODIFY"
    }
  ],
  "contract_symbols": [
    "sym:querysource/parsers/abstract.pyx#AbstractParser",
    "sym:querysource/parsers/abstract.pyx#AbstractParser._query_refresh_sync",
    "sym:querysource/parsers/abstract.pyx#AbstractParser._offset_pagination_sync"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- Catch `ValueError` explicitly in both blocks — a new exception escaping a `cdef void` fails the whole query.
- Keep `cdef object refresh` / `cdef object paged` typed locals.
- Warnings: `repr(value)[:64]`, lazy `%s` args.
- `make build-inplace` is required; it rebuilds shared `.so` files → this task is exclusive.

---

## Implementation Blueprint

### Steps (in order)
1. Replace the import at line 15 — *why*: after steps 2-3 nothing else in the file uses `strtobool`/`is_boolean`; confirm with `grep -n "strtobool\|is_boolean" querysource/parsers/abstract.pyx` before removing.
2. Rewrite `_query_refresh_sync` body — *why*: provider/parser parity (spec G2).
3. Rewrite the `paged` try-block in `_offset_pagination_sync` — *why*: malformed `paged` must not raise (spec G4).
4. `make build-inplace`, then run the validation commands — *why*: tests otherwise run against the stale `.so`.

### `querysource/parsers/abstract.pyx` (MODIFY)
```python
# occurrences: 1 (verified: grep -c 'from ..types import strtobool, is_boolean' querysource/parsers/abstract.pyx)
# REPLACE line 15:
from ..types import to_flag

# occurrences: 1 (verified: grep -c 'self.refresh = strtobool(str(refresh))' querysource/parsers/abstract.pyx)
# REPLACE the body of `cdef void _query_refresh_sync(self):` (lines 178-186, verified: abstract.pyx:177-186)
        cdef object refresh = None
        try:
            refresh = self.conditions.pop('refresh', False)
            self.refresh = to_flag(refresh)
        except (KeyError, AttributeError):
            self.refresh = False
        except ValueError:
            self.logger.warning(
                "Unrecognized 'refresh' condition value %s; treating as False",
                repr(refresh)[:64]
            )
            self.refresh = False

# occurrences: 1 (verified: grep -c 'if is_boolean(paged):' querysource/parsers/abstract.pyx)
# REPLACE the `paged` try-block (lines 210-219, verified: abstract.pyx:211-219); leave the `_offset` block above
# and the `page` block below untouched
        try:
            paged = self.conditions.pop('paged', False)
            self._paged = to_flag(paged)
        except (KeyError, AttributeError):
            self._paged = False
        except ValueError:
            # FILL IN: warning via self.logger mirroring the refresh block (condition name 'paged', repr(paged)[:64]) — bounded by spec §7 Patterns
            self._paged = False
```
**Why**: `cdef object paged` is already declared at the top of `_offset_pagination_sync` (line 205) — do not redeclare it. Initialising `refresh = None` guarantees the warning can format it. The `page` pop must stay a separate block so it always runs.

### FILL IN checklist
- [ ] `_offset_pagination_sync` `ValueError` branch — warning text mirroring `refresh`; bounded by spec §7

---

## Acceptance Criteria

- [ ] `await set_options()` does not raise for `{'paged': 'maybe', 'page': 3}` or `{'paged': '', 'page': 3}`; `page` is still consumed.
- [ ] Parser `refresh`: `'false'` → False, `''` → True, `'maybe'` → False + WARNING.
- [ ] No remaining references to `strtobool`/`is_boolean` in `parsers/abstract.pyx`.
- [ ] `make build-inplace` succeeds; existing parser suites pass.

---

## Validation Commands

- `pytest tests/test_grouping_sync.py -q`
- `pytest tests/test_sql_parser_combinations.py -q`

---

## Test Specification

Dedicated tests are written in TASK-747 (`test_parser_refresh_parity`,
`test_parser_paged_unrecognized_no_raise`, `test_parser_paged_empty_is_true`).

---

## Agent Instructions

1. Read the spec (§1 table, §3 Module 3, §7 risks).
2. Confirm TASK-744 is done; verify anchors with `grep -c`.
3. Update the index → `"in-progress"`; implement; complete the `# FILL IN:`.
4. `make build-inplace`; run the validation commands.
5. Move this file to `sdd/tasks/completed/`, set the index to `"done"`, fill the Completion Note.

---

## Completion Note

**Completed by**: sdd-worker (Claude Sonnet 5, sequential fallback loop)
**Date**: 2026-09-24
**Notes**: Replaced the `strtobool`/`is_boolean` import with `to_flag`; rewrote
`_query_refresh_sync` and the `paged` try-block in `_offset_pagination_sync`
per the blueprint, including the `# FILL IN:` `ValueError` warning branch for
`paged` (mirrors the `refresh` warning). `page` pop untouched. `make
build-inplace` succeeded. `pytest tests/test_grouping_sync.py
tests/test_sql_parser_combinations.py -q` → 105 passed. Manually verified
`{'paged': 'maybe'|''}` no longer raise from `set_options()`, `page` still
consumed, and parser `refresh` parity with the provider (`'false'`→False,
`''`→True). No remaining `strtobool`/`is_boolean` references in this file.

**Deviations from spec**: none
