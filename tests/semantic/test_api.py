"""Tests for the public semantic Python API."""
from __future__ import annotations

import pytest

from querysource import semantic


@pytest.mark.asyncio
async def test_api_compile_returns_compiled_query(
    sidemantic_available, semantic_definitions, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Compile without executing a query."""
    from querysource.providers.semantic import semanticProvider

    async def fail_query(*args: object, **kwargs: object) -> object:
        raise AssertionError("compile() must not execute a query")

    monkeypatch.setattr(semanticProvider, "query", fail_query)

    compiled = await semantic.compile("epson.sales", metrics=["revenue"])

    assert compiled.model_version
    assert compiled.parameters["slug"] == "epson.sales"
