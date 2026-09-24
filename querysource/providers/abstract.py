"""Base Provider.

Abstract Provider for all Datasource objects.
"""
import asyncio
import copy
import re
import traceback
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any, Union

from aiohttp import web
from navconfig.logging import logging

from ..exceptions import DataNotFound, ParserError, QueryException, RawQueryPlaceholderError
from ..models import QueryModel
from ..parsers.abstract import AbstractParser
from ..qsurl import capabilities as qsurl_caps
from ..types import to_flag
from ..utils.functions import get_hash

# Replacement placeholders, same key rule as the Rust safe_format_map (which
# also substitutes inside quotes, e.g. ``day = '{day}'``).
_PLACEHOLDER = re.compile(r"\{([A-Za-z0-9_.]+)\}")
# Why a raw query's leftover placeholders can never be filled.
RAW_DEFINITION_REASON = (
    "is_raw=True queries bypass the parser, so placeholders are never replaced; "
    "remove them from query_raw or set is_raw=False"
)
RAW_QUERY_REASON = "no value was supplied for them in the conditions"


class BaseProvider(ABC):

    __parser__: AbstractParser = None
    _parser_options: dict = {}

    #: qsurl capabilities this provider renders natively (querysource/qsurl/capabilities.py).
    capabilities: frozenset[str] = qsurl_caps.BASE
    #: False when a residual-only qsurl filter would be a full scan the store must not run.
    residual_scan: bool = True

    replacement: dict = {
        "fields": "*",
        "filterdate": "current_date",
        "firstdate": "current_date",
        "lastdate": "current_date",
        "where_cond": "",
        "and_cond": "",
        "filter": ""
    }

    def __init__(
        self,
        slug: str = '',
        query: Any = None,
        qstype: str = '',
        connection: Callable = None,
        definition: Union[QueryModel, dict] = None,  # Model Object or a dictionary defining a Query.
        conditions: dict = None,
        request: web.Request = None,
        **kwargs
    ):
        self.__name__ = self.__class__.__name__
        self._logger = logging.getLogger(f'QS.{self.__name__}')
        # Provider Object from Table
        self._definition = definition  # definition Object
        self._conditions: dict = conditions
        self._connection = connection

        ### basic information
        self._slug: str = slug
        self._type: str = qstype
        self.is_raw: bool = False
        self._query: str = query  # Initialize _query with the parameter first
        if self._slug:
            try:
                self._query = definition.query_raw
            except AttributeError:
                pass  # Keep the original query parameter value
        ## Attributes of Query:
        self._columns: list = []
        self._sentence: str = ''
        self._parser: AbstractParser = None
        self._result = None
        self._refresh: bool = False
        self._provider: str = 'base'
        self._program: str = 'default'
        # Aiohttp Request:
        self._request: web.Request = request
        try:
            self._program = self._definition.program_slug
        except (AttributeError, TypeError, KeyError):
            self._program = 'default'
        if conditions:
            # making a copy of conditions:
            self._conditions = copy.deepcopy(conditions)
            if 'refresh' in self._conditions:
                raw_refresh = self._conditions['refresh']
                try:
                    self._refresh = to_flag(raw_refresh)
                except ValueError:
                    self._logger.warning(
                        "Unrecognized 'refresh' condition value %s; treating as False",
                        repr(raw_refresh)[:64]
                    )
                    self._refresh = False
                del self._conditions['refresh']
        else:
            self._conditions: dict = {}
        ### asyncio loop:
        if 'loop' in kwargs:
            self._loop = kwargs['loop']
            del kwargs['loop']
        else:
            try:
                self._loop = asyncio.get_running_loop()
            except RuntimeError as ex:
                raise RuntimeError(
                    f"There is no Running Loop on Query Provider: {ex}"
                ) from ex
        ## Parser Logic:
        self._parser: Callable = None
        if self.__parser__:
            try:
                self._parser = self.__parser__(  # pylint: disable=E1102
                    query=self._query,
                    definition=definition,
                    conditions=conditions,
                    **self._parser_options
                )
            except Exception as err:
                self._logger.error(
                    f'ERROR ON PARSER: {err}'
                )
                raise ParserError(
                    f"Error on Query Parser: {err}"
                ) from err

        # driver information
        if 'driver' in kwargs:
            self._driver = kwargs['driver']
            del kwargs['driver']
        else:
            self._driver = None
        self.kwargs = kwargs

    def accepts(self) -> str:
        return None

    def get_definition(self) -> Union[QueryModel, dict]:
        """Return the definition of the Query.
        """
        return self._definition

    def _get_cond_definition(self) -> dict:
        """Return the per-placeholder type hints (`cond_definition`) declared on
        the Query definition, used by the Rust validating substitution
        (`safe_format_map_validated`) to type-check raw-query conditions.

        Supports both `QueryModel` objects (attribute access) and plain dicts,
        and normalizes a missing/None definition to an empty dict.
        """
        definition = self._definition
        if isinstance(definition, dict):
            cond_definition = definition.get('cond_definition')
        else:
            cond_definition = getattr(definition, 'cond_definition', None)
        return cond_definition or {}

    def _udf_resolved_conditions(self) -> dict:
        """Conditions with relative-date keywords resolved for the Rust substitution.

        Returns:
            A new dict from ``resolve_udf_conditions(self._conditions, cond_definition)``;
            ``self._conditions`` is never mutated.
        """
        from ..types.validators import resolve_udf_conditions
        return resolve_udf_conditions(
            dict(self._conditions or {}), self._get_cond_definition()
        )

    @staticmethod
    def find_placeholders(query: object) -> list[str]:
        """Return the ``{placeholder}`` names left in a SQL statement.

        Uses the same key rule as the Rust ``safe_format_map`` substitution
        (ASCII alphanumerics, ``_`` and ``.``), quoted or not, so it reports
        exactly what that substitution would have replaced. Brace literals that
        are not identifiers (``'{a,b}'::text[]``, JSON ``'{"k": 1}'``) are not
        reported; a single-element array literal must be written ``ARRAY['a']``.

        Args:
            query: The statement to inspect; non-strings have no placeholders.

        Returns:
            Placeholder names in order of first appearance, without duplicates.
        """
        if not isinstance(query, str) or '{' not in query:
            return []
        return list(dict.fromkeys(_PLACEHOLDER.findall(query)))

    def _check_raw_placeholders(self, query: object, reason: str) -> None:
        """Raise when a raw (parser-bypassing) query still carries placeholders.

        Args:
            query: The final statement that would be sent to the datasource.
            reason: Why the placeholders cannot be filled, appended to the message.

        Raises:
            RawQueryPlaceholderError: If ``query`` contains ``{placeholder}`` tokens.
        """
        placeholders = self.find_placeholders(query)
        if placeholders:
            names = ', '.join(f'{{{name}}}' for name in placeholders)
            label = f"Raw query {self._slug!r}" if self._type == 'slug' else "Raw query"
            raise RawQueryPlaceholderError(
                f"{label} has unresolved placeholders {names}: {reason}",
                placeholders=placeholders,
            )

    def NotFound(self, message: str):
        """Raised when Data not Found.
        """
        return DataNotFound(message, code=404)

    def Error(
        self,
        message: str,
        exception: BaseException = None,
        code: int = 500
    ) -> BaseException:
        """Error.

        Useful Function to raise Exceptions.
        Args:
            message (str): Exception Message.
            exception (BaseException, optional): Exception captured. Defaults to None.
            code (int, optional): Error Code. Defaults to 500.

        Returns:
            BaseException: an Exception Object.
        """
        trace = None
        message = f"{message}: {exception!s}"
        if exception:
            trace = traceback.format_exc(limit=20)
        return QueryException(
            message,
            stacktrace=trace,
            code=code
        )

    def __str__(self) -> str:
        return f"<{self.__name__}>"

    async def prepare_connection(self):
        """Signal run before connection is made.
        """
        ## Calling the parser:
        if self._parser:
            await self._parser.set_options()

    async def columns(self):
        """Return the columns (fields) involved on the query (when possible).
        """
        if self._qs:
            self._columns = await self._qs.columns()
        return self._columns

    async def describe_columns(self) -> list[dict]:
        """Return output columns as ``[{'name': str, 'type': Optional[str]}]``.

        Default: untyped names derived from :meth:`columns`. Never executes the query.
        Returns ``[]`` when column discovery is unsupported or yields nothing.
        """
        try:
            cols = await self.columns()
        except (AttributeError, NotImplementedError):
            return []
        if not cols:
            return []
        result = []
        for item in cols:
            if isinstance(item, str):
                result.append({"name": item, "type": None})
            elif isinstance(item, dict):
                result.append({
                    "name": str(item.get("name", "")),
                    "type": item.get("type")
                })
            else:
                result.append({"name": str(item), "type": None})
        return result

    async def dry_run(self):
        """Running Build Query and return the Query to be executed (without execution).
        """
        return (self._query, None)

    @abstractmethod
    async def query(self):
        """Run a query on the Data Provider.
        """

    async def close(self):
        """Closing the Provider.
        """
        try:
            await self._qs.close()  # pylint: disable=E0203
        except Exception:  # pylint: disable=W0703
            pass
        self._qs = None

    @property
    def parser(self):
        return self._parser

    def refresh(self):
        return self._refresh

    def get_resultset(self):
        return self._dict

    def get_result(self):
        return self._result

    def checksum(self):
        return get_hash(self._query)

    def connection(self):
        return self._connection

    def get_query(self):
        return self._query
