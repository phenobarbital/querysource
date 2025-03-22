# cython: language_level=3, embedsignature=True
# Copyright (C) 2018-present Jesus Lara
#
# file: bigquery.pyx
"""
BigQuery SQL Parser.
"""
import re
from typing import Union, Dict, Any, List, Tuple
from datamodel.typedefs import NullDefault, SafeDict
from .sql cimport SQLParser
from ..types.validators import Entity, field_components

COMPARISON_TOKENS = ('>=', '<=', '<>', '!=', '<', '>',)

cdef class BigQueryParser(SQLParser):
    """ BigQuery SQL Parser with support for JSON columns. """

    def __cinit__(self, *args, **kwargs):
        """Initialize the critical attributes in __cinit__ to ensure they exist."""
        self._json_pattern = re.compile(r"^([a-zA-Z0-9_]+)\.([a-zA-Z0-9_\.]+)$")

    def __init__(
        self,
        *args,
        **kwargs
    ):
        super(BigQueryParser, self).__init__(
            *args,
            **kwargs
        )


    async def get_json_path(self, field: str) -> Tuple[str, str, bool]:
        """
        Parse a field with dot notation into column and JSON path.

        Args:
            field: Field name that might contain dot notation

        Returns:
            Tuple containing:
                - column_name: The base column name
                - json_path: The JSON path in '$' notation
                - is_json_field: Boolean indicating if this is a JSON field
        """
        if match := self._json_pattern.match(field):
            column_name, json_path = match.groups()
            # Convert dot notation to JSON path
            json_path_parts = json_path.split('.')
            json_path = f"$.{'.'.join(json_path_parts)}"
            return column_name, json_path, True
        return field, None, False

    async def process_json_field(self, field: str) -> str:
        """
        Process a field that might be a JSON field.

        Args:
            field: The field name

        Returns:
            Processed field string with JSON_VALUE if needed
        """
        column_name, json_path, is_json_field = await self.get_json_path(field)

        # Check if this field is defined as JSON in cond_definition
        is_json_defined = False
        try:
            is_json_defined = self.cond_definition.get(field) == "json"
        except (AttributeError, KeyError):
            pass

        if is_json_field or is_json_defined:
            if is_json_field and json_path:
                return f"JSON_VALUE({column_name}, '{json_path}')"
            else:
                # If defined as JSON but no path provided, use $ as root
                return f"JSON_VALUE({field}, '$')"
        return field

    async def filter_conditions(self, sql):
        """
        Override filter_conditions to handle JSON fields in BigQuery.
        """
        _sql = sql
        if self.filter:
            where_cond = []
            for key, value in self.filter.items():
                try:
                    if isinstance(int(key), (int, float)):
                        key = f'"{key}"'
                except ValueError:
                    pass

                # Check if this is a JSON field in cond_definition
                is_json_field = False
                try:
                    is_json_field = self.cond_definition.get(key) == "json"
                except (AttributeError, KeyError):
                    pass

                # Parse field components and check for dot notation
                field_info = field_components(key)[0]
                _, name, end = field_info

                # Process potential JSON field
                json_field_name, json_path, has_json_path = await self.get_json_path(key)

                # Determine if we need to use JSON_VALUE
                # Format the field name for the condition
                if use_json_value := is_json_field or has_json_path:
                    if has_json_path:
                        field_expr = f"JSON_VALUE({json_field_name}, '{json_path}')"
                    else:
                        field_expr = f"JSON_VALUE({key}, '$')"
                else:
                    field_expr = key

                # Handle various value types
                if isinstance(value, dict):
                    op, v = value.popitem()
                    if op in COMPARISON_TOKENS:
                        where_cond.append(f"{field_expr} {op} {v}")
                    else:
                        # Discard non-supported comparison token
                        continue

                elif isinstance(value, list):
                    fval = value[0]
                    if fval in self.valid_operators:
                        where_cond.append(f"{field_expr} {fval} {value[1]}")
                    else:
                        # List of values for IN clause
                        val = ','.join([f"{Entity.quoteString(v)}" for v in value])
                        # Check for negation operator
                        if end == '!':
                            where_cond.append(f"{name} NOT IN ({val})")
                        else:
                            where_cond.append(f"{field_expr} IN ({val})")

                elif isinstance(value, (str, int)):
                    if "BETWEEN" in str(value):
                        where_cond.append(f"({field_expr} {value})")
                    elif value in ('null', 'NULL'):
                        where_cond.append(f"{field_expr} IS NULL")
                    elif value in ('!null', '!NULL'):
                        where_cond.append(f"{field_expr} IS NOT NULL")
                    elif end == '!':
                        where_cond.append(f"{name} != {value}")
                    elif str(value).startswith('!'):
                        where_cond.append(
                            f"{field_expr} != {Entity.quoteString(value[1:])}"
                        )
                    else:
                        where_cond.append(
                            f"{field_expr}={Entity.quoteString(value)}"
                        )

                elif isinstance(value, bool):
                    where_cond.append(f"{field_expr} = {value}")

                else:
                    where_cond.append(
                        f"{field_expr}={Entity.quoteString(value)}"
                    )

            # Build WHERE clause
            if _sql.count('and_cond') > 0:
                _and = ' AND '.join(where_cond)
                _filter = f' AND {_and}'
                _sql = _sql.format_map(SafeDict(and_cond=_filter))
            elif _sql.count('where_cond') > 0:
                _and = ' AND '.join(where_cond)
                _filter = f' WHERE {_and}'
                _sql = _sql.format_map(SafeDict(where_cond=_filter))
            elif _sql.count('filter') > 0:
                _and = ' AND '.join(where_cond)
                _filter = f' WHERE {_and}'
                _sql = _sql.format_map(SafeDict(filter=_filter))
            else:
                # Need to attach the condition
                _and = ' AND '.join(where_cond)
                if 'WHERE' in _sql:
                    _filter = f' AND {_and}'
                else:
                    _filter = f' WHERE {_and}'
                _sql = f'{_sql}{_filter}'

        # Clean up any remaining placeholders
        if '{where_cond}' in _sql:
            _sql = _sql.format_map(SafeDict(where_cond=''))
        if '{and_cond}' in _sql:
            _sql = _sql.format_map(SafeDict(and_cond=''))
        if '{filter}' in _sql:
            _sql = _sql.format_map(SafeDict(filter=''))

        return _sql

    async def process_fields(self, sql: str):
        """
        Override process_fields to handle JSON fields in the SELECT clause.
        """
        if isinstance(self.fields, list) and len(self.fields) > 0:
            if self._add_fields:
                # Only add new fields if requested
                if match := self._select_pattern.search(sql):
                    # Extract the current SELECT fields
                    _fields = [field.strip() for field in match.group(2).split(",")]
                    # Process fields for JSON notation
                    processed_fields = []

                    for field in self.fields:
                        processed_field = await self.process_json_field(field)
                        # Add an alias if the field was transformed
                        if processed_field != field and "AS" not in processed_field:
                            # Extract the last part of the field name as alias
                            alias = field.split('.')[-1]
                            processed_field = f"{processed_field} AS {alias}"
                        processed_fields.append(processed_field)

                    # Add the new fields after the current fields
                    all_fields = _fields + processed_fields
                    # Reconstruct the SQL query with the modified SELECT clause
                    sql = sql[:match.start(2)] + ' ' + ", ".join(all_fields) + ' ' + sql[match.end(2):]
                    return sql

            # Process fields for JSON notation
            processed_fields = []
            for field in self.fields:
                processed_field = await self.process_json_field(field)
                # Add an alias if the field was transformed
                if processed_field != field and "AS" not in processed_field:
                    # Extract the last part of the field name as alias
                    alias = field.split('.')[-1]
                    processed_field = f"{processed_field} AS {alias}"
                processed_fields.append(processed_field)

            sql = sql.replace(' * FROM', ' {fields} FROM')
            fields = ', '.join(processed_fields)
            sql = sql.format_map(SafeDict(fields=fields))

        elif isinstance(self.fields, str):
            sql = sql.replace(' * FROM', ' {fields} FROM')
            field_list = self.fields.split(',')
            processed_fields = []

            for field in field_list:
                field = field.strip()
                processed_field = await self.process_json_field(field)
                # Add an alias if the field was transformed
                if processed_field != field and "AS" not in processed_field:
                    # Extract the last part of the field name as alias
                    alias = field.split('.')[-1]
                    processed_field = f"{processed_field} AS {alias}"
                processed_fields.append(processed_field)

            fields = ', '.join(processed_fields)
            sql = sql.format_map(SafeDict(fields=fields))

        elif '{fields}' in self.query_raw:
            self._conditions.update({'fields': '*'})

        return sql
