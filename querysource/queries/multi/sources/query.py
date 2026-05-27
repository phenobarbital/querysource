import asyncio
from typing import Optional

import pandas as pd
from aiohttp import web

from .base import ThreadSource
from .executors import LocalExecutor, RemoteConfig, RemoteExecutor


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
            {
                "name": "remote",
                "type": "bool",
                "required": False,
                "default": False,
                "description": (
                    "When ``True``, dispatch this query to a remote qworker "
                    "server instead of executing locally. Requires either a "
                    "``worker`` key on the same entry or the central "
                    "``QWORKER_HOST`` / ``QWORKER_PORT`` configuration."
                ),
            },
            {
                "name": "worker",
                "type": "str",
                "required": False,
                "default": None,
                "description": (
                    "Address of the remote qworker to use for this query, "
                    "in ``host:port`` format (e.g. ``qworker1.internal:8888``). "
                    "Overrides the central ``QWORKER_HOST`` / ``QWORKER_PORT`` "
                    "settings. Only relevant when ``remote: true``. "
                    "If no port is specified, QWORKER_PORT is used as the "
                    "fallback (default: 8888)."
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
                "remote": {
                    "type": "boolean",
                    "description": (
                        "When true, dispatch this query to a remote qworker "
                        "server. Requires 'worker' or central QWORKER_HOST config."
                    ),
                    "default": False,
                },
                "worker": {
                    "type": "string",
                    "description": (
                        "Remote qworker address in 'host:port' format. "
                        "Overrides central QWORKER_HOST/QWORKER_PORT settings. "
                        "Only used when remote=true."
                    ),
                },
            },
            "oneOf": [
                {"required": ["slug"]},
                {"required": ["query"]},
            ],
            "$comment": (
                "oneOf enforces mutual exclusion: a query must have exactly one of "
                "'slug' (named query) or 'query' (raw SQL), not both."
            ),
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
            '    },\n'
            '    "remote_orders": {\n'
            '      "slug": "daily_orders",\n'
            '      "remote": true,\n'
            '      "worker": "qworker1.internal:8888",\n'
            '      "store_id": 42\n'
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
        remote_config: Optional[RemoteConfig] = None,
    ):
        assert isinstance(query, dict), (
            f"ThreadQuery expects a dict for 'query', got {type(query).__name__!r}"
        )
        super().__init__(name, query, request, queue)
        # self._query aliases self._options (set by super().__init__); kept for
        # backward-compat with the slug property and internal fetch() references.
        self._query = query
        # Note: self._request is already set by ThreadSource.__init__ (via super());
        # the redundant assignment is intentionally omitted here.
        if remote_config is not None:
            self._executor = RemoteExecutor(
                remote_config.host,
                remote_config.port,
                remote_config.timeout,
                workers=remote_config.workers,
            )
        else:
            self._executor = LocalExecutor()

    @property
    def slug(self) -> str:
        """Return the query slug.

        ``self._query`` is always a dict (invariant enforced by __init__).
        Falls back to the thread name when no ``slug`` key is present.
        """
        return self._query.get('slug', self._name)

    async def fetch(self) -> pd.DataFrame | None:
        """Delegate query execution to the configured executor.

        The executor (either :class:`~.executors.LocalExecutor` or
        :class:`~.executors.RemoteExecutor`) is responsible for placing
        ``{name: DataFrame}`` into ``self._queue``.  This method returns
        ``None`` to signal :meth:`ThreadSource.run` that the queue-put
        has already been performed.

        Returns:
            ``None`` — the queue is written by the executor.

        Raises:
            :class:`~querysource.exceptions.QueryException`: On provider
                build or query execution failure (local or remote).
        """
        await self._executor.execute(
            self._name,
            self._query,
            self._queue,
            self._request,
        )
        return None
