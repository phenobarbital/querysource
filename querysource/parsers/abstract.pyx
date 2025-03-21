# cython: language_level=3, embedsignature=True
# Copyright (C) 2018-present Jesus Lara
#
# file: abstract.pyx
from abc import abstractmethod
import asyncio
from navconfig.logging import logging
from asyncdb import AsyncDB
from . import QS_FILTERS, QS_VARIABLES
from ..types import strtobool, is_boolean
from ..models import QueryObject, QueryModel
from ..exceptions cimport EmptySentence
from ..conf import REDIS_URL
from ..types.validators import Entity, is_valid, field_components
from ..utils.parseqs import is_parseable



cdef tuple START_TOKENS = ('@', '$', '~', '^', '?', '*', )
cdef tuple END_TOKENS = ('|', '&', '!', '<', '>', )
cdef tuple KEYWORD_TOKENS = ('::', '@>', '<@', '->', '->>', '>=', '<=', '<>', '!=', '<', '>', )


cdef class AbstractParser:
    def __cinit__(
        self,
        *args,
        definition: object,
        conditions: object,
        query: str = None,
        **kwargs: P.kwargs
    ):
        self._name_ = type(self).__name__
        self.logger = logging.getLogger(f'QS.Parser.{self._name_}')
        self.query_raw = query
        self.query_parsed: str = None
        self.schema_based = kwargs.pop('schema_based', False)
        self._limit = kwargs.pop('max_limit', 0)
        self.string_literal = kwargs.pop('string_literal', False)
        self.definition: QueryModel = definition if definition else None
        self._distinct: bool = False
        self.set_attributes()
        self.define_conditions(conditions)
        ## redis connection:
        self._redis = AsyncDB(
            'redis',
            dsn=REDIS_URL
        )

    def __init__(
        self,
        *args,
        **kwargs
    ):
        """Constructor."""
        pass

    def _str__(self):
        return f"<{type(self).__name__}>"

    def __repr__(self):
        return f"<{type(self).__name__}>"

    cdef void set_attributes(self):
        self._query_filters: dict = {}
        self._hierarchy: list = []
        self.fields: list = []
        self.params: dict = {}
        self._limit: int = 0
        self._offset: int = 0
        self._conditions: dict = {}

    cdef void define_conditions(self, object conditions):
        """
        define_conditions.

        Build the options needed by every query in QuerySource.
        """
        if isinstance(conditions, dict):
            qobj = QueryObject(**conditions)
        else:
            qobj = conditions
        # Use qobj to set up various attributes
        self.conditions = qobj
        if self.definition:
            self.attributes = self.definition.attributes
        if not self.attributes:
            self.attributes = {}
        # save substitution:
        self._safe_substitution = self.attributes.get('safe_substitution', False)
        if not self.query_raw:
            if self.definition:
                # Query comes from Definition Database:
                self.query_raw = self.definition.query_raw
            else:
                try:
                    self.query_raw = qobj.query_raw
                except KeyError:
                    pass
        if not self.query_raw:
            raise EmptySentence(
                "Parse: Cannot Work with an Empty Sentence."
            )

    cpdef str query(self):
        return self.query_parsed

    async def get_query(self):
        return await self.build_query()

    cpdef object sentence(self, str sentence):
        self.query_raw = sentence
        return self

    @abstractmethod
    async def build_query(self):
        """_summary_
        Build a QuerySource Query.
        """

    async def _parse_hierarchy(self):
        """
        _parse_hierarchy.

        Parse the hierarchy of the query.
        """
        try:
            self._hierarchy = self.conditions.pop('hierarchy', [])
        except (KeyError, AttributeError):
            ### get hierarchy from function:
            self._hierarchy = []

    async def _program_slug(self):
        try:
            self.program_slug = self.definition.program_slug
        except (KeyError, IndexError, AttributeError):
            self.program_slug = None

    async def _query_slug(self):
        try:
            self._slug = self.definition.query_slug
        except (KeyError, IndexError, AttributeError):
            try:
                self._slug = self.conditions.pop('slug', None)
            except (KeyError, AttributeError):
                self._slug = None

    async def _query_refresh(self):
        try:
            refresh = self.conditions.pop('refresh', False)
            if isinstance(refresh, bool):
                self.refresh = refresh
            else:
                self.refresh = strtobool(str(refresh))
        except (KeyError, AttributeError, ValueError):
            self.refresh = False

    async def _query_fields(self):
        # FIELDS (Columns needed by the Query)
        self.fields = self.conditions.pop('fields', [])
        if not self.fields:
            try:
                self.fields = self.definition.fields
            except AttributeError:
                self.fields = []

    async def _query_limit(self):
        # Limiting the Query
        try:
            self.querylimit = int(self.conditions.pop('_limit', 0))
            if not self.querylimit:
                self.querylimit = int(self.conditions.pop('querylimit', 0))
        except (KeyError, AttributeError) as e:
            self.querylimit = 0

    async def _offset_pagination(self):
        # OFFSET, number of rows offset.
        try:
            self._offset = self.conditions.pop('_offset', 0)
        except (KeyError, AttributeError):
            self._offset = 0
        # PAGINATION
        try:
            paged = self.conditions.pop('paged', False)
            if is_boolean(paged):
                self._paged = paged
            elif isinstance(paged, str):
                self._paged = strtobool(paged)
            else:
                self._paged = False
        except (KeyError, AttributeError):
            self._paged = False
        try:
            self._page_ = self.conditions.pop('page', 0)
        except (KeyError, AttributeError):
            self._page = 0

    async def _grouping(self):
        # # GROUPING
        self.grouping: list = []
        group1: list = []
        group2: list = []
        try:
            group1 = self.conditions.pop('group_by', [])
        except TypeError:
            # group is an string:
            g = self.conditions.pop('group_by')
            group1 = [a.strip() for a in g.split(',')]
        except AttributeError:
            pass
        try:
            group2 = self.conditions.pop('grouping', [])
        except TypeError:
            # group is an string:
            g = self.conditions.pop('grouping')
            group2 = [a.strip() for a in g.split(',')]
        except AttributeError:
            pass
        if isinstance(group1, str):
            group1 = [a.strip() for a in group1.split(',')]
        if isinstance(group2, str):
            group2 = [a.strip() for a in group2.split(',')]
        self.grouping = group1 + group2
        if not self.grouping:
            try:
                self.grouping = self.definition.grouping
            except AttributeError:
                self.grouping: list = []

    async def _ordering(self):
        # ordering condition
        self.ordering: list = []
        order1: object = None
        order2: object = None
        try:
            order1 = self.conditions.pop('order_by', [])
        except AttributeError:
            pass
        try:
            order2 = self.conditions.pop('ordering', [])
        except AttributeError:
            pass
        if isinstance(order1, str):
            order1 = [a.strip() for a in order1.split(',')]
        if isinstance(order2, str):
            order2 = [a.strip() for a in order2.split(',')]
        self.ordering = order1 + order2
        if not self.ordering:
            try:
                self.ordering = self.definition.ordering
            except AttributeError:
                pass

    async def _filter_options(self):
        # filtering options
        try:
            self.filter_options = self.conditions.pop('filter_options', {})
        except (KeyError, AttributeError):
            self.filter_options: dict = {}

    async def _query_filter(self):
        ## FILTERING
        # where condition (alias for Filter)
        self.filter = {}
        try:
            self.filter = self.conditions.pop('where_cond', {})
        except (KeyError, AttributeError):
            pass
        if not self.filter:
            try:
                self.filter = self.conditions.pop('filter', {})
            except (KeyError, AttributeError):
                pass
        if not self.filter:
            try:
                self.filter = self.definition.filtering
                if self.filter is None:
                    self.filter = {}
            except (TypeError, AttributeError):
                self.filter = {}

    async def _qs_filters(self):
        # FILTER OPTIONS
        for _filter, fn in QS_FILTERS.items():
            if _filter in self.conditions:
                _f = self.conditions.pop(_filter)
                self._query_filters[_filter] = (fn, _f)

    cpdef dict get_query_filters(self):
        return self._query_filters

    async def _col_definition(self):
        # Data Type: Definition of columns
        self.cond_definition = self.conditions.pop('cond_definition', {})
        if self.definition.cond_definition:
            self.cond_definition = {
                **self.cond_definition,
                **self.definition.cond_definition
            }
        try:
            if self.conditions.coldef:
                self.cond_definition = {
                    **self.cond_definition,
                    **self.conditions.coldef
                }
                del self.conditions.coldef
        except (KeyError, AttributeError):
            pass
        if self.cond_definition:
            self.c_length = len(self.cond_definition)
        else:
            self.c_length = 0
            self.cond_definition = {}

    async def set_options(self):
        """
        set_options.

        Set the options for the query.
        """
        if not self.tablename:
            self.tablename = self.conditions.pop('tablename', None)
        if not self.schema:
            self.schema = self.conditions.pop('schema', None)
        if not self.database:
            self.database = self.conditions.pop('database', None)
        self._distinct = self.conditions.pop('distinct', None)
        self._add_fields: bool = self.conditions.pop('add_fields', False)
        # Data Type: Definition of columns
        await asyncio.gather(
            self._parse_hierarchy(),
            self._program_slug(),
            self._query_slug(),
            self._query_refresh(),
            self._query_fields(),
            self._query_limit(),
            self._offset_pagination(),
            self._grouping(),
            self._ordering(),
            self._filter_options(),
            self._query_filter(),
            self._qs_filters(),
            self._col_definition()
        )
        # other options are set of conditions
        try:
            params = {}
            conditions: dict = dict(self.conditions) if self.conditions else {}
            try:
                def_conditions = self.definition.conditions
                if def_conditions is None:
                    def_conditions = {}
            except AttributeError:
                def_conditions = {}
            params = conditions.pop('conditions', {})
            if params is None:
                params = {}
            conditions = {**def_conditions, **conditions, **params}
            await self._parser_conditions(
                conditions=conditions
            )
        except KeyError as err:
            print(err)
        return self

    cdef object _get_function_replacement(self, object function, str key, object val):
        fn = QS_VARIABLES.get(function, None)
        if callable(fn):
            return fn(key, val)
        return None

    async def _get_operational_value(self, value: object, connection: object) -> object:
        try:
            # if isinstance(value, str):
            #     result = await connection.get(value)
            #     return Entity.quoteString(result)
            return None
        except Exception:
            return None

    cpdef str filtering_options(self, str sentence):
        """
        Add Filter Options.
        """
        if self.filter_options:
            # TODO: get instructions for getting the filter from session
            self.logger.notice(
                f" == FILTER OPTION: {self.filter_options}"
            )
            if self.filter:
                self.filter = {**self.filter, **self.filter_options}
            else:
                self.filter = self.filter_options
            if 'where_cond' not in sentence or 'filter' not in sentence:
                return f'{sentence!s} {{filter}}'
        return sentence

    async def _parser_conditions(self, conditions: dict):
        async with await self._redis.connection() as conn:
            # One sigle connection for all Redis variables
            # every other option then set where conditions
            _filter = await self.set_conditions(conditions, conn)
            await self.set_where(_filter, conn)
        return self

    cdef object _merge_conditions_and_filters(self, dict conditions):
        """Merge conditions with filters, handling potential TypeError."""
        try:
            return {**conditions, **self.filter}
        except TypeError:
            return conditions

    cdef bint _handle_keys(self, str key, object val, dict _filter):
        _type = self.cond_definition.get(key, None)
        if isinstance(val, dict):  # its a comparison operator:
            op, value = val.popitem()
            result = is_valid(key, value, _type)
            self._conditions[key] = {op: result}
            return True
        ## if value start with a symbol (ex: @, : or #), variable replacement.
        try:
            prefix, fn, _ = field_components(str(val))[0]
            if prefix == '@':
                ## Calling a Variable Replacement:
                result = self._get_function_replacement(fn, key, val)
                result = is_valid(key, result, _type)
                self._conditions[key] = result
                return True
        except IndexError:
            return False
        return False

    async def _process_element(self, name: str, value: object, connection: object):
        """Process a single element and return the key-value pair to be added to the filter."""
        _, key, _ = field_components(name)[0]
        if key in self.cond_definition:
            if self._handle_keys(key, value, {}):
                return None
            _type = self.cond_definition.get(key, None)
            self.logger.debug(
                f'SET conditions: {key} = {value} with type {_type}'
            )
            if new_val := await self._get_operational_value(value, connection):
                result = new_val
            else:
                try:
                    result = is_valid(key, value, _type)
                except TypeError as exc:
                    self.logger.warning(
                        f'Error on: {key} = {value} with type {_type}, {exc}'
                    )
                    if isinstance(value, list):
                        return name, value
                    return None
            return key, result
        else:
            return name, value

    async def set_conditions(self, conditions: dict, connection: object) -> dict:
        """Check if all conditions are valid and return the value."""
        elements = self._merge_conditions_and_filters(conditions)

        tasks = []
        for name, val in elements.items():
            tasks.append(self._process_element(name, val, connection))
        # tasks = [self._process_element(name, val, connection) for name, val in elements.items()]
        results = await asyncio.gather(*tasks)

        _filter = {}
        for result in results:
            if result:
                key, value = result
                if key in self.cond_definition:
                    self._conditions[key] = value
                else:
                    _filter[key] = value
        return _filter

    cpdef object where_cond(self, dict where):
        self.filter = where
        return self

    async def _where_element(self, key, value, connection):
        """Process a single element for the WHERE clause and return the key-value pair."""
        # self.logger.debug(
        #     f"SET WHERE: key is {key}, value is {value}:{type(value)}"
        # )
        if isinstance(value, dict):  # its a comparison operator
            op, v = value.popitem()
            result = is_valid(key, v, noquote=self.string_literal)
            return key, {op: result}

        if isinstance(value, str):
            parser = is_parseable(value)
            if parser:
                try:
                    value = parser(value)
                except (TypeError, ValueError):
                    pass

        try:
            prefix, fn, _ = field_components(str(value))[0]
            if prefix == '@':
                result = self._get_function_replacement(fn, key, value)
                result = is_valid(key, result, noquote=self.string_literal)
                return key, result
            elif prefix in ('|', '!', '&', '>', '<'):
                # Leave as-is because we use it in WHERE
                return key, value
        except IndexError:
            pass

        new_val = await self._get_operational_value(value, connection)
        if new_val:
            result = new_val
        else:
            result = is_valid(key, value, noquote=self.string_literal)

        return key, result

    async def set_where(self, _filter: dict, connection: object) -> object:
        """Set the WHERE clause conditions in parallel."""
        tasks = [self._where_element(key, value, connection) for key, value in _filter.items()]
        results = await asyncio.gather(*tasks)

        where_cond = {key: value for key, value in results}
        self.filter = where_cond
        return self
