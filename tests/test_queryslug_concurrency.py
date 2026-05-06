"""Concurrency regression for the ``QueryModel.Meta.connection`` race.

Background
----------
``QueryModel.Meta.connection`` is a class attribute. The pre-migration code
mutated it from every concurrent ``get_slug()`` caller and cleared it in a
``finally`` block. Under FlowTask concurrency this intermittently raised
``ConnectionMissing("Missing Connection for Model: <class …QueryModel>")``
because one coroutine's ``finally`` could clear the slot before another
coroutine read it.

These tests exercise the **real** ``QueryModel.get`` (the one shipped in
asyncdb after the ``_connection=`` migration) using a stub driver, so they
run anywhere — no Postgres needed.

Two assertions:

* ``test_new_pattern_is_race_free`` — passing ``_connection=conn`` keeps
  every call independent. No matter how the scheduler interleaves them,
  no ``ConnectionMissing`` ever fires.
* ``test_old_pattern_races_under_yields`` — the legacy
  set-Meta-then-call pattern, with an ``await asyncio.sleep(0)`` between
  the set and the read, reliably triggers the race. This is the negative
  control: it proves the test setup is sensitive enough to catch a
  regression if the migration ever drifts back.
"""
from __future__ import annotations

import asyncio

import pytest

from asyncdb.exceptions import ConnectionMissing
from querysource.models import QueryModel


CONCURRENCY = 50
ITERATIONS = 20


class StubDriver:
    """Minimal stand-in for the asyncdb pg driver.

    Implements only the surface ``Model.get`` touches: ``is_connected()``,
    ``connection()``, and ``_get_``. ``_get_`` yields once via
    ``asyncio.sleep(0)`` so the scheduler can interleave coroutines —
    that's what makes any race observable.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        # Truthy so ``Model.get`` passes the ``if not conn`` check.
        self._connection = object()

    def is_connected(self) -> bool:
        return True

    async def connection(self):
        return self

    async def _get_(self, _model, **kwargs):
        # Yield to give the scheduler a chance to interleave us with
        # peer coroutines. Without this yield no race could ever fire
        # in either pattern; with it, the legacy pattern races and the
        # new pattern still doesn't.
        await asyncio.sleep(0)
        return {"query_slug": kwargs.get("query_slug", "?")}


@pytest.fixture(autouse=True)
def _reset_meta_connection():
    """Ensure tests don't leak ``Meta.connection`` state into each other."""
    QueryModel.Meta.connection = None
    yield
    QueryModel.Meta.connection = None


@pytest.mark.asyncio
async def test_new_pattern_is_race_free():
    """``_connection=conn`` keeps every call independent of class state."""

    async def safe_call(driver: StubDriver):
        return await QueryModel.get(query_slug="x", _connection=driver)

    for _ in range(ITERATIONS):
        drivers = [StubDriver(f"d{i}") for i in range(CONCURRENCY)]
        results = await asyncio.gather(
            *[safe_call(d) for d in drivers], return_exceptions=True
        )
        missing = [r for r in results if isinstance(r, ConnectionMissing)]
        assert not missing, (
            f"Race regression! {len(missing)}/{len(results)} calls raised "
            f"ConnectionMissing under the new ``_connection=`` API."
        )


@pytest.mark.asyncio
async def test_old_pattern_races_deterministically():
    """Negative control: the legacy pattern races, proving the test bites.

    Uses two events to force the exact interleaving that explains the
    production failure:

        1. A enters the ``with`` block and sets ``Meta.connection = drvA``.
        2. A yields.
        3. B sets ``Meta.connection = drvB``, runs ``QueryModel.get`` to
           completion, and its ``finally`` sets ``Meta.connection = None``.
        4. A resumes and calls ``QueryModel.get`` — sees ``None`` and
           raises ``ConnectionMissing`` (the production symptom).

    This is the same shape FlowTask hit under load, just compressed into
    two coroutines instead of dozens.
    """
    a_set_done = asyncio.Event()
    b_done = asyncio.Event()

    async def coroutine_a():
        # Pre-migration pattern: mutate the class-level slot, then call
        # ``QueryModel.get`` with no ``_connection=`` so it falls back to
        # ``cls.Meta.connection``.
        QueryModel.Meta.connection = StubDriver("A")
        a_set_done.set()
        # Hold here until B has fully completed (and cleared the slot
        # in its ``finally``). This is the suspension point that exists
        # in real life as the await on ``_get_`` / ``__aenter__`` /
        # async logging / network roundtrip.
        await b_done.wait()
        try:
            return await QueryModel.get(query_slug="x")
        finally:
            QueryModel.Meta.connection = None

    async def coroutine_b():
        await a_set_done.wait()
        QueryModel.Meta.connection = StubDriver("B")
        try:
            return await QueryModel.get(query_slug="x")
        finally:
            # Pre-migration ``get_slug`` cleared the slot here.
            QueryModel.Meta.connection = None
            b_done.set()

    results = await asyncio.gather(
        coroutine_a(), coroutine_b(), return_exceptions=True
    )

    # B should succeed; A should fail with ConnectionMissing because B's
    # ``finally`` cleared ``Meta.connection`` before A's get() read it.
    a_result, b_result = results
    assert not isinstance(b_result, BaseException), (
        f"B unexpectedly failed: {b_result!r}"
    )
    assert isinstance(a_result, ConnectionMissing), (
        f"Expected legacy pattern to race with ConnectionMissing, "
        f"got: {a_result!r}"
    )
