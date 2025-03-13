"""AWS DocumentDB.

Data Provider for AWS DocumentDB.
"""
from typing import (
    Any,
    List,
    Optional,
    Union,
    Tuple
)
from collections import defaultdict
import contextlib
from aiohttp import web
from datamodel.typedefs import SafeDict
from datamodel.parsers.json import json_encoder, json_decoder
from datamodel.exceptions import ParserError as JSONParserError
from asyncdb.exceptions import (
    StatementError,
    ProviderError,
    NoDataFound
)
from ..exceptions import DriverError, ParserError, DataNotFound
from ..models import QueryModel
from ..parsers.mongo import MongoParser
from .abstract import BaseProvider


class documentdbProvider(BaseProvider):
    """documentdbProvider.

    Querysource Provider for AWS DocumentDB (MongoDB-based).
    """
    __parser__ = MongoParser

    def __init__(
        self,
        slug: str = None,
        query: Any = None,
        qstype: str = None,
        definition: Union[QueryModel, dict] = None,  # Model Object or a dictionary defining a Query.
        conditions: dict = None,
        request: web.Request = None,
        **kwargs
    ):
        """Class Initialization for AWS DocumentDB Provider."""
        super(documentdbProvider, self).__init__(
            slug=slug,
            query=query,
            qstype=qstype,
            definition=definition,
            conditions=conditions,
            request=request,
            **kwargs
        )
        self.is_raw = False
        self._database = self._definition.source
        if qstype == 'slug':
            if self._definition.is_raw is True:
                self.is_raw = True
                self._query = self.get_raw_query(self._definition.query_raw)
                self._logger.notice(f"= Query is:: {self._query}")
        else:
            self._query = kwargs['query_raw']
            if kwargs.get('raw_query', False):
                try:
                    self._query = self.get_raw_query(self._query)
                    self._logger.notice(f"= RAW Query: {self._query}")
                except Exception as err:
                    raise DriverError(
                        f'Mongo Query Error: {err}'
                    ) from err

    def get_raw_query(self, query: str) -> dict:
        """
        Process a raw query string with parameter substitution.

        Args:
            query: The raw query string (JSON format)

        Returns:
            dict: Processed MongoDB query object
        """
        if self._conditions:
            try:
                return query.format_map(
                    defaultdict(str, SafeDict(**self._conditions))
                )
            except ValueError:
                pass
        # Parse back into dictionary
        try:
            qry = json_decoder(query)
            _filter = qry.pop('filter', {})
            try:
                qry['query'].update(_filter)
            except KeyError:
                qry['query'] = _filter
            return qry
        except JSONParserError as ex:
            self._logger.error(
                f"Error Parsing Query: {ex}"
            )
            return query

    async def prepare_connection(self):
        """
        Prepare the query for execution.

        This method will use the parser to build the MongoDB query
        from the JSON definition if needed.
        """
        await super(documentdbProvider, self).prepare_connection()
        if not self._connection:
            # TODO: get a new connection
            raise DriverError(
                'DocumentDB: Database connection not prepared'
            )
        if self.is_raw is False:
            try:
                self._query = await self._parser.build_query()
                self._logger.debug(
                    f":: Built Query: {self._query}"
                )
            except Exception as ex:
                raise ParserError(
                    f"Unable to parse Query: {ex}"
                ) from ex

    async def close(self):
        with contextlib.suppress(Exception):
            await self._connection.close()

    async def columns(self) -> List[str]:
        """
        Get the columns (fields) of the query result.

        Returns:
            List[str]: List of column names
        """
        if hasattr(self, '_columns') and self._columns:
            return self._columns

        # For MongoDB, columns come from the projection if specified
        if isinstance(self._query, dict) and 'projection' in self._query:
            projection = self._query['projection']
            if projection:
                # If projection specifies included fields with 1, use those
                included = [field for field, value in projection.items()
                            if value == 1 and field != '_id']
                if included:
                    return included

        # If no projection or it only excludes fields, can't determine columns
        # without executing the query
        return []

    async def dry_run(self) -> Tuple[Any, Optional[Exception]]:
        """
        Run the query preparation without execution and return the query.

        Returns:
            Tuple[Any, Optional[Exception]]: The prepared query and any error
        """
        return (self._query, None)

    async def query(self):
        """Query Build Query and return the Query to be executed."""
        result = []
        error = None
        try:
            error = None
            async with await self._connection.connection() as conn:
                await conn.use(self._database)
                result, error = await conn.query(
                    **self._query
                )
                if error:
                    return [None, error]
                if result:
                    self._result = result
                else:
                    raise DataNotFound(
                        f'Empty Result for {self._query!r}'
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
