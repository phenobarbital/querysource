"""Abstract BaseQuery.

Base Class for all Query-objects in QuerySource.
"""
import asyncio
from abc import abstractmethod
from typing import Any, Union, Optional
from collections.abc import Callable
import time
from datetime import datetime, timezone
import traceback
from functools import partial
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
from pandas import DataFrame
from asyncdb import AsyncDB
from asyncdb.exceptions import ProviderError
from navigator_session import get_session
from navigator_session import SessionData
from aiohttp import web
from navconfig.logging import logging
from ..libs.encoders import DefaultEncoder
from ..conf import (
    SEMAPHORE_LIMIT,
    QUERYSET_REDIS,
    DEFAULT_QUERY_TIMEOUT,
    DEFAULT_QUERY_FORMAT
)
from ..exceptions import (
    QueryException,
    CacheException,
    DataNotFound
)
from .connections import Connection
from ..events import LogEvent
from ..utils.events import enable_uvloop


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
            loop: Optional[asyncio.AbstractEventLoop] = None,
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
        # trying to configure the asyncio loop
        if loop:
            self._loop = loop
        else:
            try:
                self._loop = asyncio.get_event_loop()
            except RuntimeError:
                self._logger.warning(
                    "Couldn't get event loop for current thread. Creating a new event loop"
                )
                self._loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self._loop)
        Connection.__init__(self, loop=self._loop, **kwargs)
        self._result: Union[dict, list] = None
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
        self._generated: Union[int, datetime] = None
        self._starttime: Union[int, datetime] = self.epoch_time()
        ## set the Output factory for Query:
        frm = kwargs.pop('output_format', DEFAULT_QUERY_FORMAT)
        self.output_format(frm)
        # Any other keyword arguments be passed to Provider.
        self.kwargs = kwargs
        # configuring the encoder:
        self._encoder = DefaultEncoder()
        ## default executor:
        self._executor = ThreadPoolExecutor(max_workers=2)

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
    def query_model(self, data: Union[str, dict]) -> Any:
        pass

    @abstractmethod
    def get_result(
        self,
        query: object,
        data: Optional[Union[list, dict]],
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
    def save_cache(self, checksum, result, **kwargs):
        """_thread_func.
        Returns a future to be executed into a Thread Pool.
        """
        loop = asyncio.new_event_loop()
        func = partial(
            self.save_in_cache,
            checksum,
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
                    self.caching_data(checksum, result)
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
        checksum: str,
        loop: asyncio.AbstractEventLoop,
        task: asyncio.Task,
        **kwargs
    ):
        """Notification when Query was saved in Cache.
        """
        try:
            if callable(self.post_cache):
                self._thread_func(
                    self.post_cache, checksum, loop, **kwargs
                )
        except Exception as exc:
            self._logger.error(
                f"Error running post_cache function: {exc}"
            )
        self._logger.notice(
            f"QuerySource: Cached {checksum} at {time.strftime('%X')}"
        )

    def save_in_cache(
        self,
        checksum: str,
        result: Any,
        loop: asyncio.AbstractEventLoop
    ):
        asyncio.set_event_loop(loop)
        fut = loop.create_task(
            self.caching_data(checksum, result)
        )
        # done callback
        done_callback = partial(
            self.cache_saved, checksum, loop
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
        checksum: str,
        result: Any
        # loop: asyncio.AbstractEventLoop
    ):
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
                if isinstance(result, DataFrame):
                    records = result.to_dict(orient='records')
                    data = self._encoder(
                        records
                    )
                else:
                    data = self._encoder(
                        [dict(row) if not isinstance(row, dict) else row for row in result]
                    )
            except Exception as err:  # pylint: disable=W0703
                self._logger.error(
                    f'Cache Encode Error: {err}'
                )
                return None
            async with await redis.connection() as conn:
                # async with  as conn:
                await conn.setex(
                    checksum,
                    data,
                    self._timeout
                )
                self._logger.debug(
                    f"Successfully Cached: {checksum}"
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
        return await LogEvent(
            payload=payload,
            status=status,
            **kwargs
        )
