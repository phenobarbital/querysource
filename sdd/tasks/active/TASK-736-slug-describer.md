# TASK-736: Pure slug describer (variables, capabilities, redaction)

**Feature**: FEAT-148 — Describe Query-Slug REST Endpoints
**Spec**: `sdd/specs/describe-queryslug.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

This task implements spec §3 **Module 2**. It is a pure transformation `QueryModel` (or dict) + `DescribeGrants` → the detail payload of `GET /api/v1/queries/{slug}/describe`.
- **Output:** the typed variable contract, structural placeholders, effective `cond_definition`, capabilities, links and redaction.
- **No I/O:** no request, database or provider access.
- **Consumers:** the handler (TASK-740) calls it. TASK-741 reuses `extract_placeholders` and `RESERVED_PLACEHOLDERS` to detect unresolved placeholders before `prepare`.

---

## Scope

- Create `querysource/queries/describe.py` with the constants, the `DescribeVariable` and `DescribeGrants` models, and the functions `extract_placeholders`, `is_json_dialect`, `normalize_type`, `build_variables`, `redact_payload` and `describe_slug`.
- Write `tests/unit/test_slug_describer.py`.

**NOT in scope**:
- Computing grants (TASK-737).
- HTTP and JSON encoding (TASK-740).
- Columns (TASK-738/741).
- The vocabulary (TASK-739).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/queries/describe.py` | CREATE | pure describer |
| `tests/unit/test_slug_describer.py` | CREATE | unit tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.models import QueryModel     # verified: querysource/models.py:48 (use only under TYPE_CHECKING or for columns())
from pydantic import BaseModel, ConfigDict     # pydantic v2 in use: querysource/handlers/_pagination.py:29
import json, re, string                        # stdlib
```

### Existing Signatures to Use
```python
# querysource/models.py:48-107 — QueryModel fields (in column order, verified via QueryModel.columns(QueryModel) 2026-09-15):
# query_slug, description, source, params, attributes, conditions, cond_definition, fields, filtering,
# ordering, grouping, qry_options, h_filtering, query_raw, is_raw, is_cached, provider, parser,
# cache_timeout, cache_refresh, cache_options, program_id, program_slug, dwh, dwh_driver, dwh_info,
# dwh_scheduler, created_at, created_by, updated_at, updated_by
QueryModel.columns(QueryModel) -> dict   # keys = column names (used by _pagination.py:47)
# NOTE: QueryModel.to_dict() DROPS None values (Meta.remove_nulls=True) — do NOT use it to build the payload.

# querysource/providers/sql.py:37-45 — sqlProvider.replacement implicit defaults (mirror, do not import):
#   "firstdate": "current_date", "lastdate": "current_date", "filterdate": "current_date"
# querysource/providers/sql.py:48-62 — _PARSER_PLACEHOLDERS (where_cond, and_cond, filter, fields, group_by,
#   grouping, order_by, ordering, querylimit, _limit, _offset, schema, table)
# querysource/parsers/sql.pyx:97-98 — '{schema}.{table}', 'SELECT {fields} FROM {tablename} {filter} {grouping} {offset} {limit}'
# rust/src/safe_dict.rs:116-120 — substitutable key grammar: non-empty, no '{', chars [A-Za-z0-9_.]
# querysource/parsers/abstract.pyx:307-327 — _col_definition_sync: conditions-level cond_definition, then definition-level overrides
# querysource/providers/abstract.py:83-85 — 'refresh' popped from conditions (refresh_param)
```

### Does NOT Exist
- ~~A placeholder or variable extractor in Cython/Rust~~: none exists. Use stdlib `string.Formatter().parse`.
- ~~`QS.get_definition()`~~: irrelevant here. This module never touches `QS`.
- ~~`querysource.queries.describe`~~: created by this task.
- ~~`{today}` / `{fdom}` placeholder expressions~~: keywords are values, never placeholder names.

---

## Implementation Notes

### Key Constraints (spec §3 Module 2 — binding)
- **Import cost.** `querysource/queries/__init__.py` imports `QS` and `MultiQS`, so importing this module is not cheap. Keep module-level imports to stdlib, pydantic and `typing`. Import `QueryModel` only under `TYPE_CHECKING`, and get the column list lazily inside `describe_slug` via `from ..models import QueryModel`.
- **Input shape.** Accept `definition` as a `QueryModel` or a `dict`, reading fields with `getattr(definition, name, None)` or `dict.get`. Build the base payload from **all** column names, with values possibly `None`.
- **JSON dialect.** `is_json_dialect` is true when the stripped `query_raw` starts with `{` or `[` **and** `json.loads` succeeds. In that case `variables=None`, `variables_supported=False` and `structural_placeholders=[]`.
- **`extract_placeholders`:**
  - Iterate `string.Formatter().parse(query_raw)` and take `field_name`.
  - Skip `None` and names not matching `^[A-Za-z0-9_.]+$`.
  - Deduplicate, keeping first-seen order.
  - On `ValueError`, return `(None, str(err))`. Empty or `None` input returns `([], None)`.
- **Constants:**
  - `RESERVED_PLACEHOLDERS = frozenset({"schema","table","tablename","fields","filter","where_cond","and_cond","grouping","group_by","ordering","order_by","offset","_offset","limit","_limit","querylimit"})`
  - `CANONICAL_TYPES = frozenset({"literal","integer","float","numeric","decimal","epoch","boolean","string","field","date","datetime","timestamp","uuid","array","json","numrange","int4range","int8range"})`
  - `CANONICAL_ALIASES = {"int": "integer", "varchar": "string"}`
  - `IMPLICIT_DEFAULTS = {"firstdate": "current_date", "lastdate": "current_date", "filterdate": "current_date"}`
  - `RAW_FIELDS = ("query_raw",)`
  - `ADMIN_FIELDS = ("dwh_info", "dwh_scheduler", "cache_options", "created_by", "updated_by")`
  - `KEYWORD_TYPES = frozenset({"date","datetime","timestamp"})`
- **Variables:**
  - Non-reserved placeholders come first, in order; `cond_definition` keys not already listed follow, in key order.
  - A reserved name also in `cond_definition` stays structural and adds a warning: `"reserved placeholder '<n>' declared in cond_definition"`.
- **Default / required / source:**
  - `default = conditions[name]` if `name in conditions`, else `IMPLICIT_DEFAULTS.get(name)`.
  - `required = name not in conditions and name not in IMPLICIT_DEFAULTS`.
  - `source` is `"cond_definition"` if in `cond_definition`, else `"conditions"` if in `conditions`, else `"placeholder"`.
- **Types:**
  - `raw_type = cond_definition.get(name)`, verbatim.
  - `type = normalize_type(raw_type)`. An unknown non-null `raw_type` gives `type=None` and the warning `"unknown type '<raw>' for '<name>'"`.
  - `accepts_keywords = type in KEYWORD_TYPES or raw_type is None`.
- **`effective_cond_definition`** = `{**(conditions.get("cond_definition") or {}), **(cond_definition or {})}`. When computing variables, exclude the nested `"cond_definition"` key of `conditions` itself.
- **`capabilities`** = `fields` (`[]` if None), `filtering` (`{}`), `ordering` (`[]`), `grouping` (`[]`), `h_filtering` (`False`), `qry_options` (`{}`), `refresh_param` (`"refresh"`).
- **`redact_payload`** removes `RAW_FIELDS` unless `grants.raw` and `ADMIN_FIELDS` unless `grants.admin`. It records a key as redacted even when its value was `None`, and returns a **new** dict plus the sorted names.
- **`describe_slug` output:** `{**redacted_base, "derived": {...}, "redacted": [...]}`.
  - `derived` keys: `variables` (list of `DescribeVariable.model_dump()` or `None`), `variables_supported`, `structural_placeholders`, `effective_cond_definition`, `capabilities`, `links` (`{"columns": columns_link, "vocabulary": vocabulary_link}`), `warnings`, and `variables_error` only when present.
  - Derived metadata is computed from the **unredacted** `query_raw` (variables are visible even without the raw grant), but the payload never contains `query_raw` without the grant.

---

## Implementation Blueprint

### Steps (in order)
1. Create the module with constants and models — *why*: later functions and tests import them by name.
2. Implement `extract_placeholders`, `is_json_dialect` and `normalize_type` — *why*: they are the leaves of `build_variables`.
3. Implement `build_variables`, then `redact_payload`, then `describe_slug` — *why*: this is the dependency order.
4. Write the tests and run `pytest tests/unit/test_slug_describer.py -q` — *why*: AC11–AC13.

### `querysource/queries/describe.py` (CREATE)
```python
"""Pure describer for stored query slugs (FEAT-148).

Transforms a stored definition into the ``GET /api/v1/queries/{slug}/describe`` payload.
No I/O: no request, database, provider or parser access.
"""
from __future__ import annotations

import json
import re
import string
from typing import TYPE_CHECKING, Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:  # pragma: no cover
    from ..models import QueryModel

RESERVED_PLACEHOLDERS: frozenset[str] = frozenset({
    "schema", "table", "tablename", "fields", "filter", "where_cond", "and_cond",
    "grouping", "group_by", "ordering", "order_by", "offset", "_offset",
    "limit", "_limit", "querylimit",
})
CANONICAL_TYPES: frozenset[str] = frozenset({
    "literal", "integer", "float", "numeric", "decimal", "epoch", "boolean", "string",
    "field", "date", "datetime", "timestamp", "uuid", "array", "json",
    "numrange", "int4range", "int8range",
})
CANONICAL_ALIASES: dict[str, str] = {"int": "integer", "varchar": "string"}
KEYWORD_TYPES: frozenset[str] = frozenset({"date", "datetime", "timestamp"})
# Mirrors querysource/providers/sql.py:37-45 (sqlProvider.replacement).
IMPLICIT_DEFAULTS: dict[str, str] = {
    "firstdate": "current_date", "lastdate": "current_date", "filterdate": "current_date",
}
RAW_FIELDS: tuple[str, ...] = ("query_raw",)
ADMIN_FIELDS: tuple[str, ...] = ("dwh_info", "dwh_scheduler", "cache_options", "created_by", "updated_by")
_KEY_GRAMMAR = re.compile(r"^[A-Za-z0-9_.]+$")  # rust/src/safe_dict.rs:116-120

VariableSource = Literal["cond_definition", "conditions", "placeholder"]


class DescribeVariable(BaseModel):
    """One user-facing input of a slug."""

    model_config = ConfigDict(frozen=True)
    name: str
    type: Optional[str] = None
    raw_type: Optional[str] = None
    default: Any = None
    required: bool
    source: VariableSource
    accepts_keywords: bool


class DescribeGrants(BaseModel):
    """What the caller may see beyond the base payload."""

    model_config = ConfigDict(frozen=True)
    raw: bool = False
    admin: bool = False


def extract_placeholders(query_raw: Optional[str]) -> tuple[Optional[list[str]], Optional[str]]:
    """Return (ordered unique placeholder names, error); ([], None) for empty input."""
    # FILL IN: Formatter().parse + grammar filter + dedupe; ValueError → (None, msg)
    raise NotImplementedError


def is_json_dialect(query_raw: Optional[str]) -> bool:
    """True when query_raw is a JSON object/array (Mongo/Elastic/Arango style)."""
    # FILL IN: startswith {/[ and json.loads succeeds
    raise NotImplementedError


def normalize_type(raw_type: Optional[str]) -> Optional[str]:
    """Canonical lowercase type or None (aliases int→integer, varchar→string)."""
    # FILL IN
    raise NotImplementedError
```
**Why this shape**: the names and constants are fixed by spec §3 Module 2 and consumed by TASK-740/741. The `TYPE_CHECKING` import avoids a runtime dependency on models for pure use.

### `querysource/queries/describe.py` (CREATE — continued, append)
```python
def build_variables(
    query_raw: Optional[str], conditions: Optional[dict], cond_definition: Optional[dict]
) -> dict:
    """Return {'variables', 'variables_supported', 'structural_placeholders', 'warnings'[, 'variables_error']}."""
    conditions = dict(conditions or {})
    conditions.pop("cond_definition", None)
    cond_definition = dict(cond_definition or {})
    warnings: list[str] = []
    if is_json_dialect(query_raw):
        return {"variables": None, "variables_supported": False,
                "structural_placeholders": [], "warnings": warnings}
    names, error = extract_placeholders(query_raw)
    if error is not None:
        return {"variables": None, "variables_supported": True, "structural_placeholders": [],
                "warnings": warnings, "variables_error": error}
    # FILL IN: split names into structural (RESERVED_PLACEHOLDERS) vs variables; append cond_definition-only keys;
    #          reserved-in-cond_definition warning; build DescribeVariable per Implementation Notes — bounded by AC13
    raise NotImplementedError


def redact_payload(payload: dict, grants: DescribeGrants) -> tuple[dict, list[str]]:
    """Return (new payload without restricted fields, sorted redacted names)."""
    # FILL IN: RAW_FIELDS unless grants.raw; ADMIN_FIELDS unless grants.admin — bounded by AC12
    raise NotImplementedError


def describe_slug(
    definition: Union["QueryModel", dict],
    grants: DescribeGrants,
    *,
    columns_link: str,
    vocabulary_link: str,
) -> dict:
    """Full detail payload: model fields (redacted) + 'derived' + 'redacted'."""
    from ..models import QueryModel  # lazy: keeps module import cheap for pure use

    def _get(name: str) -> Any:
        if isinstance(definition, dict):
            return definition.get(name)
        return getattr(definition, name, None)

    base = {name: _get(name) for name in QueryModel.columns(QueryModel)}
    # FILL IN: derived = build_variables(...) + effective_cond_definition + capabilities + links;
    #          payload, redacted = redact_payload(base, grants); return {**payload, "derived": ..., "redacted": ...}
    #          — bounded by AC11/AC12
    raise NotImplementedError
```
**Why**: the base payload must include every column, `None`s included, so redaction can report fields that are absent. `to_dict()` drops nulls, which is why it is not used.

### `tests/unit/test_slug_describer.py` (CREATE)
```python
"""FEAT-148 TASK-736 — pure slug describer."""
import pytest

from querysource.queries.describe import (
    ADMIN_FIELDS, DescribeGrants, build_variables, describe_slug,
    extract_placeholders, is_json_dialect, normalize_type,
)


@pytest.fixture
def sample_definition():
    return {
        "query_slug": "epson_field_activity", "provider": "pg", "parser": "SQLParser",
        "program_slug": "epson",
        "query_raw": "SELECT {fields} FROM t WHERE visit_date BETWEEN {firstdate} AND {lastdate} "
                     "AND store_id = {store_id} AND region = {region} {and_cond}",
        "conditions": {"store_id": 10},
        "cond_definition": {"firstdate": "date", "lastdate": "DATE", "store_id": "integer", "extra": "STRING"},
        "dwh_info": {"x": 1}, "cache_options": {"y": 2}, "created_by": 1, "updated_by": 2,
    }


def test_extract_placeholders_order_and_grammar(): ...        # FILL IN: dedupe/order; '{{x}}' escape ignored; '{a b}' dropped
def test_extract_placeholders_malformed(): ...                # FILL IN: 'SELECT {' → (None, msg)
def test_json_dialect_unsupported(): ...                      # FILL IN: '{"$match": {"a": 1}}' → supported False
def test_structural_placeholders_reported(sample_definition): ...  # FILL IN: fields/and_cond structural, not variables
def test_reserved_in_cond_definition_warns(): ...             # FILL IN
def test_variable_types_and_defaults(sample_definition): ...  # FILL IN: DATE→date, STRING→string, int→integer, unknown→None+warning;
                                                              #          firstdate default current_date required False
def test_variable_required_and_source(sample_definition): ... # FILL IN: region required, source placeholder; extra appended
def test_effective_cond_definition_merge_order(): ...         # FILL IN
@pytest.mark.parametrize("raw,admin", [(False, False), (True, False), (False, True), (True, True)])
def test_redaction_matrix(sample_definition, raw, admin): ... # FILL IN: exact redacted lists; fields omitted not nulled
def test_describe_links_and_capabilities(sample_definition): ...  # FILL IN: refresh_param, links, None normalisation
```

### FILL IN checklist
- [ ] `extract_placeholders`, `is_json_dialect`, `normalize_type`: bounded by Implementation Notes.
- [ ] `build_variables` classification: bounded by AC13.
- [ ] `redact_payload`: bounded by AC12.
- [ ] `describe_slug` assembly: bounded by AC11.
- [ ] All test bodies.

---

## Acceptance Criteria

- [ ] `pytest tests/unit/test_slug_describer.py -q` passes.
- [ ] Spec AC11: `derived` contains `variables` (with `name`, `type`, `raw_type`, `default`, `required`, `source`, `accepts_keywords`), `variables_supported`, `structural_placeholders`, `effective_cond_definition`, `capabilities` (incl. `refresh_param`), `links` and `warnings`.
- [ ] Spec AC12: restricted fields are omitted (never `null`) and listed in `redacted`.
- [ ] Spec AC13: structural placeholders never appear in `variables`; implicit date defaults work; JSON dialect → `variables: null`; malformed braces → `variables_error`.
- [ ] The module does no I/O and has no module-level import of providers, `QS` or the database.
- [ ] `ruff check querysource/queries/describe.py tests/unit/test_slug_describer.py` is clean.

---

## Test Specification

See the blueprint test file above.

---

## Agent Instructions

1. **Read the spec** (§3 Module 2, §5 AC11–AC13).
2. **Check dependencies**: none. This task is parallel-safe with TASK-734/735.
3. **Verify the Codebase Contract**: re-check `QueryModel.columns(QueryModel)` keys.
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
