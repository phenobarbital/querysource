# cython: language_level=3, embedsignature=True
# Copyright (C) 2018-present Jesus Lara
#
# file: mongo.pyx
"""
MongoDB/DocumentDB Parser for QuerySource.
"""
import re
from typing import Any, Union, Optional, Dict, List
from collections import defaultdict
from datamodel.typedefs import SafeDict, NullDefault
from datamodel.parsers.json import json_encoder, json_decoder
from querysource.exceptions cimport EmptySentence
from ..types.validators import Entity, field_components
from .abstract cimport AbstractParser


cdef class MongoParser(AbstractParser):
    """
    MongoDB/DocumentDB Parser

    Translates JSON query configuration into MongoDB query formats.
    """
    def __cinit__(self, *args, **kwargs):
        """Initialize the critical attributes in __cinit__ to ensure they exist."""
        self.valid_operators = (
            '$eq', '$gt', '$gte', '$lt', '$lte', '$ne', '$in', '$nin', '$exists', '$type', '$regex'
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

    def __init__(
        self,
        *args,
        **kwargs
    ):
        super(MongoParser, self).__init__(
            *args,
            **kwargs
        )


    async def get_query(self):
        """Return the built query."""
        return await self.build_query()

    async def process_fields(self) -> dict:
        """
        Process fields into MongoDB projection format.

        Returns:
            dict: MongoDB projection object
        """
        projection = {}

        if isinstance(self.fields, list) and len(self.fields) > 0:
            # Include only specified fields
            for field in self.fields:
                projection[field] = 1
        elif isinstance(self.fields, str) and self.fields:
            # Handle comma-separated string of fields
            field_list = [f.strip() for f in self.fields.split(',')]
            for field in field_list:
                projection[field] = 1

        # If _id wasn't explicitly included but other fields were, exclude it by default
        if projection and '_id' not in projection:
            projection['_id'] = 0

        return projection if projection else None

    async def process_filter_conditions(self) -> dict:
        """
        Process filter conditions into MongoDB query format.

        Returns:
            dict: MongoDB query filter
        """
        filter_conditions = {}

        if not self.filter:
            return filter_conditions

        for key, value in self.filter.items():
            field_name = key
            field_type = self.cond_definition.get(key, None)

            # Parse special field endings (e.g., field! for negation)
            parts = field_components(key)
            if parts:
                _, field_name, suffix = parts[0]

            # Handle different value types
            if isinstance(value, dict):
                # Already in MongoDB operator format or needs conversion
                op, val = next(iter(value.items()))

                # Convert SQL-style operators to MongoDB
                if op in self.operator_map:
                    mongo_op = self.operator_map[op]
                    filter_conditions[field_name] = {mongo_op: self._convert_value(val, field_type)}
                else:
                    # Assume it's already a MongoDB operator
                    filter_conditions[field_name] = {op: self._convert_value(val, field_type)}

            elif isinstance(value, list):
                # Handle list values - check if first item is an operator
                if value and value[0] in self.valid_operators:
                    op = value[0]
                    val = value[1] if len(value) > 1 else None
                    filter_conditions[field_name] = {op: self._convert_value(val, field_type)}
                else:
                    # Treat as $in operator if list contains values
                    if suffix == '!':
                        filter_conditions[field_name] = {'$nin': [self._convert_value(v, field_type) for v in value]}
                    else:
                        filter_conditions[field_name] = {'$in': [self._convert_value(v, field_type) for v in value]}

            elif isinstance(value, str):
                # Handle string-based special values
                if value.upper() in ('NULL', 'NONE'):
                    filter_conditions[field_name] = {'$exists': False}
                elif value.upper() in ('!NULL', '!NONE'):
                    filter_conditions[field_name] = {'$exists': True}
                elif 'BETWEEN' in value.upper():
                    # Parse BETWEEN syntax (MongoDB uses $gte and $lte)
                    match = re.search(r'BETWEEN\s+(\S+)\s+AND\s+(\S+)', value, re.IGNORECASE)
                    if match:
                        low, high = match.groups()
                        filter_conditions[field_name] = {
                            '$gte': self._convert_value(low, field_type),
                            '$lte': self._convert_value(high, field_type)
                        }
                elif value.startswith('!'):
                    # Negation
                    filter_conditions[field_name] = {'$ne': self._convert_value(value[1:], field_type)}
                else:
                    # Simple equality
                    filter_conditions[field_name] = self._convert_value(value, field_type)

            elif isinstance(value, bool):
                # Boolean value
                filter_conditions[field_name] = value

            elif value is None:
                # Handle NULL/None values
                filter_conditions[field_name] = {'$exists': False}

            else:
                # Default for numbers and other types
                filter_conditions[field_name] = value

        return filter_conditions

    def _convert_value(self, value: Any, field_type: Optional[str] = None) -> Any:
        """
        Convert a value based on its field type.

        Args:
            value: The value to convert
            field_type: Optional type hint

        Returns:
            Converted value
        """
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
        # Return as-is for other types or when no conversion is needed
        return value

    async def process_ordering(self) -> Optional[List[tuple]]:
        """
        Process ordering into MongoDB sort format.

        Returns:
            List of tuples or None: MongoDB sort specification
        """
        if not self.ordering:
            return None

        sort_list = []

        if isinstance(self.ordering, list):
            for item in self.ordering:
                if isinstance(item, str):
                    if item.startswith('-'):
                        sort_list.append((item[1:], -1))  # Descending
                    else:
                        sort_list.append((item, 1))  # Ascending
        elif isinstance(self.ordering, str):
            fields = [f.strip() for f in self.ordering.split(',')]
            for field in fields:
                if field.startswith('-'):
                    sort_list.append((field[1:], -1))  # Descending
                else:
                    sort_list.append((field, 1))  # Ascending

        return sort_list if sort_list else None

    async def build_query(self, querylimit: int = None, offset: int = None):
        """
        Build a MongoDB/DocumentDB query from the JSON configuration.

        Args:
            querylimit: Optional limit for the query
            offset: Optional offset for the query

        Returns:
            dict: Complete MongoDB query specification
        """
        print("Building MongoDB Query")
        try:
            query = json_decoder(self.query_raw)
            self._base_query.update(query)
        except Exception as e:
            self.logger.error(f"Error parsing query JSON: {e}")
        # Create base query
        query = dict(self._base_query)

        # Set collection name (uses either schema.table or just table)
        if self.schema and self.tablename:
            query['collection_name'] = f"{self.schema}.{self.tablename}"
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
        query['limit'] = 1
        if offset:
            query['skip'] = offset
        elif self._offset:
            query['skip'] = self._offset

        # Apply any additional conditions from self._conditions
        if self._conditions:
            try:
                # Format conditions that might be in the query string
                for k, v in self._conditions.items():
                    placeholder = "{" + k + "}"
                    # Check if this is a placeholder in a filter condition
                    for filter_key, filter_val in query['filter'].items():
                        if isinstance(filter_val, str) and placeholder in filter_val:
                            query['filter'][filter_key] = filter_val.replace(placeholder, str(v))
            except Exception as e:
                self.logger.warning(f"Error applying conditions to query: {e}")

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
