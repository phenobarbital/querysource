"""
QuerySource.
QS.
Get queries from databases and other data sources.

QS uses "slugs" (named queries) to know which query need to be executed.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import partial

from aiohttp import web
from asyncdb.exceptions import (
    NoDataFound,
    ProviderError,
    StatementError,
    DriverError
)

from querysource.connections import QueryConnection
from querysource.exceptions import (
    CacheException,
    DataNotFound,
    EmptySentence,
    QueryException,
    QueryError,
    SlugNotFound,
)
from querysource.providers import BaseProvider  # renamed to Providers.
from querysource.types.mutables import ClassDict
from querysource.types.typedefs import AttrDict
from .abstract import BaseQuery


class QS(BaseQuery):
    """
    QS.

       Query multiple data-origins for Navigator.
    """
    def __init__(
            self,
            slug: str = '',
            conditions: dict = None,
            request: web.Request = None,
            loop: asyncio.AbstractEventLoop = None,
            **kwargs
    ):
        super(QS, self).__init__(
            slug,
            conditions=conditions,
            request=request,
            loop=loop,
            **kwargs
        )
        if not conditions:
            conditions = {}
        self._timeout = 3600
        self._qs: BaseProvider = None
        self._query: str = None
        self._type: str = ''
        self.is_cached: bool = False
        if 'dwh' in kwargs:
            self._dwh = kwargs['dwh']
            del kwargs['dwh']
        elif 'dwh' in conditions:
            self._dwh = conditions['dwh']
            del conditions['dwh']
        # if slug:
        if slug:
            self._logger.debug(f'Initialize Slug: {slug!s}')
            self._query = slug
            self._type = 'slug'
        elif 'query' in self.kwargs:
            try:
                self._query = self.kwargs['query']
                self._type = 'query'
                del self.kwargs['query']
            except KeyError as ex:
                raise ValueError(
                    'QuerySource Error: needed *slug*, *query* or *raw query* in arguments'
                ) from ex
            try:
                self._driver = self.kwargs['driver']
                del self.kwargs['driver']
            except KeyError:
                self._driver = 'db'
        elif 'raw_query' in self.kwargs:
            try:
                self._query = self.kwargs['raw_query']
                self._type = 'raw'
                del self.kwargs['raw_query']
            except KeyError as ex:
                raise ValueError(
                    'QuerySource Error: needed *slug* or a *raw_query*'
                ) from ex
        elif 'driver' in self.kwargs:
            self._driver = self.kwargs['driver']
            self._query = self._driver
            del self.kwargs['driver']
            self._type = 'driver'
        if not self._query:
            raise EmptySentence(
                "QS Error: Empty Query, Driver or Slug."
            )
        if 'lazy' in kwargs:
            lazy = kwargs['lazy']
            del kwargs['lazy']
        else:
            lazy = True
        ### Connection is always one single object (singleton)
        self.connection = QueryConnection(lazy=lazy, loop=self._loop)

    def __repr__(self) -> str:
        return f'<QS: {self._type}:"{self._query}" >'

    @property
    def timeout(self):
        return self._timeout

    @timeout.setter
    def timeout(self, timeout: int = 3600):
        self._timeout = timeout
        return self

    def get_source(self):
        return self._qs

    async def columns(self):
        """
        columns
           prepare the sentence and return the columns
        """
        if self._qs:
            self._columns = await self._qs.columns()
        return self._columns

    async def build_provider(self):
        """
        build_provider.
           create a query based on a query_slug, a raw query or an Object Query.
        """
        if not self._query:
            raise EmptySentence(
                "QS Error: cannot run with Empty Query/Sentence."
            )
        if self._type == 'slug': # query-based provider:
            self._logger.debug(f'Starting Slug-based Query: {self._query!s}')
            try:
                objquery = await self.connection.get_slug(self._query, program=self._program)
            except (SlugNotFound):
                raise
            except Exception:
                raise
            ### getting the connection and the provider from Slug:
            try:
                self._conn, self._provider = await self.connection.from_provider(objquery)
            except (QueryException, DriverError, ProviderError) as ex:
                raise QueryException(
                    str(ex)
                ) from ex
            except Exception as ex:
                raise QueryException(
                    f"{ex}"
                ) from ex
            ### Check conditions:
            conditions = {}
            # get all information for query
            self.is_cached = objquery.is_cached
            # timeout in the cache.
            try:
                self.timeout = objquery.cache_timeout
            except (TypeError, AttributeError):
                self.timeout = 3600
            if self._conditions:
                try:
                    conditions = {**objquery.conditions, **self._conditions}
                except (AttributeError,TypeError):
                    conditions = {**self._conditions}
            else:
                if objquery.conditions:
                    conditions = {**objquery.conditions}
            ### TODO: moving build conditions from request to middleware:
            ## if self._request is not None:
            ## user = await get_session(self._request, new=False)
            # TODO: try to discovering the type of conditions
            self._logger.debug(f":: = SLUG {self._query}, provider: {self._provider!s}")
            try:
                args = {
                    "slug": self._query,
                    "query": self._query,
                    "qstype": self._type,
                    "connection": self._conn,
                    "definition": objquery,
                    "conditions": conditions,
                    "request": self._request,
                    "loop": self._loop
                }
                self._qs = self._provider(**args)
                await self._qs.prepare_connection()
                return self
            except Exception as err:
                self._logger.exception(
                    f"Cannot Initialize the provider {self._provider}, error: {err}"
                )
                raise QueryError(
                    f"Cannot Initialize the provider {self._provider}, error: {err}"
                ) from err
        elif self._type == 'query':
            ## Query Object (TBD)
            self._logger.debug(
                f'Starting Query: {self._query!s} for {self._driver}'
            )
            ### build manually objquery:
            objquery = AttrDict({"provider": self._driver})
            ### getting the connection and the provider from Slug:
            try:
                self._conn, self._provider = await self.connection.from_provider(objquery)
            except (QueryException, DriverError) as ex:
                raise QueryError(
                    str(ex)
                ) from ex
            ### Check conditions:
            conditions = {}
            self.timeout = 3600
            conditions = {**self._conditions}
            self._logger.debug(f":: = Query, provider: {self._provider!s}")
            try:
                args = {
                    "slug": self._query,
                    "query": self._query,
                    "qstype": self._type,
                    "connection": self._conn,
                    "definition": None,
                    "conditions": conditions,
                    "request": self._request,
                    "loop": self._loop,
                    **self.kwargs
                }
                self._qs = self._provider(**args)
                await self._qs.prepare_connection()
                return self
            except Exception as err:
                self._logger.exception(
                    f"Cannot Initialize the provider {self._provider}, error: {err}"
                )
                raise QueryError(
                    f"Cannot Initialize the provider {self._provider}, error: {err}"
                ) from err
        elif self._type == 'raw':
            ## Raw Query
            pass
        elif self._type == 'driver':
            ### calling an HTTP, REST or other provider:
            self._logger.debug(
                f'Starting Driver: {self._driver!s}, Args: {self.kwargs!s}'
            )
            try:
                driver = ClassDict({"provider": self._driver['driver']})
                del self._driver['driver']
            except KeyError:
                self._logger.error('QS: Missing Driver declaration on Request.')
                driver = 'rest'
            try:
                self._conn, self._provider = await self.connection.from_provider(driver)
            except (QueryException, DriverError) as ex:
                raise QueryException(
                    str(ex)
                ) from ex
            ### build object:
            args = {
                "slug": self._query,
                "query": self._query,
                "qstype": self._type,
                "connection": self._conn,
                "definition": None,
                "conditions": self._conditions,
                "request": self._request,
                **self._driver
            }
            try:
                self._qs = self._provider(**args)
                await self._qs.prepare_connection()
                return self
            except Exception as err:
                self._logger.exception(
                    f"Cannot Initialize Provider {self._provider}, error: {err}"
                )
                raise QueryError(
                    f"Cannot Initialize Provider {self._provider}, error: {err}"
                ) from err
        else:
            raise QueryError(
                f"Invalid type of Query: {self._query}"
            )


    async def query(self, output_format: str = None):
        result = []
        error = None
        self._result = []
        exists = False
        if not self._qs:
            await self.build_provider()
        refresh = self._qs.refresh()
        ## check the provider:
        if output_format is not None:
            self.output_format(output_format)
        try:
            self._logger.debug(f"= Refresh status: {refresh}")
            if self.is_cached is True:
                # get the cache
                self._logger.debug('= Query Cache is Enabled =')
                checksum = self._qs.checksum()
                self._logger.debug(f"= Query Checksum is {checksum}")
                try:
                    exists = bool(
                        await self.connection.in_cache(checksum)
                    )
                    self._logger.debug(f"= Detected on Cache? {exists}")
                except (ProviderError, DriverError, RuntimeError) as err:
                    self._logger.error(f'Error over caching System: {err!s}')
                    self.is_cached = False
                    exists = False
                    result = []
            if refresh is True and exists is True:
                # we need to refresh the query and got from database
                exists = False
                error = None
                result = []
            # get this query from cache
            if self.is_cached is True and exists is True:
                # cache exists from this query
                try:
                    result = await self.connection.get_from_cache(checksum)
                except (ProviderError, DriverError, RuntimeError) as err:
                    self._logger.warning(
                        f'Querysource: Error getting from Cache: {err!s}'
                    )
                if result:
                    self._logger.debug(
                        f"Query {checksum} was cached!"
                    )
                    result = self._encoder.loads(result)
                    self._result = result
                    return await self._output_format(self._result, error)  # pylint: disable=W0150
            # getting data directly from provider instead:
            self._logger.debug('= Query from PROVIDER =')
            async with self.semaphore: # pylint: disable=E1701
                try:
                    self._logger.debug(
                        f':: Query: {self._query}'
                    )
                    result, error = await self._qs.query()
                    if error:
                        if isinstance(error, DataNotFound):
                            raise error
                        return await self._output_format(self._result, error)  # pylint: disable=W0150
                except (NoDataFound, DataNotFound) as err:
                    raise DataNotFound(
                        f'{self._qs.__name__!s}: {err}'
                    ) from err
                except (StatementError, EmptySentence) as err:
                    raise QueryError(
                        f"Query Error: {err}"
                    ) from err
                except Exception as ex:
                    raise self.Error(
                        "QS unhandler Error",
                        exception=ex,
                        code=500
                    )
                if result:
                    self._result = result
                    ## Saving into Cache:
                    if result and self.is_cached is True:
                        try:
                            loop = asyncio.new_event_loop()
                            fn = partial(
                                self.save_in_cache,
                                checksum,
                                result,
                                loop
                            )
                            with ThreadPoolExecutor(max_workers=2) as pool:
                                status = loop.run_in_executor(pool, fn)
                                self._logger.debug(f'Saved in cache with status: {status}')
                        except (CacheException, ProviderError) as err:
                            self._logger.error(f'Redis Saving Error {err!s}')
                        finally:
                            loop.stop()
                    return await self._output_format(self._result, error)  # pylint: disable=W0150
                else:
                    raise DataNotFound(
                        f'{self._qs.__name__!s} Empty Result'
                    )
        finally:
            try:
                await self.connection.dispose(
                    self._conn
                )
            except AttributeError:
                pass

    async def close(self):
        if self._conn:
            await self.connection.dispose(
                self._conn
            )
        try:
            await self._qs.close()
        except Exception: # pylint: disable=W0703
            pass

    async def dry_run(self):
        if not self._qs:
            await self.build_provider()
        try:
            result, error = await self._qs.dry_run()
            return [result, error]
        except Exception:  # pylint: disable=W0706
            raise
