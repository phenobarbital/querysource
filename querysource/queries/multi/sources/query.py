import asyncio
import threading
from aiohttp import web
from ...obj import QueryObject
from ....exceptions import QueryException


class ThreadQuery(threading.Thread):
    """ThreadQuery.

    Class that will run a QueryObject in a separate thread.

    """
    def __init__(
        self,
        name: str,
        query: dict,
        request: web.Request,
        queue: asyncio.Queue
    ):
        super().__init__()
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._queue = queue
        self.exc = None
        self._name = name
        self._query = query
        self._request = request
        self._loop = None  # Delay loop initialization

    @property
    def slug(self):
        return self._query.slug

    def run(self):
        try:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            # I need to build a QueryObject task, and put arguments on there.
            self._query = QueryObject(
                self._name,
                self._query,
                queue=self._queue,
                request=self._request,
                loop=self._loop
            )
        except Exception as ex:
            self.exc = ex
            return
        try:
            self._loop.run_until_complete(
                self._query.build_provider()
            )
        except QueryException as ex:
            self.exc = ex
            return
        except Exception as ex:
            self.exc = ex
            return
        try:
            self._loop.run_until_complete(
                self._query.query()
            )
        except Exception as ex:
            self.exc = ex
        finally:
            try:
                self._loop.stop()
                self._loop.close()
            except Exception as ex:
                print('ThreadQuery Loop Close: ', ex)
