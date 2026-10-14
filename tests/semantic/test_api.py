"""Tests for the public semantic Python API."""
from __future__ import annotations

import pytest

from querysource import semantic


@pytest.mark.asyncio
async def test_api_compile_returns_compiled_query(
    sidemantic_available, semantic_definitions, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Compile without opening a datasource connection or executing a query."""
    from asyncdb import AsyncDB

    def fail_connection(*args: object, **kwargs: object) -> object:
        raise AssertionError("compile() must not open a datasource connection")

    monkeypatch.setattr(AsyncDB, "connection", fail_connection)

    compiled = await semantic.compile("epson.sales", metrics=["revenue"])

    assert compiled.model_version
    assert compiled.parameters["slug"] == "epson.sales"
