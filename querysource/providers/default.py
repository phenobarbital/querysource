"""
Basic PostgreSQL Provider (based on asyncpg).

Default QS database.
"""
from typing import (
    Any,
    Union
)
from collections.abc import Callable
from collections import defaultdict
from aiohttp import web
from datamodel.typedefs import SafeDict
from asyncdb.exceptions import ProviderError, DriverError, NoDataFound
from ..models import QueryModel
from ..exceptions import QueryException, ParserError
from ..parsers.sql import SQLParser
from ..qs_parsers import HAS_RUST
if HAS_RUST:
    from ..qs_parsers import _qs_parsers as _rs
from .abstract import BaseProvider


class defaultProvider(BaseProvider):
    """A Default Provider using AsyncDb for all connections.
    """
    __parser__ = SQLParser

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
        super(defaultProvider, self).__init__(
            slug=slug,
            query=query,
            qstype=qstype,
            definition=definition,
            conditions=conditions,
            request=request,
            **kwargs
        )
        self.is_raw = False
        if qstype == 'slug':
            if self._definition.is_raw is True:
                self.is_raw = True
                self._query = self._definition.query_raw

    async def prepare_connection(self) -> Callable:
        """Signal run before connection is made.
        """
        await super(defaultProvider, self).prepare_connection()
        if not self._connection:
            raise QueryException(
                "Connection Object Missing for this Provider."
            )
        if self.is_raw is False:
            try:
                self._query = await self._parser.build_query()
            except Exception as ex:
                raise ParserError(
                    f"Unable to parse Query: {ex}"
                ) from ex

    def raw_query(self, query: str):
        # SECURITY (FEAT-103): Two-phase substitution (trusted replacement first,
        # then validating substitution for user-supplied conditions).
        if HAS_RUST:
            sql = _rs.safe_format_map(query, self.replacement)
            if self._conditions:
                try:
                    sql = _rs.safe_format_map_validated(sql, self._conditions, self._get_cond_definition())
                except ValueError as err:
                    self._logger.warning(
                        "raw_query: validating substitution rejected conditions: %s", err
                    )
                    raise ParserError("Invalid query conditions") from err
            return sql
        # DIVERGENCE (FEAT-103): pure-Python fallback (HAS_RUST=False) has no
        # injection validation and ignores cond_definition entirely — type
        # hints (e.g. "array") only take effect via the Rust path. Accepted
        # because the Rust extension is required in all supported
        # deployments; replicating context-aware validation in pure Python
        # would duplicate the security-critical logic in two places.
        conditions = {**self.replacement, **(self._conditions or {})}
        return query.format_map(
            defaultdict(str, SafeDict(**conditions))
        )

    async def columns(self):
        """Return the columns (fields) involved on the query (when possible).
        """
        if self._query:
            stmt, _ = await self._connection.prepare(self._query)
            self._columns = [a.name for a in stmt.get_attributes()]
        return self._columns

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
        """Run a query on the Data Provider.
        """
        error = None
        try:
            async with await self._connection.connection() as conn:
                result, error = await conn.query(self._query)
            if error:
                return [result, error]
            if result:
                # check if return a dataframe instead
                self._result = result
                return [self._result, error]
            else:
                raise self.NotFound(
                    'DB: Empty Result'
                )
        except NoDataFound as ex:
            raise self.NotFound(
                f'DB: Empty Result: {ex}'
            ) from ex
        except (ProviderError, DriverError) as ex:
            raise QueryException(
                f"Query Error: {ex}"
            ) from ex
        except Exception as err:
            self._logger.exception(err, stack_info=False)
            raise self.Error(
                "Query: Uncaught Error",
                exception=err,
                code=406
            )

    async def close(self):
        """Closing all resources used by the Provider.
        """
        self._connection = None
