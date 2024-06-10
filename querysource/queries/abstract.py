"""Abstract BaseQuery.

Base Class for all Query-objects in QuerySource.
"""
import asyncio
from abc import abstractmethod
from typing import Any, Union, Optional
from collections.abc import Callable
import time
from datetime import datetime
import traceback
from functools import partial
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
from datamodel.exceptions import ValidationError
from asyncdb import AsyncDB
from asyncdb.exceptions import ProviderError
from navigator_session import get_session
from navigator_session.storages import SessionData
from aiohttp import web
from navconfig.logging import logging
from ..libs.encoders import DefaultEncoder
from ..conf import (
    SEMAPHORE_LIMIT,
    QUERYSET_REDIS,
    DEFAULT_QUERY_TIMEOUT
)
from ..exceptions import (
    QueryException,
    CacheException,
    DataNotFound
)
from ..interfaces.connections import Connection
from ..events import LogEvent
from .outputs import OutputFactory
from .models import Query, QueryResult
from ..utils.events import enable_uvloop


vs = logging.getLogger('visions.backends')
vs.setLevel(logging.WARNING)


matlog = logging.getLogger('matplotlib')
matlog.setLevel(logging.WARNING)

class BaseQuery(Connection):

    post_cache: Callable = None
    _timeout: int = 3600
    # SEMAPHORE LIMIT
    semaphore = asyncio.Semaphore(int(SEMAPHORE_LIMIT))

    def __init__(
            self,
            slug: str = None,
            conditions: dict = None,
            request: web.Request = None,
            **kwargs
    ):
        """
        Initialize the Query Object
        """
        enable_uvloop()
        __name__ = type(self).__name__
        Connection.__init__(self, **kwargs)
        self.slug = slug
        self._result: Union[dict, list] = None
        self._output_format: OutputFactory = None
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
        self._conditions = conditions if conditions else {}
        # web Request:
        self._request = request
        self._generated: Union[int, datetime] = None
        self._starttime: Union[int, datetime] = self.epoch_time()
        self._logger = logging.getLogger(f'QS.{__name__}')
        ## set the Output factory for Query:
        frm = kwargs.pop('output_format', 'native')
        self.output_format(frm)
        # Any other keyword arguments be passed to Provider.
        self.kwargs = kwargs
        # trying to configure the asyncio loop
        try:
            self._loop = kwargs.pop('loop', asyncio.get_running_loop())
        except RuntimeError:
            self._logger.error(
                "Couldn't get event loop for current thread. Creating a new event loop"
            )
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
        # configuring the encoder:
        self._encoder = DefaultEncoder()
        ## default executor:
        self._executor = ThreadPoolExecutor(max_workers=10)

    async def output(self, result, error):
        # return result in default format
        self._result = result
        return [result, error]

    def output_format(self, frmt: str = 'native', **kwargs):  # pylint: disable=W1113
        self._output_format = OutputFactory(
            self,
            frmt=frmt,
            **kwargs
        )

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
            started = datetime.utcnow()
        self._starttime = started
        return self._starttime

    def generated_at(self, started: datetime):
        self._generated = datetime.utcnow() - started
        return self._generated

    def last_duration(self):
        return self._generated

    def query_model(self, data: Union[str, dict]) -> Query:
        if isinstance(data, str):
            q = {
                "query": data
            }
        else:
            q = data
        try:
            return Query(**q)
        except (ValueError, TypeError, ValidationError) as ex:
            raise TypeError(
                f"Invalid Query Object: {ex}"
            ) from ex

    def get_result(
        self,
        query: Query,
        data: Optional[Union[list, dict]],
        duration: float,
        errors: list = None,
        state: str = None
    ) -> QueryResult:
        if query.raw_result is True:
            return data
        else:
            try:
                obj = QueryResult(
                    driver=query.driver,
                    query=query.query,
                    duration=duration,
                    errors=errors,
                    data=data,
                    state=state
                )
                return obj
            except (TypeError, ValueError) as ex:
                raise TypeError(
                    f"Invalid data for QueryResult: {ex}"
                ) from ex
            except ValidationError as ex:
                print(ex, ex.payload)
                errors = ex.payload
                raise TypeError(
                    f"Invalid data for QueryResult: {errors}"
                ) from ex

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
                data = self._encoder(
                    [dict(row) for row in result]
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

    @abstractmethod
    async def query(self):
        """query.

        Run an arbitrary query in async mode.
        """

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
