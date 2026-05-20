"""
ComponentHandler — HTTP handler for MultiQuery component documentation and pipeline validation.

Provides:
  - GET  /api/v3/components  — returns JSON catalog of all registered components
  - POST /api/v3/validate    — validates a MultiQuery pipeline definition payload
"""
from __future__ import annotations

import logging
from dataclasses import asdict

from aiohttp import web

from .abstract import AbstractHandler
from ..queries.multi.registry import ComponentRegistry


class ComponentHandler(AbstractHandler):
    """HTTP handler for component documentation and pipeline validation endpoints.

    Methods:
        list_components: Handle GET /api/v3/components
        validate_pipeline: Handle POST /api/v3/validate
    """

    async def list_components(self, request: web.Request) -> web.Response:
        """Return a JSON array of all registered MultiQuery components.

        Supports an optional ``?category=`` query parameter to filter results
        by component category (e.g. ``Operators``, ``Transformations``).

        Args:
            request: The aiohttp web request.

        Returns:
            200 JSON response containing a list of component information objects.
        """
        try:
            category = request.rel_url.query.get("category")
            catalog = ComponentRegistry.get_catalog()

            if category:
                catalog = [c for c in catalog if c.category == category]

            # Convert dataclasses to plain dicts for JSON serialization
            result = [_info_to_dict(c) for c in catalog]
            return web.Response(
                content_type="application/json",
                body=_dumps(result),
                status=200,
            )
        except Exception as exc:
            self.logger.error("Error in list_components: %s", exc)
            return web.Response(
                content_type="application/json",
                body=_dumps({"error": str(exc)}),
                status=500,
            )

    async def validate_pipeline(self, request: web.Request) -> web.Response:
        """Validate a MultiQuery pipeline definition payload.

        Reads the JSON body from the request and runs syntactic + structural
        validation via :meth:`ComponentRegistry.validate_pipeline`.

        Returns 400 if the request body is not valid JSON.
        Returns 200 with ``{"valid": false, "errors": [...]}`` for invalid pipelines.

        Args:
            request: The aiohttp web request with JSON body.

        Returns:
            200 JSON response with ``{valid: bool, errors: [{step, field, message}]}``.
            400 JSON response if the body is not valid JSON.
        """
        try:
            payload = await request.json()
        except Exception:
            return web.Response(
                content_type="application/json",
                body=_dumps({
                    "valid": False,
                    "errors": [{"step": "", "field": "", "message": "Invalid JSON body"}],
                }),
                status=400,
            )

        try:
            result = ComponentRegistry.validate_pipeline(payload)
            return web.Response(
                content_type="application/json",
                body=_dumps(_validation_result_to_dict(result)),
                status=200,
            )
        except Exception as exc:
            self.logger.error("Error in validate_pipeline: %s", exc)
            return web.Response(
                content_type="application/json",
                body=_dumps({"error": str(exc)}),
                status=500,
            )


# ---------------------------------------------------------------------------
# Private serialization helpers
# ---------------------------------------------------------------------------

def _info_to_dict(info) -> dict:
    """Convert a ComponentInfo dataclass to a plain dict."""
    import dataclasses
    if dataclasses.is_dataclass(info) and not isinstance(info, type):
        result = {}
        for f in dataclasses.fields(info):
            value = getattr(info, f.name)
            result[f.name] = _info_to_dict(value)
        return result
    if isinstance(info, list):
        return [_info_to_dict(i) for i in info]
    return info


def _validation_result_to_dict(result) -> dict:
    """Convert a ValidationResult dataclass to a plain dict."""
    return {
        "valid": result.valid,
        "errors": [
            {"step": e.step, "field": e.field, "message": e.message}
            for e in result.errors
        ],
    }


def _dumps(data) -> bytes:
    """Serialize data to JSON bytes, using orjson if available."""
    try:
        import orjson
        return orjson.dumps(data)
    except ImportError:
        import json
        return json.dumps(data, default=str).encode("utf-8")
