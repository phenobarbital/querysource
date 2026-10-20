"""Shared fixtures for the FEAT-176 explicit-move tests.

A disposable PostgreSQL plus the two store shapes a move runs between: a legacy table
(with ``program_slug``) and a tenant table (without it). There is no control-plane DDL:
a move is one transaction, so the only schema these tests need is the query tables
themselves and their run-as audit tables.
"""
import glob
import os
import shutil
import socket
import subprocess
import tempfile
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from asyncdb import AsyncDB

from querysource.query_moves import ACK_CONSUMERS, ACK_PROGRAM
from querysource.repositories.definitions import DefinitionRepository
from querysource.tenant_errors import TenantError
from querysource.tenants import QueryIdentity, QueryStore, TenantRegistry

NS = "h:5432/db"
T0 = datetime(2026, 1, 5, 8, 0, tzinfo=timezone.utc)
ACTOR = 77
RUN_AS = 5
ACKS = {ACK_CONSUMERS: True, ACK_PROGRAM: True}
SCHEDULED = {"scheduler": {"schedule_type": "interval", "schedule": {"minutes": 5}}}


def _find_bin(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    for candidate in sorted(glob.glob(f"/usr/lib/postgresql/*/bin/{name}"), reverse=True):
        return candidate
    return None


@pytest.fixture(scope="module")
def pg_dsn():
    """A disposable PostgreSQL, or ``QS_TEST_PG_DSN`` when one is provided."""
    dsn = os.environ.get("QS_TEST_PG_DSN")
    if dsn:
        yield dsn
        return
    initdb, pg_ctl = _find_bin("initdb"), _find_bin("pg_ctl")
    if not initdb or not pg_ctl:
        pytest.skip("no PostgreSQL binaries and QS_TEST_PG_DSN unset")
    root = tempfile.mkdtemp(prefix="qs_move_pg_")
    data = os.path.join(root, "data")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    subprocess.run([initdb, "-D", data, "-A", "trust", "-U", "qs"], check=True, capture_output=True)
    subprocess.run(
        [pg_ctl, "-D", data, "-o", f"-p {port} -k {root} -c listen_addresses=127.0.0.1 -c max_connections=100",
         "-l", os.path.join(root, "log"), "-w", "start"],
        check=True, capture_output=True,
    )
    try:
        yield f"postgresql://qs@127.0.0.1:{port}/postgres"
    finally:
        subprocess.run([pg_ctl, "-D", data, "-m", "immediate", "stop"], capture_output=True)
        shutil.rmtree(root, ignore_errors=True)


def _factory(dsn: str):
    """A connection factory for ``dsn``."""
    async def factory():
        return await AsyncDB("pg", dsn=dsn).connection()

    return factory


def _store(schema: str, table: str = "queries", contract: str = "tenant") -> QueryStore:
    """One physical store descriptor."""
    return QueryStore(NS, schema, table, contract, frozenset({"query_slug"}))


LEGACY = _store("public", "queries", "legacy")
TENANT = _store("wm_assembly", "queries", "tenant")


class _Registry(TenantRegistry):
    """A registry pinned to one configured default, with discovery already done."""

    def __init__(self, default: QueryStore) -> None:
        super().__init__()
        self._default_store = default
        self._discovered = True


TABLE_DDL = """
CREATE TABLE {t} (
  query_slug text PRIMARY KEY, description text, source text,
  params jsonb DEFAULT '{{}}', attributes jsonb DEFAULT '{{}}', conditions jsonb DEFAULT '{{}}',
  cond_definition jsonb DEFAULT '{{}}', fields text[] DEFAULT '{{}}', filtering jsonb DEFAULT '{{}}',
  ordering text[] DEFAULT '{{}}', grouping text[] DEFAULT '{{}}', columns_definition text[] DEFAULT '{{}}',
  qry_options jsonb DEFAULT '{{}}', h_filtering boolean DEFAULT false, query_raw text, is_raw boolean DEFAULT false,
  is_cached boolean DEFAULT true, provider text DEFAULT 'db', parser text DEFAULT 'SQLParser',
  cache_timeout int NOT NULL DEFAULT 3600, cache_refresh int NOT NULL DEFAULT 0, cache_options jsonb DEFAULT '{{}}',
  program_id int NOT NULL DEFAULT 1, dwh boolean NOT NULL DEFAULT false, dwh_driver text, dwh_info jsonb,
  dwh_scheduler jsonb, created_at timestamptz DEFAULT now(), created_by int, updated_at timestamptz DEFAULT now(),
  updated_by int, scheduler_run_as_user_id int{legacy}
);
CREATE TABLE {a} (
  audit_id bigserial PRIMARY KEY, query_slug varchar NOT NULL, old_user_id int, new_user_id int,
  operation varchar NOT NULL, changed_by int NOT NULL, changed_at timestamptz NOT NULL DEFAULT now(),
  request_info jsonb
);
"""


async def setup_stores(dsn: str) -> None:
    """(Re)create an empty legacy and tenant store with their run-as audit tables."""
    async with await _factory(dsn)() as conn:
        await conn.execute('DROP TABLE IF EXISTS "public"."queries", "public"."queries_run_as_audit" CASCADE')
        await conn.execute('DROP SCHEMA IF EXISTS "wm_assembly" CASCADE')
        for store, legacy in ((LEGACY, ", program_slug text"), (TENANT, "")):
            await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{store.schema}"')
            res = await conn.execute(TABLE_DDL.format(
                t=f'"{store.schema}"."{store.table}"',
                a=f'"{store.schema}"."{store.table}_run_as_audit"', legacy=legacy))
            assert res[1] is None, res


async def insert_row(dsn, store, slug="s", **cols) -> None:
    """Insert one definition row into ``store``."""
    base = {"query_slug": slug, "description": "d", "query_raw": "SELECT 1", "created_by": 3,
            "created_at": datetime(2025, 5, 5, tzinfo=timezone.utc), "attributes": {}}
    if store.contract == "legacy":
        base["program_slug"] = "prog"
    base.update(cols)
    names = list(base)
    sql = (f'INSERT INTO "{store.schema}"."{store.table}" ({", ".join(names)}) VALUES '
           f'({", ".join(f"${i + 1}" for i in range(len(names)))})')
    async with await _factory(dsn)() as conn:
        res = await conn.execute(sql, *base.values())
        assert res[1] is None, res


async def fetch_row(dsn, store, slug="s"):
    """Read one row back from ``store`` (None when absent)."""
    async with await _factory(dsn)() as conn:
        return await conn.fetch_one(f'SELECT * FROM "{store.schema}"."{store.table}" WHERE query_slug = $1', slug)


async def scalar(dsn, sql, *args):
    """Run a scalar query."""
    async with await _factory(dsn)() as conn:
        return await conn.fetchval(sql, *args, column=0)


def ident(slug="s", store=LEGACY) -> QueryIdentity:
    """A resolved source identity."""
    return QueryIdentity(store=store, slug=slug)


def raw_row(**over) -> dict:
    """An in-memory raw definition row for the fake repository."""
    base = {"query_slug": "s", "provider": "db", "attributes": {}, "program_slug": "prog", "is_cached": False,
            "cache_options": {}, "query_raw": "SELECT 1", "scheduler_run_as_user_id": None}
    base.update(over)
    return base


class FakeRepo:
    """Minimal ``DefinitionRepository`` stand-in: one dict of raw rows keyed by (schema, slug)."""

    def __init__(self, dsn, rows) -> None:
        self.registry = _Registry(LEGACY)
        self.connection_factory = _factory(dsn)
        self.rows = rows
        self.fetches = 0

    async def _fetch_row(self, store, slug):
        self.fetches += 1
        return self.rows.get((store.schema, slug))

    async def get(self, identity):
        row = self.rows.get((identity.store.schema, identity.slug))
        if row is None:
            raise TenantError("missing", error_code="query_not_found")
        runtime = SimpleNamespace(
            attributes=row.get("attributes"), cache_options=row.get("cache_options"),
            provider=row.get("provider"), is_cached=row.get("is_cached", False),
            dwh=row.get("dwh", False), dwh_scheduler=row.get("dwh_scheduler"),
            program_slug=row.get("program_slug") if identity.store.contract == "legacy" else identity.store.schema,
            query_raw=row.get("query_raw"),
        )
        return SimpleNamespace(runtime=runtime, revision="r")

    def _run_as_audit_table(self, store):
        return f'"{store.schema}"."{store.table}_run_as_audit"'

    def _qualified_table(self, store):
        return f'"{store.schema}"."{store.table}"'

    def _allowed_columns(self, store):
        return DefinitionRepository._allowed_columns(self, store)


class FakeScheduler:
    """Records pause/resume and re-registration the way QSScheduler would perform them."""

    def __init__(self) -> None:
        self.paused: list[tuple[str, str, bool]] = []
        self.registered: list[str | None] = []

    def set_slug_jobs_paused(self, slug, store, paused):
        self.paused.append((slug, store.schema, paused))
        return [f"query_{slug}"]

    async def register_slug(self, slug, *, tenant=None):
        self.registered.append(tenant)
        return {"slug": slug, "tenant": tenant, "registered": [], "removed": []}
