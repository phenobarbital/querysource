# TASK-739: Relative-date keyword vocabulary registry

**Feature**: FEAT-148 — Describe Query-Slug REST Endpoints
**Spec**: `sdd/specs/describe-queryslug.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-734
**Assigned-to**: unassigned

---

## Context

This task implements spec §3 **Module 6**: a curated, hand-written registry that documents the relative-date keywords accepted as condition values.
- **Why curated:** the `utils/functions.pyx` helpers have no docstrings to introspect.
- **Registry contents:** the keywords, the PG constants and a small informational function block.
- **Endpoint:** `GET /api/v1/queries/vocabulary` (wired in TASK-741) returns `build_vocabulary(...)`.
- **Dependency:** it needs the Python accessors `udf_keywords()`, `pg_constants()` and `pg_udfs()` added by TASK-734.

**Correction to the brainstorm.** The code is authoritative:
- `LAST_YEAR` resolves to a date string, not an integer (`functions.pyx:162`).
- `CURRENT_MONTH` resolves to an integer (`:229`).
- `TODAY` resolves with mask `%m/%d/%Y` (`:94`).

For that reason, examples are computed at request time with `to_udf`, never hardcoded.

---

## Scope

- Create `querysource/utils/vocabulary.py` with `KeywordEntry`, `FunctionArg`, `FunctionEntry`, `VOCABULARY_VERSION`, `KEYWORD_REGISTRY`, `FUNCTION_REGISTRY`, `keyword_example()` and `build_vocabulary()`.
- Write `tests/unit/test_vocabulary.py`.

**NOT in scope**:
- The HTTP route (TASK-741).
- Making functions invocable as condition values.
- Changing date masks.
- Including `previous_year`.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/utils/vocabulary.py` | CREATE | registry + builder |
| `tests/unit/test_vocabulary.py` | CREATE | unit tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from pydantic import BaseModel                                             # pydantic v2 (handlers/_pagination.py:29)
from querysource.utils.functions import to_udf                             # verified: querysource/utils/functions.pyx:821
from querysource.types.validators import udf_keywords, pg_constants, pg_udfs   # added by TASK-734
```

### Existing Signatures to Use
```python
# querysource/utils/functions.pyx — keyword targets (to_udf calls globals()[name.lower()]())
cpdef str today(str mask = "%m/%d/%Y", tz: str = None)                   # :94   TODAY
cpdef int current_year()                                                 # :104  CURRENT_YEAR
cpdef int previous_year()                                                # :107  EXCLUDED (returns datetime despite int signature)
cpdef str previous_month(str mask = "%m/%d/%Y", int months = 1)          # :111
cpdef str fdom(object value = None, str mask = "%Y-%m-%d", str zone = None)  # :133  FDOM
cpdef str fdow(object value=None, str mask="%Y-%m-%d", str zone=None)    # :147
cpdef str ldow(object value=None, str mask="%Y-%m-%d", str zone=None)    # :150
cpdef str last_year(object value=None, str mask="%Y-%m-%d", str zone=None)  # :162  LAST_YEAR
cpdef str ldom(object value = None, str mask = "%Y-%m-%d", str zone = None)  # :183  LDOM
cpdef str yesterday(str mask = '%Y-%m-%d')                               # :217  YESTERDAY
cpdef int current_month()                                                # :229  CURRENT_MONTH
cpdef str days_ago(str mask = "%m/%d/%Y", int offset = 1)                # :264
cpdef str date_diff(object value, int diff = 1, str mode = 'days', str mask = "%Y-%m-%d", str tz = None)  # :454
cpdef str date_sum(object value, int diff = 1, str mode = 'days', str mask = "%Y-%m-%d", str tz = None)   # :503
def to_udf(str value, *args, **kwargs)                                   # :821
# Measured 2026-09-15: to_udf('TODAY')='09/15/2026', 'FDOM'='2026-09-01', 'LDOM'='2026-09-30',
#   'YESTERDAY'='2026-09-14', 'LAST_YEAR'='2025-09-15', 'CURRENT_YEAR'=2026, 'CURRENT_MONTH'=9
```

### Does NOT Exist
- ~~Docstrings on these helpers~~: none exist, so the registry text is authored here.
- ~~`querysource/utils/vocabulary.py`~~: created by this task.
- ~~A way to invoke `date_diff`/`date_sum`/… from HTTP condition values~~: none exists (only MultiQuery `masks`/`fnExecutor`), hence `invocable: false`.
- ~~Python-visible `UDF_LIST`~~: use the TASK-734 accessors, never `validators.UDF_LIST`.

---

## Implementation Notes

### Key Constraints
- **`KEYWORD_REGISTRY`** (keys uppercase):
  - `TODAY`: returns `date-string`, "Current date in the server timezone, formatted MM/DD/YYYY."
  - `YESTERDAY`: returns `date-string`, "Previous day, formatted YYYY-MM-DD."
  - `FDOM`: returns `date-string`, "First day of the current month, YYYY-MM-DD."
  - `LDOM`: returns `date-string`, "Last day of the current month, YYYY-MM-DD."
  - `LAST_YEAR`: returns `date-string`, "Same calendar day one year ago, YYYY-MM-DD."
  - `CURRENT_YEAR`: returns `integer`, "Current four-digit year."
  - `CURRENT_MONTH`: returns `integer`, "Current month number (1-12)."
- **`FUNCTION_REGISTRY`** has exactly `date_diff`, `date_sum`, `days_ago`, `previous_month`, `fdow` and `ldow`, in that order.
  - Args and defaults are copied from the signatures above.
  - Each entry has `invocable=False` and a one-sentence description.
- **`keyword_example(name)`:** `try: value = to_udf(name.strip())`.
  - Return `value` if it is an `int`, else `str(value)`.
  - On any exception, return `None` and log at debug with `logging.getLogger(__name__)`.
- **`build_vocabulary(udf_list, pg_constants, pg_udfs)`:**
  - Iterate `udf_list` **in order**. Use the registry entry when the uppercased name is found; otherwise `KeywordEntry(name=n, returns="unknown", description=None)`.
  - Output keys, in this order: `version`, `case_insensitive` (`True`), `keywords` (dicts with `name`, `category`, `returns`, `description`, `example`), `constants` (a list copy of `pg_constants`), `pg_functions` (a list copy of `pg_udfs`), `functions` (`[f.model_dump() for f in FUNCTION_REGISTRY]`), `usage`.
  - `usage`: `'Pass a keyword as a condition value, e.g. {"firstdate": "FDOM", "lastdate": "TODAY"}. Keywords are case-insensitive.'`

---

## Implementation Blueprint

### Steps (in order)
1. Create the models and registries — *why*: static data the tests assert against.
2. Implement `keyword_example` and `build_vocabulary` — *why*: request-time resolution keeps examples truthful.
3. Write the tests and run `pytest tests/unit/test_vocabulary.py -q` — *why*: AC17.

### `querysource/utils/vocabulary.py` (CREATE)
```python
"""Curated relative-date keyword vocabulary (FEAT-148).

Documents keywords accepted as condition *values* (resolved by ``to_udf``), PostgreSQL
constants, and an informational set of date helpers. Served at GET /api/v1/queries/vocabulary.
"""
from __future__ import annotations

import logging
from typing import Any, Literal, Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)

VOCABULARY_VERSION: str = "1.0"


class KeywordEntry(BaseModel):
    """A relative-date keyword accepted as a condition value."""

    name: str
    category: Literal["date"] = "date"
    returns: str
    description: Optional[str] = None


class FunctionArg(BaseModel):
    """One documented argument of an informational helper."""

    name: str
    default: Any = None


class FunctionEntry(BaseModel):
    """Informational date helper (not invocable from HTTP conditions in v1)."""

    name: str
    args: list[FunctionArg]
    description: str
    invocable: bool = False


KEYWORD_REGISTRY: dict[str, KeywordEntry] = {
    # FILL IN: 7 entries exactly as listed in Implementation Notes
}

FUNCTION_REGISTRY: tuple[FunctionEntry, ...] = (
    # FILL IN: date_diff, date_sum, days_ago, previous_month, fdow, ldow — args/defaults from functions.pyx signatures
)


def keyword_example(name: str) -> Any:
    """Resolve ``name`` via to_udf now; int stays int, others str; None on any error."""
    from .functions import to_udf  # compiled module; lazy to keep import side effects local
    # FILL IN: try/except per Implementation Notes
    return None


def build_vocabulary(udf_list: list[str], pg_constants: list[str], pg_udfs: list[str]) -> dict:
    """Build the vocabulary response for the effective keyword/constant lists.

    ``keywords`` follows ``udf_list`` order exactly; names missing from the registry
    are listed with ``description: None``.
    """
    # FILL IN: assemble dict per Implementation Notes — bounded by AC17
    return {}
```
**Why**: the names are fixed by spec §3 Module 6. The handler (TASK-741) calls `build_vocabulary(udf_keywords(), pg_constants(), pg_udfs())`, so the effective environment override flows through.

### `tests/unit/test_vocabulary.py` (CREATE)
```python
"""FEAT-148 TASK-739 — date keyword vocabulary."""
from querysource.types.validators import pg_constants, pg_udfs, udf_keywords
from querysource.utils.functions import fdom
from querysource.utils.vocabulary import FUNCTION_REGISTRY, build_vocabulary, keyword_example


def test_build_vocabulary_matches_udf_list():
    # FILL IN: names == udf_keywords() order; constants/pg_functions copies; version & case_insensitive
    ...


def test_unknown_keyword_has_null_description():
    # FILL IN: build_vocabulary(['TODAY', 'NEXT_WEEK'], [], []) → NEXT_WEEK description None
    ...


def test_functions_informational_only():
    # FILL IN: names exactly the 6; all invocable False; 'previous_year' absent
    ...


def test_keyword_example_resolves():
    # FILL IN: keyword_example('FDOM') == fdom(); keyword_example('NOPE') is None
    ...
```

### FILL IN checklist
- [ ] `KEYWORD_REGISTRY` (7 entries) and `FUNCTION_REGISTRY` (6 entries).
- [ ] `keyword_example` error handling.
- [ ] `build_vocabulary` assembly: bounded by AC17.
- [ ] Test bodies.

---

## Acceptance Criteria

- [ ] `pytest tests/unit/test_vocabulary.py -q` passes.
- [ ] Spec AC17: `keywords` equals the effective `UDF_LIST` order with request-time examples; unknown keywords get `description: null`; `functions` holds the 6 helpers with `invocable: false`; `previous_year` is absent.
- [ ] `ruff check querysource/utils/vocabulary.py tests/unit/test_vocabulary.py` is clean.

---

## Test Specification

See the blueprint test file above.

---

## Agent Instructions

1. **Read the spec** (§3 Module 6, AC17, §7 keyword-format risks).
2. **Check dependencies**: TASK-734 completed (accessors exist; the Cython extension is rebuilt).
3. **Verify the Codebase Contract**: re-check the `functions.pyx` signatures.
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
