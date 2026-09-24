"""
Basic PostgreSQL Provider (based on asyncpg).

Default QS database.
"""
from typing import Any, Union
from aiohttp import web
from ..exceptions import ParserError
from ..models import QueryModel
from ..parsers.pgsql import pgSQLParser
from ..qsurl import capabilities as qsurl_caps
from .sql import sqlProvider


class pgProvider(sqlProvider):
    """
    pgProvider.

    Provider for PostgreSQL.
    """

    __parser__ = pgSQLParser
    capabilities = sqlProvider.capabilities | {qsurl_caps.TEXT_MATCH}

    def __init__(
        self,
        slug: str = '',
        query: Any = None,
        qstype: str = '',
        definition: Union[QueryModel, dict] = None,  # Model Object or a dictionary defining a Query.
        conditions: dict = None,
        request: web.Request = None,
        **kwargs
    ):
        self.is_raw = False
        super(pgProvider, self).__init__(
            slug=slug,
            query=query,
            qstype=qstype,
            definition=definition,
            conditions=conditions,
            request=request,
            **kwargs
        )

    async def columns(self):
        """Return the columns (fields) involved on the query (when possible).
        """
        if self._query:
            try:
                async with await self._connection.connection() as conn:
                    stmt, _ = await conn.prepare(self._query)
                    self._columns = [a.name for a in stmt.get_attributes()]
            except AttributeError as ex:
                raise ParserError(
                    f"Invalid Query or Column for query: {self._query}"
                ) from ex
        return self._columns

    async def describe_columns(self) -> list[dict]:
        """Prepare (never execute) the query and return typed columns.

        Returns:
            ``[{'name': a.name, 'type': a.type.name}]`` from the prepared statement,
            or ``[]`` when there is no rendered query.

        Raises:
            ParserError: When the statement cannot be described (same as :meth:`columns`).
        """
        if not self._query:
            return []
        try:
            async with await self._connection.connection() as conn:
                stmt, _ = await conn.prepare(self._query)
                return [
                    {
                        "name": a.name,
                        "type": getattr(getattr(a, "type", None), "name", None)
                    }
                    for a in stmt.get_attributes()
                ]
        except AttributeError as ex:
            raise ParserError(
                f"Invalid Query or Column for query: {self._query}"
            ) from ex
