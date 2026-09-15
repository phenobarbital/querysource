"""Load and identify scheduled definitions per physical store regression contracts."""
import logging

import pytest

from querysource.scheduler.scheduler import QSScheduler
from querysource.tenant_errors import TenantError
from querysource.tenants import QueryStore


def _store(schema: str, contract: str = "tenant", table: str = "queries") -> QueryStore:
    return QueryStore(
        database_namespace="localhost:5432/querysource",
        schema=schema,
        table=table,
        contract=contract,
        columns=frozenset(
            {"query_slug", "attributes", "cache_options", "provider", "is_cached", "query_raw"}
        ),
    )


def _row(slug: str, *, seconds: int = 60) -> dict:
    return {
        "query_slug": slug,
        "attributes": {
            "scheduler": {"schedule_type": "interval", "schedule": {"seconds": seconds}}
        },
        "cache_options": {},
        "provider": "db",
        "is_cached": False,
        "query_raw": None,
    }


class _FakeRuntime:
    """Stand-in for QueryModel: exposes exactly the attributes _fetch_slug_row reads."""

    def __init__(self, row: dict) -> None:
        self.query_slug = row["query_slug"]
        self.attributes = row["attributes"]
        self.cache_options = row["cache_options"]
        self.provider = row["provider"]
        self.is_cached = row["is_cached"]
        self.query_raw = row["query_raw"]


class _FakeLoaded:
    def __init__(self, row: dict) -> None:
        self.runtime = _FakeRuntime(row)


class _FakeRegistry:
    """Stand-in TenantRegistry: pre-discovered, no DB access."""

    def __init__(self, stores: tuple[QueryStore, ...], default: QueryStore) -> None:
        self._stores_tuple = stores
        self._default = default

    def stores(self) -> tuple[QueryStore, ...]:
        return self._stores_tuple

    def resolve(self, tenant: str | None = None) -> QueryStore:
        if tenant is None:
            return self._default
        for store in self._stores_tuple:
            if store.schema == tenant:
                return store
        raise TenantError(f"Tenant not found: {tenant}", error_code="tenant_not_available")


class _FakeRepo:
    """Stand-in DefinitionRepository: schedulable() + get() from in-memory fixtures."""

    def __init__(
        self,
        rows_by_store: dict[str, tuple[dict, ...]] | None = None,
        get_results: dict[tuple[str, str], object] | None = None,
    ) -> None:
        self._rows_by_store = rows_by_store or {}
        # get_results maps (schema, slug) -> row dict (success) or Exception instance (raise).
        self._get_results = get_results or {}

    async def schedulable(self, store: QueryStore) -> tuple[dict, ...]:
        return self._rows_by_store.get(store.schema, ())

    async def get(self, identity) -> _FakeLoaded:
        key = (identity.store.schema, identity.slug)
        result = self._get_results.get(key)
        if isinstance(result, BaseException):
            raise result
        if result is not None:
            return _FakeLoaded(result)
        raise TenantError(f"Query not found: {identity.slug!r}", error_code="query_not_found")


def _make_scheduler() -> QSScheduler:
    return QSScheduler(loop=None)


@pytest.mark.asyncio
async def test_startup_all_stores_and_legacy_override() -> None:
    """startup all stores and legacy override."""
    # A configured default that is NOT the literal "public" schema still
    # gets legacy (unqualified) job ids (AC-1 "Honor configured legacy
    # overrides"); the other store gets qsj2-qualified ids (AC-2).
    default_store = _store("configured_default", contract="legacy")
    tenant_store = _store("tenant1", contract="tenant")
    registry = _FakeRegistry((default_store, tenant_store), default=default_store)
    repo = _FakeRepo(
        rows_by_store={
            "configured_default": (_row("legacy_slug"),),
            "tenant1": (_row("tenant_slug"),),
        }
    )

    scheduler = _make_scheduler()
    app = {"qs_tenant_registry": registry, "qs_definition_repository": repo}
    await scheduler.startup(app)
    try:
        # Configured default -> unqualified legacy id.
        assert scheduler._scheduler.get_job("query_legacy_slug") is not None
        # Non-default store -> qsj2-qualified id, not the legacy shape.
        assert scheduler._scheduler.get_job("query_tenant_slug") is None
        qualified = [
            j.id for j in scheduler._scheduler.get_jobs() if "tenant_slug" in j.id
        ]
        assert len(qualified) == 1
        assert qualified[0].startswith("qsj2-query-")
        assert qualified[0].endswith("-tenant_slug")

        # Every registered job kwargs carries a validated owner envelope.
        legacy_job = scheduler._scheduler.get_job("query_legacy_slug")
        assert legacy_job.kwargs["owner"]["contract"] == "legacy"
        assert legacy_job.kwargs["owner"]["schema"] == "configured_default"
        tenant_job = scheduler._scheduler.get_job(qualified[0])
        assert tenant_job.kwargs["owner"]["contract"] == "tenant"
        assert tenant_job.kwargs["owner"]["schema"] == "tenant1"
        assert tenant_job.kwargs["owner"]["version"] == 1
    finally:
        scheduler._scheduler.shutdown(wait=False)


@pytest.mark.asyncio
async def test_alias_dedup_and_same_slug_job_ids() -> None:
    """alias dedup and same slug job ids."""
    store_a = _store("store_a", contract="legacy")
    store_b = _store("store_b", contract="tenant")
    registry = _FakeRegistry((store_a, store_b), default=store_a)
    # Same slug text appears in BOTH stores (a physical alias / accidental
    # name collision) — startup must schedule it exactly once (AC-2
    # "physical aliases schedule once").
    repo = _FakeRepo(
        rows_by_store={
            "store_a": (_row("shared_slug"),),
            "store_b": (_row("shared_slug"),),
        }
    )

    scheduler = _make_scheduler()
    app = {"qs_tenant_registry": registry, "qs_definition_repository": repo}
    await scheduler.startup(app)
    try:
        matching = [
            j for j in scheduler._scheduler.get_jobs() if "shared_slug" in j.id
        ]
        assert len(matching) == 1
        # It was scheduled from the FIRST store in registry.stores() order
        # (store_a, the default) -> legacy id, not qsj2-qualified.
        assert matching[0].id == "query_shared_slug"
    finally:
        scheduler._scheduler.shutdown(wait=False)

    # _slug_job_ids for the same slug text differs by store: the default
    # store's ids stay legacy-shaped, a non-default store's ids are also
    # qualified — so a later, deliberate registration of "shared_slug" for
    # store_b would never collide with store_a's job id.
    scheduler2 = _make_scheduler()
    scheduler2._registry = registry
    default_ids = scheduler2._slug_job_ids("shared_slug", store=store_a)
    other_ids = scheduler2._slug_job_ids("shared_slug", store=store_b)
    assert default_ids == ["query_shared_slug", "multi_shared_slug", "cache_shared_slug"]
    assert set(default_ids).isdisjoint(
        {jid for jid in other_ids if jid.startswith("qsj2-")}
    )


@pytest.mark.asyncio
async def test_register_update_remove_only_owner() -> None:
    """register update remove only owner."""
    default_store = _store("public", contract="legacy")
    tenant_store = _store("tenant1", contract="tenant")
    registry = _FakeRegistry((default_store, tenant_store), default=default_store)
    repo = _FakeRepo(
        get_results={
            ("public", "shared"): _row("shared"),
            ("tenant1", "shared"): _row("shared", seconds=30),
        }
    )

    scheduler = _make_scheduler()
    scheduler._scheduler = scheduler._create_scheduler()
    scheduler._registry = registry
    scheduler._repository = repo

    # Register the same slug text for two different owners.
    default_result = await scheduler.register_slug("shared", tenant=None)
    tenant_result = await scheduler.register_slug("shared", tenant="tenant1")

    assert default_result["registered"] == ["query_shared"]
    assert scheduler._scheduler.get_job("query_shared") is not None
    tenant_job_id = tenant_result["registered"][0]
    assert tenant_job_id != "query_shared"
    assert scheduler._scheduler.get_job(tenant_job_id) is not None

    # Mutating (re-syncing) the tenant1 owner's slug must not touch the
    # default owner's job for the identical slug text (AC-3).
    second_tenant_result = await scheduler.register_slug("shared", tenant="tenant1")
    assert scheduler._scheduler.get_job("query_shared") is not None
    assert scheduler._scheduler.get_job(tenant_job_id) is not None
    assert second_tenant_result["tenant"] == "tenant1"
    assert second_tenant_result["store"]["schema"] == "tenant1"
    # Not .start()-ed in this test (jobs are added "tentatively" and
    # inspected directly via get_job), so no shutdown() is needed/valid.


@pytest.mark.asyncio
async def test_runtime_store_error_not_missing(caplog) -> None:
    """runtime store error not missing."""
    default_store = _store("public", contract="legacy")
    registry = _FakeRegistry((default_store,), default=default_store)

    # Case 1: genuinely missing definition -> TenantError(query_not_found)
    # -> DEBUG log, not a warning.
    repo_missing = _FakeRepo(get_results={})
    scheduler = _make_scheduler()
    scheduler._registry = registry
    scheduler._repository = repo_missing
    with caplog.at_level(logging.DEBUG, logger="QSScheduler"):
        row = await scheduler._fetch_slug_row("absent_slug", tenant=None)
    assert row is None
    assert any(
        "not found" in rec.message for rec in caplog.records
    )
    assert not any(
        rec.levelno >= logging.WARNING for rec in caplog.records
    )

    # Case 2: a store/connection failure (never TenantError-wrapped by the
    # repository) must be logged as a WARNING and must NOT read as "not
    # found" — a down store is not the same as an absent definition (AC-4).
    caplog.clear()
    repo_broken = _FakeRepo(
        get_results={("public", "flaky_slug"): ConnectionError("db unreachable")}
    )
    scheduler2 = _make_scheduler()
    scheduler2._registry = registry
    scheduler2._repository = repo_broken
    with caplog.at_level(logging.DEBUG, logger="QSScheduler"):
        row2 = await scheduler2._fetch_slug_row("flaky_slug", tenant=None)
    assert row2 is None
    warnings = [rec for rec in caplog.records if rec.levelno >= logging.WARNING]
    assert len(warnings) == 1
    assert "unavailable" in warnings[0].message
    assert "not found" not in warnings[0].message
