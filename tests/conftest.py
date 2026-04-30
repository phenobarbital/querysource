"""Shared pytest fixtures for PBAC unit tests (TASK-641) and integration tests (TASK-642)."""
import socket
import pytest
from unittest.mock import AsyncMock, MagicMock


# ---------------------------------------------------------------------------
# Unit-test fixtures (TASK-641)
# ---------------------------------------------------------------------------

@pytest.fixture
def session_factory():
    """Factory: build a SessionData-like dict with given userinfo."""
    def _build(username="alice", groups=("analysts",), service=False, **extra):
        return {
            "username": username,
            "user_id": username,
            "groups": list(groups),
            "roles": [],
            "service": service,
            **extra,
        }
    return _build


@pytest.fixture
def mock_evaluator_allow():
    """PolicyEvaluator mock that always returns ALLOW."""
    ev = MagicMock()
    ev.check_access = MagicMock(return_value=MagicMock(
        allowed=True, effect="ALLOW", matched_policy="P", reason=""))
    return ev


@pytest.fixture
def mock_evaluator_deny():
    """PolicyEvaluator mock that always returns DENY."""
    ev = MagicMock()
    ev.check_access = MagicMock(return_value=MagicMock(
        allowed=False, effect="DENY", matched_policy="P", reason="denied"))
    return ev


# ---------------------------------------------------------------------------
# Integration-test helpers (TASK-642)
# ---------------------------------------------------------------------------

def _pg_reachable(host: str = "127.0.0.1", port: int = 5432) -> bool:
    """Return True if Postgres is listening on host:port."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1)
    try:
        return s.connect_ex((host, port)) == 0
    except OSError:
        return False
    finally:
        s.close()


# Policy YAML used in integration tests.
# Two policies:
#   - superuser_all: superuser group can do everything
#   - analysts_finance_slugs: analysts can execute finance_* slugs and use postgres
_INTEGRATION_POLICIES = """\
version: "1.0"
defaults:
  effect: deny
policies:
  - name: superuser_all
    effect: allow
    resources: ["slug:*", "datasource:*", "driver:*", "raw_query"]
    actions:
      - "slug:execute"
      - "slug:list"
      - "datasource:use"
      - "datasource:list"
      - "driver:use"
      - "driver:list"
      - "raw_query:execute"
    subjects:
      groups: ["superuser"]
    priority: 100
    enforcing: true
  - name: analysts_finance_slugs
    effect: allow
    resources: ["slug:finance_*", "datasource:postgres", "driver:postgres"]
    actions:
      - "slug:execute"
      - "slug:list"
      - "datasource:use"
      - "datasource:list"
      - "driver:use"
      - "driver:list"
    subjects:
      groups: ["analysts"]
    priority: 30
    enforcing: true
"""


@pytest.fixture
def policies_dir(tmp_path):
    """Sample policy YAMLs the integration tests use."""
    (tmp_path / "defaults.yaml").write_text(_INTEGRATION_POLICIES)
    return str(tmp_path)


@pytest.fixture
async def qs_app_pbac_on(policies_dir):
    """aiohttp TestClient with a real PBAC-enabled QuerySource app.

    Returns (app, client) — the client is already started.
    """
    from aiohttp import web
    from aiohttp.test_utils import TestServer, TestClient
    from querysource.auth import setup_pbac
    from querysource.handlers.service import QueryService
    from querysource.handlers.executor import QueryExecutor
    from querysource.handlers.multi import QueryHandler

    app = web.Application()

    # Bootstrap real PBAC stack.
    setup_pbac(app, policy_dir=policies_dir)

    # Register only the routes we test — avoids the QuerySource Singleton.
    qs = QueryService()
    app.router.add_get(
        "/api/v2/services/queries/{slug}", qs.query, allow_head=False
    )
    app.router.add_post("/api/v2/services/queries/{slug}", qs.query)

    ds = QueryExecutor()
    app.router.add_post("/api/v1/queries/test", ds.dry_run)
    app.router.add_post("/api/v1/queries/run", ds.query)

    mq = QueryHandler()
    app.router.add_post("/api/v3/queries/{slug}{meta}", mq.query)
    app.router.add_post("/api/v3/queries{meta}", mq.query)

    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    try:
        yield app, client
    finally:
        await client.close()


@pytest.fixture
async def qs_app_pbac_off():
    """aiohttp TestClient with a PBAC-disabled app (no app['security'])."""
    from aiohttp import web
    from aiohttp.test_utils import TestServer, TestClient
    from querysource.handlers.service import QueryService
    from querysource.handlers.executor import QueryExecutor

    app = web.Application()
    # No setup_pbac call — PBAC disabled.

    qs = QueryService()
    app.router.add_get(
        "/api/v2/services/queries/{slug}", qs.query, allow_head=False
    )
    app.router.add_post("/api/v2/services/queries/{slug}", qs.query)

    ds = QueryExecutor()
    app.router.add_post("/api/v1/queries/test", ds.dry_run)
    app.router.add_post("/api/v1/queries/run", ds.query)

    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    try:
        yield app, client
    finally:
        await client.close()
