"""Opt-in isolated tenant integration fixtures."""
import os

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def tenant_services():
    """Provision isolated test metadata/data stores and a dedicated Redis namespace."""
    postgres_dsn = os.environ.get("QS_TEST_POSTGRES_DSN")
    redis_url = os.environ.get("QS_TEST_REDIS_URL")
    if not postgres_dsn or not redis_url:
        pytest.skip("Requires explicit isolated PostgreSQL and Redis test services")
    # FILL IN: provision isolated legacy/two-tenant/override fixtures; yield handles;
    # always clean only objects created by this fixture, including on setup failure.
    raise NotImplementedError
