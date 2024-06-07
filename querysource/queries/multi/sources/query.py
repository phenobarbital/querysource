import asyncio
import threading
from aiohttp import web
from ...obj import QueryObject


class ThreadQuery(threading.Thread):
    """ThreadQuery is a class that will run a QueryObject in a separate thread."""
    def __init__(
        self,
        name: str,
        query: dict,
        request: web.Request,
        queue: asyncio.Queue
    ):
        super().__init__()
        self._loop = asyncio.new_event_loop()
        self._queue = queue
        self.exc = None
        # I need to build a QueryObject task, and put arguments on there.
        self._query = QueryObject(
            name,
            query,
            queue=queue,
            request=request,
            loop=self._loop
        )

    def slug(self):
        return self._query.slug

    def run(self):
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(
                self._query.build_provider()
            )
        except Exception as ex:
            self.exc = ex
        try:
            self._loop.run_until_complete(
                self._query.query()
            )
        except Exception as ex:
            print('ERROR >>>> ', ex)
            self.exc = ex
        finally:
            self._loop.close()
