# file: abstract.pyx
from abc import abstractmethod
import asyncio
from cpython cimport list, dict
from navconfig.logging import logging
from asyncdb import AsyncDB
from ..services import QS_FILTERS, QS_VARIABLES
from ..providers import BaseProvider
from ..types.mapping cimport ClassDict
from ..types import strtobool, is_boolean
from ..models import QueryObject
from ..exceptions import EmptySentence
from ..conf import REDIS_URL


cdef class AbstractParser:
    def __cinit__(
        self,
        *args,
        query: str,
        definition: dict,
        conditions: dict,
        **kwargs: P.kwargs
    ):
        self._name_ = type(self).__name__
        self.logger = logging.getLogger(f'QS.Parser.{self._name_}')
        self.query_raw = query
        self.query_parsed: str = None
        self.definition: BaseProvider = definition if definition else None
        self.set_attributes()
        self.define_conditions(conditions)
        self._limit = kwargs.get('max_limit', 0)
        ## redis connection:
        self._redis = AsyncDB(
            'redis',
            dsn=REDIS_URL
        )

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

    cdef void define_conditions(self, dict conditions):
        """
        define_conditions.

        Build the options needed by every query in QuerySource.
        """
        cdef ClassDict qobj = QueryObject(**conditions)
        # Use qobj to set up various attributes
        self.conditions = qobj
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
            self.program_slug = self.options.program_slug
        except (KeyError, IndexError, AttributeError):
            self.program_slug = None

    async def _query_slug(self):
        try:
            self._slug = self.options.query_slug
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
                self.fields = self.options.fields
            except AttributeError:
                self.fields = []

    async def _query_limit(self):
        # Limiting the Query
        try:
            self.querylimit = self.conditions.pop('querylimit', 0)
        except (KeyError, AttributeError):
            try:
                self.querylimit = self.conditions.pop('_limit', 0)
            except (KeyError, AttributeError):
                self.querylimit = 0

    async def _offset_pagination(self):
        # OFFSET, number of rows offset.
        try:
            self._offset = self.conditions.pop('offset', 0)
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
        try:
            self.grouping = self.conditions.pop('group_by', [])
        except (KeyError, AttributeError):
            try:
                self.grouping = self.conditions.pop('grouping', [])
            except (KeyError, AttributeError):
                self.grouping: list = []
        if not self.grouping:
            try:
                self.grouping = self.options.grouping
            except AttributeError:
                self.grouping: list = []

    async def _ordering(self):
        # ordering condition
        try:
            self.ordering = self.conditions.pop('order_by', [])
        except (KeyError, AttributeError):
            try:
                self.ordering = self.conditions.pop('ordering', [])
            except (KeyError, AttributeError):
                self.ordering: list = []
        if not self.ordering:
            try:
                self.ordering = self.options.ordering
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
                self.filter = self.options.filtering
            except (TypeError, AttributeError):
                self.filter = {}

    async def _qs_filters(self):
        # FILTER OPTIONS
        for _filter, fn in QS_FILTERS.items():
            if _filter in self.conditions:
                _f = self.conditions.pop(_filter)
                self._query_filters[_filter] = (fn, _f)

    async def _col_definition(self):
        # Data Type: Definition of columns
        try:
            self.cond_definition = self.options.cond_definition
        except (KeyError, AttributeError):
            self.cond_definition: dict = {}
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
        self.table = self.conditions.pop('table', None)
        self.database = self.conditions.pop('database', None)
        self._distinct = self.conditions.pop('distinct', None)
        # Data Type: Definition of columns
        try:
            self.cond_definition = self.options.cond_definition
        except (KeyError, AttributeError):
            self.cond_definition: dict = {}
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
            conditions: QueryObject = self.conditions if self.conditions else {}
            params = conditions.pop('conditions', {})
            if params is None:
                params = {}
            conditions = {**dict(conditions), **params}
            await self._parser_conditions(
                conditions=conditions
            )
        except KeyError as err:
            print(err)
        return self

    async def _parser_conditions(self, conditions: dict):
        async with await self._redis.connection() as conn:
            # One sigle connection for all Redis variables
            # every other option then set where conditions
            #_filter = await self.set_conditions(conditions, conn)
            # await self.set_where(_filter, conn)
            print('LAST CONDITIONS > ', conditions)
        return self
