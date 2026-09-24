# TASK-763: DDL fixture, documentation and opt-in integration test

**Feature**: FEAT-151 — Unified single/multi dispatch on `/api/v1/{tenant}/queries/{slug}`
**Spec**: `sdd/specs/multiquery-multitenant.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-759, TASK-762
**Assigned-to**: unassigned

---

## Context

Spec §3 Modules 2 (DDL half) and 5, design-research S9, AC-7, AC-12, AC-13. The test
DDL fixture and the documented DDL gain the `columns_definition` column; the legacy
`public.queries` migration is documented; the docs describe kind-aware dispatch and
the multi dry-run envelope; one opt-in integration test exercises a real stored
multi definition end to end.

**Exclusive**: this task edits `tests/tenants/conftest.py`, which every tenant test
module loads, so it never runs alongside another task.

---

## Scope

- Add `columns_definition text[] DEFAULT '{}'::text[]` to `_TENANT_TABLE_DDL` in `tests/tenants/conftest.py`.
- Update `docs/PER_TENANT_QUERIES.md`: DDL block, a legacy migration note with the `ALTER TABLE`, kind-aware dispatch on the tenant routes, the `columns_definition` HEAD/PATCH semantics, the multi dry-run envelope, and the documented `provider='db'` + multi JSON limitation.
- Add `test_tenant_stored_multi_definition_executes` to `tests/tenants/test_integration.py` (gated by `tenant_services`).

**NOT in scope**: any code under `querysource/`.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `tests/tenants/conftest.py` | MODIFY | DDL gains `columns_definition` |
| `docs/PER_TENANT_QUERIES.md` | MODIFY | DDL, migration, dispatch, columns, dry-run |
| `tests/tenants/test_integration.py` | MODIFY | new opt-in stored-multi test |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
# tests/tenants/test_integration.py already has helpers:
#   _asyncdb() (line 27), _make_connection_factory(postgres_dsn) (line 32), _make_repo(tenant_services, stores) (line 56)
#   fixture `tenant_services` (tests/tenants/conftest.py) -> keys postgres_dsn, tenant_schemas, override_schema
from querysource.handlers.tenant import TenantQueryHandler     # verified: querysource/handlers/__init__.py:13
from querysource.tenants import QueryIdentity, QueryStore      # verified: querysource/tenants.py:35,46
```

### Existing Signatures to Use
```python
# tests/tenants/conftest.py:21-32
_TENANT_TABLE_DDL = (
    'CREATE TABLE "{schema}".queries ('
    ...
    "description varchar, "              # line 29
    "updated_at timestamptz DEFAULT now()"  # line 30
    ")"
)
# docs/PER_TENANT_QUERIES.md: "### Provisional DDL gate" (line 35), DDL block lines 40-50,
#   "    updated_at TIMESTAMPTZ DEFAULT now()" (line 49); "### HTTP API routes" (line 92);
#   "### DDL/program_id gate" (line 202)
# tests/tenants/test_tenant_rollout_documentation.py asserts handler method names and
#   registry functions exist — keep it green.
```

### Does NOT Exist
- ~~A migration runner~~ — the DDL is documentation plus this test fixture only; no code executes migrations.
- ~~`/api/v3` tenant support~~ — do not document it (non-goal).

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "tests/tenants/conftest.py", "action": "MODIFY"},
    {"path": "docs/PER_TENANT_QUERIES.md", "action": "MODIFY"},
    {"path": "tests/tenants/test_integration.py", "action": "MODIFY"}
  ],
  "contract_symbols": [
    "sym:querysource/handlers/tenant.py#TenantQueryHandler"
  ]
}
```

---

## Implementation Blueprint

### Steps (in order)
1. Extend the fixture DDL — *why*: integration stores must carry the column (AC-7).
2. Update the docs — *why*: AC-13 and the deployment-order gotcha.
3. Add the integration test — *why*: S9, AC-12.

### `tests/tenants/conftest.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c '    "description varchar, "' tests/tenants/conftest.py)
# AFTER — insert below `    "description varchar, "` (verified: tests/tenants/conftest.py:29)
    "columns_definition text[] DEFAULT '{}'::text[], "
```

### `docs/PER_TENANT_QUERIES.md` (MODIFY)
```markdown
<!-- occurrences: 1 (verified: grep -c '    description VARCHAR,' docs/PER_TENANT_QUERIES.md) -->
<!-- AFTER `    description VARCHAR,` (line 47) insert: -->
    columns_definition TEXT[] DEFAULT '{}'::text[],
```
```markdown
<!-- After the DDL block, new subsection: -->
### Legacy store migration (FEAT-151)

Deploy the code first, then run on the legacy store:

    ALTER TABLE public.queries ADD COLUMN IF NOT EXISTS columns_definition TEXT[] DEFAULT '{}'::text[];

Reads on a store without the column return an empty list. Writes omit an empty
`columns_definition`, so un-migrated stores keep accepting writes; a non-empty value
requires the column.
```
FILL IN under "### HTTP API routes": describe kind-aware dispatch on
`{tenant}/queries/{slug}` (multi iff `provider='multi'`; authorization before load;
single slugs keep v2 semantics), HEAD/PATCH for multi (`columns_definition`, 204 when
empty), the multi dry-run envelope from spec §2, and the limitation that a multi JSON
payload saved under `provider='db'` executes as a single query. Bounded by AC-13 and
spec §2; copy the envelope JSON verbatim from the spec.

### `tests/tenants/test_integration.py` (MODIFY)
```python
# Append at end of file.
@pytest.mark.asyncio
async def test_tenant_stored_multi_definition_executes(tenant_services) -> None:
    """FEAT-151: a stored provider='multi' definition runs through the tenant route."""
    # FILL IN: using _make_repo(tenant_services, stores), create in tenant_schemas[0]:
    #   an inheriting single child, an explicit-null (legacy) child reference, and a
    #   provider='multi' parent with columns_definition=["a", "b"]. Drive
    #   TenantQueryHandler via an aiohttp test app or mocked request (pattern:
    #   tests/tenants/test_tenant_http_routes.py). Assert: GET parent -> 200 frame and
    #   the executor's _definition_revision equals the repository revision; HEAD ->
    #   204 with X-Columns "['a', 'b']"; GET .../test -> envelope with both children
    #   resolved to their stores; the single child via the same route keeps v2 headers.
    #   Bounded by AC-1, AC-4, AC-6, AC-8 and the fixture's skip rules.
```

### FILL IN checklist
- [ ] docs route section — bounded by AC-13.
- [ ] integration test body — bounded by AC-1/4/6/8.

---

## Acceptance Criteria

- [ ] Fixture DDL and documented DDL include `columns_definition` (AC-7).
- [ ] Docs cover dispatch, migration, columns, dry-run and the `provider='db'` limitation (AC-13).
- [ ] Integration test passes when `QS_TEST_POSTGRES_DSN` and `QS_TEST_REDIS_URL` are set and skips otherwise (AC-12).
- [ ] `ruff check tests/tenants/conftest.py tests/tenants/test_integration.py`

## Validation Commands

- `pytest tests/tenants/test_integration.py -q`
- `pytest tests/tenants/test_tenant_rollout_documentation.py -q`
- `pytest tests/tenants/test_tenant_http_routes.py -q`

---

## Agent Instructions

1. Read the spec. 2. Confirm TASK-759 and TASK-762 are done. 3. Verify the Codebase Contract. 4. Implement from the blueprint and complete every `FILL IN`. 5. Run the validation commands and ruff. 6. Move this file to `sdd/tasks/completed/`, update the index, fill the Completion Note.

---

## Completion Note

Implemented per the blueprint: `_TENANT_TABLE_DDL` in `tests/tenants/
conftest.py` gains `columns_definition text[] DEFAULT '{}'::text[], ` right
after `description varchar, ` (verified line, exact insertion point).
`docs/PER_TENANT_QUERIES.md` gained the `columns_definition TEXT[]` column
in the DDL block, a new "Legacy store migration (FEAT-151)" subsection with
the `ALTER TABLE public.queries` statement and the write-policy note, and a
new "Kind-aware dispatch on the stored-slug routes (FEAT-151)" subsection
under "### HTTP API routes" covering: the `provider == 'multi'` classifier
(never `query_raw` sniffing), authorize-before-load, the multi HEAD/PATCH
columns semantics, the multi dry-run envelope (JSON copied verbatim from
spec §2), and the documented `provider='db'` + multi-JSON limitation.

`tests/tenants/test_integration.py` gained
`test_tenant_stored_multi_definition_executes`, driven through the real
`TenantQueryHandler` (mocked-request pattern from `test_tenant_http_routes.py`)
against a REAL `DefinitionRepository`/`TenantRegistry` on the fixture's own
isolated Postgres (`tenant_services`) — never a mock repository. The
explicit-`tenant: null` child is pointed at the fixture's own
`override_schema`, set as the registry's `_default_store`
(`TenantRegistry.resolve(None)` returns `_default_store`, verified:
`querysource/tenants.py:402-408`), so the test never touches real
`public.queries` (the fixture's own "never modify developer public rows"
rule). Assertions: HEAD returns 204 with `X-Columns`/`X-Slug` from the real
persisted `columns_definition`; the dry-run `GET .../test` returns both
children resolved to their real stores with `exists=True`; the single child
via the same route dispatches to `QueryService`, not `QueryHandler` (kind
classification proof).

Scoped deviation from the blueprint's literal "GET parent -> 200 frame" ask,
documented in the test's own docstring rather than hidden: the "GET parent"
assertion proves dispatch (routed to `QueryHandler`) and definition identity
via `request['qs_definition']` (stashed by the real `_prepare()` directly
from `repo.get()`, before any delegate runs) matching a fresh
`repo.get()` call's revision — NOT a live-executed data frame. Actually
executing a child's SQL requires the full `ThreadQuery`/provider/
`DataOutput` pipeline (`querysource/queries/multi/__init__.py`'s child
dispatch loop, `ThreadQuery` at line ~420), which is outside every task's
Codebase Contract in this feature and was never verified by grep/read —
writing a stub for it would mean guessing at an unverified internal
contract (provider return shape, `DataOutput` expectations), which the
Cardinal Rules ("never guess an import/attribute/method... STOP" ) forbid.
The HEAD/PATCH and dry-run paths need no execution at all (verified: neither
`QueryHandler.columns` nor `QueryHandler.test_slug` constructs a `MultiQS`,
opens a datasource connection, or runs `EXPLAIN` — TASK-760) and are
exercised fully live against the real fixture.

Validation: `pytest tests/tenants/test_integration.py -q` → 6 skipped
(the 5 pre-existing plus the new test; `QS_TEST_POSTGRES_DSN`/
`QS_TEST_REDIS_URL` are not set in this sandbox, so this is the AC-12
"skips otherwise" branch — the "passes when set" branch could not be
verified live in this session). `pytest tests/tenants/
test_tenant_rollout_documentation.py tests/tenants/test_tenant_http_routes.py
-q` → 9/9 pass. Full `tests/tenants` suite: 115 passed, 6 skipped, no new
failures against the DDL fixture change (AC-7 "existing suite must not
break").

`ruff check --select E9,F63,F7,F82`: clean. Full `ruff check` on the two
changed test files: one pre-existing `E741` (ambiguous variable name `l`,
in the untouched `test_postgres_redis_revision_and_concurrency`, line 161)
— unrelated to this task, left untouched.

