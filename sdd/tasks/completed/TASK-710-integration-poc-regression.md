# TASK-710: Integration tests — PoC replay, golden-slug regression, datasource e2e

**Feature**: FEAT-103 — Malforming Query-Slug Issue (SQLi & Datasource Credential Exposure)
**Spec**: `sdd/specs/malforming-queryslug-issue.spec.md`
**Status**: done
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-707, TASK-708, TASK-709
**Assigned-to**: unassigned

---

## Context

Final verification that both vulnerabilities are closed end-to-end and no legitimate
behavior regressed. Implements spec §4 Integration Tests and the §5 acceptance
criteria that span multiple modules.

---

## Scope

- **SQLi replay**: drive a raw slug (mirroring `troc_client_tenant`) through the query
  endpoint with the PoC payloads; assert HTTP 400 and that the response body contains
  no schema/data/DB-error/SQL fragment.
- **Golden-slug regression**: run representative legitimate slugs (raw + non-raw) with
  valid conditions; assert results/rendered SQL are unchanged vs a pre-fix baseline.
- **Cython fallback parity**: run the SQLi replay with `HAS_RUST` forced False; assert
  the same rejection.
- **Datasource e2e**: `GET /api/v1/datasource` and `/{source}` return no plaintext
  secrets; the `datasource:read` gate denies fail-closed.
- Add the shared `sqli_payloads` and `raw_slug_template` fixtures (spec §4).

**NOT in scope**: implementing the fixes (TASK-704..709). This task only verifies.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `tests/integration/test_slug_injection.py` | CREATE | PoC replay (Rust + forced-fallback) |
| `tests/integration/test_slug_regression.py` | CREATE | Golden legitimate slugs |
| `tests/integration/test_datasource_endpoint.py` | CREATE | Redaction + fail-closed PBAC e2e |
| `tests/conftest.py` | MODIFY (if needed) | Shared fixtures (`sqli_payloads`, app client) |

---

## Codebase Contract (Anti-Hallucination)

> Verified 2026-06-19 on `dev`.

### Verified Imports / Routes
```python
from querysource.exceptions import ParserError, QueryError
# Routes (querysource/services.py):
#   POST/GET /api/v2/services/queries/{slug}   → qs.query           (services.py:148-150)
#   add_view  /api/v1/datasource               → DatasourceView     (services.py:282)
#   add_view  /api/v1/datasource/{source}      → DatasourceView     (services.py:284)
from querysource.qs_parsers import _qs_parsers as _rs   # HAS_RUST flag for forced-fallback test
```

### Existing Signatures to Use
```python
# querysource/queries/qs.py
class QS(BaseQuery):
    async def query(self, output_format=None): ...   # line 363 (raises QueryError/Error on failure)
    async def dry_run(self): ...                      # line 529 (returns [sql, error] without executing)
```

### Does NOT Exist
- ~~a prod `troc_client_tenant` fixture in the repo~~ — slugs are DB-stored; build a
  representative raw-slug fixture/definition in the test (do not depend on prod data).
- ~~`tests/integration/` guaranteed to exist~~ — create the directory if missing.

---

## Implementation Notes

### Key Constraints
- Use `pytest`/`pytest-asyncio` and an aiohttp test client/app fixture (follow
  existing patterns in `tests/`). `source .venv/bin/activate` first.
- For the forced-fallback test, monkeypatch `HAS_RUST` (or the relevant parser flag)
  to exercise the Cython path from TASK-708.
- Assert on *absence* of leaked tokens (`version()`, `pg_`, `information_schema`,
  password-hash shapes) in bodies, not just status codes.

### References in Codebase
- `tests/` — existing async handler test patterns and fixtures.
- `querysource/queries/qs.py:529` — `dry_run()` is useful to assert rendered SQL
  without hitting a live DB.

---

## Acceptance Criteria

- [ ] PoC payloads (UNION/version()/pg_database/pg_user/information_schema) → 400, no
      leak in body — on both Rust and forced-fallback paths.
- [ ] Legitimate raw + non-raw slugs return unchanged results (golden).
- [ ] Datasource list + single responses carry no plaintext secrets; gate fails closed.
- [ ] `pytest tests/integration -v` passes.

---

## Test Specification

```python
# tests/integration/test_slug_injection.py
import pytest

@pytest.fixture
def sqli_payloads():
    return [
        '" IS NULL UNION SELECT version(),null,null,null,null,null--',
        "' UNION SELECT string_agg(datname,',') FROM pg_database--",
        "' UNION SELECT usename||':'||passwd FROM pg_shadow--",
    ]

async def test_injection_blocked(client, sqli_payloads):
    for p in sqli_payloads:
        resp = await client.post("/api/v2/services/queries/<raw_slug>",
                                 params={"client_slug": p}, json={})
        assert resp.status == 400
        body = await resp.text()
        assert "version()" not in body and "pg_" not in body
```

---

## Agent Instructions

1. Confirm TASK-707, TASK-708, TASK-709 are in `completed/`.
2. Update index → `in-progress`; `source .venv/bin/activate`.
3. Implement the three integration test modules + fixtures.
4. Run `pytest tests/integration -v`.
5. Move to `completed/`, update index, fill Completion Note.

---

## Completion Note

**Completed by**: <id>
**Date**: YYYY-MM-DD
**Notes**:
**Deviations from spec**: none
