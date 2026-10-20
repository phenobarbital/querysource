"""Dependency analysis for explicit query moves (FEAT-176).

A saved child inside a multi-query pipeline that omits ``tenant`` resolves against
its parent's owner (see ``MultiQS.resolve_child_owner``). Moving the parent would
therefore re-point every such child at the destination store — silently, because
nothing fails at move time. If the child does not exist there the pipeline breaks
on its next run; if a *different* definition happens to share the slug there, the
pipeline quietly runs the wrong query.

This module finds those references so the move can refuse until the operator pins
them explicitly. It never rewrites ``query_raw`` and never scans SQL with a regex.
"""
import json
import re
from collections.abc import Mapping
from typing import Any

# A selector/slug that is not a literal string (templated, computed) cannot be proven safe.
_DYNAMIC = re.compile(r"\{\{|\{%|\$\{|\$\(")


def _as_mapping(value: Any) -> Mapping[str, Any] | None:
    """Coerce a mapping or a JSON object string to a mapping (None when neither)."""
    if isinstance(value, Mapping):
        return value
    if isinstance(value, (str, bytes)) and value:
        try:
            parsed = json.loads(value)
        except ValueError:
            return None
        return parsed if isinstance(parsed, Mapping) else None
    return None


def analyze_references(provider: Any, query_raw: Any) -> tuple[list[dict], list[dict]]:
    """Find saved-child references whose owner is inherited from this definition.

    An explicit ``tenant`` key (including an explicit ``null``) is a pinned
    reference and never blocks. Unknown or dynamic forms are reported rather than
    guessed at.

    Args:
        provider: The definition's provider; only ``multi`` has saved children.
        query_raw: The raw pipeline definition (mapping or JSON string).

    Returns:
        ``(inherited_references, blockers)``. Each blocker carries a stable
        ``code``, a ``message`` and the ``path`` to fix.
    """
    if provider != "multi":
        return [], []
    inherited: list[dict] = []
    blockers: list[dict] = []
    pipeline = _as_mapping(query_raw)
    if pipeline is None:
        if query_raw in (None, "", b""):
            return [], []
        return [], [
            {
                "code": "move_dependency_analysis_unsupported",
                "message": "The pipeline definition cannot be parsed, so its saved-child references cannot be verified.",
                "path": "query_raw",
            }
        ]
    queries = pipeline.get("queries")
    if queries is not None and not isinstance(queries, Mapping):
        blockers.append(
            {
                "code": "move_dependency_analysis_unsupported",
                "message": "'queries' has an unknown shape; saved-child references cannot be verified.",
                "path": "queries",
            }
        )
        queries = {}
    for alias, cfg in (queries or {}).items():
        path = f"queries.{alias}"
        if not isinstance(cfg, Mapping):
            blockers.append(
                {
                    "code": "move_dependency_analysis_unsupported",
                    "message": f"Entry {alias!r} has an unknown shape.",
                    "path": path,
                }
            )
            continue
        if "slug" not in cfg:
            continue  # raw child: no saved-definition owner involved
        slug = cfg.get("slug")
        tenant = cfg.get("tenant", "<omitted>")
        if not isinstance(slug, str) or not slug or _DYNAMIC.search(slug) or (
            isinstance(tenant, str) and _DYNAMIC.search(tenant)
        ) or (tenant != "<omitted>" and tenant is not None and not isinstance(tenant, str)):
            blockers.append(
                {
                    "code": "move_dependency_analysis_unsupported",
                    "message": f"Entry {alias!r} uses a dynamic slug/tenant form that cannot be analysed.",
                    "path": path,
                }
            )
            continue
        if "tenant" not in cfg:
            inherited.append({"path": path, "slug": slug})
            blockers.append(
                {
                    "code": "move_inherited_references",
                    "message": (
                        f"Child {alias!r} ({slug!r}) inherits its owner; pin 'tenant' explicitly "
                        "(null selects the default store) before moving."
                    ),
                    "path": f"{path}.tenant",
                }
            )
    sources = pipeline.get("sources")
    entries: list[tuple[str, Any]] = []
    if isinstance(sources, list):
        for index, entry in enumerate(sources):
            if isinstance(entry, Mapping):
                entries.extend((f"sources[{index}].{kind}", cfg) for kind, cfg in entry.items())
    elif isinstance(sources, Mapping):
        entries.extend((f"sources.{alias}", cfg) for alias, cfg in sources.items())
    for path, cfg in entries:
        if isinstance(cfg, Mapping) and "slug" in cfg:
            blockers.append(
                {
                    "code": "move_dependency_analysis_unsupported",
                    "message": "A source component references a saved slug in a form that cannot be analysed.",
                    "path": path,
                }
            )
    return inherited, blockers
