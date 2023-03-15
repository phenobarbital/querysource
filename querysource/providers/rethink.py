from typing import (
    Union,
    Any
)
from collections.abc import Callable
import hashlib
from aiohttp import web
from asyncdb.exceptions import ProviderError
from querysource.models import QueryModel
from querysource.exceptions import (
    QueryException,
    ParserError,
    DriverError,
    DataNotFound
)
from querysource.parsers.rethink import RethinkParser
from .abstract import BaseProvider


class rethinkProvider(BaseProvider):
    __parser__ = RethinkParser

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
        super(rethinkProvider, self).__init__(
            slug=slug,
            query=query,
            qstype=qstype,
            connection=connection,
            definition=definition,
            conditions=conditions,
            request=request,
            **kwargs
        )
        # getting conditions
        self.is_raw = False
        if qstype == 'slug':
            if self._definition.is_raw is True:
                self.is_raw = True # calling without passing the parser:
            try:
                if not self._parser.database:
                    self._parser.database = self._program
                if not self._parser.table:
                    table = self._definition.source if self._definition.source else slug
                    self._parser.table = table
            except Exception as err:
                raise DriverError(
                    f"Exception running Rethink Server: {err}"
                ) from err


    def checksum(self):
        name = f'{self._slug}:{self._conditions!s}'
        return hashlib.sha1(f'{name}'.encode('utf-8')).hexdigest()

    async def prepare_connection(self) -> Callable:
        """Signal run before connection is made.
        """
        await super(rethinkProvider, self).prepare_connection()
        if not self._connection:
            raise QueryException(
                "Connection Object Missing for this Provider."
            )

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
            self._query = await self._parser.build_query(run=False)
        except Exception as ex:
            raise ParserError(
                f"Unable to parse Query: {ex}"
            ) from ex
        return (self._query, None)

    async def query(self):
        """
        query
           get data from rethinkdb
           TODO: need to check datatypes
        """
        result = []
        error = None
        try:
            result = await self._parser.build_query(run=True)
            if result:
                self._result = result
            else:
                raise DataNotFound("No data was found")
            return [result, error]
        except (RuntimeError, ParserError) as err:
            raise Exception(
                f"Querysource RT Error: {err}"
            ) from err

    async def close(self):
        try:
            await self._connection.close()
        except (ProviderError, DriverError, RuntimeError):
            pass
