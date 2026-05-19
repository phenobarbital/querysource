import asyncio

import pandas as pd
from aiohttp import web

from ...obj import QueryObject
from .base import ThreadSource


class ThreadQuery(ThreadSource):
    """ThreadQuery runs a QueryObject in a separate thread.

    Executes a slug-based or raw QuerySource query within the thread's own
    asyncio event loop.  Results are placed directly into the shared queue by
    :class:`~querysource.queries.obj.QueryObject`; therefore ``fetch()``
    returns ``None`` to signal the base class not to perform an additional
    queue-put step.
    """

    def __init__(
        self,
        name: str,
        query: dict,
        request: web.Request,
        queue: asyncio.Queue,
    ):
        super().__init__(name, query, request, queue)
        self._query = query
        self._request = request

    @property
    def slug(self):
        """Return the query slug.

        Before ``fetch()`` runs this accesses the dict; after ``fetch()`` the
        dict has been replaced with a :class:`~querysource.queries.obj.QueryObject`
        that exposes ``.slug`` directly.
        """
        if isinstance(self._query, dict):
            return self._query.get('slug', self._name)
        return self._query.slug

    async def fetch(self) -> pd.DataFrame | None:
        """Build and execute the QueryObject.

        :class:`~querysource.queries.obj.QueryObject` already puts the result
        into ``self._queue`` at the end of its ``query()`` call, so this
        method returns ``None`` to prevent the base :meth:`ThreadSource.run`
        from performing a duplicate queue-put.

        Returns:
            ``None`` — the queue is written by :class:`QueryObject` internally.

        Raises:
            :class:`~querysource.exceptions.QueryException`: On provider build
                or query execution failure.
        """
        loop = asyncio.get_event_loop()
        self._query = QueryObject(
            self._name,
            self._query,
            queue=self._queue,
            request=self._request,
            loop=loop,
        )
        await self._query.build_provider()
        await self._query.query()
        # QueryObject.query() already queued the result — return None so that
        # ThreadSource.run() skips its own queue-put step.
        return None
