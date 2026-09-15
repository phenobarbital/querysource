"""Pure identity helpers for result-cache keys and definition revisions.

Both functions are pure (no I/O, no database access) so every cache path —
HTTP execution, threaded writers, refresh — shares the same namespace and
revision algorithm (spec §2 "Cache, jobs, remote execution and outputs").

Note: this module intentionally does NOT use ``from __future__ import
annotations``. See ``querysource/tenant_models.py`` for why: this project's
Cython ``datamodel`` validator resolves field types at runtime, and this
module is imported by ``querysource/tenants.py``/``querysource/repositories/
definitions.py`` which construct ``datamodel.BaseModel`` instances; keeping
the same convention across the tenant module family avoids surprises.
"""
import hashlib
import json
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from querysource.tenants import QueryIdentity


def _canonicalize(value: Any) -> Any:
    """Recursively reduce a persisted value to a canonical, hashable shape.

    Explicit encodings for dates, nested mappings, arrays and nulls (not
    just ``updated_at``) per spec §2: "Revision is a stable hash of
    canonical persisted fields, with ordered keys and explicit date/array/
    null encodings, not just updated_at."
    """
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _canonicalize(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_canonicalize(item) for item in value]
    if isinstance(value, (str, int, float, bool)):
        return value
    # Fallback for any other type (Decimal, UUID, etc.): stable string form.
    return str(value)


def _canonical_json(value: Any) -> str:
    """Render ``value`` as deterministic JSON: sorted keys, compact separators."""
    return json.dumps(_canonicalize(value), sort_keys=True, separators=(",", ":"))


def definition_revision(row: Mapping[str, Any]) -> str:
    """Hash canonical persisted data before runtime mutation.

    Args:
        row: Persisted fields as they exist in storage (e.g. the dict
            returned by ``TenantQueryDefinition.to_dict()``), never a
            runtime-mutated ``QueryModel``.

    Returns:
        A stable, deterministic sha256 hex digest of the canonicalized row.
    """
    canonical = _canonical_json(dict(row))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def result_cache_key(identity: QueryIdentity, revision: str, provider_checksum: str) -> str:
    """Return the ``qs:r2`` key; never use an unqualified compatibility read.

    Per spec §2: ``qs:r2:<sha256(canonical tuple)>`` where the tuple is the
    physical store identity (database namespace, schema, table — the same
    triple ``TenantRegistry`` deduplicates stores on), the slug, the
    definition revision and the existing provider checksum. Same physical-
    store aliases therefore share identity; different owners with
    identical SQL do not.

    Args:
        identity: The resolved owner/slug identity for this definition.
        revision: The value returned by :func:`definition_revision`.
        provider_checksum: The existing, provider-specific SQL checksum.

    Returns:
        A ``qs:r2:<hex digest>`` cache key. Never includes credentials.
    """
    store = identity.store
    tuple_repr = (
        store.database_namespace,
        store.schema,
        store.table,
        identity.slug,
        revision,
        provider_checksum,
    )
    canonical = _canonical_json(list(tuple_repr))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"qs:r2:{digest}"
