# TASK-709: Datasource credential redaction + fail-closed PBAC gate (V2)

**Feature**: FEAT-103 — Malforming Query-Slug Issue (SQLi & Datasource Credential Exposure)
**Spec**: `sdd/specs/malforming-queryslug-issue.spec.md`
**Status**: done
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: none
**Assigned-to**: unassigned
**Parallel**: true — pure-Python, disjoint files from the V1 Rust/Cython tasks; runs in its own worktree and can land first.

---

## Context

V2: `GET /api/v1/datasource(s)` returns full datasource records (incl. `credentials`
and `dsn`) unredacted on the list path (`datasource.py:187` requests the fields,
`:218` returns them); only the single-source path masks `credentials['password']`
(`:242`). The list PBAC filter (`_pbac_filter`) **fails open**. This task redacts all
secrets on every datasource GET and gates reads behind a **fail-closed**
`datasource:read` PBAC check. Implements spec §3 Module 6.

---

## Scope

- Add `_redact_datasource(record) -> dict` (module-level or method) that, on a copy:
  - replaces every secret-bearing key in `credentials` (`password`, `pwd`, `secret`,
    `token`, `api_key`, `apikey`, `key`) with `'(hidden)'`;
  - masks secrets embedded in `dsn` (e.g. `://user:****@host`) or drops `dsn`.
- Apply `_redact_datasource` to **both** GET branches: the list result before
  `json_response` (`:218`) and the single-source result (`:235-249`, replacing the
  password-only masking at `:242`).
- Replace the fail-open `_pbac_filter` usage on the read path with a **fail-closed**
  `datasource:read` check modeled on `AbstractHandler._enforce_pbac`
  (`handlers/abstract.py:259`): guardian absent → no-op; guardian present but no
  session/evaluator → deny (404/403). Since `DatasourceView` extends
  `navigator.views.BaseView` (not `AbstractHandler`), extract the check into a shared
  helper or replicate it locally — do NOT assume `self._enforce_pbac` exists here.
- Tests: list/single responses contain no plaintext secrets; fail-closed behavior on
  guardian error / missing session.

**NOT in scope**: PUT/POST/DELETE bodies, a separate "reveal secrets" endpoint (see
spec §8 open question), and all V1 work.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/datasources/handlers/datasource.py` | MODIFY | `_redact_datasource` + apply to GET; fail-closed `datasource:read` gate |
| `tests/unit/test_datasource_redaction.py` | CREATE | Redaction + fail-closed PBAC tests |

---

## Codebase Contract (Anti-Hallucination)

> Verified 2026-06-19 on `dev`.

### Verified Imports
```python
from querysource.auth import ResourceType                 # querysource/auth/_resource_types.py
from ...utils.functions import anonymize                  # datasource.py:16 (already imported, verified by use)
from navigator.views import BaseView                       # datasource.py:14
```

### Existing Signatures to Use
```python
# querysource/datasources/handlers/datasource.py
class DatasourceView(BaseView):                            # line 43
    async def _pbac_filter(self, request, items, name_key, resource_type, action):  # line 47
        #   FAIL-OPEN: guardian None → return items (71); guardian error → return items (86-98)
    async def get(self) -> web.Response:                   # line 148
        fields = ["uid","driver","name","description","params","credentials","dsn","program_slug"]  # 187
        # list path returns unredacted result via json_response                     # 218
        result.credentials['password'] = '(hidden)'        # 242 (single — only password)

# querysource/handlers/abstract.py — fail-closed pattern to MODEL the gate on (NOT inherited here)
class AbstractHandler(...):
    async def _enforce_pbac(self, request, resource_type, resource_name: str, action: str) -> None:  # 259
        #   guardian None → no-op (284); no session → HTTPNotFound (300-308);
        #   evaluator missing → HTTPNotFound (311-316); deny → HTTPNotFound (352+)

# querysource/datasources/models.py
class DataSource(Model):                                   # line 20
    params: dict        # 34
    credentials: dict   # 35
    dsn: str            # 36

# querysource/auth/_resource_types.py
DATASOURCE = _StringResourceType("datasource")             # line 43
```

### Does NOT Exist
- ~~`DatasourceView._enforce_pbac`~~ — `_enforce_pbac` is on `AbstractHandler`, NOT on
  `BaseView`/`DatasourceView`. Extract a shared helper or replicate the fail-closed
  logic; do not call `self._enforce_pbac` directly.
- ~~`ResourceType.DATASOURCE_READ` / a `datasource:read` constant~~ — actions are free
  strings; pass `action="datasource:read"`. Existing members: `DATASOURCE`, `DRIVER`,
  `RAW_QUERY`.
- ~~a built-in DSN-masking helper~~ — implement masking in `_redact_datasource`
  (or reuse `anonymize` for individual secret values).

---

## Implementation Notes

### Key Constraints
- Redact on a COPY — never mutate the ORM/Model instance in place in a way that could
  persist. Handle both dict records (`default_sources()`) and `DataSource` Model
  instances (use the `_item_get` accessor pattern already in this file, lines 30-40).
- Fail-closed gate: when `app['security']` is set but no session/evaluator, DENY.
  When `app['security']` is absent (PBAC disabled), allow (no-op) — same as
  `_enforce_pbac`.
- Generic responses; do not leak which datasources exist to unauthorized callers
  beyond current behavior.

### References in Codebase
- `querysource/datasources/handlers/datasource.py:30-40` — `_item_get` dict/Model accessor.
- `querysource/handlers/multi.py:61-74` — guardian `filter_resources` usage example.

---

## Acceptance Criteria

- [ ] `GET /api/v1/datasource` (list) and `/api/v1/datasource/{source}` (single)
      return no plaintext `credentials` secret values and no secret-bearing `dsn`.
- [ ] The `datasource:read` gate denies when PBAC is enabled but session/evaluator is
      missing (fail-closed) — not unfiltered.
- [ ] With PBAC disabled (`app['security']` absent), behavior is unchanged (no-op).
- [ ] `ruff check querysource/datasources/handlers/datasource.py` clean.
- [ ] `pytest tests/unit/test_datasource_redaction.py -v` passes.

---

## Test Specification

```python
# tests/unit/test_datasource_redaction.py
import pytest
from querysource.datasources.handlers.datasource import _redact_datasource  # or method

def test_redacts_credentials_and_dsn():
    rec = {"name":"db","credentials":{"username":"u","password":"p"},
           "dsn":"postgres://u:p@h:5432/db"}
    out = _redact_datasource(rec)
    assert out["credentials"]["password"] == "(hidden)"
    assert "p@" not in out.get("dsn","")

async def test_pbac_fail_closed(...):
    # guardian present, no session → deny (HTTPNotFound/403), result not returned
    ...
```

---

## Agent Instructions

1. Update index → `in-progress`; `source .venv/bin/activate`.
2. Implement redaction + fail-closed gate; cover list + single paths.
3. `pytest` + `ruff`.
4. Move to `completed/`, update index, fill Completion Note.

---

## Completion Note

**Completed by**: <id>
**Date**: YYYY-MM-DD
**Notes**:
**Deviations from spec**: none
