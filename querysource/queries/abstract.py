"""Abstract BaseQuery.

Base Class for all Query-objects in QuerySource.
"""
import asyncio
from abc import ABC, abstractmethod
from typing import Any, Union, Optional
from collections.abc import Callable
import time
from datetime import datetime
import traceback
from functools import partial
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
from importlib import import_module
from datamodel.exceptions import ValidationError
from asyncdb import AsyncDB
from asyncdb.exceptions import DriverError, ProviderError, handle_done_tasks
from navigator_session import get_session
from navigator_session.storages import SessionData
from aiohttp import web
from navconfig.logging import logging
from querysource.libs.encoders import DefaultEncoder
from querysource.conf import (
        SEMAPHORE_LIMIT,
        QUERYSET_REDIS
)
from querysource.exceptions import (
    QueryException,
    CacheException,
    DataNotFound
)
from querysource.connections import DATASOURCES
from .outputs import OutputFactory
from .models import Query, QueryResult, supported_drivers

vs = logging.getLogger('visions.backends')
vs.setLevel(logging.WARNING)

matlog = logging.getLogger('matplotlib')
matlog.setLevel(logging.WARNING)

class BaseQuery(ABC):

    post_cache: Callable = None
    _timeout: int = 10
    # SEMAPHORE LIMIT
    semaphore: Callable = asyncio.Semaphore(SEMAPHORE_LIMIT)

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
        self.slug = slug
        self._result: Union[dict, list] = None
        self._output_format: OutputFactory = None
        try:
            self._program = conditions['program']
        except (TypeError, KeyError):
            self._program: str = 'public' # TODO: changing to public schema.
        try:
            self._provider = conditions['provider']
            del conditions['provider']
        except (TypeError, KeyError):
            self._provider: str = 'db'
        # defining conditions
        self._conditions = conditions if conditions else {}
        # web Request:
        self._request = request
        self._generated: datetime = None
        self._starttime: datetime = None
        self._logger = logging.getLogger('QuerySource')
        ## set the Output factory for Query:
        try:
            self.output_format(kwargs['output_format'])
            del kwargs['output_format']
        except KeyError:
            self.output_format('native')
        self.kwargs = kwargs
        # trying to configure the asyncio loop
        try:
            if 'loop' in kwargs:
                self._loop = kwargs['loop']
                del kwargs['loop']
            else:
                self._loop = asyncio.get_event_loop()
        except RuntimeError:
            logging.error(
                "Couldn't get event loop for current thread. Creating a new event loop to be used!"
            )
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
        # configuring the encoder:
        self._encoder = DefaultEncoder()
        ## default executor:
        self._executor = ProcessPoolExecutor

    async def output(self, result, error):
        # return result in default format
        self._result = result
        return [result, error]

    def output_format(self, frmt: str = 'native', *args, **kwargs): # pylint: disable=W1113
        self._output_format = OutputFactory(self, frmt=frmt, *args, **kwargs)

    @property
    def provider(self):
        return self._qs

    @property
    def timeout(self):
        return self._timeout

    @timeout.setter
    def timeout(self, timeout: int = 3600):
        self._timeout = timeout

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
        except (ValueError, TypeError, ValidationError)  as ex:
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
    def get_executor(self, executor = 'thread', max_workers: int = 2) -> Any:
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

    #### Datasources and drivers:
    async def get_datasource(self, datasource: str) -> Any:
        try:
            source = DATASOURCES[datasource]
        except KeyError:
            return None
        if source.driver_type == 'asyncdb':
            ### making an AsyncDB connection:
            driver = source.driver
            try:
                return AsyncDB(driver, dsn=source.dsn, params=source.params())
            except (DriverError, ProviderError) as ex:
                raise QueryException(
                    f"Error creating AsyncDB instance: {ex}"
                ) from ex
        else:
            raise DriverError(
                f'Invalid Datasource type {source.driver_type} for {datasource}'
            )

    async def default_driver(self, driver: str) -> Any:
        if not supported_drivers(None, driver=driver):
            raise TypeError(
                f"QS: Invalid Database Driver: {driver}"
            )
        clspath = f'querysource.datasources.drivers.{driver}'
        default = None
        try:
            cls = import_module(clspath)
            clsname = f'{driver}_default'
            default = getattr(cls, clsname)
        except (AttributeError, ImportError) as ex:
            # No module for driver exists.
            raise RuntimeError(
                f"QS: There is no default connection for Driver {driver}: {ex}"
            ) from ex
        ### creating a connector for this driver:
        if default.driver_type == 'asyncdb':
            try:
                return AsyncDB(driver, dsn=default.dsn, params=default.params(), loop=self._loop)
            except (DriverError, ProviderError) as ex:
                raise QueryException(
                    f"Error creating AsyncDB instance: {ex}"
                ) from ex

    #### Caching facilities
    def cache_saved(self, checksum: str, loop: asyncio.AbstractEventLoop, task: asyncio.Task):
        """Notification when Query was saved in Cache.
        """
        try:
            if callable(self.post_cache):
                self._thread_func(
                    self.post_cache, checksum, loop
                )
        finally:
            task.cancel()
            loop.stop()

    def save_in_cache(self, checksum: str, result: Any, loop: asyncio.AbstractEventLoop):
        asyncio.set_event_loop(loop)
        loop.set_exception_handler(handle_done_tasks)
        fut = loop.create_task(
            self.caching_data(checksum, result, loop)
        )
        # done callback
        fn = partial(self.cache_saved, checksum, loop)
        fut.add_done_callback(fn)
        try:
            loop.run_until_complete(
                fut
            )
        except Exception as err: # pylint: disable=W0703
            self._logger.debug(
                f'Querysource: Error on caching: {err}'
            )

    async def caching_data(self, checksum: str, result: Any, loop: asyncio.AbstractEventLoop):
        try:
            data = None
            redis = AsyncDB(
                'redis',
                dsn=QUERYSET_REDIS,
                loop=loop
            )
            if not self._timeout:
                self._timeout = 3600
            try:
                data = self._encoder([dict(row) for row in result])
            except Exception as err: # pylint: disable=W0703
                self._logger.error(f'Cache Encode Error: {err}')
            if data:
                async with await redis.connection() as conn:
                    # async with  as conn:
                    result = await conn.setex(
                        checksum,
                        data,
                        self._timeout
                    )
                    self._logger.debug(
                        f"QuerySource: Caching query {checksum} finished at {time.strftime('%X')}"
                    )
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

    def Error(self, message: str, exception: BaseException = None, code: int = 500) -> BaseException:
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
