"""QS residual stage: both result paths, cost guard, empty result (spec AC12)."""
from __future__ import annotations

import json

import pytest

from querysource import conf
from querysource.exceptions import DataNotFound
from querysource.qsurl import QSUrlError, ResidualPlan
from querysource.queries.qs import QS

ROWS = [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}, {"a": 3, "b": "x"}, {"a": 4, "b": "z"}]
PLAN = ResidualPlan(filter={"and": [{"column": "b", "expression": "==", "value": "x"}]})


class _FakeProvider:
    """Minimal stand-in for a built BaseProvider, exposing only what QS.query() touches."""

    __name__ = "fake"

    def __init__(self, rows):
        self._rows = rows

    def refresh(self):
        return False

    def accepts(self):
        return None

    def checksum(self):
        return "c"

    async def query(self):
        return self._rows, None


class _FakeConnection:
    """Minimal stand-in for QueryConnection, exposing only what QS.query() touches."""

    def __init__(self, *, cached: bool = False, cached_payload: str | None = None):
        self._cached = cached
        self._cached_payload = cached_payload

    async def in_cache(self, _cache_key):
        return self._cached

    async def from_cache(self, _cache_key):
        return self._cached_payload

    async def dispose(self, _conn):
        return None


async def _identity_output_format(result, _error=None):
    return result


def _qs(plan, *, rows=None, is_cached: bool = False, cached_payload: str | None = None) -> QS:
    qs = QS(slug="s", residual=plan)
    qs._qs = _FakeProvider(rows if rows is not None else ROWS)
    qs._conn = None
    qs.connection = _FakeConnection(cached=is_cached, cached_payload=cached_payload)
    qs.is_cached = is_cached
    qs._output_format = _identity_output_format
    # No definition was loaded (no build_provider() call), so the real
    # result_cache_key() has no QueryIdentity to compose a key from; this
    # task only tests the residual stage, not cache-key composition.
    qs.result_cache_key = lambda _checksum: "test-cache-key"
    return qs


async def test_provider_path_applies_plan():
    qs = _qs(PLAN)
    result = await qs.query()
    assert result == [{"a": 1, "b": "x"}, {"a": 3, "b": "x"}]


async def test_cache_hit_applies_plan():
    payload = json.dumps(ROWS)
    qs = _qs(PLAN, is_cached=True, cached_payload=payload)
    result = await qs.query()
    assert result == [{"a": 1, "b": "x"}, {"a": 3, "b": "x"}]


async def test_cost_guard(monkeypatch):
    monkeypatch.setattr(conf, "QSURL_MAX_RESIDUAL_ROWS", 3)
    qs = _qs(PLAN)
    with pytest.raises(QSUrlError) as exc:
        await qs.query()
    assert exc.value.kind == "cost"


async def test_empty_after_residual_raises():
    empty_plan = ResidualPlan(filter={"and": [{"column": "b", "expression": "==", "value": "nope"}]})
    qs = _qs(empty_plan)
    with pytest.raises(DataNotFound):
        await qs.query()


async def test_no_plan_is_a_noop():
    qs = _qs(None)
    result = await qs.query()
    assert result == ROWS


def test_residual_not_in_kwargs():
    assert "residual" not in QS(slug="s", residual=PLAN).kwargs
