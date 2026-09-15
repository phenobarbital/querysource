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
from navconfig.logging import logging

from ...obj import QueryObject
from ....exceptions import QueryException
from ....conf import QWORKER_TIMEOUT, QWORKER_QUERY_TIMEOUT
from querysource.ownership_logging import ownership_fields
from querysource.tenants import QueryStore, TenantOwnerEnvelope

logger = logging.getLogger("QS.RemoteExecutor")


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
        *,
        store: QueryStore | None = None,
    ) -> None:
        """Put {alias: DataFrame}; route metadata never becomes SQL conditions."""


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
        *,
        store: QueryStore | None = None,
    ) -> None:
        """Execute the query locally using QueryObject.

        Args:
            name: DataFrame key name.
            query: Query dict containing ``slug`` or ``query`` key.
            queue: Shared asyncio queue for the result.
            request: aiohttp request for credential lookup.
            store: Resolved QueryStore for the query.

        Returns:
            None — QueryObject places the result in the queue directly.
        """
        loop = asyncio.get_running_loop()
        # LocalExecutor forwards resolved store into loop-local QueryObject
        tenant_selector = store.schema if store else None
        query_obj = QueryObject(
            name,
            query,
            queue=queue,
            request=request,
            loop=loop,
            tenant=tenant_selector,
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
        *,
        store: QueryStore | None = None,
    ) -> None:
        """Dispatch the query to a remote qworker and place the result in the queue.

        Routes on the resolved ``store`` (TASK-728): a tenant-owned store
        (``store.contract == "tenant"``) is always dispatched through the
        versioned ``querysource.remote.tenant_query_handler_v1`` contract
        with a validated :class:`TenantOwnerEnvelope` (AC-1) — a distinct
        callable name so an old, ``**kwargs``-tolerant worker that doesn't
        understand ``owner=`` fails explicitly (unknown-handler on the
        worker side) instead of silently executing against its own
        default schema. A legacy/default store (``store is None`` or
        ``store.contract == "legacy"``) keeps dispatching through the
        unchanged ``querysource.remote.query_handler`` contract (AC-2).
        Neither path ever falls back to the other, and neither ever falls
        back to local execution (AC-3) — any handler-, version- or
        owner-related failure on the worker side propagates as-is.

        Args:
            name: DataFrame key name.
            query: Query dict; ``slug`` key is the primary dispatch handle.
                When ``slug`` is absent, the dict must contain a ``query``
                key with raw SQL plus ``driver`` or ``datasource``.
            queue: Shared asyncio queue for the result.
            request: aiohttp request (not sent to qworker; credentials are
                resolved server-side by the qworker's own QuerySource install).
            store: Resolved QueryStore for the query. ``None`` or a
                ``"legacy"`` contract dispatches through the unchanged
                legacy handler; a ``"tenant"`` contract dispatches through
                the versioned handler with an owner envelope.

        Returns:
            None — the result is placed into the queue directly.

        Raises:
            QueryException: When ``store`` carries an unsupported
                contract, the qworker is unreachable, the TCP connection
                fails, or the query times out. qworker-side errors
                (SlugNotFound, DriverError, missing/incompatible handler,
                etc.) propagate as-is — there is no fallback to the other
                handler or to local execution.
        """
        if store is not None and store.contract not in ("legacy", "tenant"):
            logger.warning(
                "Remote query %r rejected: unsupported store contract (%s)",
                name, ownership_fields(store),
            )
            raise QueryException(
                f"Remote query {name!r} rejected: unsupported store contract "
                f"{store.contract!r}."
            )
        is_tenant = store is not None and store.contract == "tenant"

        owner: TenantOwnerEnvelope | None = None
        if is_tenant:
            handler = "querysource.remote.tenant_query_handler_v1"
            owner = TenantOwnerEnvelope(
                version=1,
                database_namespace=store.database_namespace,
                schema=store.schema,
                table=store.table,
                contract=store.contract,
            )
        else:
            handler = "querysource.remote.query_handler"

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
            call_kwargs = {"conditions": conditions}
            if owner is not None:
                call_kwargs["owner"] = owner
            result = await asyncio.wait_for(
                client.run(handler, slug, **call_kwargs),
                timeout=QWORKER_QUERY_TIMEOUT,
            )
            await queue.put({name: result})
        except asyncio.TimeoutError as exc:
            logger.warning(
                "Remote query %r timed out on %s:%s (%s)",
                name, self._host, self._port, ownership_fields(owner or store),
            )
            raise QueryException(
                f"Remote query {name!r} timed out after {QWORKER_QUERY_TIMEOUT}s "
                f"on {self._host}:{self._port}"
            ) from exc
        except (ConnectionError, OSError) as exc:
            logger.warning(
                "Remote query %r failed on %s:%s (%s): %s",
                name, self._host, self._port, ownership_fields(owner or store), exc,
            )
            raise QueryException(
                f"Remote query {name!r} failed on {self._host}:{self._port}: {exc}"
            ) from exc
        finally:
            if hasattr(client, 'close') and asyncio.iscoroutinefunction(client.close):
                await client.close()
            elif hasattr(client, 'close'):
                client.close()
