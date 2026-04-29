# TASK-642: PBAC integration tests (handler enforcement, list filtering, multi-query)

**Feature**: pbac-support
**Spec**: `sdd/specs/pbac-support.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: L (4-8h)
**Depends-on**: TASK-631, TASK-632, TASK-633, TASK-634, TASK-637, TASK-638, TASK-639
**Assigned-to**: unassigned

---

## Context

Implements the integration-test slice of **Module 15** of the spec. End-to-end
verification that:

- `QS_PBAC_ENABLED=False` is a perfect no-op.
- `QS_PBAC_ENABLED=True` enforces the policies on every entry point with
  the expected 200/404 outcomes.
- All-or-nothing semantics work for multi-query.
- List filtering works for datasources and drivers.
- `dry_run` is gated identically to `query`.
- `raw_query:execute` gates raw inline queries.
- `pg_admin` visibility/use is policy-gated end-to-end.
- Per-user credential resolution actually drives driver instantiation.
- Profile-from-policy credentials work.

This is the broadest test in the feature. Expect 4–8h.

---

## Scope

Implement every row in the spec's "Integration Tests" table (§4) **except**
the perf test (`test_perf_regression`, that's TASK-643):

| Test | Purpose |
|---|---|
| `test_pbac_off_baseline` | Regression guard — PBAC off ⇒ today's behaviour. |
| `test_qs_anonymous_denied` | PBAC on, no session ⇒ 404 across all execution endpoints. |
| `test_slug_execute_allowed` | Allowed slug ⇒ 200. |
| `test_slug_execute_denied_404` | Denied slug ⇒ 404 with no leak. |
| `test_raw_query_blocked_without_permission` | Inline raw query without `raw_query:execute` ⇒ 404. |
| `test_raw_query_allowed_with_permission` | With permission ⇒ executes. |
| `test_datasource_use_denied` | Slug allowed, datasource denied ⇒ 404. |
| `test_driver_use_denied` | Slug + datasource allowed, driver denied ⇒ 404. |
| `test_dry_run_gated` | `dry_run` enforces identically to `query`. |
| `test_multiquery_all_or_nothing` | One denied ⇒ whole rejected; no thread starts. |
| `test_multiquery_all_allowed` | All allowed ⇒ identical payload to today. |
| `test_datasource_list_filtered` | List endpoint silently filters. |
| `test_driver_list_filtered` | Same for drivers. |
| `test_pg_admin_visible_to_admins_only` | End-to-end policy gating of the new datasource. |
| `test_per_user_credentials_used` | `PG_<USERNAME>_*` env vars drive the connection. |
| `test_profile_from_policy_credentials` | `credential_profile` attr drives `PG_<PROFILE>_*` lookup. |

**NOT in scope**: perf test (TASK-643); unit-level mock-only tests
(TASK-641).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `tests/integration/test_pbac_enforcement.py` | CREATE | Slug/raw/datasource/driver tests + dry_run + multi-query. |
| `tests/integration/test_pbac_listing.py` | CREATE | DatasourceView + drivers list filtering. |
| `tests/integration/test_pbac_credentials.py` | CREATE | Per-user + profile-from-policy credential tests. |
| `tests/conftest.py` | MODIFY | Add `policies_dir`, `qs_app_pbac_on`, `qs_app_pbac_off` fixtures. |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports

```python
from aiohttp.test_utils import AioHTTPTestCase, TestClient, TestServer
import pytest
from querysource.services import QuerySource           # querysource/services.py:45
# Real PBAC stack (do NOT mock at this layer):
from navigator_auth.abac.pdp import PDP
from navigator_auth.abac.guardian import Guardian
```

### Existing Test Patterns to Mirror

```python
# /home/jesuslara/proyectos/parallel/querysource/tests/test_api.py — example structure
import pytest
import aiohttp
from navconfig.logging import logging

DRIVER = 'postgres'
DSN = "postgres://qs_data:12345678@127.0.0.1:5432/navigator_dev"

async def query_api(slug, ev):
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=timeout) as response:
            pytest.assume(response.status in (200, 204, 404))
            ...
```

### Required fixtures

```python
# tests/conftest.py
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer, TestClient


@pytest.fixture
def policies_dir(tmp_path):
    """Sample policy YAMLs the integration tests use."""
    (tmp_path / "defaults.yaml").write_text("""
version: "1.0"
defaults: { effect: deny }
policies:
  - name: superuser_all
    effect: allow
    resources: ['slug:*', 'datasource:*', 'driver:*', 'raw_query']
    actions: ['slug:execute', 'slug:list', 'datasource:use',
              'datasource:list', 'driver:use', 'driver:list',
              'raw_query:execute']
    subjects: { groups: ['superuser'] }
    priority: 100
    enforcing: true
  - name: analysts_finance_slugs
    effect: allow
    resources: ['slug:finance_*', 'datasource:postgres', 'driver:postgres']
    actions: ['slug:execute', 'slug:list', 'datasource:use',
              'datasource:list', 'driver:use', 'driver:list']
    subjects: { groups: ['analysts'] }
    priority: 30
""")
    return str(tmp_path)


@pytest.fixture
async def qs_app_pbac_on(policies_dir, monkeypatch):
    monkeypatch.setenv("QS_PBAC_ENABLED", "True")
    monkeypatch.setenv("QS_POLICY_PATH", policies_dir)
    monkeypatch.setenv("QS_PBAC_CACHE_TTL", "300")
    # Force re-import of conf for the env override to take effect:
    import importlib
    import querysource.conf as _conf
    importlib.reload(_conf)
    # Build app:
    app = web.Application()
    from querysource.services import QuerySource
    qs = QuerySource(lazy=True)
    qs.setup(app)
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    try:
        yield app, client
    finally:
        await client.close()


@pytest.fixture
async def qs_app_pbac_off(monkeypatch):
    monkeypatch.setenv("QS_PBAC_ENABLED", "False")
    import importlib
    import querysource.conf as _conf
    importlib.reload(_conf)
    app = web.Application()
    from querysource.services import QuerySource
    qs = QuerySource(lazy=True)
    qs.setup(app)
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    try:
        yield app, client
    finally:
        await client.close()


@pytest.fixture
def session_inject():
    """Helper to attach a fake session to a test request.

    The exact mechanism depends on how navigator_session integrates with
    aiohttp test fixtures. Two common approaches:
      (a) Monkey-patch `navigator_session.get_session` to return a dict.
      (b) Use a session-injecting middleware on the test app.
    Pick one and stay consistent across tests.
    """
    ...
```

### Does NOT Exist

- ~~A "test as superuser" decorator~~ — inject the session via the
  `session_inject` fixture or a test-only middleware.
- ~~A real Postgres at `127.0.0.1:5432` in CI without configuration~~ —
  the existing `tests/test_api.py` references it. If CI doesn't have
  Postgres, mark tests `@pytest.mark.skipif` accordingly.
- ~~`QuerySource()` non-singleton~~ — `QuerySource` is a Singleton
  (`metaclass=Singleton`). Tests that re-instantiate must reset the
  singleton or use `lazy=True` to avoid double-init issues.

---

## Implementation Notes

### Test patterns

```python
# tests/integration/test_pbac_enforcement.py

class TestSlugEnforcement:
    async def test_pbac_off_baseline(self, qs_app_pbac_off):
        app, client = qs_app_pbac_off
        # Hit a known slug and assert 200/204/404 just like
        # tests/test_api.py — exact equality with PBAC-off behaviour.
        ...

    async def test_anonymous_denied(self, qs_app_pbac_on):
        app, client = qs_app_pbac_on
        # No session injection → 404
        async with client.get("/api/v2/services/queries/any_slug") as r:
            assert r.status == 404

    async def test_slug_execute_allowed(self, qs_app_pbac_on, session_inject):
        app, client = qs_app_pbac_on
        session_inject(client, {"username": "alice", "groups": ["analysts"]})
        async with client.get("/api/v2/services/queries/finance_revenue") as r:
            assert r.status in (200, 204)

    async def test_slug_execute_denied_404(self, qs_app_pbac_on, session_inject):
        app, client = qs_app_pbac_on
        session_inject(client, {"username": "alice", "groups": ["analysts"]})
        async with client.get("/api/v2/services/queries/admin_only_slug") as r:
            assert r.status == 404
            body = await r.text()
            # No leak of slug existence:
            assert "admin_only_slug" not in body
```

### Multi-query all-or-nothing test

```python
async def test_multiquery_all_or_nothing(self, qs_app_pbac_on, session_inject):
    app, client = qs_app_pbac_on
    session_inject(client, {"username": "alice", "groups": ["analysts"]})
    body = {
        "queries": {
            "finance_revenue": {"params": {}},
            "admin_only_slug":  {"params": {}},  # denied
        },
    }
    # Spy on thread starters to verify nothing executes:
    started = []
    monkeypatch_threadquery(started)  # helper hooks ThreadQuery.start
    async with client.post("/api/v3/queries/", json=body) as r:
        assert r.status == 404
    assert started == [], "No component should have started"
```

### Per-user credential test

```python
async def test_per_user_credentials_used(
    qs_app_pbac_on, session_inject, monkeypatch,
):
    monkeypatch.setenv("PG_BOB_HOST", "192.168.99.99")
    monkeypatch.setenv("PG_BOB_PORT", "9999")
    monkeypatch.setenv("PG_BOB_USER", "bob")
    monkeypatch.setenv("PG_BOB_PASSWORD", "secret")
    monkeypatch.setenv("PG_BOB_DATABASE", "bob_db")
    captured_params = []
    # Patch the driver's params_for to capture rather than connect:
    from querysource.datasources.drivers.pg import pgDriver
    orig = pgDriver.params_for
    def spy(self, session, app=None):
        result = orig(self, session, app)
        captured_params.append(result)
        return result
    monkeypatch.setattr(pgDriver, "params_for", spy)
    app, client = qs_app_pbac_on
    session_inject(client, {"username": "bob", "groups": ["analysts"]})
    # Trigger any Postgres slug:
    async with client.get("/api/v2/services/queries/finance_revenue"):
        pass
    assert captured_params, "params_for must be called at least once"
    assert captured_params[0]["host"] == "192.168.99.99"
    assert captured_params[0]["username"] == "bob"
```

### `dry_run` parity test

```python
async def test_dry_run_gated(self, qs_app_pbac_on, session_inject):
    app, client = qs_app_pbac_on
    session_inject(client, {"username": "alice", "groups": ["analysts"]})
    # alice cannot access admin_only_slug
    body = {"slug": "admin_only_slug"}
    async with client.post("/api/v1/queries/test", json=body) as r:
        assert r.status == 404, "dry_run must be gated like query"
```

### Key Constraints

- **Real PBAC engine**, mock-free at this layer. The point is end-to-end.
- **Session injection**: pick ONE approach (monkeypatch `get_session`
  globally for the fixture lifetime, or a session-injecting middleware)
  and stay consistent.
- **Skip on missing Postgres**: tests that require an actual DB
  connection should `pytest.mark.skipif(not _pg_reachable())`.
- **Strict response-body assertions**: when checking 404s on denied
  slugs, also assert the slug name does NOT appear in the body. Hide-
  existence is a hard requirement.

### References in Codebase

- `tests/test_api.py` — existing integration-test patterns.
- `pyproject.toml:171-177` — pytest config.

---

## Acceptance Criteria

- [ ] Every row in the spec's "Integration Tests" table is implemented
      and passing (except perf, which is TASK-643).
- [ ] `pytest tests/integration/ -v` green.
- [ ] No regressions in pre-existing tests.
- [ ] All denial responses (404) verified to NOT include the denied
      resource name in the body.
- [ ] Multi-query all-or-nothing verified by spy that no component thread
      starts on denial.
- [ ] Per-user credential test asserts the captured `params_for` output
      reflects the env-var override.

---

## Test Specification

The tests themselves are the deliverable. See Implementation Notes for the
patterns.

---

## Agent Instructions

1. Read spec §4 (Integration Tests) end-to-end.
2. Verify all dependency tasks (TASK-631..634, 637, 638, 639) are
   in `tasks/done/`.
3. Build the shared fixtures in `tests/conftest.py`.
4. Implement all 16 integration tests (per the spec table) split across
   the three files listed in "Files to Create / Modify".
5. Run `pytest tests/integration/ -v` until green.
6. Run full suite `pytest tests/ -x -q`.
7. Move task to `done/` and update the index.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Tests passing (count)**:
**Skipped tests + reason**:

**Deviations from spec**: none | describe if any
