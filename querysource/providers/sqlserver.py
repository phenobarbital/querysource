"""SQL Server.

Microsoft SQL Server Driver for QuerySource.
"""
from typing import (
    Any,
    Union
)
from collections import defaultdict
from functools import partial
from aiohttp import web
from asyncdb.exceptions import StatementError, ProviderError, NoDataFound
from querysource.exceptions import DriverError, ParserError, DataNotFound
from querysource.types import SafeDict
from querysource.models import QueryModel
from querysource.parsers.sqlserver import msSQLParser
from .abstract import BaseProvider


class sqlserverProvider(BaseProvider):
    """sqlserverProvider.

    Querysource Provider for MS SQL Server.
    """
    __parser__ = msSQLParser

    def __init__(
        self,
        slug: str = '',
        query: Any = None,
        qstype: str = '',
        definition: Union[QueryModel, dict] = None, # Model Object or a dictionary defining a Query.
        conditions: dict = None,
        request: web.Request = None,
        **kwargs
    ):
        """Class Initialization for MS SQL Server Provider."""
        ## setting the perser option to Procedure (or not):
        try:
            if definition.attributes['procedure'] is True:
                self._parser_options = {
                    "is_procedure": True
                }
        except (TypeError, AttributeError, KeyError):
            pass
        super(sqlserverProvider, self).__init__(
            slug=slug,
            query=query,
            qstype=qstype,
            definition=definition,
            conditions=conditions,
            request=request,
            **kwargs
        )
        self.default_fn = 'query'
        self.default_options = {}
        self._arguments: dict = {}
        try:
            if self._definition.attributes['procedure'] is True:
                self.default_fn = 'exec'
                self.default_options = self._definition.attributes['default_options']
        except (AttributeError, KeyError, TypeError):
            pass
        self.is_raw = False
        if qstype == 'slug':
            if self._definition.is_raw is True:
                self.is_raw = True
                self._query = self._definition.query_raw
        else:
            self._query = kwargs['query_raw']
            print('RAW QUERY: ', self._query)
            print(kwargs)
            if kwargs['raw_query']:
                try:
                    self._query = self.get_raw_query(self._query)
                    print(f"= SQL is:: {self._query}")
                except Exception as err:
                    raise DriverError(
                        f'MS SQL Server in SQL: {err}'
                    ) from err


    def get_raw_query(self, query):
        sql = query
        conditions = {**self.replacement }
        if self._conditions:
            return sql.format_map(
                defaultdict(str, SafeDict(**self._conditions))
            )
        else:
            return sql.format_map(
                defaultdict(str, SafeDict(**conditions))
            )

    async def prepare_connection(self):
        await super(sqlserverProvider, self).prepare_connection()
        if not self._connection:
            # TODO: get a new connection
            raise DriverError(
                'SQLServer: Database connection not prepared'
            )
        if self.is_raw is False:
            try:
                self._query = await self._parser.build_query()
                self._arguments = self._parser.filter
            except Exception as ex:
                raise ParserError(
                    f"Unable to parse Query: {ex}"
                ) from ex

    async def close(self):
        try:
           await self._connection.close()
        except Exception: #pylint: disable=W0703
            pass

    async def columns(self):
        # TODO: getting the columns of a prepared sentence
        if self._sentence:
            stmt, _ = await self._connection.prepare(self._query)
            self._columns = [a.name for a in stmt.get_attributes()]
        return self._columns

    async def dry_run(self):
        """Running Build Query and return the Query to be executed (without execution).
        """
        return (self._query, None)

    async def query(self):
        result = []
        error = None
        try:
            error = None
            async with await self._connection.connection() as conn:
                query = getattr(conn, self.default_fn)
                fn = partial(query, **self.default_options)
                if self.default_fn == 'exec':
                    result, error = await conn.exec(
                        self._query, **self._arguments, **self.default_options
                    )
                else:
                    result, error = await fn(self._query)
                if error:
                    return [None, error]
                if result:
                    self._result = result
                else:
                    raise DataNotFound(
                        'Empty Result'
                    )
                return [result, error]
        except StatementError as err:
            raise ProviderError(
                'Statement Error: {err}'
            ) from err
        except (ProviderError, DriverError) as err:
            raise DriverError(
                str(err)
            ) from err
        except (NoDataFound, DataNotFound) as e:
            raise DataNotFound(
                str(e)
            ) from e
        except Exception as err:
            raise DriverError(
                f'Error on QuerySource {err}'
            ) from err
