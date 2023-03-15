"""Base Provider.

Abstract Provider for all Datasource objects.
"""
import asyncio
import copy
import traceback
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any, Union

from aiohttp import web
from navconfig.logging import logging

from querysource.exceptions import DataNotFound, ParserError, QueryException
from querysource.models import QueryModel
from querysource.utils.functions import get_hash


class BaseProvider(ABC):

    __parser__: Callable = None
    _parser_options: dict = {}

    replacement: dict = {
       "fields": "*",
       "filterdate": "current_date",
       "firstdate": "current_date",
       "lastdate": "current_date",
       "where_cond": "",
       "and_cond": "",
       "filter": ""
    }

    def __init__(
        self,
        slug: str = '',
        query: Any = None,
        qstype: str = '',
        connection: Callable = None,
        definition: Union[QueryModel, dict] = None, # Model Object or a dictionary defining a Query.
        conditions: dict = None,
        request: web.Request = None,
        **kwargs
    ):
        self.__name__ = self.__class__.__name__
        self._logger = logging.getLogger(f'QS.{self.__name__}')
        # Provider Object from Table
        self._definition = definition # definition Object
        self._conditions: dict = conditions
        self._connection = connection

        ### basic information
        self._slug: str = slug
        self._type: str = qstype
        self.is_raw: bool = False
        if self._slug:
            try:
                self._query = definition.query_raw
            except AttributeError:
                pass
        else: # is a raw query
            self._query: str = query
        ## Attributes of Query:
        self._columns: list = []
        self._sentence: str = ''
        self._parser: Callable = None
        self._result = None
        self._refresh: bool = False
        self._provider: str = 'base'
        self._program: str = 'default'
        # Aiohttp Request:
        self._request: web.Request = request
        try:
            self._program = self._definition.program_slug
        except (AttributeError, TypeError, KeyError):
            self._program = 'default'
        if conditions:
            # making a copy of conditions:
            self._conditions = copy.deepcopy(conditions)
            if 'refresh' in self._conditions:
                self._refresh = bool(self._conditions['refresh'])
                del self._conditions['refresh']
        else:
            self._conditions: dict = {}
        ### asyncio loop:
        if 'loop' in kwargs:
            self._loop = kwargs['loop']
            del kwargs['loop']
        else:
            self._loop = asyncio.get_event_loop()
        asyncio.set_event_loop(self._loop)
        ## Parser Logic:
        self._parser: Callable = None
        if self.__parser__:
            try:
                self._parser = self.__parser__( # pylint: disable=E1102
                    query=self._query,
                    options=definition,
                    conditions=conditions,
                    connection=connection,
                    **self._parser_options
                )
            except Exception as err:
                self._logger.error(
                    f'ERROR ON PARSER: {err}'
                )
                raise ParserError(
                    f"Error on Query Parser: {err}"
                ) from err

        # driver information
        if 'driver' in kwargs:
            self._driver = kwargs['driver']
            del kwargs['driver']
        else:
            self._driver = None
        self.kwargs = kwargs

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

    def __str__(self) -> str:
        return f"<{self.__name__}>"

    async def prepare_connection(self):
        """Signal run before connection is made.
        """
        ## Calling the parser:
        if self._parser:
            await self._parser.set_options()

    async def columns(self):
        """Return the columns (fields) involved on the query (when possible).
        """
        if self._qs:
            self._columns = await self._qs.columns()
        return self._columns

    async def dry_run(self):
        """Running Build Query and return the Query to be executed (without execution).
        """
        return (self._query, None)

    @abstractmethod
    async def query(self):
        """Run a query on the Data Provider.
        """

    async def close(self):
        """Closing the Provider.
        """
        try:
            await self._qs.close() # pylint: disable=E0203
        except Exception: # pylint: disable=W0703
            pass
        self._qs = None

    @property
    def parser(self):
        return self._parser

    def refresh(self):
        return self._refresh

    def get_resultset(self):
        return self._dict

    def get_result(self):
        return self._result

    def checksum(self):
        return get_hash(self._query)

    def connection(self):
        return self._connection

    def get_query(self):
        return self._query
