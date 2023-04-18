"""QueryParser.

Base Query Parser.
"""
from abc import ABC, abstractmethod
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from asyncdb import AsyncDB
from navconfig.logging import logging

from querysource.exceptions import EmptySentence
from querysource.models import QueryObject
from querysource.providers import BaseProvider
from querysource.service import QS_FILTERS, QS_VARIABLES
from querysource.types import strtobool, is_boolean
from querysource.types.validators import field_components, is_valid, Entity
from querysource.utils.parseqs import is_parseable
from querysource.conf import REDIS_URL

START_TOKENS = ('@', '$', '~', '^', '?', '*', )
END_TOKENS = ('|', '&', '!', '<', '>', )
KEYWORD_TOKENS = ('::', '@>', '<@', '->', '->>', '>=', '<=', '<>', '!=', '<', '>', )
COMPARISON_TOKENS = ('>=', '<=', '<>', '!=', '<', '>',)


class QueryParser(ABC):
    def __init__(
        self,
        query: str = None,
        options: BaseProvider = None,
        conditions: QueryObject = None,
        connection: Callable = None,
        **kwargs
    ):
        self.logger = logging.getLogger('QS.Parser')
        self.set_attributes()
        try:
            self.conditions = QueryObject(**conditions)
        except (ValueError, TypeError) as ex:
            raise TypeError(
                f"Error Parsing conditions into QueryObject: {ex}"
            ) from ex
        if options:
            self.options = options
        else:
            self.options: dict = {}
        # base query
        self.query_raw: str = query
        self.query_parsed: str = None
        self._query_filters: dict = {}
        ## query limit
        try:
            self._limit = kwargs['max_limit']
            del kwargs['max_limit']
        except KeyError:
            self._limit = None
        ## Connection:
        self._connection = connection
        ## redis connection:
        self._redis = AsyncDB(
            'redis',
            dsn=REDIS_URL
        )
        ## Threadpool Executor:
        self._executor = ThreadPoolExecutor(
            max_workers=10
        )

    def set_attributes(self):
        self.options: BaseProvider = None
        self._hierarchy: list = []
        self.fields: list = []
        self.params: dict = {}
        self._limit: int = None
        self._offset: int = None

    def query(self) -> str:
        return self.query_parsed

    async def set_options(self):
        """
        set_options.

        Build the options needed by every query in QuerySource.
        """
        ## first: query:
        if not self.query_raw:
            self.query_raw = self.options.query_raw
        if not self.query_raw:
            raise EmptySentence(
                "Parse: Cannot Work with Empty Sentence."
            )
        try:
            self._hierarchy = self.conditions.hierarchy
            del self.conditions.hierarchy
        except (KeyError, AttributeError):
            ### get hierarchy from function:
            self._hierarchy = []
        try:
            self.program_slug = self.options.program_slug
        except (KeyError, IndexError, AttributeError):
            self.program_slug = None
        # Query itself.
        try:
            self._slug = self.options.query_slug
        except (KeyError, IndexError, AttributeError):
            try:
                self._slug = self.conditions.slug
                del self.conditions.slug
            except (KeyError, AttributeError):
                self._slug = None
        # Refresh query functionality
        try:
            if isinstance(self.conditions.refresh, bool):
                self.refresh = self.conditions.refresh
            else:
                self.refresh = strtobool(str(self.conditions.refresh))
            del self.conditions.refresh
        except (KeyError, AttributeError, ValueError):
            self.refresh = False
        # FIELDS (Columns needed by the Query)
        try:
            self.fields = self.conditions.fields
            del self.conditions.fields
        except (KeyError, AttributeError):
            try:
                self.fields = self.options.fields
            except AttributeError:
                self.fields = []
        # Limiting the Query
        try:
            self.querylimit = self.conditions.querylimit
            del self.conditions.querylimit
        except (KeyError, AttributeError):
            try:
                self.querylimit = self.conditions._limit
                del self.conditions._limit
            except (KeyError, AttributeError):
                self.querylimit = None
        # OFFSET, number of rows offset.
        try:
            self._offset = self.conditions.offset
            del self.conditions.offset
        except (KeyError, AttributeError):
            self._offset = None
        # PAGINATION
        try:
            if is_boolean(self.conditions.paged):
                self._paged = strtobool(self.conditions.paged)
            else:
                self._paged = False
            del self.conditions.paged
        except (KeyError, AttributeError):
            self._paged = False
        try:
            self._page = self.conditions.page
            del self.conditions.page
        except (KeyError, AttributeError):
            self._page = 1
        # # GROUPING
        try:
            self.grouping = self.conditions.group_by
            del self.conditions.group_by
        except (KeyError, AttributeError):
            try:
                self.grouping = self.conditions.grouping
                del self.conditions.grouping
            except (KeyError, AttributeError):
                self.grouping: list = []
        if not self.grouping:
            try:
                self.grouping = self.options.grouping
            except AttributeError:
                self.grouping: list = []
        ## FILTERING
        # where condition (alias for Filter)
        self.filter = {}
        try:
            self.filter = self.conditions.get('where_cond', {})
            del self.conditions.where_cond
        except (KeyError, AttributeError):
            pass
        if not self.filter:
            try:
                self.filter = self.conditions.get('filter', {})
                del self.conditions.filter
            except (KeyError, AttributeError):
                pass
        if not self.filter:
            try:
                self.filter = self.options.filtering
            except (TypeError, AttributeError):
                self.filter = {}
        # filtering options
        try:
            self.filter_options = self.conditions.filter_options
            del self.conditions.filter_options
        except (KeyError, AttributeError):
            self.filter_options: dict = {}
        # ordering condition
        try:
            self.ordering = self.conditions.order_by
            del self.conditions.order_by
        except (KeyError, AttributeError):
            try:
                self.ordering = self.conditions.ordering
                del self.conditions.ordering
            except (KeyError, AttributeError):
                self.ordering: list = []
        if not self.ordering:
            try:
                self.ordering = self.options.ordering
            except AttributeError:
                pass
        # support for distinct
        # Understanding "DISTINCT"
        try:
            self._distinct = self.conditions.distinct
            del self.conditions.distinct
        except (KeyError, AttributeError):
            self._distinct = None
        # FILTER OPTIONS
        for _filter, fn in QS_FILTERS.items():
            if _filter in self.conditions:
                self._query_filters[_filter] = (fn, self.conditions[_filter])
                del self.conditions[_filter]
        try:
            self.table = self.conditions.table
            del self.conditions.table
        except KeyError:
            self.table = None
        try:
            self.database = self.conditions.database
            del self.conditions.database
        except KeyError:
            self.database = None

        # Data Type: Definition of columns
        try:
            self.cond_definition = self.options.cond_definition
        except (KeyError, AttributeError):
            self.cond_definition: dict = {}

        try:
            if self.conditions.coldef:
                self.cond_definition = {**self.cond_definition, **self.conditions.coldef}
                del self.conditions.coldef
        except (KeyError, AttributeError):
            pass
        if self.cond_definition:
            self.c_length = len(self.cond_definition)
        else:
            self.c_length = 0
            self.cond_definition = {}
        # other options are set of conditions
        try:
            params = {}
            conditions: dict = self.conditions
            if 'conditions' in conditions:
                params = conditions['conditions']
                del conditions['conditions']
                if params is None:
                    params = {}
            conditions = {**dict(conditions), **params}
            await self.set_conditions(
                conditions=conditions
            )
        except KeyError as err:
            print(err)
        return self

    async def set_conditions(self, conditions: dict = None):
        if conditions is None:
            conditions = {}
        # check if all conditions are valid and return the value
        try:
            elements = {**conditions, **self.filter}
        except TypeError:
            elements = conditions
        _filter = {}
        for name, val in elements.items():
            # All kind of new expressions like: @, |, # or ~
            _, key, _ = field_components(name)[0]
            if key in self.cond_definition:
                _type = self.cond_definition[key]
                if isinstance(val, dict): # its a comparison operator:
                    op, value = val.popitem()
                    result = is_valid(key, value, _type)
                    self.conditions[key] = {op: result}
                    continue
                ## if value start with a symbol (ex: @, : or #), variable replacement.
                try:
                    prefix, fn, _ = field_components(str(val))[0]
                    if prefix == '@':
                        ## Calling a Variable Replacement:
                        result = self._get_function_replacement(fn, key, val)
                        result = is_valid(key, result, _type)
                        self.conditions[key] = result
                        continue
                except IndexError:
                    pass
                self.logger.debug(f'SET conditions: {key} = {val} with type {_type}')
                if new_val := await self.get_operational_value(val):
                    result = new_val
                else:
                    result = is_valid(key, val, _type)
                self.conditions[key] = result
            else:
                _filter[name] = val
        # every other option then set where conditions
        await self.set_where(_filter)
        return self

    async def set_where(self, _filter: dict) -> None:
        where_cond = {}
        for key, value in _filter.items():
            self.logger.debug(f"SET WHERE: key is {key}, value is {value}:{type(value)}")
            if isinstance(value, dict): # its a comparison operator:
                    op, v = value.popitem()
                    result = is_valid(key, v)
                    where_cond[key] = {op: result}
                    continue
            if isinstance(value, str):
                if parser:=is_parseable(value):
                    try:
                        value = parser(value)
                    except (TypeError, ValueError):
                        pass
            try:
                prefix, fn, _ = field_components(str(value))[0]
                if prefix == '@':
                    result = self._get_function_replacement(fn, key, value)
                    result = is_valid(key, result)
                    where_cond[key] = result
                    continue
                elif prefix in ('|', '!', '&', '>', '<'):
                    # Leave -as-is- because we use it in WHERE
                    where_cond[key] = value
                    continue
            except IndexError:
                pass
            if new_val := await self.get_operational_value(value):
                result = new_val
            else:
                result = is_valid(key, value)
            where_cond[key] = result
        self.filter = where_cond
        return self

    def _get_function_replacement(self, function, key: str, val: Any) -> Any:
        if function in QS_VARIABLES:
            fn = QS_VARIABLES[function]
            return fn(key, val)
        return None

    async def get_operational_value(self, value: str) -> Any:
        async with await self._redis.connection() as conn:
            try:
                result = await conn.get(value)
                return Entity.quoteString(result)
            except Exception:
                return None


    async def get_query(self):
        query = await self.build_query()
        return query

    def sentence(self, sentence):
        self.query_raw = sentence
        return self

    @abstractmethod
    async def build_query(self):
        """_summary_
        Build a QuerySource Query.
        """

    async def filtering_options(self):
        """
        Add Filter Options.
        """
        if self.filter_options:
            # TODO: get instructions for getting the filter from session
            self.logger.debug(f" == FILTER OPTION: {self.filter_options}")
            if self.filter:
                self.filter = {**self.filter, **self.filter_options}
            else:
                self.filter = self.filter_options
