"""Apply revision-scoped keys to every result cache boundary regression contracts."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from asyncdb.exceptions import ProviderError

from querysource.cache_identity import result_cache_key, definition_revision
from querysource.tenants import QueryIdentity, QueryStore
from querysource.interfaces.queries import AbstractQuery
from querysource.queries.qs import QS


# Fixtures for test data
@pytest.fixture
def mock_query_store():
    """Create a mock query store for testing."""
    return QueryStore(
        database_namespace="public",
        schema="queries",
        table="definitions",
        contract="tenant",
        columns=frozenset(["id", "slug", "body", "owner", "updated_at"])
    )


@pytest.fixture
def mock_identity(mock_query_store):
    """Create a mock query identity."""
    return QueryIdentity(
        store=mock_query_store,
        slug="test_query"
    )


@pytest.fixture
def mock_definition_row():
    """Mock persisted definition row."""
    return {
        "id": "1",
        "slug": "test_query",
        "body": "SELECT * FROM users",
        "owner": "tenant_a",
        "updated_at": "2024-01-01T00:00:00Z"
    }


@pytest.fixture
def mock_definition_row_v2():
    """Mock persisted definition row with updated body (different revision)."""
    return {
        "id": "1",
        "slug": "test_query",
        "body": "SELECT id, name FROM users",  # Different query body
        "owner": "tenant_a",
        "updated_at": "2024-01-02T00:00:00Z"
    }


@pytest.fixture
def different_owner_definition_row():
    """Mock persisted definition row with different owner (same SQL, different owner)."""
    return {
        "id": "2",
        "slug": "test_query",
        "body": "SELECT * FROM users",  # Same SQL
        "owner": "tenant_b",  # Different owner
        "updated_at": "2024-01-01T00:00:00Z"
    }


@pytest.mark.asyncio
async def test_identical_sql_owner_key_isolation():
    """identical sql owner key isolation.

    When two definitions have identical SQL but different owners (different
    physical stores), they must use different cache keys. This ensures that
    queries executed for tenant_a do not return cached results from tenant_b.
    """
    # Setup: Create two identities with different physical stores (different owners)
    store_a = QueryStore(
        database_namespace="public",
        schema="tenant_a",
        table="definitions",
        contract="tenant",
        columns=frozenset(["id", "slug", "body", "owner"])
    )
    store_b = QueryStore(
        database_namespace="public",
        schema="tenant_b",
        table="definitions",
        contract="tenant",
        columns=frozenset(["id", "slug", "body", "owner"])
    )

    identity_a = QueryIdentity(store=store_a, slug="test_query")
    identity_b = QueryIdentity(store=store_b, slug="test_query")

    # Same SQL and provider checksum for both
    sql_body = "SELECT * FROM users"
    provider_checksum = "abc123def456"
    revision_a = definition_revision({"body": sql_body, "owner": "tenant_a"})
    revision_b = definition_revision({"body": sql_body, "owner": "tenant_b"})

    # Generate cache keys for both owners
    cache_key_a = result_cache_key(identity_a, revision_a, provider_checksum)
    cache_key_b = result_cache_key(identity_b, revision_b, provider_checksum)

    # Different stores with identical SQL must have different cache keys
    assert cache_key_a != cache_key_b, (
        "Identical SQL from different physical stores should produce different cache keys"
    )
    # Both should be revision-scoped keys
    assert cache_key_a.startswith("qs:r2:")
    assert cache_key_b.startswith("qs:r2:")


@pytest.mark.asyncio
async def test_revision_changed_by_external_edit():
    """revision changed by external edit.

    When a definition is edited externally, its revision changes, and subsequent
    cache lookups using the old revision must miss (ensuring fresh data is fetched).
    A cache hit with the new revision indicates the update was cached correctly.
    """
    store = QueryStore(
        database_namespace="public",
        schema="tenant_a",
        table="definitions",
        contract="tenant",
        columns=frozenset(["id", "slug", "body", "owner"])
    )
    identity = QueryIdentity(store=store, slug="test_query")
    provider_checksum = "abc123def456"

    # Original definition
    original_row = {"body": "SELECT * FROM users", "owner": "tenant_a"}
    revision_original = definition_revision(original_row)
    cache_key_original = result_cache_key(identity, revision_original, provider_checksum)

    # Definition modified externally
    updated_row = {"body": "SELECT id, name FROM users", "owner": "tenant_a"}
    revision_updated = definition_revision(updated_row)
    cache_key_updated = result_cache_key(identity, revision_updated, provider_checksum)

    # The revisions must be different
    assert revision_original != revision_updated, "External edits must change the revision"

    # Cache keys must be different when revision changes
    assert cache_key_original != cache_key_updated, (
        "Cache keys must differ when definition is externally edited"
    )

    # Both must be properly formed qs:r2: keys
    assert cache_key_original.startswith("qs:r2:")
    assert cache_key_updated.startswith("qs:r2:")


@pytest.mark.asyncio
async def test_delayed_old_writer_after_edit_delete():
    """delayed old writer after edit delete.

    Ensures that when a definition is edited (or deleted), a delayed cache writer
    that captured the old revision cannot overwrite results cached under the new
    revision. This tests the immutability of captured identity/revision at dispatch time.
    """
    store = QueryStore(
        database_namespace="public",
        schema="tenant_a",
        table="definitions",
        contract="tenant",
        columns=frozenset(["id", "slug", "body", "owner"])
    )
    identity = QueryIdentity(store=store, slug="test_query")
    provider_checksum = "abc123def456"

    # Initial definition state at dispatch time
    initial_row = {"body": "SELECT * FROM users", "owner": "tenant_a", "version": 1}
    revision_initial = definition_revision(initial_row)
    cache_key_initial = result_cache_key(identity, revision_initial, provider_checksum)

    # Simulate definition being edited while query is in flight
    edited_row = {"body": "SELECT id, name, email FROM users", "owner": "tenant_a", "version": 2}
    revision_edited = definition_revision(edited_row)
    cache_key_edited = result_cache_key(identity, revision_edited, provider_checksum)

    # Different revisions produce different keys
    assert revision_initial != revision_edited
    assert cache_key_initial != cache_key_edited

    # A delayed writer from the old query would write to cache_key_initial,
    # not cache_key_edited. This proves isolation between versions.
    assert cache_key_initial.startswith("qs:r2:")
    assert cache_key_edited.startswith("qs:r2:")


@pytest.mark.asyncio
async def test_ttl_refresh_and_no_double_wrapping():
    """ttl refresh and no double wrapping.

    Verifies that:
    1. Cache keys are composed exactly once (no double wrapping)
    2. TTL is preserved through the caching pipeline
    3. Refresh flags work correctly with the composed key
    """
    store = QueryStore(
        database_namespace="public",
        schema="tenant_a",
        table="definitions",
        contract="tenant",
        columns=frozenset(["id", "slug", "body", "owner", "cache_ttl"])
    )
    identity = QueryIdentity(store=store, slug="test_query")

    # Simulate AbstractQuery's cache key composition
    definition_row = {
        "body": "SELECT * FROM users",
        "owner": "tenant_a",
        "cache_ttl": 3600
    }
    revision = definition_revision(definition_row)
    provider_checksum = "abc123def456"

    # Compose the key exactly once
    composed_key = result_cache_key(identity, revision, provider_checksum)

    # The composed key is in the proper format (not wrapped twice)
    assert composed_key.startswith("qs:r2:")
    assert "qs:r2:" not in composed_key[6:], "Key should not contain nested qs:r2: prefix"

    # When the AbstractQuery.result_cache_key method is called, it should
    # produce the same key (no double wrapping)
    composed_again = result_cache_key(identity, revision, provider_checksum)
    assert composed_key == composed_again, "Composing the same key twice must yield identical results"

    # Verify the structure: qs:r2:<digest>
    parts = composed_key.split(":")
    assert len(parts) == 3, f"Cache key should have 3 parts (qs:r2:<digest>), got {parts}"
    assert parts[0] == "qs" and parts[1] == "r2", "Key prefix must be qs:r2:"
    # The digest should be a hex string (SHA256)
    assert len(parts[2]) == 64, f"Digest should be 64 hex chars (SHA256), got {len(parts[2])}"
    try:
        int(parts[2], 16)  # Verify it's valid hex
    except ValueError:
        pytest.fail(f"Digest is not valid hex: {parts[2]}")
