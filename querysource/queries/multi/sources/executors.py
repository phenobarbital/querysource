"""Executor strategy classes for ThreadQuery.

This module defines the QueryExecutor ABC and two concrete implementations:
- LocalExecutor: wraps the current QueryObject execution path (default behavior)
- RemoteExecutor: dispatches queries to a remote qworker server via QClient

Also defines RemoteConfig, the immutable value object passed from MultiQS to
ThreadQuery when remote execution is requested.
"""
import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from aiohttp import web

from ...obj import QueryObject
from ....exceptions import QueryException
from ....conf import QWORKER_TIMEOUT, QWORKER_QUERY_TIMEOUT


@dataclass(frozen=True)
class RemoteConfig:
    """Configuration for remote query execution.

    Immutable value object passed from MultiQS to ThreadQuery when a query
    has ``remote: true`` in its config dict.

    Attributes:
        host: Hostname or IP of the remote qworker server.
        port: TCP port of the remote qworker server.
        timeout: TCP connection timeout in seconds (default 5).
        workers: Pre-parsed worker list of (host, port) tuples.  When
            non-empty, overrides the single host/port pair.  Populated
            from QWORKER_WORKERS when no per-query ``worker:`` key is set.
    """

    host: str
    port: int
    timeout: int = 5
    workers: list = field(default_factory=list)


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
        queue: asyncio.Queue[dict],
        request: web.Request,
    ) -> None:
        """Execute a query and put the result into the queue.

        The method MUST put a dict ``{name: DataFrame}`` into the queue
        before returning. Always returns None. Results are delivered by
        putting a ``{name: result}`` dict into ``queue``.

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
        queue: asyncio.Queue[dict],
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
        loop = asyncio.get_running_loop()
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

    def __init__(
        self,
        host: str,
        port: int,
        timeout: int = QWORKER_TIMEOUT,
        workers: list | None = None,
    ) -> None:
        """Store connection parameters.

        Args:
            host: Hostname or IP of the remote qworker server.
            port: TCP port of the remote qworker server.
            timeout: TCP connection timeout in seconds (default from
                QWORKER_TIMEOUT, default 5).
            workers: Optional pre-parsed list of ``(host, port)`` tuples.
                When non-empty, overrides the single ``host``/``port`` pair.
                Populated from :data:`QWORKER_WORKERS` when no per-query
                ``worker:`` key is present.
        """
        self._host = host
        self._port = port
        self._timeout = timeout
        self._workers: list = workers or []

    async def execute(
        self,
        name: str,
        query: dict,
        queue: asyncio.Queue[dict],
        request: web.Request,
    ) -> None:
        """Dispatch the query to a remote qworker and place the result in the queue.

        Args:
            name: DataFrame key name.
            query: Query dict; ``slug`` key is the primary dispatch handle.
                When ``slug`` is absent, the dict must contain a ``query``
                key with raw SQL plus ``driver`` or ``datasource``.
            queue: Shared asyncio queue for the result.
            request: aiohttp request (not sent to qworker; credentials are
                resolved server-side by the qworker's own QuerySource install).

        Returns:
            None — the result is placed into the queue directly.

        Raises:
            QueryException: When the qworker is unreachable, the TCP
                connection fails, or the query times out.  qworker-side
                errors (SlugNotFound, DriverError, etc.) propagate as-is.
        """
        from qw.client import QClient  # lazy import — qworker is optional

        slug = query.get("slug")
        _routing_keys = ("slug", "query", "driver", "datasource", "remote", "worker")
        if slug is not None:
            # Slug-based dispatch: forward everything except routing/identity keys.
            conditions = {
                k: v for k, v in query.items() if k not in _routing_keys
            }
        else:
            # Raw SQL dispatch: forward query, driver, datasource plus any extra
            # conditions — exclude only the remote-routing keys, not SQL keys.
            conditions = {
                k: v for k, v in query.items()
                if k not in ("slug", "remote", "worker")
            }

        # Use the pre-parsed worker list when available; otherwise fall back to
        # the single host/port stored at construction time.
        worker_list = self._workers if self._workers else [(self._host, self._port)]
        client = QClient(worker_list=worker_list, timeout=self._timeout)
        try:
            result = await asyncio.wait_for(
                client.run(
                    "querysource.remote.query_handler",
                    slug,
                    conditions=conditions,
                ),
                timeout=QWORKER_QUERY_TIMEOUT,
            )
            await queue.put({name: result})
        except asyncio.TimeoutError as exc:
            raise QueryException(
                f"Remote query {name!r} timed out after {QWORKER_QUERY_TIMEOUT}s "
                f"on {self._host}:{self._port}"
            ) from exc
        except (ConnectionError, OSError) as exc:
            raise QueryException(
                f"Remote query {name!r} failed on {self._host}:{self._port}: {exc}"
            ) from exc
        finally:
            if hasattr(client, 'close') and asyncio.iscoroutinefunction(client.close):
                await client.close()
            elif hasattr(client, 'close'):
                client.close()
