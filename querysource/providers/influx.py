from typing import (
    Union,
    Any
)
from collections.abc import Callable
import hashlib
from aiohttp import web
from asyncdb.exceptions import ProviderError, DriverError, NoDataFound
from querysource.models import QueryModel
from querysource.exceptions import (
    DataNotFound,
    QueryException,
    ParserError
)
from querysource.parsers.influx import InfluxParser
from .abstract import BaseProvider


class influxProvider(BaseProvider):
    __parser__ = InfluxParser

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
        super(influxProvider, self).__init__(
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
            else:
                try:
                    if not self._parser.bucket:
                        self._parser.bucket = self._program
                    if self._parser.measurement is None:
                        table = self._definition.source if self._definition.source else slug
                        self._parser.measurement = table
                except Exception as err:
                    raise DriverError(
                        f"Exception InfluxDB: {err}"
                    ) from err
        elif qstype == 'raw':
            self.is_raw = True # calling without passing the parser:


    def checksum(self):
        name = f'{self._slug}:{self._conditions!s}'
        return hashlib.sha1(f'{name}'.encode('utf-8')).hexdigest()

    async def prepare_connection(self):
        await super(influxProvider, self).prepare_connection()
        if not self._connection:
            # TODO: get a new connection
            raise DriverError(
                'InfluxDB: Database connection not prepared'
            )
        if self.is_raw is False:
            try:
                self._query = await self._parser.build_query()
                self._arguments = self._parser.filter
            except Exception as ex:
                raise ParserError(
                    f"InfluxDB: Unable to parse Query: {ex}"
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
            self._query = await self._parser.build_query()
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
            async with await self._connection.connection() as conn:
                result, error = await conn.query(self._query, frmt='recordset')
            if error:
                return [result, error]
            if result:
                # check if return a dataframe instead
                self._result = result
                return [self._result, error]
            else:
                raise self.NotFound(
                    'Influx: Empty Result'
                )
        except (NoDataFound, DataNotFound) as ex:
            raise self.NotFound(
                f'Influx: Empty Result: {ex}'
            ) from ex
        except (ProviderError, DriverError) as ex:
            raise QueryException(
                f"Influx Query Error: {ex}"
            ) from ex
        except Exception as err:
            self._logger.exception(err, stack_info=False)
            raise self.Error(
                "Influx: Uncaught Error",
                exception=err,
                code=406
            )

    async def close(self):
        try:
            await self._connection.close()
        except (ProviderError, DriverError, RuntimeError):
            pass
