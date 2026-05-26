"""Executor strategy classes for ThreadQuery.

This module defines the QueryExecutor ABC and two concrete implementations:
- LocalExecutor: wraps the current QueryObject execution path (default behavior)
- RemoteExecutor: dispatches queries to a remote qworker server via QClient

Also defines RemoteConfig, the immutable value object passed from MultiQS to
ThreadQuery when remote execution is requested.
"""
import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass

from aiohttp import web

from ...obj import QueryObject
from ....exceptions import QueryException


@dataclass(frozen=True)
class RemoteConfig:
    """Configuration for remote query execution.

    Immutable value object passed from MultiQS to ThreadQuery when a query
    has ``remote: true`` in its config dict.

    Attributes:
        host: Hostname or IP of the remote qworker server.
        port: TCP port of the remote qworker server.
        timeout: Query execution timeout in seconds (default 60).
    """

    host: str
    port: int
    timeout: int = 60


class QueryExecutor(ABC):
    """Strategy interface for query execution inside ThreadQuery.

    Implementations must place ``{name: DataFrame}`` into the queue before
    returning, matching the contract established by QueryObject.query() and
    ThreadSource.run().
    """

    @abstractmethod
    async def execute(
        self,
        name: str,
        query: dict,
        queue: asyncio.Queue,
        request: web.Request,
    ) -> None:
        """Execute a query and put the result into the queue.

        The method MUST put a dict ``{name: DataFrame}`` into the queue
        before returning. Returning None signals that the queue was already
        written (matching the current ThreadQuery/QueryObject contract).

        Args:
            name: The DataFrame key name for the result.
            query: The query dict (slug-based or raw SQL).
            queue: Shared asyncio queue where results are placed.
            request: The current aiohttp web request for credential resolution.

        Returns:
            None — always. The queue is the output channel.
        """


class LocalExecutor(QueryExecutor):
    """Executes queries locally via QueryObject (current behavior).

    Replicates the execution flow from the original ThreadQuery.fetch():
    creates a QueryObject, calls build_provider(), then calls query().
    QueryObject.query() puts ``{name: DataFrame}`` into the queue
    internally, so this method returns None.
    """

    async def execute(
        self,
        name: str,
        query: dict,
        queue: asyncio.Queue,
        request: web.Request,
    ) -> None:
        """Execute the query locally using QueryObject.

        Args:
            name: DataFrame key name.
            query: Query dict containing ``slug`` or ``query`` key.
            queue: Shared asyncio queue for the result.
            request: aiohttp request for credential lookup.

        Returns:
            None — QueryObject places the result in the queue directly.
        """
        loop = asyncio.get_event_loop()
        query_obj = QueryObject(
            name,
            query,
            queue=queue,
            request=request,
            loop=loop,
        )
        await query_obj.build_provider()
        await query_obj.query()
        return None


class RemoteExecutor(QueryExecutor):
    """Dispatches queries to a remote qworker server via QClient.

    QClient is imported lazily inside execute() so that qworker remains an
    optional dependency — installations without qworker can still use
    LocalExecutor without import errors.

    The QClient instance is created fresh inside execute() (not __init__)
    because QClient captures the running event loop at construction time
    (qw/client.py:74).  Since execute() runs inside a thread's own event
    loop (created by ThreadSource.run()), QClient must be instantiated
    after that loop is active.
    """

    def __init__(self, host: str, port: int, timeout: int = 60) -> None:
        """Store connection parameters.

        Args:
            host: Hostname or IP of the remote qworker server.
            port: TCP port of the remote qworker server.
            timeout: Query execution timeout in seconds (default 60).
        """
        self._host = host
        self._port = port
        self._timeout = timeout

    async def execute(
        self,
        name: str,
        query: dict,
        queue: asyncio.Queue,
        request: web.Request,
    ) -> None:
        """Dispatch the query to a remote qworker and place the result in the queue.

        Args:
            name: DataFrame key name.
            query: Query dict; ``slug`` key is the primary dispatch handle.
            queue: Shared asyncio queue for the result.
            request: aiohttp request (not sent to qworker; credentials are
                resolved server-side by the qworker's own QuerySource install).

        Returns:
            None — the result is placed into the queue directly.

        Raises:
            QueryException: When the qworker is unreachable or the TCP
                connection fails.  qworker-side errors (SlugNotFound,
                DriverError, etc.) propagate as-is.
        """
        from qw.client import QClient  # lazy import — qworker is optional

        slug = query.get("slug")
        # Extract conditions: everything except execution/routing keys that
        # QueryObject strips itself but which we must not forward.
        conditions = {
            k: v for k, v in query.items()
            if k not in ("slug", "query", "driver", "datasource")
        }
        try:
            client = QClient(
                worker_list=[(self._host, self._port)],
                timeout=self._timeout,
            )
            result = await client.run(
                "querysource.remote.query_handler",
                slug,
                conditions=conditions,
            )
            await queue.put({name: result})
        except (ConnectionError, TimeoutError, OSError) as exc:
            raise QueryException(
                f"Remote query {name!r} failed on {self._host}:{self._port}: {exc}"
            ) from exc
