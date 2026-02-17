"""ArangoDB Provider.

Data Provider for ArangoDB using AQL queries.
"""
from typing import Any, Union
from collections.abc import Callable
import contextlib
import hashlib
from aiohttp import web
from datamodel.parsers.json import json_decoder
from asyncdb.exceptions import ProviderError, NoDataFound
from ..models import QueryModel
from ..exceptions import (
    QueryError,
    QueryException,
    ParserError,
    DriverError,
    DataNotFound
)
from ..parsers.arangodb import ArangoDBParser
from .abstract import BaseProvider


class arangodbProvider(BaseProvider):
    """ArangoDB query provider.

    Builds AQL queries via ArangoDBParser, executes them through the
    asyncdb ArangoDB driver, and returns the result set.
    """
    __parser__ = ArangoDBParser

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
        super(arangodbProvider, self).__init__(
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
        self._database: str = ''
        self._collection: str = ''

        if qstype == 'slug' and self._definition:
            # Extract database/collection from query_raw JSON
            if self._definition.query_raw:
                try:
                    query_raw = json_decoder(self._definition.query_raw)
                    self._database = query_raw.get('database', '')
                    self._collection = query_raw.get('collection', '')
                    if not self._parser.tablename and self._collection:
                        self._parser.tablename = self._collection
                    if not self._parser.database and self._database:
                        self._parser.database = self._database
                except Exception as err:
                    self._logger.error(
                        f"Unable to parse query_raw for ArangoDB: {err}"
                    )
            # Fallback: use definition source or slug
            if not self._database:
                self._database = getattr(
                    self._definition, 'source', ''
                ) or self._program
            if not self._collection:
                self._collection = getattr(
                    self._definition, 'source', ''
                ) or slug
            if self._definition.is_raw is True:
                self.is_raw = True

    def checksum(self) -> str:
        """Return a SHA-1 checksum of the query identity."""
        name = f'{self._slug}:{self._conditions!s}'
        return hashlib.sha1(name.encode('utf-8')).hexdigest()

    async def prepare_connection(self) -> Callable:
        """Prepare the AQL query for execution."""
        await super(arangodbProvider, self).prepare_connection()
        if not self._connection:
            raise QueryException(
                "Connection Object Missing for ArangoDB Provider."
            )
        if not self.is_raw:
            try:
                self._query = await self._parser.build_query()
                self._logger.debug(
                    f"AQL Query :: {self._query}"
                )
            except Exception as ex:
                raise ParserError(
                    f"Unable to parse AQL Query: {ex}"
                ) from ex

    async def columns(self):
        """Return the columns involved in the query."""
        return self._columns

    async def dry_run(self):
        """Build and return the query without execution."""
        return (self._query, None)

    async def query(self):
        """Execute the AQL query against ArangoDB."""
        result = []
        error = None
        try:
            async with await self._connection.connection() as conn:
                if self._database:
                    await conn.use(self._database)
                result, error = await conn.query(self._query)
                if error:
                    return [None, error]
                if result:
                    self._result = result
                else:
                    raise DataNotFound(
                        f'Empty Result for AQL: {self._query!r}'
                    )
                return [result, error]
        except (NoDataFound, DataNotFound) as exc:
            raise DataNotFound(
                str(exc)
            ) from exc
        except (ParserError, TypeError) as exc:
            raise QueryError(
                f"Error parsing AQL Query: {exc}"
            ) from exc
        except (ProviderError, DriverError) as err:
            raise DriverError(
                str(err)
            ) from err
        except RuntimeError as err:
            raise QueryException(
                f"ArangoDB Query Error: {err}"
            ) from err

    async def close(self):
        """Close the ArangoDB connection."""
        with contextlib.suppress(ProviderError, DriverError, RuntimeError):
            await self._connection.close()
