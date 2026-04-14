# cython: language_level=3, embedsignature=True, boundscheck=False, wraparound=False
# Copyright (C) 2018-present Jesus Lara
#
# file: mongo.pyx
"""MongoDB/DocumentDB Parser for QuerySource."""
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


cdef class MongoParser(AbstractParser):
    """MongoDB/DocumentDB Parser.

    Translates JSON query configuration into MongoDB query formats.
    """
    def __cinit__(self, *args, **kwargs):
        """Initialize the critical attributes in __cinit__."""
        self.valid_operators = (
            '$eq', '$gt', '$gte', '$lt', '$lte', '$ne',
            '$in', '$nin', '$exists', '$type', '$regex'
        )
        self.operator_map = {
            '=': '$eq',
            '>': '$gt',
            '>=': '$gte',
            '<': '$lt',
            '<=': '$lte',
            '<>': '$ne',
            '!=': '$ne',
            'IS': '$exists',
            'IS NOT': '$exists'
        }
        self._base_query = {
            'collection_name': None,
            'query': {},
        }

    def __init__(self, *args, **kwargs):
        super(MongoParser, self).__init__(*args, **kwargs)

    async def get_query(self):
        """Return the built query."""
        return await self.build_query()

    # ----- process_fields -----

    async def process_fields(self):
        """Process fields into MongoDB projection format."""
        if HAS_RUST and isinstance(self.fields, list) and len(self.fields) > 0:
            try:
                return _rs.mongo_process_fields(list(self.fields))
            except Exception:
                pass
        return self._process_fields_cy()

    cdef object _process_fields_cy(self):
        """Cython fallback for process_fields."""
        cdef dict projection = {}
        cdef str field
        cdef list field_list

        if isinstance(self.fields, list) and len(self.fields) > 0:
            for field in self.fields:
                projection[field] = 1
        elif isinstance(self.fields, str) and self.fields:
            field_list = [f.strip() for f in self.fields.split(',')]
            for field in field_list:
                projection[field] = 1

        if projection and '_id' not in projection:
            projection['_id'] = 0

        return projection if projection else None

    # ----- process_filter_conditions -----

    async def process_filter_conditions(self):
        """Process filter conditions into MongoDB query format."""
        if HAS_RUST and self.filter and isinstance(self.filter, dict):
            try:
                return _rs.mongo_filter_conditions(
                    self.filter,
                    self.cond_definition if self.cond_definition else {}
                )
            except Exception:
                pass
        return self._filter_conditions_cy()

    cdef object _filter_conditions_cy(self):
        """Cython fallback for filter_conditions."""
        cdef dict filter_conditions = {}
        cdef str key
        cdef str field_name
        cdef str suffix
        cdef object value

        if not self.filter:
            return filter_conditions

        for key, value in self.filter.items():
            field_name = key
            field_type = self.cond_definition.get(key, None)

            parts = field_components(key)
            if parts:
                _, field_name, suffix = parts[0]

            if isinstance(value, dict):
                op, val = next(iter(value.items()))
                if op in self.operator_map:
                    mongo_op = self.operator_map[op]
                    filter_conditions[field_name] = {
                        mongo_op: self._convert_value(val, field_type)
                    }
                else:
                    filter_conditions[field_name] = {
                        op: self._convert_value(val, field_type)
                    }

            elif isinstance(value, list):
                if value and value[0] in self.valid_operators:
                    op = value[0]
                    val = value[1] if len(value) > 1 else None
                    filter_conditions[field_name] = {
                        op: self._convert_value(val, field_type)
                    }
                else:
                    if suffix == '!':
                        filter_conditions[field_name] = {
                            '$nin': [
                                self._convert_value(v, field_type)
                                for v in value
                            ]
                        }
                    else:
                        filter_conditions[field_name] = {
                            '$in': [
                                self._convert_value(v, field_type)
                                for v in value
                            ]
                        }

            elif isinstance(value, str):
                if value.upper() in ('NULL', 'NONE'):
                    filter_conditions[field_name] = {'$exists': False}
                elif value.upper() in ('!NULL', '!NONE'):
                    filter_conditions[field_name] = {'$exists': True}
                elif 'BETWEEN' in value.upper():
                    match = re.search(
                        r'BETWEEN\s+(\S+)\s+AND\s+(\S+)',
                        value, re.IGNORECASE
                    )
                    if match:
                        low, high = match.groups()
                        filter_conditions[field_name] = {
                            '$gte': self._convert_value(low, field_type),
                            '$lte': self._convert_value(high, field_type)
                        }
                elif value.startswith('!'):
                    filter_conditions[field_name] = {
                        '$ne': self._convert_value(value[1:], field_type)
                    }
                else:
                    filter_conditions[field_name] = self._convert_value(
                        value, field_type
                    )

            elif isinstance(value, bool):
                filter_conditions[field_name] = value

            elif value is None:
                filter_conditions[field_name] = {'$exists': False}

            else:
                filter_conditions[field_name] = value

        return filter_conditions

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
        """Process ordering into MongoDB sort format."""
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
                    return _rs.mongo_process_ordering(ordering_list)
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
                        sort_list.append((item[1:], -1))
                    else:
                        sort_list.append((item, 1))
        elif isinstance(self.ordering, str):
            fields = [f.strip() for f in self.ordering.split(',')]
            for field in fields:
                if field.startswith('-'):
                    sort_list.append((field[1:], -1))
                else:
                    sort_list.append((field, 1))

        return sort_list if sort_list else None

    # ----- build_query -----

    async def build_query(self, querylimit: int = None, offset: int = None):
        """Build a MongoDB/DocumentDB query from the JSON configuration."""
        cdef dict query
        cdef str collection_name

        try:
            query = json_decoder(self.query_raw)
            self._base_query.update(query)
        except Exception as e:
            self.logger.error(f"Error parsing query JSON: {e}")

        query = dict(self._base_query)

        # Set collection name
        if self.schema and self.tablename:
            collection_name = f"{self.schema}.{self.tablename}"
            query['collection_name'] = collection_name
        elif self.tablename:
            query['collection_name'] = self.tablename

        # Process query parts
        query['query'] = await self.process_filter_conditions()

        if process := await self.process_fields():
            query['projection'] = process

        # Process sort order
        if ordering := await self.process_ordering():
            query['sort'] = ordering

        # Handle pagination
        if querylimit:
            query['limit'] = querylimit
        elif self.querylimit:
            query['limit'] = self.querylimit

        if offset:
            query['skip'] = offset
        elif self._offset:
            query['skip'] = self._offset

        # Apply any additional conditions from self._conditions
        if self._conditions:
            try:
                for k, v in self._conditions.items():
                    placeholder = "{" + k + "}"
                    if 'query' in query and isinstance(query['query'], dict):
                        for filter_key, filter_val in query['query'].items():
                            if (isinstance(filter_val, str)
                                    and placeholder in filter_val):
                                query['query'][filter_key] = filter_val.replace(
                                    placeholder, str(v)
                                )
            except Exception as e:
                self.logger.warning(
                    f"Error applying conditions to query: {e}"
                )

        self.query_object = query

        if 'collection_name' not in self.query_object:
            raise RuntimeError(
                "Missing 'collection' in MongoDB query"
            )

        self.logger.debug(
            f"MongoDB Query :: {json_encoder(query)}"
        )

        if not self.query_object or not self.query_object['collection_name']:
            raise EmptySentence(
                'QS MongoDB Error, no valid Query to parse.'
            )

        return self.query_object
