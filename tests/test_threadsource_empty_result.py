"""An empty child result is "no data" (HTTP 204), not a failure."""
import asyncio
import logging

import pytest
from asyncdb.exceptions import NoDataFound

from querysource.exceptions import DataNotFound
from querysource.queries.multi.sources.base import ThreadSource


class _RaisingSource(ThreadSource):
    """ThreadSource whose fetch raises a fixed exception."""

    def __init__(self, exc: Exception) -> None:
        super().__init__("empty_slug", {}, None, asyncio.Queue())  # type: ignore[arg-type]
        self._raise = exc

    async def fetch(self):
        raise self._raise


@pytest.mark.parametrize(
    "exc", [DataNotFound("DB: Empty Result"), NoDataFound("empty")]
)
def test_empty_result_is_not_logged_as_error(exc, caplog):
    """DataNotFound/NoDataFound is kept in ``exc`` without an ERROR log."""
    source = _RaisingSource(exc)
    with caplog.at_level(logging.DEBUG):
        source.start()
        source.join()
    assert source.exc is exc
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_real_failure_is_still_logged_as_error(caplog):
    """Any other exception is still reported as a failure."""
    source = _RaisingSource(RuntimeError("boom"))
    with caplog.at_level(logging.DEBUG):
        source.start()
        source.join()
    assert isinstance(source.exc, RuntimeError)
    assert [r for r in caplog.records if r.levelno >= logging.ERROR]
