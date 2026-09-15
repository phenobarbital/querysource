"""Opt-in isolated tenant integration fixtures.

Every fixture/helper here is gated behind explicit environment variables
(``QS_TEST_POSTGRES_DSN`` / ``QS_TEST_REDIS_URL``) and skips entirely when
they are absent — these fixtures must never touch a developer's real
``public`` schema/rows or an unconfigured Redis instance. Every schema,
table, and Redis key created is uniquely namespaced per test run
(``run_id``) and torn down in a ``finally`` block, even when setup itself
fails partway through.
"""
import logging
import os
import uuid
from contextlib import asynccontextmanager, suppress

import pytest
import pytest_asyncio

_logger = logging.getLogger("tests.tenants.conftest")

_TENANT_TABLE_DDL = (
    'CREATE TABLE "{schema}".queries ('
    "query_slug varchar PRIMARY KEY, "
    "attributes jsonb, "
    "cache_options jsonb, "
    "provider varchar, "
    "is_cached boolean DEFAULT false, "
    "query_raw text, "
    "description varchar, "
    "updated_at timestamptz DEFAULT now()"
    ")"
)


def tenant_services_configured() -> bool:
    """True when both explicit test-service env vars are set.

    Exposed as a plain (non-fixture) helper so a test can run a mandatory,
    no-external-service check UNCONDITIONALLY first, and only pull in
    ``provision_tenant_services()`` — which may skip — afterward (pytest's
    async-fixture machinery does not support pulling an async fixture in
    mid-test via ``request.getfixturevalue``, so a plain async context
    manager is used for that case instead of a second fixture).
    """
    return bool(os.environ.get("QS_TEST_POSTGRES_DSN")) and bool(
        os.environ.get("QS_TEST_REDIS_URL")
    )


@asynccontextmanager
async def provision_tenant_services():
    """Provision isolated test metadata/data stores and a dedicated Redis namespace.

    Yields a dict with the connection strings plus the identities of every
    physical store this fixture provisioned:

    - ``legacy_schema``: the existing configured/default store (``public``).
      Never created or dropped here — it is real, pre-existing structural
      ownership, not a fixture-owned object (AC-1 "never modify developer
      public rows").
    - ``tenant_schemas``: two distinct tenant-contract schemas
      (``qs_it_<run_id>_t1`` / ``_t2``), each with its own ``queries``
      table matching the tenant contract's columns.
    - ``override_schema``: a third, explicitly-selected store used for the
      "override" scenarios (AC-1's "override store").

    Every schema created here is namespaced with a fresh ``run_id`` so two
    concurrent test runs never collide, and every Redis key this fixture
    (or the test using it) writes MUST be prefixed with
    ``f"qs:it:{run_id}:"`` so teardown can find and flush exactly (and
    only) what this run created.

    Raises:
        pytest.skip.Exception: if either required env var is absent —
            callers must invoke this only after any mandatory-locally
            (no-external-service) assertions have already run.
    """
    postgres_dsn = os.environ.get("QS_TEST_POSTGRES_DSN")
    redis_url = os.environ.get("QS_TEST_REDIS_URL")
    if not postgres_dsn or not redis_url:
        pytest.skip("Requires explicit isolated PostgreSQL and Redis test services")

    from asyncdb import AsyncDB

    run_id = uuid.uuid4().hex[:8]
    tenant_schemas = [f"qs_it_{run_id}_t1", f"qs_it_{run_id}_t2"]
    override_schema = f"qs_it_{run_id}_override"
    created_schemas: list[str] = []
    cleanup_errors: list[tuple[str, Exception]] = []

    db = AsyncDB("pg", dsn=postgres_dsn)
    conn = None
    try:
        conn = await db.connection()
        for schema in [*tenant_schemas, override_schema]:
            _, error = await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
            if error:
                raise RuntimeError(f"Failed to create schema {schema!r}: {error}")
            created_schemas.append(schema)
            _, error = await conn.execute(_TENANT_TABLE_DDL.format(schema=schema))
            if error:
                raise RuntimeError(
                    f"Failed to create {schema!r}.queries: {error}"
                )

        yield {
            "run_id": run_id,
            "postgres_dsn": postgres_dsn,
            "redis_url": redis_url,
            "legacy_schema": "public",
            "tenant_schemas": tenant_schemas,
            "override_schema": override_schema,
            "connection": conn,
        }
    finally:
        # Only ever drop schemas THIS fixture created (AC-1) — never
        # "public" or anything not in created_schemas, even on partial
        # setup failure above.
        if conn is not None:
            for schema in created_schemas:
                try:
                    await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
                except Exception as exc:  # noqa: BLE001 - best-effort teardown must not mask the real test failure
                    cleanup_errors.append((schema, exc))
            with suppress(Exception):
                await conn.close()

        # Best-effort Redis namespace flush — only keys prefixed with this
        # run's own id, never a blanket FLUSHDB.
        try:
            redis_db = AsyncDB("redis", dsn=redis_url)
            async with await redis_db.connection() as rconn:
                keys = await rconn.execute("KEYS", f"qs:it:{run_id}:*")
                if keys:
                    await rconn.execute("DEL", *keys)
        except Exception as exc:  # noqa: BLE001 - best-effort teardown
            cleanup_errors.append(("redis", exc))

        if cleanup_errors:
            _logger.warning(
                "tenant_services cleanup encountered errors (run_id=%s): %s",
                run_id, cleanup_errors,
            )


@pytest_asyncio.fixture
async def tenant_services():
    """Fixture wrapper around ``provision_tenant_services`` for tests that
    need the isolated stores for their entire body (no mandatory-locally
    check to run first)."""
    async with provision_tenant_services() as services:
        yield services
