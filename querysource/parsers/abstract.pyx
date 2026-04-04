# cython: language_level=3, embedsignature=True, boundscheck=False, wraparound=False
# Copyright (C) 2018-present Jesus Lara
#
# file: abstract.pyx
"""
AbstractParser.

Base class for all QuerySource parsers.
"""
from abc import abstractmethod
import asyncio
from navconfig.logging import logging
from asyncdb import AsyncDB
from . import QS_FILTERS, QS_VARIABLES
from ..types import strtobool, is_boolean
from ..models import QueryObject, QueryModel
from ..exceptions import EmptySentence
from ..conf import REDIS_URL
from ..types.validators import Entity, is_valid, field_components
from ..utils.parseqs import is_parseable


cdef tuple START_TOKENS = ('@', '$', '~', '^', '?', '*')
cdef tuple END_TOKENS = ('|', '&', '!', '<', '>')
cdef tuple KEYWORD_TOKENS = ('::', '@>', '<@', '->', '->>', '>=', '<=', '<>', '!=', '<', '>')


cdef class AbstractParser:
    """Base class for all QuerySource parsers."""

    def __cinit__(
        self,
        *args,
        definition: object,
        conditions: object,
        query: str = None,
        **kwargs
    ):
        self._name_ = type(self).__name__
        self.logger = logging.getLogger(f'QS.Parser.{self._name_}')
        self.query_raw = query
        self.query_parsed = None
        self.schema_based = kwargs.pop('schema_based', False)
        self._limit = kwargs.pop('max_limit', 0)
        self.string_literal = kwargs.pop('string_literal', False)
        self.definition = definition if definition else None
        self._distinct = False
        self.set_attributes()
        self.define_conditions(conditions)
        # Lazy Redis — initialized on first use via _get_redis()
        self._redis = None

    def __init__(self, *args, **kwargs):
        """Constructor."""
        pass

    def __str__(self):
        return f"<{type(self).__name__}>"

    def __repr__(self):
        return f"<{type(self).__name__}>"

    cdef object _get_redis(self):
        """Lazy Redis connection initialization."""
        if self._redis is None:
            self._redis = AsyncDB('redis', dsn=REDIS_URL)
        return self._redis

    cdef void set_attributes(self):
        self._query_filters = {}
        self._hierarchy = []
        self.fields = []
        self.params = {}
        self._limit = 0
        self._offset = 0
        self._conditions = {}
        self.attributes = {}
        self.filter = {}
        self.filter_options = {}
        self.ordering = []
        self.grouping = []
        self.program_slug = None
        self.tablename = None
        self.schema = None
        self.database = None
        self.refresh = False
        self.querylimit = 0
        self.cond_definition = {}
        self._slug = None
        self._paged = False
        self._page_ = 0
        self._add_fields = False
        self._safe_substitution = False
        self.c_length = 0

    cdef void define_conditions(self, object conditions):
        """Build the options needed by every query in QuerySource."""
        if isinstance(conditions, dict):
            qobj = QueryObject(**conditions)
        else:
            qobj = conditions
        self.conditions = qobj
        if self.definition:
            self.attributes = self.definition.attributes
        if not self.attributes:
            self.attributes = {}
        self._safe_substitution = self.attributes.get('safe_substitution', False)
        if not self.query_raw:
            if self.definition:
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
        """Build a QuerySource Query."""

    # ------------------------------------------------------------------
    # Synchronous option extractors (replace asyncio.gather overhead)
    # ------------------------------------------------------------------

    cdef void _extract_options(self):
        """Extract all query options synchronously — no event loop overhead."""
        self._parse_hierarchy_sync()
        self._program_slug_sync()
        self._query_slug_sync()
        self._query_refresh_sync()
        self._query_fields_sync()
        self._query_limit_sync()
        self._offset_pagination_sync()
        self._grouping_sync()
        self._ordering_sync()
        self._filter_options_sync()
        self._qry_options_sync()
        self._query_filter_sync()
        self._qs_filters_sync()
        self._col_definition_sync()

    cdef void _parse_hierarchy_sync(self):
        try:
            self._hierarchy = self.conditions.pop('hierarchy', [])
        except (KeyError, AttributeError):
            self._hierarchy = []

    cdef void _program_slug_sync(self):
        try:
            self.program_slug = self.definition.program_slug
        except (KeyError, IndexError, AttributeError):
            self.program_slug = None

    cdef void _query_slug_sync(self):
        try:
            self._slug = self.definition.query_slug
        except (KeyError, IndexError, AttributeError):
            try:
                self._slug = self.conditions.pop('slug', None)
            except (KeyError, AttributeError):
                self._slug = None

    cdef void _query_refresh_sync(self):
        cdef object refresh
        try:
            refresh = self.conditions.pop('refresh', False)
            if isinstance(refresh, bool):
                self.refresh = refresh
            else:
                self.refresh = strtobool(str(refresh))
        except (KeyError, AttributeError, ValueError):
            self.refresh = False

    cdef void _query_fields_sync(self):
        self.fields = self.conditions.pop('fields', [])
        if not self.fields:
            try:
                self.fields = self.definition.fields
            except AttributeError:
                self.fields = []

    cdef void _query_limit_sync(self):
        try:
            self.querylimit = int(self.conditions.pop('_limit', 0))
            if not self.querylimit:
                self.querylimit = int(self.conditions.pop('querylimit', 0))
        except (KeyError, AttributeError):
            self.querylimit = 0

    cdef void _offset_pagination_sync(self):
        cdef object paged
        try:
            self._offset = self.conditions.pop('_offset', 0)
        except (KeyError, AttributeError):
            self._offset = 0
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
            self._page_ = 0

    cdef void _grouping_sync(self):
        cdef list group1 = []
        cdef list group2 = []
        cdef str g
        try:
            group1 = self.conditions.pop('group_by', [])
        except TypeError:
            g = self.conditions.pop('group_by')
            group1 = [a.strip() for a in g.split(',')]
        except AttributeError:
            pass
        try:
            group2 = self.conditions.pop('grouping', [])
        except TypeError:
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
                self.grouping = []

    cdef void _ordering_sync(self):
        cdef object order1 = []
        cdef object order2 = []
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
        self.ordering = (order1 or []) + (order2 or [])
        if not self.ordering:
            try:
                self.ordering = self.definition.ordering
            except AttributeError:
                pass

    cdef void _filter_options_sync(self):
        try:
            self.filter_options = self.conditions.pop('filter_options', {})
        except (KeyError, AttributeError):
            self.filter_options = {}

    cdef void _qry_options_sync(self):
        try:
            self._qry_options = self.conditions.pop('qry_options', {})
        except (KeyError, AttributeError):
            self._qry_options = {}

    cdef void _query_filter_sync(self):
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

    cdef void _qs_filters_sync(self):
        cdef str _filter_name
        for _filter_name, fn in QS_FILTERS.items():
            if _filter_name in self.conditions:
                _f = self.conditions.pop(_filter_name)
                self._query_filters[_filter_name] = (fn, _f)

    cdef void _col_definition_sync(self):
        self.cond_definition = self.conditions.pop('cond_definition', {})
        if self.definition and self.definition.cond_definition:
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

    # ------------------------------------------------------------------
    # Legacy async wrappers (kept for subclass compatibility)
    # ------------------------------------------------------------------

    async def _parse_hierarchy(self):
        self._parse_hierarchy_sync()

    async def _program_slug(self):
        self._program_slug_sync()

    async def _query_slug(self):
        self._query_slug_sync()

    async def _query_refresh(self):
        self._query_refresh_sync()

    async def _query_fields(self):
        self._query_fields_sync()

    async def _query_limit(self):
        self._query_limit_sync()

    async def _offset_pagination(self):
        self._offset_pagination_sync()

    async def _grouping(self):
        self._grouping_sync()

    async def _ordering(self):
        self._ordering_sync()

    async def _filter_options(self):
        self._filter_options_sync()

    async def _query_filter(self):
        self._query_filter_sync()

    async def _qs_filters(self):
        self._qs_filters_sync()

    async def _col_definition(self):
        self._col_definition_sync()

    cpdef dict get_query_filters(self):
        return self._query_filters

    # ------------------------------------------------------------------
    # set_options — now synchronous extraction + async Redis path
    # ------------------------------------------------------------------

    async def set_options(self):
        """Set the options for the query."""
        if not self.tablename:
            self.tablename = self.conditions.pop('tablename', None)
        if not self.schema:
            self.schema = self.conditions.pop('schema', None)
        if not self.database:
            self.database = self.conditions.pop('database', None)
        self._distinct = bool(self.conditions.pop('distinct', False))
        self._add_fields = self.conditions.pop('add_fields', False)
        # Synchronous extraction — no asyncio.gather overhead
        self._extract_options()
        # other options are set of conditions
        try:
            params = {}
            conditions = dict(self.conditions) if self.conditions else {}
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
            self.logger.warning(f'set_options KeyError: {err}')
        return self

    cdef object _get_function_replacement(self, object function, str key, object val):
        fn = QS_VARIABLES.get(function, None)
        if callable(fn):
            return fn(key, val)
        return None

    async def _get_operational_value(self, value: object, connection: object) -> object:
        try:
            return None
        except Exception:
            return None

    cpdef str filtering_options(self, str sentence):
        """Add Filter Options."""
        if self.filter_options:
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
        redis = self._get_redis()
        async with await redis.connection() as conn:
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
        cdef object _type = self.cond_definition.get(key, None)
        cdef str prefix
        cdef str fn
        cdef object result
        if isinstance(val, dict):
            op, value = val.popitem()
            result = is_valid(key, value, _type)
            self._conditions[key] = {op: result}
            return True
        comps = field_components(str(val))
        if comps:
            prefix, fn, _ = comps[0]
            if prefix == '@':
                result = self._get_function_replacement(fn, key, val)
                result = is_valid(key, result, _type)
                self._conditions[key] = result
                return True
        else:
            return False
        return False

    async def _process_element(self, name: str, value: object, connection: object):
        """Process a single element and return the key-value pair to be added to the filter."""
        comps = field_components(name)
        if not comps:
            return name, value
        _, key, _ = comps[0]
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
            # Handle @-prefixed function replacements even for unknown keys
            if isinstance(value, str):
                val_comps = field_components(value)
                if val_comps:
                    prefix, fn, _ = val_comps[0]
                    if prefix == '@':
                        result = self._get_function_replacement(fn, key, value)
                        self._conditions[key] = result
                        return None
            return name, value

    async def set_conditions(self, conditions: dict, connection: object) -> dict:
        """Check if all conditions are valid and return the value."""
        elements = self._merge_conditions_and_filters(conditions)
        tasks = []
        _filter = {}

        for name, val in elements.items():
            tasks.append(self._process_element(name, val, connection))
        results = await asyncio.gather(*tasks)

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
        """Process a single element for the WHERE clause."""

        if isinstance(value, dict):
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

        comps = field_components(str(value))
        if comps:
            prefix, fn, _ = comps[0]
            if prefix == '@':
                result = self._get_function_replacement(fn, key, value)
                result = is_valid(key, result, noquote=self.string_literal)
                return key, result
            elif prefix in ('|', '!', '&', '>', '<'):
                return key, value

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
