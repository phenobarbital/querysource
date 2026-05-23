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

    # User-facing catalog override. ThreadQuery is dispatched from the YAML
    # ``queries:`` block — each value (not the class itself) is the unit users
    # write — so the introspected ``__init__`` shape is meaningless here.
    _catalog = {
        "display_name": "Query",
        "description": (
            "Run a stored slug-based query or a raw SQL query and expose the "
            "result as a named DataFrame inside the MultiQS pipeline."
        ),
        "usage": (
            "Add entries to the top-level ``queries:`` block. Each key is the "
            "DataFrame name; each value must specify either ``slug`` (named "
            "stored query) or ``query`` (raw SQL). For raw SQL, set either "
            "``driver`` (e.g. ``pg``, ``mysql``) or ``datasource`` (named "
            "connection). Any additional keys on a ``slug`` entry are passed "
            "through as conditions."
        ),
        "icon": "database",
        "attributes": [
            {
                "name": "slug",
                "type": "str",
                "required": False,
                "default": None,
                "description": (
                    "Stored query slug to execute. Mutually exclusive with "
                    "``query``."
                ),
            },
            {
                "name": "query",
                "type": "str",
                "required": False,
                "default": None,
                "description": (
                    "Raw SQL string to execute. Mutually exclusive with "
                    "``slug``."
                ),
            },
            {
                "name": "driver",
                "type": "str",
                "required": False,
                "default": None,
                "description": (
                    "Default driver to use when running a raw ``query`` "
                    "(e.g. ``pg``, ``mysql``, ``bigquery``)."
                ),
            },
            {
                "name": "datasource",
                "type": "str",
                "required": False,
                "default": None,
                "description": (
                    "Named datasource (connection) to use when running a "
                    "raw ``query``. Takes precedence over ``driver``."
                ),
            },
        ],
        "json_schema": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "title": "Query",
            "description": (
                "A single entry under the MultiQS ``queries:`` block. Must "
                "provide either ``slug`` or ``query`` (but not both)."
            ),
            "properties": {
                "slug": {
                    "type": "string",
                    "description": "Stored query slug.",
                },
                "query": {
                    "type": "string",
                    "description": "Raw SQL statement.",
                },
                "driver": {
                    "type": "string",
                    "description": "Driver alias for raw queries.",
                },
                "datasource": {
                    "type": "string",
                    "description": "Named datasource for raw queries.",
                },
            },
            "oneOf": [
                {"required": ["slug"]},
                {"required": ["query"]},
            ],
            "additionalProperties": True,
        },
        "example": (
            '{\n'
            '  "queries": {\n'
            '    "stores": {\n'
            '      "query": "select * from hisense.stores",\n'
            '      "driver": "pg"\n'
            '    },\n'
            '    "products": {\n'
            '      "slug": "all_products"\n'
            '    }\n'
            '  }\n'
            '}'
        ),
    }

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
