"""Verify full CSV responses do not advertise partial byte ranges."""

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from querysource.outputs.output import DataOutput


@pytest.mark.asyncio
@pytest.mark.parametrize("body_size", [421, 16384, 16385, 32769])
async def test_full_csv_stream_has_no_content_range(body_size: int) -> None:
    value = "x" * (body_size - len(b"name\r\n\r\n"))
    expected = f"name\r\n{value}\r\n".encode("utf-8")

    async def handler(request: web.Request) -> web.StreamResponse:
        output = DataOutput(
            request,
            query=[{"name": value}],
            ctype="csv",
            slug="pokemon_warehouse_list_count",
            writer_options={"delimiter": ",", "quoting": "minimal"},
        )
        return await output.response()

    app = web.Application()
    app.router.add_get("/csv", handler)
    async with TestClient(TestServer(app)) as client:
        async with client.get(
            "/csv", headers={"Accept-Encoding": "gzip, deflate, br"}
        ) as response:
            assert response.status == 200
            assert response.content_type == "text/csv"
            assert response.content_length == body_size
            assert await response.read() == expected
            assert "Content-Range" not in response.headers
