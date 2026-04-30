"""
querysource.auth._resource_types — ResourceType shim for PBAC enforcement.

Tries to import ResourceType from navigator-auth (available once TASK-640
upstream PR is merged and navigator-auth >= 0.20.0 is pinned). Falls back to
a plain-string stand-in that preserves the same attribute interface.

Usage::

    from querysource.auth._resource_types import ResourceType
    await self._enforce_pbac(request, ResourceType.SLUG, slug, "slug:execute")

Once TASK-640 lands, the try-branch is taken automatically and this shim
becomes a transparent pass-through — no handler changes required.
"""
from __future__ import annotations

try:
    from navigator_auth.abac.policies.resources import ResourceType
    # Validate that the expected QS-specific attrs are present.
    # Raises AttributeError if the upstream PR hasn't landed yet.
    _check = (
        ResourceType.SLUG,       # noqa: WPS226
        ResourceType.DATASOURCE,
        ResourceType.DRIVER,
        ResourceType.RAW_QUERY,
    )
    del _check
except (ImportError, AttributeError):
    # navigator-auth not installed, or SLUG/DATASOURCE/DRIVER/RAW_QUERY not
    # yet added upstream. Use a lightweight string-based stand-in.
    class _StringResourceType(str):  # type: ignore[misc]
        """String subclass so isinstance checks still pass for str."""
        __slots__ = ()

    class ResourceType:  # type: ignore[no-redef]
        """String-shim ResourceType for QuerySource PBAC enforcement.

        Attributes mirror the planned navigator-auth ResourceType enum values
        (FEAT-091 TASK-640 upstream PR). Switch to the real enum once merged.
        """
        SLUG = _StringResourceType("slug")
        DATASOURCE = _StringResourceType("datasource")
        DRIVER = _StringResourceType("driver")
        RAW_QUERY = _StringResourceType("raw_query")


__all__ = ("ResourceType",)
