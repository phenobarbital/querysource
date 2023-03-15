from typing import (
    Union,
    Any
)
from collections.abc import Callable
from abc import ABC, abstractmethod
import hashlib
from aiohttp import web
from navconfig.logging import logger
from querysource.models import QueryModel
from querysource.exceptions import (
    DriverError,
    ParserError
)
from .abstract import BaseProvider

logger.disable('urllib3.connectionpool')

class externalProvider(BaseProvider, ABC):
    __parser__ = None
    drvname = '_EXTERNAL_'

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
        # getting conditions
        self.is_raw = False
        ## parser arguments:
        self._parser_args: list = []
        if qstype == 'slug':
            if self._definition.is_raw is True:
                self.is_raw = True # calling without passing the parser:
            self._query = self._definition.query_raw
        elif qstype == 'raw':
            self.is_raw = True # calling without passing the parser:
            self._query = self.raw_query(self._query)
        elif qstype == 'query':
            try:
                self.is_raw = kwargs['raw_query']
            except KeyError:
                self.is_raw = False
            self._query = query
        else:
            self.is_raw = True
            self._query = query
        super(externalProvider, self).__init__(
            slug=slug,
            query=query,
            qstype=qstype,
            connection=connection,
            definition=definition,
            conditions=conditions,
            request=request,
            **kwargs
        )

    def refresh(self) -> bool:
        return True

    def checksum(self):
        name = f'{self._query}:{self._conditions!s}'
        return hashlib.sha1(f'{name}'.encode('utf-8')).hexdigest()

    @abstractmethod
    async def get_connection(self):
        ## used for creating Object connection
        pass

    async def prepare_connection(self):
        await super(externalProvider, self).prepare_connection()
        if not self._connection:
            # TODO: get a new connection
            raise DriverError(
                f'{self.drvname}: connection is not prepared'
            )
        ### getting connection object:
        await self.get_connection()
        if self.is_raw is False:
            try:
                self._query = await self._parser.build_query(
                    *self._parser_args
                )
                self._arguments = self._parser.filter
            except Exception as ex:
                raise ParserError(
                    f"{self.drvname}: Unable to parse Query: {ex}"
                ) from ex

    async def columns(self):
        if self._connection:
            try:
                self._columns = await self._parser.columns()
            except Exception as err: # pylint: disable=W0703
                print(
                    f"Empty Result: {err}"
                )
                self._columns = []
            return self._columns
        else:
            return False

    async def dry_run(self):
        """Running Build Query and return the Query to be executed (without execution).
        """
        try:
            self._query = await self._parser.build_query(
                *self._parser_args
            )
        except Exception as ex:
            raise ParserError(
                f"{self.drvname} Unable to parse Query: {ex}"
            ) from ex
        return (self._query, None)

    @abstractmethod
    async def _get_query(self) -> tuple:
        """_get_query
            Used for getting data from provider.
        Returns:
            tuple: returns a tuple with "result" and "error" if any.
        """

    async def query(self):
        """
        query
           get data from SalesForce
        """
        result, error = await self._get_query()
        if error:
            return [result, error]
        if result:
            # check if return a dataframe instead
            self._result = result
            return [self._result, error]
        else:
            raise self.NotFound(
                f'{self.drvname}: Empty Result'
            )

    async def close(self):
        pass
