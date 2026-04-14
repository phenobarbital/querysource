# cython: language_level=3, embedsignature=True, boundscheck=False, wraparound=False
# Copyright (C) 2018-present Jesus Lara
#
# file: arangodb.pyx
"""ArangoDB AQL Parser for QuerySource."""
from datamodel.parsers.json import json_encoder, json_decoder
from querysource.exceptions import EmptySentence
from .abstract cimport AbstractParser

# Try to import Rust extension for accelerated parsing
try:
    from querysource.qs_parsers import _qs_parsers as _rs
    HAS_RUST = True
except ImportError:
    HAS_RUST = False


cdef class ArangoDBParser(AbstractParser):
    """ArangoDB AQL Parser.

    Translates JSON query configuration into AQL query strings.
    Supports standard collection queries, graph traversals, and ArangoSearch.
    """
    def __cinit__(self, *args, **kwargs):
        """Initialize ArangoDB-specific attributes."""
        self._doc_var = kwargs.pop('doc_var', 'doc')
        self._base_query = {}
        self._graph_options = {}
        self._search_options = {}

    def __init__(self, *args, **kwargs):
        super(ArangoDBParser, self).__init__(*args, **kwargs)

    async def get_query(self) -> str:
        """Return the built AQL query."""
        return await self.build_query()

    # ----- Filter conditions -----

    async def process_filter_conditions(self) -> list:
        """Process filter conditions into AQL FILTER clause fragments."""
        if HAS_RUST and self.filter and isinstance(self.filter, dict):
            try:
                return _rs.aql_filter_conditions(
                    self.filter,
                    self.cond_definition if self.cond_definition else {},
                    self._doc_var,
                )
            except Exception:
                pass
        return self._build_filter_clauses_cy()

    def _build_filter_clauses_cy(self) -> list:
        """Cython fallback for filter conditions."""
        return self._build_filter_clauses_list()

    cdef str _build_filter_clause_cy(self):
        """Build FILTER clauses as a single string for internal use."""
        cdef list clauses = self._build_filter_clauses_list()
        if not clauses:
            return ''
        cdef list parts = []
        cdef str clause
        for clause in clauses:
            parts.append(f'FILTER {clause}')
        return '\n    '.join(parts)

    cdef list _build_filter_clauses_list(self):
        """Build filter clause fragments (list of strings)."""
        cdef list clauses = []
        cdef str key
        cdef str field_name
        cdef object value
        cdef str doc_var = self._doc_var

        if not self.filter:
            return clauses

        for key, value in self.filter.items():
            field_name = key
            doc_field = f'{doc_var}.{field_name}'

            if isinstance(value, dict):
                op, val = next(iter(value.items()))
                aql_op = self._map_operator(op)
                formatted = self._format_value(val)
                clauses.append(f'{doc_field} {aql_op} {formatted}')
            elif isinstance(value, list):
                formatted_items = ', '.join(
                    self._format_value(v) for v in value
                )
                if key.endswith('!'):
                    clauses.append(
                        f'{doc_var}.{key[:-1]} NOT IN [{formatted_items}]'
                    )
                else:
                    clauses.append(f'{doc_field} IN [{formatted_items}]')
            elif isinstance(value, str):
                upper = value.upper()
                if upper in ('NULL', 'NONE'):
                    clauses.append(f'{doc_field} == null')
                elif upper in ('!NULL', '!NONE'):
                    clauses.append(f'{doc_field} != null')
                elif value.startswith('!'):
                    formatted = self._format_value(value[1:])
                    clauses.append(f'{doc_field} != {formatted}')
                elif '%' in value:
                    formatted = self._format_value(value)
                    clauses.append(f'LIKE({doc_field}, {formatted})')
                else:
                    formatted = self._format_value(value)
                    clauses.append(f'{doc_field} == {formatted}')
            elif isinstance(value, bool):
                val_str = 'true' if value else 'false'
                clauses.append(f'{doc_field} == {val_str}')
            elif isinstance(value, (int, float)):
                clauses.append(f'{doc_field} == {value}')
            elif value is None:
                clauses.append(f'{doc_field} == null')

        return clauses

    def _map_operator(self, str op) -> str:
        """Map SQL/generic operators to AQL operators."""
        cdef dict op_map = {
            '=': '==', '>': '>', '>=': '>=',
            '<': '<', '<=': '<=', '<>': '!=',
            '!=': '!=', '==': '==',
            'LIKE': 'LIKE', 'like': 'LIKE',
        }
        return op_map.get(op, op)

    def _format_value(self, object value) -> str:
        """Format a Python value for AQL syntax."""
        if value is None:
            return 'null'
        if isinstance(value, bool):
            return 'true' if value else 'false'
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, str):
            upper = value.upper()
            if upper in ('NULL', 'TRUE', 'FALSE'):
                return upper
            return f'"{value}"'
        return str(value)

    # ----- Field projection -----

    async def process_fields(self) -> str:
        """Process fields into AQL RETURN clause."""
        if HAS_RUST:
            try:
                field_list = []
                if isinstance(self.fields, list):
                    field_list = list(self.fields)
                elif isinstance(self.fields, str) and self.fields:
                    field_list = [f.strip() for f in self.fields.split(',')]
                return _rs.aql_process_fields(field_list, self._doc_var)
            except Exception:
                pass
        return self._build_return_clause_cy()

    cdef str _build_return_clause_cy(self):
        """Cython fallback for field projection."""
        cdef str doc_var = self._doc_var
        cdef list field_list = []

        if isinstance(self.fields, list) and len(self.fields) > 0:
            field_list = self.fields
        elif isinstance(self.fields, str) and self.fields:
            field_list = [f.strip() for f in self.fields.split(',')]

        if not field_list:
            return f'RETURN {doc_var}'

        cdef list parts = []
        cdef str field
        for field in field_list:
            lower = field.lower()
            if ' as ' in lower:
                pos = lower.find(' as ')
                name = field[:pos].strip()
                alias = field[pos + 4:].strip().replace('"', '')
                parts.append(f'{alias}: {doc_var}.{name}')
            else:
                parts.append(f'{field}: {doc_var}.{field}')

        return 'RETURN {{ {} }}'.format(', '.join(parts))

    # ----- Ordering -----

    async def process_ordering(self) -> str:
        """Process ordering into AQL SORT clause."""
        if HAS_RUST and self.ordering:
            try:
                ordering_list = []
                if isinstance(self.ordering, str):
                    ordering_list = [
                        f.strip() for f in self.ordering.split(',')
                    ]
                elif isinstance(self.ordering, list):
                    ordering_list = list(self.ordering)
                if ordering_list:
                    return _rs.aql_process_ordering(
                        ordering_list, self._doc_var
                    )
            except Exception:
                pass
        return self._build_sort_clause_cy()

    cdef str _build_sort_clause_cy(self):
        """Cython fallback for ordering."""
        cdef str doc_var = self._doc_var
        cdef list sort_parts = []

        if not self.ordering:
            return ''

        cdef list ordering_list
        if isinstance(self.ordering, str):
            ordering_list = [f.strip() for f in self.ordering.split(',')]
        elif isinstance(self.ordering, list):
            ordering_list = self.ordering
        else:
            return ''

        cdef str item
        for item in ordering_list:
            trimmed = item.strip()
            if trimmed.startswith('-'):
                sort_parts.append(f'{doc_var}.{trimmed[1:]} DESC')
            else:
                parts = trimmed.split(' ', 1)
                if len(parts) == 2:
                    direction = parts[1].strip().upper()
                    if direction != 'DESC':
                        direction = 'ASC'
                    sort_parts.append(f'{doc_var}.{parts[0]} {direction}')
                else:
                    sort_parts.append(f'{doc_var}.{trimmed} ASC')

        if sort_parts:
            return 'SORT {}'.format(', '.join(sort_parts))
        return ''

    # ----- Build graph traversal -----

    cdef str _build_graph_for_clause(self):
        """Build graph traversal FOR clause."""
        cdef dict graph = self._graph_options
        if not graph:
            return ''

        cdef str direction = graph.get('direction', 'OUTBOUND').upper()
        cdef str start_vertex = graph.get('start_vertex', '')
        cdef str edge_collection = graph.get('edge_collection', '')
        cdef int min_depth = graph.get('min_depth', 1)
        cdef int max_depth = graph.get('max_depth', 1)

        if not start_vertex or not edge_collection:
            return ''

        return (
            f"FOR v, e, p IN {min_depth}..{max_depth} "
            f"{direction} '{start_vertex}' {edge_collection}"
        )

    # ----- Build search clause -----

    cdef str _build_search_clause(self):
        """Build ArangoSearch SEARCH clause."""
        cdef dict search = self._search_options
        if not search:
            return ''

        cdef str view = search.get('view', '')
        if not view:
            return ''

        cdef str analyzer = search.get('analyzer', '')
        cdef dict fields = search.get('fields', {})
        cdef dict phrase = search.get('phrase', {})
        cdef str doc_var = self._doc_var

        cdef list conditions = []
        cdef str field_name
        cdef str field_value

        for field_name, field_value in fields.items():
            escaped = field_value.replace('\\', '\\\\').replace('"', '\\"')
            conditions.append(f'{doc_var}.{field_name} == "{escaped}"')

        for field_name, field_value in phrase.items():
            escaped = field_value.replace('\\', '\\\\').replace('"', '\\"')
            conditions.append(
                f'PHRASE({doc_var}.{field_name}, "{escaped}")'
            )

        if not conditions:
            return ''

        cdef str joined = ' AND '.join(conditions)
        if analyzer:
            return f'SEARCH ANALYZER({joined}, "{analyzer}")'
        return f'SEARCH {joined}'

    # ----- Build full query -----

    async def build_query(
        self, querylimit: int = None, offset: int = None
    ) -> str:
        """Build a complete AQL query from the JSON configuration."""
        cdef str collection_name
        cdef str aql_query

        # Parse the raw query JSON
        try:
            query_config = json_decoder(self.query_raw)
            self._base_query.update(query_config)
        except Exception as e:
            self.logger.error(f"Error parsing query JSON: {e}")

        # Extract graph and search options
        self._graph_options = self._base_query.pop('graph', {})
        self._search_options = self._base_query.pop('search', {})

        # Determine collection name
        if self.schema and self.tablename:
            collection_name = f'{self.schema}.{self.tablename}'
        elif self.tablename:
            collection_name = self.tablename
        else:
            collection_name = self._base_query.get('collection', '')

        if not collection_name:
            raise EmptySentence(
                'ArangoDB Error: no collection name specified.'
            )

        # Try Rust fast-path for the full query build
        if HAS_RUST:
            try:
                aql_query = _rs.aql_build_query(
                    collection_name,
                    self.filter if self.filter else {},
                    self.cond_definition if self.cond_definition else {},
                    list(self.fields) if self.fields else [],
                    list(self.ordering) if self.ordering else [],
                    list(self.grouping) if self.grouping else [],
                    querylimit or self.querylimit or 0,
                    offset or self._offset or 0,
                    self._doc_var,
                    self._graph_options if self._graph_options else None,
                    self._search_options if self._search_options else None,
                )
                self.query_parsed = aql_query
                self.logger.debug(f"AQL Query :: {aql_query}")
                return aql_query
            except Exception as e:
                self.logger.warning(
                    f"Rust AQL build failed, using Cython fallback: {e}"
                )

        # Cython fallback
        aql_query = self._build_query_cy(
            collection_name,
            querylimit or self.querylimit or 0,
            offset or self._offset or 0,
        )
        self.query_parsed = aql_query
        self.logger.debug(f"AQL Query :: {aql_query}")
        return aql_query

    cdef str _build_query_cy(
        self, str collection, int limit, int offset
    ):
        """Cython fallback for full query assembly."""
        cdef list parts = []
        cdef str doc_var = self._doc_var

        # FOR clause
        cdef str graph_clause = self._build_graph_for_clause()
        if graph_clause:
            parts.append(graph_clause)
            doc_var = 'v'
        elif self._search_options:
            view = self._search_options.get('view', collection)
            parts.append(f'FOR {self._doc_var} IN {view}')
            search_clause = self._build_search_clause()
            if search_clause:
                parts.append(search_clause)
        else:
            parts.append(f'FOR {self._doc_var} IN {collection}')

        # FILTER
        cdef str filter_str = self._build_filter_clause_cy()
        if filter_str:
            parts.append(filter_str)

        # COLLECT (GROUP BY)
        if self.grouping:
            collect_parts = ', '.join(
                f'{g.strip()} = {doc_var}.{g.strip()}'
                for g in self.grouping
            )
            parts.append(f'COLLECT {collect_parts}')

        # SORT
        cdef str sort_str = self._build_sort_clause_cy()
        if sort_str:
            parts.append(sort_str)

        # LIMIT
        if limit > 0:
            if offset > 0:
                parts.append(f'LIMIT {offset}, {limit}')
            else:
                parts.append(f'LIMIT {limit}')

        # RETURN
        cdef str return_str
        if graph_clause:
            if self.fields:
                field_parts = ', '.join(
                    f'{f.strip()}: v.{f.strip()}' for f in self.fields
                )
                parts.append(f'RETURN {{ {field_parts} }}')
            else:
                parts.append('RETURN v')
        else:
            return_str = self._build_return_clause_cy()
            parts.append(return_str)

        return '\n    '.join(parts)
