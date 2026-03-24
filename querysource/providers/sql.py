"""
Basic SQL Provider (using asyncdb).

Default QS database.
"""
import hashlib
from collections import defaultdict
from collections.abc import Callable
from typing import Any, Union

from aiohttp import web
from datamodel.typedefs import SafeDict
from asyncdb.exceptions import (
    DriverError,
    NoDataFound,
    ProviderError
)
from ..exceptions import (
    DataNotFound,
    ParserError,
    QueryException
)
from ..models import QueryModel
from ..parsers.sql import SQLParser
from ..types.validators import is_empty
from .abstract import BaseProvider


class sqlProvider(BaseProvider):
    """
    sqlProvider.

    Basic SQL Provider for all RBD databases.
    """
    replacement: dict = {
        "fields": "*",
        "filterdate": "current_date",
        "firstdate": "current_date",
        "lastdate": "current_date",
        "where_cond": "",
        "and_cond": "",
        "filter": ""
    }

    __parser__ = SQLParser
    _PARSER_PLACEHOLDERS = (
        "{where_cond}",
        "{and_cond}",
        "{filter}",
        "{fields}",
        "{group_by}",
        "{grouping}",
        "{order_by}",
        "{ordering}",
        "{querylimit}",
        "{_limit}",
        "{_offset}",
        "{schema}",
        "{table}",
    )
    _PARSER_CONDITION_KEYS = frozenset({
        "where_cond", "and_cond", "filter", "conditions",
        "fields", "add_fields", "distinct",
        "group_by", "grouping", "order_by", "ordering",
        "querylimit", "_limit", "_offset", "paged", "page",
        "filter_options", "qry_options",
        "tablename", "schema", "database",
    })

    @staticmethod
    def _query_preview(query: object, max_chars: int = 220) -> str:
        if not isinstance(query, str):
            return str(query)
        compact = " ".join(query.split())
        digest = hashlib.sha1(compact.encode("utf-8")).hexdigest()[:10]
        if len(compact) > max_chars:
            compact = f"{compact[:max_chars]}..."
        return f"len={len(query)} sha1={digest} sql={compact}"

    @classmethod
    def _needs_parser(cls, query: object) -> bool:
        if not isinstance(query, str):
            return False
        return any(token in query for token in cls._PARSER_PLACEHOLDERS)

    @classmethod
    def _has_parser_conditions(cls, conditions: object) -> bool:
        if not isinstance(conditions, dict):
            return False
        return any(key in cls._PARSER_CONDITION_KEYS for key in conditions.keys())

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
        super(sqlProvider, self).__init__(
            slug=slug,
            query=query,
            qstype=qstype,
            definition=definition,
            conditions=conditions,
            request=request,
            **kwargs
        )
        if qstype == 'slug':
            if self._definition.is_raw is True:
                self.is_raw = True
                self._query = self._definition.query_raw
        elif qstype == 'raw':
            self.is_raw = True  # calling without passing the parser:
            self._query = self.raw_query(self._query)
        elif qstype == 'query':
            self._query = query
            self._logger.debug("= Query is:: %s", self._query_preview(self._query))
            # If query is already rendered and there are no parser placeholders,
            # skip parser path to avoid empty-condition parsing edge cases.
            if not self._conditions and not self._needs_parser(self._query):
                self.is_raw = True
        else:
            self._query = kwargs['query_raw']
            if kwargs['raw_query']:
                try:
                    self._query = self.raw_query(self._query)
                    self._logger.debug("= Query is:: %s", self._query_preview(self._query))
                except Exception as err:
                    raise DriverError(
                        f'DB Error: {err}'
                    ) from err

    async def prepare_connection(self) -> Callable:
        """Signal run before connection is made.
        """
        if not self._connection:
            raise QueryException(
                "Connection Object Missing for this Provider."
            )
        # For direct query mode, only use parser when it is truly required.
        if self._type == 'query':
            use_parser = self._needs_parser(self._query) or self._has_parser_conditions(self._conditions)
            if not use_parser:
                # Keep SQL untouched to avoid formatting collisions with
                # literals that contain braces (e.g. Postgres array/json syntax).
                return self
        if self.is_raw is True:
            return self
        await super(sqlProvider, self).prepare_connection()
        ## Parse Query:
        try:
            self._query = await self._parser.build_query()
        except Exception as ex:
            raise ParserError(
                f"Unable to parse Query: {ex}"
            ) from ex

    def raw_query(self, query: str):
        sql = query
        conditions = {**self.replacement}
        if self._conditions:
            conditions = {**conditions, **self._conditions}
        return sql.format_map(
            defaultdict(str, SafeDict(**conditions))
        )

    def get_raw_query(self, query: str):
        sql = query
        conditions = {**self.replacement}
        if self._conditions:
            return sql.format_map(
                defaultdict(str, SafeDict(**self._conditions))
            )
        else:
            return sql.format_map(
                defaultdict(str, SafeDict(**conditions))
            )

    async def columns(self):
        """Return the columns (fields) involved on the query (when possible).
        """
        raise NotImplementedError(
            f"Columns not implemented for {self.__class__.__name__}"
        )

    async def query(self):
        """Run a query on the Data Provider.
        """
        error = None
        try:
            async with await self._connection.connection() as conn:
                result, error = await conn.query(self._query)
            if error:
                return [result, error]
            if not is_empty(result):
                # check if return a dataframe instead
                self._result = result
                return [self._result, error]
            else:
                raise self.NotFound(
                    'QS: Empty Result'
                )
        except (DataNotFound, NoDataFound) as ex:
            raise self.NotFound(
                f'QS: Empty Result: {ex}'
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
        try:
            await self._connection.close()
        except Exception:  # pylint: disable=W0703
            pass
