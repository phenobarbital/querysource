"""Integration tests: MultiQuery Output failures surface as HTTP 422/500.

FEAT-146, TASK-715. Drives the REAL HTTP stack end-to-end — aiohttp
TestClient -> QueryHandler.query() -> MultiQS.query() -> the Output loop ->
the OutputError -> HTTP status mapping -> the JSON response body — the exact
path Carlos needs for navigator-front-next to see real errors instead of a
silent "200 + swallow".

Only the non-deterministic / external collaborators are faked, matching the
Test Data / Fixtures section of the spec and the pattern already used by
``tests/unit/test_multiqs_output_raise.py``:

- ``ThreadQuery``: replaced with a fake that synchronously seeds the result
  queue instead of spawning a real thread / running a real DB query.
- ``get_destination``: replaced to control what the Output step does,
  instead of touching a real Postgres table.
- ``DataOutput`` (success path only): replaced with a minimal JSON
  responder. Its formatting internals are unrelated to this feature; what
  matters here is that Output ran exactly once and the handler returned 200.

No live Postgres or network access is required.
"""
from unittest.mock import patch

import pandas as pd
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import querysource.queries.multi as multiqs_module
from querysource.exceptions import OutputError
from querysource.handlers.multi import QueryHandler


class _FakeThread:
    """Stand-in for ThreadQuery: synchronously seeds the result queue."""

    def __init__(self, name, query, request, queue, remote_config=None):
        self._name = name
        self._queue = queue
        self.exc = None
        self.slug = query.get("slug", name)

    def is_alive(self):
        return False

    def start(self):
        self._queue.put_nowait({self._name: pd.DataFrame({"a": [1, 2, 3]})})

    def join(self, timeout=None):
        pass


def _destination_that_raises(exc):
    """Build a fake destination class whose .run() raises ``exc``."""

    class _Dest:
        def __init__(self, data=None, **kwargs):
            self.data = data

        async def run(self):
            raise exc

    return _Dest


class _SucceedingDestination:
    def __init__(self, data=None, **kwargs):
        self.data = data

    async def run(self):
        return self.data


class _FakeDataOutputResponse:
    """Minimal stand-in for DataOutput. DataOutput's own formatting/writer
    internals are unrelated to FEAT-146 (already covered by other test
    suites); this only proves the seam this feature touches — Output ran
    exactly once and the handler still returns 200 on a healthy pipeline."""

    def __init__(self, request, query, ctype=None, slug=None, **kwargs):
        self._query = query

    async def response(self):
        rows = len(self._query) if hasattr(self._query, "__len__") else 0
        return web.json_response({"rows": rows, "status": "ok"})


def _body(output_steps):
    return {"queries": {"q": {"slug": "s"}}, "Output": output_steps}


@pytest.fixture
async def mq_client():
    """aiohttp TestClient with a bare QueryHandler route (PBAC disabled)."""
    app = web.Application()
    mq = QueryHandler()
    app.router.add_post("/api/v3/queries", mq.query)
    app.router.add_post("/api/v3/queries{meta}", mq.query)
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    try:
        yield client
    finally:
        await client.close()


class TestOutputFailurePropagation:
    """Guards against the historical "200 + swallow" regression."""

    @pytest.mark.asyncio
    async def test_pk_collision_returns_422_with_detail(self, mq_client):
        """Duplicate-key/PK collision -> 422 with the real detail in body."""

        class IntegrityError(Exception):
            """Named to match sqlalchemy.exc.IntegrityError for classify_output_error()."""

        cause = IntegrityError(
            'duplicate key value violates unique constraint "stores_pkey" '
            "DETAIL: Key (id)=(1) already exists."
        )

        def _get_destination(_name):
            return _destination_that_raises(cause)

        with patch.object(multiqs_module, "ThreadQuery", _FakeThread), patch(
            "querysource.outputs.destinations.get_destination",
            side_effect=_get_destination,
        ):
            resp = await mq_client.post(
                "/api/v3/queries", json=_body([{"TableOutput": {}}])
            )
        assert resp.status == 422
        body = await resp.json()
        assert "duplicate key value" in body["error"]
        assert "TableOutput" in resp.headers.get("X-Output-Errors", "")

    @pytest.mark.asyncio
    async def test_unconsumed_columns_returns_422_with_detail(self, mq_client):
        """Missing/extra column ('Unconsumed column names') -> 422 with detail.

        Modeled on ``TableOutput/postgres.py``'s real shape: a column
        mismatch surfaces as an ``OutputError`` chained from the driver's
        ``ProgrammingError`` (``raise OutputError(...) from err``).
        """

        class ProgrammingError(Exception):
            """Named to match sqlalchemy.exc.ProgrammingError for classify_output_error()."""

        cause = ProgrammingError('column "extra_col" of relation "stores" does not exist')
        oe = OutputError(
            "There are missing columns on Table stores.\n"
            "Error was: Unconsumed column names: extra_col"
        )
        oe.__cause__ = cause

        def _get_destination(_name):
            return _destination_that_raises(oe)

        with patch.object(multiqs_module, "ThreadQuery", _FakeThread), patch(
            "querysource.outputs.destinations.get_destination",
            side_effect=_get_destination,
        ):
            resp = await mq_client.post(
                "/api/v3/queries", json=_body([{"TableOutput": {}}])
            )
        assert resp.status == 422
        body = await resp.json()
        assert "Unconsumed column names" in body["error"]

    @pytest.mark.asyncio
    async def test_infra_error_returns_500(self, mq_client):
        """Connection/timeout (infrastructure) failure -> 500."""

        class OperationalError(Exception):
            """Named to match sqlalchemy.exc.OperationalError for classify_output_error()."""

        cause = OperationalError("could not connect to server: Connection refused")

        def _get_destination(_name):
            return _destination_that_raises(cause)

        with patch.object(multiqs_module, "ThreadQuery", _FakeThread), patch(
            "querysource.outputs.destinations.get_destination",
            side_effect=_get_destination,
        ):
            resp = await mq_client.post(
                "/api/v3/queries", json=_body([{"TableOutput": {}}])
            )
        assert resp.status == 500

    @pytest.mark.asyncio
    async def test_successful_output_still_returns_200(self, mq_client):
        """A healthy MultiQuery + Output pipeline still returns 200 (no regression)."""

        def _get_destination(_name):
            return _SucceedingDestination

        with patch.object(multiqs_module, "ThreadQuery", _FakeThread), patch(
            "querysource.outputs.destinations.get_destination",
            side_effect=_get_destination,
        ), patch("querysource.handlers.multi.DataOutput", _FakeDataOutputResponse):
            resp = await mq_client.post(
                "/api/v3/queries", json=_body([{"TableOutput": {}}])
            )
        assert resp.status == 200
