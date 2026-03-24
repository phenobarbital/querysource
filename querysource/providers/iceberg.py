"""Apache Iceberg Provider.

QuerySource Provider for Apache Iceberg catalogs using asyncdb's iceberg driver.
Supports SQL queries via DuckDB, table scanning, schema inspection, and DataFrame conversion.
"""
import hashlib
from typing import Any, Optional, Union
from collections.abc import Callable

from aiohttp import web
from asyncdb.exceptions import DriverError, NoDataFound, ProviderError
from ..exceptions import (
    DataNotFound,
    ParserError,
    QueryException
)
from ..models import QueryModel
from ..types.validators import is_empty
from .abstract import BaseProvider

try:
    from ..parsers.iceberg import IcebergParser
except ImportError:
    from ..parsers.sql import SQLParser as IcebergParser


class icebergProvider(BaseProvider):
    """Apache Iceberg Provider.

    Queries Apache Iceberg catalogs via asyncdb's iceberg driver.
    Supports DuckDB SQL queries, table scans, schema inspection,
    and multiple output formats (pandas, arrow, polars).

    Attributes:
        __parser__: IcebergParser for SQL query building.
        _table_id: Iceberg table identifier (namespace.table).
        _namespace: Iceberg namespace for table listing.
        _factory: Output format (pandas, arrow, polars).
    """
    __parser__ = IcebergParser
    _parser_options: dict = {}

    def __init__(
        self,
        slug: str = '',
        query: Any = None,
        qstype: str = '',
        connection: Callable = None,
        definition: Union[QueryModel, dict] = None,
        conditions: dict = None,
        request: web.Request = None,
        **kwargs
    ):
        self._table_id: Optional[str] = kwargs.pop('table_id', None)
        self._namespace: Optional[str] = kwargs.pop('namespace', None)
        self._factory: str = kwargs.pop('factory', 'pandas')
        super(icebergProvider, self).__init__(
            slug=slug,
            query=query,
            qstype=qstype,
            connection=connection,
            definition=definition,
            conditions=conditions,
            request=request,
            **kwargs
        )
        self.is_raw = False
        if qstype == 'slug':
            if self._definition.is_raw is True:
                self.is_raw = True
            else:
                # Extract table_id from definition source if not provided
                if not self._table_id and self._definition:
                    try:
                        self._table_id = self._definition.source
                    except AttributeError:
                        pass
        elif qstype == 'raw':
            self.is_raw = True

        # Extract table_id and namespace from conditions
        if self._conditions:
            if not self._table_id:
                self._table_id = self._conditions.pop('table_id', None)
            if not self._namespace:
                self._namespace = self._conditions.pop('namespace', None)
            self._factory = self._conditions.pop('factory', self._factory)

    def checksum(self):
        """Generate a checksum for the query."""
        name = f'{self._slug}:{self._table_id}:{self._conditions!s}'
        return hashlib.sha1(f'{name}'.encode('utf-8')).hexdigest()

    async def prepare_connection(self) -> Callable:
        """Prepare the Iceberg connection and build the query.

        Raises:
            DriverError: When connection is not available.
            ParserError: When query parsing fails.
        """
        if not self._connection:
            raise DriverError(
                'Iceberg: Database connection not prepared'
            )
        if self.is_raw is False:
            await super(icebergProvider, self).prepare_connection()
            try:
                self._query = await self._parser.build_query()
                # Propagate parser-resolved values
                if hasattr(self._parser, 'table_id') and self._parser.table_id:
                    self._table_id = self._parser.table_id
                if hasattr(self._parser, 'namespace') and self._parser.namespace:
                    self._namespace = self._parser.namespace
                if hasattr(self._parser, '_factory') and self._parser._factory:
                    self._factory = self._parser._factory
            except Exception as ex:
                raise ParserError(
                    f"Iceberg: Unable to parse Query: {ex}"
                ) from ex

    async def columns(self):
        """Return the schema columns of the Iceberg table.

        Returns:
            list: Column names from the Iceberg table schema.
        """
        if self._connection and self._table_id:
            try:
                async with await self._connection.connection() as conn:
                    await conn.load_table(self._table_id)
                    schema = conn.schema()
                    self._columns = [field.name for field in schema]
            except Exception as err:
                self._logger.warning(
                    f"Iceberg: Error getting columns: {err}"
                )
                self._columns = []
        return self._columns

    async def dry_run(self):
        """Return the query without executing it.

        Returns:
            tuple: (query, error) pair.
        """
        try:
            self._query = await self._parser.build_query()
        except Exception as ex:
            raise ParserError(
                f"Iceberg: Unable to parse Query: {ex}"
            ) from ex
        return (self._query, None)

    async def query(self):
        """Execute a SQL query on the Iceberg catalog via DuckDB.

        Returns:
            list: [result, error] pair.

        Raises:
            DataNotFound: When query returns no data.
            QueryException: On query execution error.
        """
        error = None
        try:
            async with await self._connection.connection() as conn:
                result, error = await conn.query(
                    self._query,
                    table_id=self._table_id,
                    factory=self._factory
                )
            if error:
                return [result, error]
            if not is_empty(result):
                self._result = result
                return [self._result, error]
            else:
                raise self.NotFound(
                    'Iceberg: Empty Result'
                )
        except (DataNotFound, NoDataFound) as ex:
            raise self.NotFound(
                f'Iceberg: Empty Result: {ex}'
            ) from ex
        except (ProviderError, DriverError) as ex:
            raise QueryException(
                f"Iceberg Query Error: {ex}"
            ) from ex
        except Exception as err:
            self._logger.exception(err, stack_info=False)
            raise self.Error(
                "Iceberg: Uncaught Error",
                exception=err,
                code=406
            )

    async def close(self):
        """Close the Iceberg provider connection."""
        try:
            await self._connection.close()
        except (ProviderError, DriverError, RuntimeError):
            pass
