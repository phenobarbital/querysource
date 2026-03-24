"""DeltaTable Provider.

QuerySource Provider for DeltaTable (Delta Lake) sources using asyncdb's delta driver.
Supports SQL queries via DuckDB, partition filtering, schema inspection,
and DataFrame conversion.
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
    from ..parsers.deltatbl import DeltaTableParser
except ImportError:
    from ..parsers.sql import SQLParser as DeltaTableParser


class deltatblProvider(BaseProvider):
    """DeltaTable Provider.

    Queries parquet-based DeltaTable (Delta Lake) sources via asyncdb's delta driver.
    Supports DuckDB SQL queries, partition filtering, schema inspection,
    and multiple output formats (pandas, arrow, polars).

    Attributes:
        __parser__: DeltaTableParser for SQL query building.
        _delta_path: Filesystem path to the Delta table.
        _delta_tablename: Table alias for DuckDB SQL queries.
        _factory: Output format (pandas, arrow, polars).
        _mode: Write mode for table creation (append, overwrite, error, ignore).
    """
    __parser__ = DeltaTableParser
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
        self._delta_path: Optional[str] = kwargs.pop('delta_path', None)
        self._delta_tablename: Optional[str] = kwargs.pop('delta_tablename', None)
        self._factory: str = kwargs.pop('factory', 'pandas')
        self._mode: str = kwargs.pop('mode', 'append')
        super(deltatblProvider, self).__init__(
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
                # Extract path from definition source if not provided
                if not self._delta_path and self._definition:
                    try:
                        self._delta_path = self._definition.source
                    except AttributeError:
                        pass
        elif qstype == 'raw':
            self.is_raw = True

        # Extract options from conditions
        if self._conditions:
            if not self._delta_path:
                self._delta_path = self._conditions.pop('delta_path', None)
            if not self._delta_tablename:
                self._delta_tablename = self._conditions.pop('delta_tablename', None)
                if not self._delta_tablename:
                    self._delta_tablename = self._conditions.pop('tablename', None)
            self._factory = self._conditions.pop('factory', self._factory)
            self._mode = self._conditions.pop('mode', self._mode)

    def checksum(self):
        """Generate a checksum for the query."""
        name = f'{self._slug}:{self._delta_path}:{self._conditions!s}'
        return hashlib.sha1(f'{name}'.encode('utf-8')).hexdigest()

    async def prepare_connection(self) -> Callable:
        """Prepare the DeltaTable connection and build the query.

        Raises:
            DriverError: When connection is not available.
            ParserError: When query parsing fails.
        """
        if not self._connection:
            raise DriverError(
                'DeltaTable: Database connection not prepared'
            )
        if self.is_raw is False:
            await super(deltatblProvider, self).prepare_connection()
            try:
                self._query = await self._parser.build_query()
                # Propagate parser-resolved values
                if hasattr(self._parser, 'delta_path') and self._parser.delta_path:
                    self._delta_path = self._parser.delta_path
                if hasattr(self._parser, 'delta_tablename') and self._parser.delta_tablename:
                    self._delta_tablename = self._parser.delta_tablename
                if hasattr(self._parser, '_factory') and self._parser._factory:
                    self._factory = self._parser._factory
                if hasattr(self._parser, '_mode') and self._parser._mode:
                    self._mode = self._parser._mode
            except Exception as ex:
                raise ParserError(
                    f"DeltaTable: Unable to parse Query: {ex}"
                ) from ex

    async def columns(self):
        """Return the schema columns of the DeltaTable.

        Returns:
            list: Column names from the DeltaTable schema.
        """
        if self._connection:
            try:
                async with await self._connection.connection() as conn:
                    schema = conn.schema()
                    if hasattr(schema, 'names'):
                        self._columns = list(schema.names)
                    elif isinstance(schema, dict):
                        self._columns = list(schema.keys())
                    else:
                        self._columns = []
            except Exception as err:
                self._logger.warning(
                    f"DeltaTable: Error getting columns: {err}"
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
                f"DeltaTable: Unable to parse Query: {ex}"
            ) from ex
        return (self._query, None)

    async def query(self):
        """Execute a SQL query on the DeltaTable via DuckDB.

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
                    sentence=self._query,
                    tablename=self._delta_tablename or 'arrow_dataset',
                    factory=self._factory
                )
            if error:
                return [result, error]
            if not is_empty(result):
                self._result = result
                return [self._result, error]
            else:
                raise self.NotFound(
                    'DeltaTable: Empty Result'
                )
        except (DataNotFound, NoDataFound) as ex:
            raise self.NotFound(
                f'DeltaTable: Empty Result: {ex}'
            ) from ex
        except (ProviderError, DriverError) as ex:
            raise QueryException(
                f"DeltaTable Query Error: {ex}"
            ) from ex
        except Exception as err:
            self._logger.exception(err, stack_info=False)
            raise self.Error(
                "DeltaTable: Uncaught Error",
                exception=err,
                code=406
            )

    async def close(self):
        """Close the DeltaTable provider connection."""
        try:
            await self._connection.close()
        except (ProviderError, DriverError, RuntimeError):
            pass
