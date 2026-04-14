# cython: language_level=3, embedsignature=True, boundscheck=False, wraparound=False
# Copyright (C) 2018-present Jesus Lara
#
# file: elastic.pyx
"""Elasticsearch Parser for QuerySource."""
import re
from datamodel.parsers.json import json_encoder, json_decoder
from querysource.exceptions import EmptySentence
from ..types.validators import Entity, field_components
from .abstract cimport AbstractParser

# Try to import Rust extension for accelerated parsing
try:
    from querysource.qs_parsers import _qs_parsers as _rs
    HAS_RUST = True
except ImportError:
    HAS_RUST = False


cdef class ElasticParser(AbstractParser):
    """Elasticsearch Parser.

    Translates JSON query configuration into Elasticsearch Query DSL format.
    """
    def __cinit__(self, *args, **kwargs):
        """Initialize the critical attributes in __cinit__."""
        self.valid_operators = (
            '=', '>', '>=', '<', '<=', '!=', '<>',
            'IS', 'IS NOT',
        )
        self.operator_map = {
            '=': 'term',
            '>': 'gt',
            '>=': 'gte',
            '<': 'lt',
            '<=': 'lte',
            '<>': 'must_not_term',
            '!=': 'must_not_term',
            'IS': 'exists',
            'IS NOT': 'not_exists',
        }
        self._base_query = {
            'index': None,
            'query': {'bool': {}},
        }

    def __init__(self, *args, **kwargs):
        super(ElasticParser, self).__init__(*args, **kwargs)

    async def get_query(self):
        """Return the built query."""
        return await self.build_query()

    # ----- process_fields -----

    async def process_fields(self):
        """Process fields into Elasticsearch _source format."""
        if HAS_RUST and isinstance(self.fields, list) and len(self.fields) > 0:
            try:
                return _rs.es_process_fields(list(self.fields))
            except Exception:
                pass
        return self._process_fields_cy()

    cdef object _process_fields_cy(self):
        """Cython fallback for process_fields."""
        cdef list source_fields = []
        cdef str field
        cdef list field_list

        if isinstance(self.fields, list) and len(self.fields) > 0:
            for field in self.fields:
                source_fields.append(field)
        elif isinstance(self.fields, str) and self.fields:
            field_list = [f.strip() for f in self.fields.split(',')]
            for field in field_list:
                source_fields.append(field)

        return source_fields if source_fields else None

    # ----- process_filter_conditions -----

    async def process_filter_conditions(self):
        """Process filter conditions into Elasticsearch bool query format."""
        if HAS_RUST and self.filter and isinstance(self.filter, dict):
            try:
                return _rs.es_filter_conditions(
                    self.filter,
                    self.cond_definition if self.cond_definition else {}
                )
            except Exception:
                pass
        return self._filter_conditions_cy()

    cdef object _filter_conditions_cy(self):
        """Cython fallback for filter_conditions."""
        cdef dict bool_query = {}
        cdef list filter_clauses = []
        cdef list must_not_clauses = []
        cdef str key
        cdef str field_name
        cdef str suffix
        cdef object value

        if not self.filter:
            return bool_query

        for key, value in self.filter.items():
            field_name = key
            field_type = self.cond_definition.get(key, None)

            parts = field_components(key)
            if parts:
                _, field_name, suffix = parts[0]
            else:
                suffix = ''

            if isinstance(value, dict):
                op, val = next(iter(value.items()))
                val = self._convert_value(val, field_type)
                if op == '=' or op == 'term':
                    filter_clauses.append({'term': {field_name: val}})
                elif op in ('!=', '<>'):
                    must_not_clauses.append({'term': {field_name: val}})
                elif op in ('>', '>=', '<', '<='):
                    range_op_map = {'>': 'gt', '>=': 'gte', '<': 'lt', '<=': 'lte'}
                    filter_clauses.append({
                        'range': {field_name: {range_op_map[op]: val}}
                    })
                elif op == 'IS':
                    filter_clauses.append({'exists': {'field': field_name}})
                elif op == 'IS NOT':
                    must_not_clauses.append({'exists': {'field': field_name}})
                else:
                    filter_clauses.append({'term': {field_name: val}})

            elif isinstance(value, list):
                converted = [self._convert_value(v, field_type) for v in value]
                if suffix == '!':
                    must_not_clauses.append({
                        'terms': {field_name: converted}
                    })
                else:
                    filter_clauses.append({
                        'terms': {field_name: converted}
                    })

            elif isinstance(value, str):
                if value.upper() in ('NULL', 'NONE'):
                    must_not_clauses.append({'exists': {'field': field_name}})
                elif value.upper() in ('!NULL', '!NONE'):
                    filter_clauses.append({'exists': {'field': field_name}})
                elif 'BETWEEN' in value.upper():
                    match = re.search(
                        r'BETWEEN\s+(\S+)\s+AND\s+(\S+)',
                        value, re.IGNORECASE
                    )
                    if match:
                        low, high = match.groups()
                        filter_clauses.append({
                            'range': {
                                field_name: {
                                    'gte': self._convert_value(low, field_type),
                                    'lte': self._convert_value(high, field_type),
                                }
                            }
                        })
                elif value.startswith('!'):
                    must_not_clauses.append({
                        'term': {
                            field_name: self._convert_value(value[1:], field_type)
                        }
                    })
                else:
                    filter_clauses.append({
                        'term': {
                            field_name: self._convert_value(value, field_type)
                        }
                    })

            elif isinstance(value, bool):
                filter_clauses.append({'term': {field_name: value}})

            elif value is None:
                must_not_clauses.append({'exists': {'field': field_name}})

            else:
                filter_clauses.append({'term': {field_name: value}})

        if filter_clauses:
            bool_query['filter'] = filter_clauses
        if must_not_clauses:
            bool_query['must_not'] = must_not_clauses

        return bool_query

    def _convert_value(self, value, field_type=None):
        """Convert a value based on its field type."""
        if field_type == 'string' and not isinstance(value, str):
            return str(value)
        elif field_type == 'integer' and not isinstance(value, int):
            try:
                return int(value)
            except (ValueError, TypeError):
                return value
        elif field_type == 'float' and not isinstance(value, float):
            try:
                return float(value)
            except (ValueError, TypeError):
                return value
        elif field_type == 'boolean' and not isinstance(value, bool):
            if isinstance(value, str):
                return value.lower() in ('true', 'yes', '1')
            return bool(value)
        return value

    # ----- process_ordering -----

    async def process_ordering(self):
        """Process ordering into Elasticsearch sort format."""
        if HAS_RUST and self.ordering:
            try:
                ordering_list = None
                if isinstance(self.ordering, str):
                    ordering_list = [
                        f.strip() for f in self.ordering.split(',')
                    ]
                elif isinstance(self.ordering, list):
                    ordering_list = list(self.ordering)
                else:
                    ordering_list = []
                if ordering_list:
                    return _rs.es_process_ordering(ordering_list)
            except Exception:
                pass
        return self._process_ordering_cy()

    cdef object _process_ordering_cy(self):
        """Cython fallback for process_ordering."""
        cdef list sort_list

        if not self.ordering:
            return None

        sort_list = []
        if isinstance(self.ordering, list):
            for item in self.ordering:
                if isinstance(item, str):
                    if item.startswith('-'):
                        sort_list.append({item[1:]: 'desc'})
                    else:
                        sort_list.append({item: 'asc'})
        elif isinstance(self.ordering, str):
            fields = [f.strip() for f in self.ordering.split(',')]
            for field in fields:
                if field.startswith('-'):
                    sort_list.append({field[1:]: 'desc'})
                else:
                    sort_list.append({field: 'asc'})

        return sort_list if sort_list else None

    # ----- build_query -----

    async def build_query(self, querylimit: int = None, offset: int = None):
        """Build an Elasticsearch Query DSL body from the JSON configuration."""
        cdef dict query
        cdef str index_name

        try:
            query = json_decoder(self.query_raw)
            self._base_query.update(query)
        except Exception as e:
            self.logger.error(f"Error parsing query JSON: {e}")

        query = dict(self._base_query)

        # Set index name
        if self.schema and self.tablename:
            index_name = f"{self.schema}.{self.tablename}"
            query['index'] = index_name
        elif self.tablename:
            query['index'] = self.tablename

        # Process filter conditions into bool query
        bool_query = await self.process_filter_conditions()
        if bool_query:
            query['query'] = {'bool': bool_query}
        else:
            query['query'] = {'match_all': {}}

        # Process _source fields
        if source := await self.process_fields():
            query['_source'] = source

        # Process sort order
        if ordering := await self.process_ordering():
            query['sort'] = ordering

        # Handle pagination
        if querylimit:
            query['size'] = querylimit
        elif self.querylimit:
            query['size'] = self.querylimit

        if offset:
            query['from'] = offset
        elif self._offset:
            query['from'] = self._offset

        # Apply any additional conditions from self._conditions
        if self._conditions:
            try:
                for k, v in self._conditions.items():
                    placeholder = "{" + k + "}"
                    # Replace placeholders in string values within the query
                    if 'query' in query and isinstance(query['query'], dict):
                        self._replace_placeholders(query['query'], placeholder, str(v))
            except Exception as e:
                self.logger.warning(
                    f"Error applying conditions to query: {e}"
                )

        self.query_object = query

        if 'index' not in self.query_object or not self.query_object['index']:
            raise EmptySentence(
                'QS Elasticsearch Error, no valid index to query.'
            )

        self.logger.debug(
            f"Elasticsearch Query :: {json_encoder(query)}"
        )

        return self.query_object

    def _replace_placeholders(self, obj, placeholder, replacement):
        """Recursively replace placeholders in dict/list structures."""
        if isinstance(obj, dict):
            for key, val in obj.items():
                if isinstance(val, str) and placeholder in val:
                    obj[key] = val.replace(placeholder, replacement)
                elif isinstance(val, (dict, list)):
                    self._replace_placeholders(val, placeholder, replacement)
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                if isinstance(item, str) and placeholder in item:
                    obj[i] = item.replace(placeholder, replacement)
                elif isinstance(item, (dict, list)):
                    self._replace_placeholders(item, placeholder, replacement)
