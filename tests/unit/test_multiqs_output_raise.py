"""Unit tests for MultiQS Output fail-fast raise behavior.

FEAT-146, TASK-713. ``ThreadQuery`` (real threading + DB query execution) is
replaced with a fake that synchronously pushes a DataFrame onto the shared
queue, so these tests exercise the real ``MultiQS.query()`` Output loop
(Step 5) without touching any database or thread.
"""
from unittest.mock import patch

import pandas as pd
import pytest

import querysource.queries.multi as multiqs_module
from querysource.exceptions import DataNotFound, OutputError
from querysource.queries.multi import MultiQS


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


def _make_multiqs(output_steps):
    return MultiQS(query={"queries": {"q": {"slug": "s"}}, "Output": output_steps})


def _destination_that_raises(exc):
    """Build a fake destination class whose .run() raises ``exc``."""

    class _Dest:
        def __init__(self, data=None, **kwargs):
            self.data = data

        async def run(self):
            raise exc

    return _Dest


class _SucceedingDestination:
    ran = False

    def __init__(self, data=None, **kwargs):
        self.data = data

    async def run(self):
        type(self).ran = True
        return self.data


@pytest.mark.asyncio
async def test_multiqs_output_failure_raises():
    """A destination that raises causes query() to raise OutputError."""
    mqs = _make_multiqs([{"FailingDest": {}}])
    with patch.object(multiqs_module, "ThreadQuery", _FakeThread), patch(
        "querysource.outputs.destinations.get_destination",
        return_value=_destination_that_raises(ValueError("boom")),
    ), pytest.raises(OutputError):
        await mqs.query()


@pytest.mark.asyncio
async def test_multiqs_output_failure_is_fail_fast():
    """A failure in the first destination prevents the second from running."""
    _SucceedingDestination.ran = False
    mqs = _make_multiqs([{"FailingDest": {}}, {"SecondDest": {}}])

    def _get_destination(name):
        if name == "FailingDest":
            return _destination_that_raises(ValueError("boom"))
        return _SucceedingDestination

    with patch.object(multiqs_module, "ThreadQuery", _FakeThread), patch(
        "querysource.outputs.destinations.get_destination",
        side_effect=_get_destination,
    ), pytest.raises(OutputError):
        await mqs.query()
    assert _SucceedingDestination.ran is False


@pytest.mark.asyncio
async def test_output_error_carries_step_name():
    """The raised OutputError carries the failing destination's step name."""
    mqs = _make_multiqs([{"TableOutput": {}}])
    with patch.object(multiqs_module, "ThreadQuery", _FakeThread), patch(
        "querysource.outputs.destinations.get_destination",
        return_value=_destination_that_raises(ValueError("boom")),
    ), pytest.raises(OutputError) as exc_info:
        await mqs.query()
    assert exc_info.value.step_name == "TableOutput"


@pytest.mark.asyncio
async def test_datanotfound_from_destination_propagates_unchanged():
    """DataNotFound raised by a destination keeps its original meaning."""
    mqs = _make_multiqs([{"FailingDest": {}}])
    with patch.object(multiqs_module, "ThreadQuery", _FakeThread), patch(
        "querysource.outputs.destinations.get_destination",
        return_value=_destination_that_raises(DataNotFound("no rows")),
    ), pytest.raises(DataNotFound):
        await mqs.query()


@pytest.mark.asyncio
async def test_preexisting_output_error_gets_step_name_when_missing():
    """A destination that already raises OutputError (no step_name set, as
    TableOutput/PgOutput does today) gets step_name filled in by MultiQS."""
    mqs = _make_multiqs([{"TableOutput": {}}])
    with patch.object(multiqs_module, "ThreadQuery", _FakeThread), patch(
        "querysource.outputs.destinations.get_destination",
        return_value=_destination_that_raises(OutputError("duplicate key value ...")),
    ), pytest.raises(OutputError) as exc_info:
        await mqs.query()
    assert exc_info.value.step_name == "TableOutput"
