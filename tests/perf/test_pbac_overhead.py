"""PBAC performance regression test (TASK-643).

Measures the p95 wall-clock overhead added by PBAC enforcement relative to
PBAC-off baseline.  Budget: < 5% increase in p95 latency.

Test design:
- 10 warm-up requests to heat the PolicyEvaluator LRU cache.
- 1,000 measured sequential requests.
- p95 computed from samples.
- PBAC-off run uses no app['security'] key.
- PBAC-on run uses real navigator-auth stack with an allow-all policy.

The test is opt-in (marked with @pytest.mark.perf) and skipped when the
QuerySource fixture Postgres database is not reachable.

Run with:
    pytest -m perf -v tests/perf/test_pbac_overhead.py

The recorded p95 values are printed to stdout so CI logs can be trended.
"""
import socket
import time
import statistics
import pytest
from unittest.mock import AsyncMock, patch
from aiohttp import web
from aiohttp.test_utils import TestServer, TestClient


pytestmark = pytest.mark.perf


N_WARMUP = 10
N_MEASURE = 1000
P95_BUDGET = 0.05  # 5%

_PATCH_GET_SESSION = "querysource.handlers.abstract.get_session"

_SESSION_SUPERUSER = {
    "username": "admin",
    "user_id": "admin",
    "groups": ["superuser"],
    "roles": [],
}

# Policy that allows superuser to execute any slug.
_ALLOW_ALL_POLICY = """\
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
"""


def _pg_reachable(host: str = "127.0.0.1", port: int = 5432) -> bool:
    """Check that Postgres is listening (basic connectivity only)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        return s.connect_ex((host, port)) == 0
    except OSError:
        return False
    finally:
        s.close()


async def _build_app(pbac_on: bool, policies_dir: str) -> web.Application:
    """Build a minimal aiohttp app with or without PBAC."""
    from querysource.handlers.service import QueryService
    from querysource.auth import setup_pbac

    app = web.Application()

    if pbac_on:
        setup_pbac(app, policy_dir=policies_dir)

    qs = QueryService()
    app.router.add_get(
        "/api/v2/services/queries/{slug}", qs.query, allow_head=False
    )
    return app


async def _run_requests(
    client: TestClient,
    slug: str,
    n: int,
    session,
) -> list:
    """Run n requests against the slug endpoint, returning per-request times."""
    samples = []
    with patch(_PATCH_GET_SESSION, AsyncMock(return_value=session)):
        for _ in range(n):
            t0 = time.perf_counter()
            resp = await client.get(f"/api/v2/services/queries/{slug}")
            # Read body to ensure complete response.
            await resp.read()
            samples.append(time.perf_counter() - t0)
    return samples


@pytest.mark.skipif(
    not _pg_reachable(),
    reason="Postgres not reachable at 127.0.0.1:5432",
)
@pytest.mark.asyncio
async def test_pbac_overhead_under_5pct(tmp_path):
    """p95 PBAC overhead must be < 5% compared to PBAC-off baseline.

    The test uses a slug that is expected to exist in the test database.
    If the slug is missing, both PBAC-on and PBAC-off return the same
    error code (404 or 400 from the handler), making the comparison fair
    since PBAC is exercised before the slug lookup.
    """
    # Write the allow-all policy to a temp directory.
    policies_dir = str(tmp_path)
    (tmp_path / "defaults.yaml").write_text(_ALLOW_ALL_POLICY)

    # The slug used for measurement.  It may or may not exist in the DB;
    # what matters is that both runs hit the same code path past PBAC.
    # The PBAC enforcement (which is what we're measuring) happens before
    # slug lookup, so a non-existent slug is fine for overhead measurement.
    SLUG = "tests_smoke_slug"

    # ── PBAC OFF run ──────────────────────────────────────────────────────
    app_off = await _build_app(pbac_on=False, policies_dir=policies_dir)
    server_off = TestServer(app_off)
    client_off = TestClient(server_off)
    await client_off.start_server()
    try:
        # Warm-up (no session needed when PBAC is off).
        await _run_requests(client_off, SLUG, N_WARMUP, session=None)
        samples_off = await _run_requests(client_off, SLUG, N_MEASURE, session=None)
    finally:
        await client_off.close()

    # ── PBAC ON run ───────────────────────────────────────────────────────
    app_on = await _build_app(pbac_on=True, policies_dir=policies_dir)
    server_on = TestServer(app_on)
    client_on = TestClient(server_on)
    await client_on.start_server()
    try:
        # Warm-up with superuser session (policy cache gets hot).
        await _run_requests(client_on, SLUG, N_WARMUP, session=_SESSION_SUPERUSER)
        samples_on = await _run_requests(
            client_on, SLUG, N_MEASURE, session=_SESSION_SUPERUSER
        )
    finally:
        await client_on.close()

    # ── p95 comparison ───────────────────────────────────────────────────
    # statistics.quantiles with n=20 gives ventiles; index 18 = p95.
    p95_off = statistics.quantiles(samples_off, n=20)[18]
    p95_on = statistics.quantiles(samples_on, n=20)[18]

    delta = (p95_on - p95_off) / p95_off if p95_off > 0 else 0.0

    print(
        f"\n[perf] p95_off = {p95_off * 1000:.3f} ms | "
        f"p95_on = {p95_on * 1000:.3f} ms | "
        f"delta = {delta * 100:.2f}%"
    )

    assert delta < P95_BUDGET, (
        f"PBAC overhead {delta * 100:.2f}% exceeds the {P95_BUDGET * 100:.0f}% "
        f"p95 budget (p95_off={p95_off * 1000:.3f}ms, "
        f"p95_on={p95_on * 1000:.3f}ms)"
    )
