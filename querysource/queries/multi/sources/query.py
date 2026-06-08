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

    # User-facing documentation lives in the sibling companion file
    # ``query.catalog.yaml`` (loaded by ComponentRegistry / describe_class).
    # ThreadQuery is dispatched from the YAML ``queries:`` block — each value
    # (not the class itself) is the unit users write — so the introspected
    # ``__init__`` shape is meaningless here; the companion is the source of
    # truth for the ``Query`` catalog entry.

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
