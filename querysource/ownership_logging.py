"""Ownership diagnostics and implicit artifact naming for per-tenant queries.

This module provides two small, dependency-free formatters used at every
query execution boundary (HTTP, direct Python API, MultiQS child, scheduled
job, remote worker dispatch) so ownership context is always derived from the
*executed definition* — never guessed from a mutable request URL or an
output alias:

- ``ownership_fields``: stable owner/schema/table/slug identifiers for
  timing/failure events and audit logs. Never includes credentials, raw SQL,
  or any other mutable request field.
- ``implicit_artifact_name``: namespaces generated (implicit) tenant output
  artifacts by owner + execution id, while leaving explicitly configured
  destinations (a filename/S3 key/table the caller chose on purpose)
  completely unchanged.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from querysource.tenants import QueryIdentity, QueryStore


def ownership_fields(
    identity: QueryIdentity | QueryStore | Mapping[str, Any] | None,
) -> Mapping[str, str]:
    """Return stable owner/schema/table/slug fields without credentials or SQL.

    Accepts every shape ownership context travels in across this feature's
    execution boundaries:

    - A ``QueryIdentity`` (``store`` + ``slug``) — set on
      ``AbstractQuery._definition_identity`` once a stored definition has
      been loaded (HTTP, direct API, MultiQS child).
    - A bare ``QueryStore`` (``schema``/``table``/... — no ``slug``) — the
      resolved store itself, e.g. a MultiQS child's ``resolved_stores``
      entry or a RemoteExecutor's ``store`` parameter, where the caller
      already has the slug as a separate argument.
    - A ``TenantOwnerEnvelope`` mapping (``schema``/``table``/... — no
      ``slug``) — the serialized shape carried across scheduler jobs
      (TASK-730) and remote worker dispatch (TASK-728).
    - ``None`` — no definition resolved yet (e.g. an inline/legacy query
      with no stored identity). Returns an empty mapping so callers never
      have to special-case "no identity" themselves.

    Args:
        identity: The executed definition's identity, its resolved store,
            an owner envelope, or None.

    Returns:
        A mapping of stable identifiers suitable for logging and event
        contexts. Never includes connection secrets, raw SQL, or mutable
        request fields.
    """
    if identity is None:
        return {}
    # QueryIdentity: has both `.store` and `.slug`.
    store = getattr(identity, "store", None)
    if store is not None:
        return {
            "owner": store.schema,
            "schema": store.schema,
            "table": store.table,
            "slug": identity.slug,
        }
    # Bare QueryStore: has `.schema`/`.table` directly, no `.slug`.
    schema_attr = getattr(identity, "schema", None)
    table_attr = getattr(identity, "table", None)
    if schema_attr is not None and not isinstance(identity, Mapping):
        return {"owner": schema_attr, "schema": schema_attr, "table": table_attr}
    if isinstance(identity, Mapping):
        # TenantOwnerEnvelope (or any owner-shaped mapping): no slug here —
        # the caller already has it as a separate argument.
        fields = {}
        schema = identity.get("schema")
        table = identity.get("table")
        if schema is not None:
            fields["owner"] = schema
            fields["schema"] = schema
        if table is not None:
            fields["table"] = table
        return fields
    return {}


def implicit_artifact_name(
    identity: QueryIdentity | Mapping[str, Any] | None,
    request_id: str | None,
    filename: str | None,
) -> str | None:
    """Namespace generated tenant artifacts; preserve explicit destinations.

    Only ever called for an *implicit* output name (one QuerySource itself
    generated — e.g. defaulting to the query slug when no filename was
    explicitly requested). An explicitly configured destination — a
    filename, database table, or S3 bucket/key the caller chose — is never
    touched: this function is not in that call path at all (AC-3), and as
    defense-in-depth it also passes through unchanged anything that already
    looks like a path or a URI (contains ``/`` or a ``scheme://``).

    Args:
        identity: The executed definition's identity, an owner envelope, or
            None (falls back to the original filename unchanged).
        request_id: A unique request/execution id (one per query object;
            see ``AbstractQuery._execution_id``) used to keep two
            concurrent runs of the identical slug from colliding.
        filename: The implicit base filename (e.g. the query slug).

    Returns:
        ``f"{schema}_{request_id}_{filename}"`` for an implicit tenant
        artifact, or ``filename`` unchanged when there is no resolved
        identity, no request id, or the filename already looks like an
        explicit path/URI.
    """
    if not filename or identity is None or not request_id:
        return filename
    if "/" in filename or "://" in filename:
        return filename
    owner_fields = ownership_fields(identity)
    schema = owner_fields.get("schema")
    if not schema:
        return filename
    return f"{schema}_{request_id}_{filename}"
