"""Abstract BaseQuery.

Base Class for all Query-objects in QuerySource.
"""
import asyncio
import time
import traceback
import uuid
from abc import abstractmethod
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from datetime import datetime, timezone
from functools import partial
from typing import Any

from aiohttp import web
from asyncdb import AsyncDB
from asyncdb.exceptions import ProviderError
from navconfig.logging import logging
from navigator_session import SessionData, get_session

from ..cache_identity import result_cache_key
from ..conf import (
    DEFAULT_QUERY_FORMAT,
    DEFAULT_QUERY_TIMEOUT,
    QUERYSET_REDIS,
    SEMAPHORE_LIMIT,
)
from ..events import LogEvent
from ..exceptions import CacheException, DataNotFound, QueryException
from ..libs.encoders import DefaultEncoder
from ..ownership_logging import ownership_fields
from ..utils.cache_serialization import serialize_cache_payload
from ..utils.events import enable_uvloop
from .connections import Connection

logging.getLogger('visions.backends').setLevel(logging.WARNING)
logging.getLogger('matplotlib').setLevel(logging.WARNING)


class AbstractQuery(Connection):
    """AbstractQuery.
    Base Class for all Query-objects in QuerySource.
    """
    post_cache: Callable = None
    _timeout: int = 3600

    def __init__(
            self,
            slug: str = None,
            conditions: dict = None,
            request: web.Request = None,
            loop: asyncio.AbstractEventLoop | None = None,
            *,
            tenant: str | None = None,
            **kwargs
    ):
        """
        Initialize the Query Object
        """
        enable_uvloop()
        __name__ = type(self).__name__
        self._logger = logging.getLogger(f'QS.{__name__}')
        self.slug = slug
        self.semaphore = asyncio.Semaphore(int(SEMAPHORE_LIMIT))
        # Loop binding: prefer an explicit loop, then the running loop. Do not
        # create a new loop here — that would bind downstream asyncdb objects
        # to a loop different from the one aiohttp runs with.
        if loop is not None:
            self._loop = loop
        else:
            try:
                self._loop = asyncio.get_running_loop()
            except RuntimeError:
                self._loop = None
        Connection.__init__(self, loop=self._loop, **kwargs)
        self._result: dict | list = None
        self._output_format: Any = None
        try:
            self._program = conditions.get('program', 'public')
        except (TypeError, AttributeError):
            self._program: str = 'public'
        # default Provider:
        try:
            self._provider = conditions.pop('provider', 'db')
        except (TypeError, AttributeError):
            self._provider: str = 'db'
        # defining conditions
        self._conditions = conditions or {}
        # web Request:
        self._request = request
        self._generated: int | datetime = None
        self._starttime: int | datetime = self.epoch_time()
        ## set the Output factory for Query:
        frm = kwargs.pop('output_format', DEFAULT_QUERY_FORMAT)
        self.output_format(frm)
        # Any other keyword arguments be passed to Provider.
        self.kwargs = kwargs
        # configuring the encoder:
        self._encoder = DefaultEncoder()
        ## default executor:
        self._executor = ThreadPoolExecutor(max_workers=2)
        # Tenant selector (keyword-only, preserved from Python routing)
        self._tenant_selector = tenant
        # Definition identity and revision for result cache keys (set during load)
        self._definition_identity: Any = None
        self._definition_revision: str | None = None
        # Unique per-query execution id: assigned once here so every
        # timing/failure event and implicit output artifact this query
        # object ever produces (HTTP, direct, child, scheduled, remote)
        # correlates under the same id — never re-derived from a mutable
        # request field (TASK-731).
        self._execution_id: str = uuid.uuid4().hex

    def get_event_loop(self) -> asyncio.AbstractEventLoop:
        return self._loop if self._loop else asyncio.get_running_loop()

    @property
    def provider(self):
        return self._qs

    @property
    def timeout(self):
        return self._timeout

    @timeout.setter
    def timeout(self, timeout: int = 3600):
        self._timeout = timeout

    ## calculated the start time on Epoch:
    def epoch_time(self):
        return time.time()

    def epoch_duration(self, started: int):
        self._endtime = time.time()
        self._generated = self._endtime - started
        return self._generated

    ### function for calculate duration:
    def start_timing(self, started: datetime = None):
        if not started:
            started = datetime.now(timezone.utc)
        self._starttime = started
        return self._starttime

    def generated_at(self, started: datetime):
        self._generated = datetime.now(timezone.utc) - started
        return self._generated

    def last_duration(self):
        return self._generated

    @abstractmethod
    def query_model(self, data: str | dict) -> Any:
        pass

    @abstractmethod
    def get_result(
        self,
        query: object,
        data: list | dict | None,
        duration: float,
        errors: list = None,
        state: str = None
    ) -> object:
        pass

    def default_headers(self) -> dict:
        return {
            'X-STATUS': 'OK',
            'X-MESSAGE': 'Query Execution'
        }

    async def user_session(self, request: web.Request = None) -> SessionData:
        """user_session.

        Getting (if exists) a session object for this user.
        """
        if not request:
            return None
        try:
            # TODO: configurable by tenant (or query)
            session = await get_session(request, new=False)
        except RuntimeError:
            self._logger.error('QS: User Session system is not installed.')
            return None
        return session

    ### threads
    def get_executor(self, executor='thread', max_workers: int = 2) -> Any:
        """get_executor.
        description: Returns the executor to be used by run_in_executor.
        """
        if executor == 'thread':
            return ThreadPoolExecutor(max_workers=max_workers)
        elif executor == 'process':
            return ProcessPoolExecutor(max_workers=max_workers)
        else:
            return None

    async def _thread_func(self, fn, *args, executor: Any = None, **kwargs):
        """_thread_func.
        Returns a future to be executed into a Thread Pool.
        """
        loop = asyncio.new_event_loop()
        func = partial(fn, *args, **kwargs)
        if not executor:
            executor = self._executor
        try:
            fut = loop.run_in_executor(executor, func)
            return await fut
        except Exception as e:
            self._logger.exception(e, stack_info=True)
            raise
        finally:
            loop.close()

    #### Caching facilities
    def result_cache_key(self, provider_checksum: str) -> str:
        """Compose key from the loaded immutable definition and provider checksum.

        Combines the loaded definition identity and revision with the provider
        checksum to create a fully scoped cache key. This prevents cache hits
        across different definitions or when a definition has been externally
        modified.

        Args:
            provider_checksum: The provider-specific SQL checksum.

        Returns:
            The composed qs:r2: cache key.
        """
        return result_cache_key(
            self._definition_identity, self._definition_revision, provider_checksum
        )

    def save_cache(self, checksum, result, **kwargs):
        """_thread_func.
        Returns a future to be executed into a Thread Pool.

        Initiates cache save in a thread. The checksum is used as-is if it's
        already a composed key; otherwise wrap it with result_cache_key.
        """
        # Use the checksum directly if it's already a composed key (starts with qs:r2:),
        # otherwise compose it using the definition identity and revision
        if isinstance(checksum, str) and checksum.startswith('qs:r2:'):
            cache_key = checksum
        else:
            cache_key = self.result_cache_key(checksum)

        loop = asyncio.new_event_loop()
        func = partial(
            self.save_in_cache,
            cache_key,
            result,
            loop
        )
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                loop.run_in_executor(pool, func)
        except asyncio.TimeoutError:
            # if a timeout is reached, we try again:
            try:
                loop.run_until_complete(
                    self.caching_data(cache_key, result)
                )
            except Exception as exc:
                self._logger.exception(
                    f'Cache Exception {exc!s}',
                    stack_info=True
                )
        except (CacheException, ProviderError) as err:
            self._logger.error(
                f'Redis Saving Error {err!s}'
            )
        except Exception as exc:
            self._logger.exception(
                f'Cache Exception {exc!s}',
                stack_info=True
            )
        finally:
            loop.close()

    def cache_saved(
        self,
        cache_key: str,
        loop: asyncio.AbstractEventLoop,
        task: asyncio.Task,
        **kwargs
    ):
        """Notification when Query was saved in Cache.

        Called when caching_data completes successfully. The cache_key is the
        fully composed revision-scoped key.
        """
        try:
            if callable(self.post_cache):
                self._thread_func(
                    self.post_cache, cache_key, loop, **kwargs
                )
        except Exception as exc:
            self._logger.error(
                f"Error running post_cache function: {exc}"
            )
        self._logger.notice(
            f"QuerySource: Cached {cache_key} at {time.strftime('%X')}"
        )

    def save_in_cache(
        self,
        cache_key: str,
        result: Any,
        loop: asyncio.AbstractEventLoop
    ):
        """Write the already composed key; capture identity before threading.

        The cache_key is expected to be a fully composed revision-scoped key.
        This method preserves serialization/TTL/thread mechanics while using
        the composed key and immutable revision; no legacy-key fallback.
        """
        asyncio.set_event_loop(loop)
        fut = loop.create_task(
            self.caching_data(cache_key, result)
        )
        # done callback
        done_callback = partial(
            self.cache_saved, cache_key, loop
        )
        fut.add_done_callback(
            done_callback
        )
        try:
            loop.run_until_complete(
                fut
            )
        except asyncio.TimeoutError:
            # Redis raises Timeout on Connection:
            raise
        except Exception as err:  # pylint: disable=W0703
            self._logger.error(
                f'Querysource: Error on caching: {err}'
            )

    async def caching_data(
        self,
        cache_key: str,
        result: Any
        # loop: asyncio.AbstractEventLoop
    ):
        """Use the same composed key for TTL writes, with no second wrapping.

        The cache_key should be a fully composed revision-scoped key. This
        preserves serialization/TTL/thread mechanics while using the composed
        key and immutable revision; no legacy-key fallback.
        """
        try:
            data = None
            loop = asyncio.get_running_loop()
            redis = AsyncDB(
                'redis',
                dsn=QUERYSET_REDIS,
                loop=loop
            )
            if not self._timeout:
                self._timeout = int(DEFAULT_QUERY_TIMEOUT)
            try:
                # encode the data
                # if result is a Pandas dataframe
                try:
                    from pandas import DataFrame
                    if isinstance(result, DataFrame):
                        records = result.to_dict(orient='records')
                    else:
                        records = [
                            dict(row) if not isinstance(row, dict) else row
                            for row in result
                        ]
                except ImportError:
                    records = [
                        dict(row) if not isinstance(row, dict) else row
                        for row in result
                    ]
                try:
                    data = serialize_cache_payload(records)
                except ImportError as ierr:
                    self._logger.warning(
                        f"Parquet dependency not available, falling back to JSON: {ierr}"
                    )
                    data = self._encoder(records)
                except Exception as serr:
                    self._logger.warning(
                        f"Cache Encode using Parquet failed, falling back to JSON: {serr}"
                    )
                    data = self._encoder(records)
            except Exception as err:  # pylint: disable=W0703
                self._logger.error(
                    f'Cache Encode Error: {err}'
                )
                return
            async with await redis.connection() as conn:
                # async with  as conn:
                await conn.setex(
                    cache_key,
                    data,
                    self._timeout
                )
                self._logger.debug(
                    f"Successfully Cached: {cache_key}"
                )
        except asyncio.TimeoutError as err:
            self._logger.error(
                f"Redis timeout: {err}"
            )
            raise
        except Exception as err:
            raise CacheException(
                f'Error on Redis cache: {err}'
            ) from err

    def NotFound(self, message: str):
        """Raised when Data not Found.
        """
        return DataNotFound(message, code=404)

    def Error(
        self,
        message: str,
        exception: BaseException = None,
        code: int = 500
    ) -> BaseException:
        """Error.

        Useful Function to raise Exceptions.
        Args:
            message (str): Exception Message.
            exception (BaseException, optional): Exception captured. Defaults to None.
            code (int, optional): Error Code. Defaults to 500.

        Returns:
            BaseException: an Exception Object.
        """
        trace = None
        message = f"{message}: {exception!s}"
        if exception:
            trace = traceback.format_exc(limit=20)
        return QueryException(
            message,
            stacktrace=trace,
            code=code
        )

    async def event_log(self, payload: dict, status: str = 'query', **kwargs):
        """Emit a timing/failure event, always carrying ownership context.

        Attaches ``execution_id`` and the executed definition's
        ``ownership_fields`` (owner/schema/table/slug) to every event —
        this is the single shared choke point HTTP, direct, and MultiQS
        child execution all pass through (TASK-731 AC-2), so callers never
        have to attach ownership themselves. Existing payload keys always
        win (``setdefault``): a caller's own ``slug``/alias is preserved
        unchanged.
        """
        enriched = dict(payload)
        enriched.setdefault('execution_id', self._execution_id)
        for key, value in ownership_fields(self._definition_identity).items():
            enriched.setdefault(key, value)
        return await LogEvent(
            payload=enriched,
            status=status,
            **kwargs
        )
