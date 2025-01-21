# cython: language_level=3, embedsignature=True, boundscheck=False, wraparound=True, initializedcheck=False
# Copyright (C) 2018-present Jesus Lara
#
import os
import asyncio
import builtins
import re
import typing
from typing import (
    Dict
)
from collections.abc import Sequence
from functools import partial
from libcpp cimport bool as bool_t
from cpython cimport datetime
from cpython.datetime cimport datetime as dt
from dataclasses import _MISSING_TYPE
from navconfig import config
from decimal import Decimal
from dateutil import parser
from uuid import UUID
from ..utils.functions import *
import orjson
from numpy import int64, ndarray


cdef list udf = ["CURRENT_YEAR", "CURRENT_MONTH", "TODAY", "YESTERDAY", "LAST_YEAR", "FDOM", "LDOM"]
cdef list UDF_LIST = os.environ.get('UDF_LIST', udf)
cdef list PG_CONSTANTS = os.environ.get(
    'PG_CONSTANTS',
     ["CURRENT_DATE", "CURRENT_TIMESTAMP"]
)
cdef list PG_UDF = os.environ.get('PG_UDF', ["now()"])

cdef object eval_field = re.compile(r'^(?:(\@|!|#|~|\:|))(\w*)(?:(\||\&|\!|\~|\#)|)+$')


cpdef object strtobool(str val):
    """Convert a string representation of truth to true (1) or false (0).

    True values are 'y', 'yes', 't', 'true', 'on', and '1'; false values
    are 'n', 'no', 'f', 'false', 'off', and '0'.  Raises ValueError if
    'val' is anything else.
    """
    val = val.lower()
    if val in ('y', 'yes', 't', 'true', 'on', '1'):
        return True
    elif val in ('n', 'no', 'f', 'false', 'off', '0', 'null'):
        return False
    else:
        raise ValueError(
            f"invalid truth value for {val}"
        )

cpdef list field_components(str field):
    try:
        return re.findall(eval_field, field)
    except ValueError:
        return (None, field, None)

cpdef bool_t is_pandas_dataframe(obj):
    """
    Check if an object is a pandas DataFrame without importing pandas.

    Parameters:
    obj (any): The object to check.

    Returns:
    bool: True if the object is a pandas DataFrame, False otherwise.
    """
    return hasattr(obj, "_data") and hasattr(obj, "loc") and hasattr(obj, "iloc")

cpdef bool_t is_empty(object value):
    cdef bool_t result = False
    if value is None:
        return True
    if is_pandas_dataframe(value):
        return value.empty
    elif isinstance(value, str) and value == '':
        result = True
    elif isinstance(value, (int, float)) and value == 0:
        result = False
    elif not value:
        result = True
    return result

cpdef is_camel_case(str value):
    if ' ' in value:
        return True
    # Check for CamelCase
    camel_case_pattern = re.compile(r'^(?:[A-Z][a-z]+)+$')
    return bool(camel_case_pattern.match(value))


cdef str escapeString(str value):
    v = value if value != 'None' else ""
    v = str(v).replace("'", "''")
    v = "'{}'".format(v) if isinstance(v, str) else v
    return v


cdef str parseString(str value):
    if str(value).startswith("'"):
        return value[value.startswith('"') and len('"'):-1]
    else:
        return value

cdef str quoteString(object value):
    v = value if value != 'None' else ""
    if value == 'null' or value == 'NULL':
            return v
    if isinstance(v, int):
        # quoting to string:
        return "'{}'".format(v)
    elif isinstance(v, str):
        start_quote = v.startswith("'")
        end_quote = v.endswith("'")

        # Slice the string if it starts/ends with a quote
        inner = v[1:-1] if start_quote and end_quote else (v[1:] if start_quote else (v[:-1] if end_quote else v))
        # Escape single quotes
        inner = inner.replace("'", "''")

        if start_quote: # was already quoted
            return "'{}'".format(inner)
        elif v.startswith('"'): # is double quoted
            return v.replace('"', "'")
        else:
            return "'{}'".format(v)
    else:
        return v

cpdef bool_t is_callable(object value):
    """Return if value is a callable (function object).
    """
    if value is None:
        return False
    is_missing = (value == _MISSING_TYPE)
    return callable(value) if not is_missing else False

cpdef bool_t is_async_callable(object obj):
    while isinstance(obj, partial):
        obj = obj.func
    return asyncio.iscoroutinefunction(obj) or (
        callable(obj) and asyncio.iscoroutinefunction(obj.__call__)
    )

## Functional validators (is_xxx functions)
cpdef bool_t is_udf(object value):
    return value in UDF_LIST

cdef bool_t is_pg_function(object value):
    if '(' in value:
        return True
    else:
        return value in PG_UDF

cpdef bool_t is_pgconstant(object value):
    return value in PG_CONSTANTS


cpdef bool_t is_array(object value):
    return isinstance(value,(list, dict, Sequence, ndarray))


cpdef bool_t is_epoch(object value):
    try:
        # validate if unix epoch
        return dt.fromtimestamp(int(value))
    except Exception:
        return False

cdef object to_epoch(object value):
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.timestamp() * 1000
    elif isinstance(value, str):
        return int(value)
    else:
        return value

cpdef bool_t is_date(object value):
    response = False
    if isinstance(value, list): # between
        return True # TODO: validation of any element (reqcursive)
    elif isinstance(value, (datetime.date, datetime.datetime)):
        return True
    else:
        try:
            return parser.parse(value).date()
        except Exception:
            pass
        try:
            # validate if unix epoch
            return is_epoch(value)
        except Exception:
            return False


cdef str to_date(object value):
    """to_date.
    Returns obj converted to date.
    """
    if isinstance(value, (datetime.date, datetime.datetime)):
        return str(value)
    else:
        return quoteString(value)


cpdef bool_t is_datetime(object value):
    if isinstance(value, (datetime.datetime, datetime.timedelta)):
        return True
    else:
        try:
            val = parser.parse(value)
            if val:
                return True
            return False
        except ValueError:
            return False


cpdef bool_t is_uuid(object value):
    """Returns if value is an UUID object.
    """
    if isinstance(value, UUID):
        # already an uuid
        return value
    try:
        return UUID(str(value))
    except ValueError:
        return False

cdef str to_uuid(object value):
    """Returns a UUID version of a str column.
    """
    return quoteString(str(value))

cpdef bool_t is_integer(value):
    if isinstance(value, (dict, list)):
        return False
    if isinstance(value, int):
        return True
    else:
        try:
            return int(value)
        except (TypeError, ValueError):
            return False

cpdef bool_t is_float(object value):
    """is_float.

    Returns object converted to float.
    """
    if isinstance(value, (float, Decimal)):
        return value
    elif isinstance(value, _MISSING_TYPE):
        return False
    else:
        try:
            return isinstance(float(value), float)
        except (TypeError, ValueError):
            return False


cpdef bool_t is_decimal(object value):
    """is_decimal.

    Returns a Decimal version of object.
    """
    if isinstance(value, Decimal):
        return value
    else:
        try:
            return Decimal(value)
        except (TypeError, ValueError):
            return False


cpdef bool_t isnumber(object value):
    return is_decimal(value) or is_float(value) or is_integer(value)

is_number = isnumber


cpdef bool_t is_dict(object value):
    if isinstance(value, dict):
        return True
    else:
        return False

cpdef bool_t is_boolean(object value):
    if isinstance(value, (dict, list)):
        return False # Unable to Test
    elif isinstance(value, bool):
        return True
    try:
        return bool(strtobool(str(value)))
    except ValueError:
        return False

cdef str to_boolean(object value):
    if isinstance(value, bool):
        return value
    else:
        if strtobool(value) is True:
            return 'TRUE'
        else:
            return 'FALSE'

cpdef bool_t is_object(object value):
    return isinstance(value, object)

cpdef bool_t is_string(object value):
    if isinstance(value, int):
        return str(value)
    else:
        return isinstance(value, (
            str,
            datetime.datetime,
            datetime.time,
            datetime.timedelta,
            UUID
        ))

cdef object get_config_var(object value):
    return config.get(value)

cdef str to_string(object value):
    return quoteString(escape_string(value))

cpdef object escape_string(object value):
    try:
        return value.translate(
            value.maketrans({
                "\0": "\\0",
                "\r": "\\r",
                "\x08": "\\b",
                "\x09": "\\t",
                "\x1a": "\\z",
                "\n": "\\n",
                "\r": "\\r",
                "\"": "",
                "'": "",
                "\\": "\\\\",
                "%": "\\%"
            }))
    except AttributeError:
        return value
    except (TypeError, ValueError):
        return None

cdef int to_unquoted(value):
    if isinstance(value, int):
        return value
    else:
        try:
            return int(value)
        except (ValueError, TypeError):
            raise

cdef dict type_validators = {
    "uuid": [ is_uuid, to_uuid ],
    "array": [ is_array, to_unquoted ],
    "json": [ is_array, to_unquoted ],
    # "object": is_object,
    "int": [is_integer, to_unquoted],
    "integer": [is_integer, to_unquoted],
    "float": [is_float, to_unquoted],
    "numeric": [is_number, to_unquoted],
    "epoch": [ is_epoch, to_epoch ],
    # "callable": is_callable,
    # "function": is_callable,
    # "async_fn": is_async_callable,
    "datetime": [is_datetime, to_date],
    "date": [is_date, to_date],
    "timestamp": [is_datetime, to_date],
    "decimal": [is_decimal, to_unquoted],
    "boolean": [is_boolean, to_boolean],
    "udf": [is_udf, to_udf],
    "field": [is_string, to_string],
    "string": [is_string, to_string],
    "varchar": [is_string, to_string],
    "literal": [is_object, None],
}

## Entity Class:
cdef class Entity:
    """Entity.
    Used to convert entities (string, number, dates) to appropiated string on SQL queries.
    """

    @classmethod
    def is_integer(cls, _type):
        return _type in (int, int64)

    @classmethod
    def is_number(cls, _type):
        return _type in (int, int64, float, Decimal, bytes, bool)

    @classmethod
    def is_string(cls, _type):
        return isinstance(_type, (
            str,
            datetime.datetime,
            datetime.time,
            datetime.timedelta,
            UUID
        ))

    @classmethod
    def escape(cls, _type):
        return _type.translate(
            _type.maketrans({
            "\0": "\\0",
            "\r": "\\r",
            "\x08": "\\b",
            "\x09": "\\t",
            "\x1a": "\\z",
            "\n": "\\n",
            "\r": "\\r",
            "\"": "",
            "'": "",
            "\\": "\\\\",
            "%": "\\%"
        }))

    @classmethod
    def is_date(cls, _type):
        return _type in (datetime.date, datetime.datetime, datetime.time, datetime.timedelta)

    @classmethod
    def is_array(cls, t):
        return isinstance(t,(list, dict, Sequence, ndarray))

    @classmethod
    def is_bool(cls, _type):
        return isinstance(_type, bool)

    @classmethod
    def is_typing(cls, _type):
        try:
            return isinstance(_type, typing._GenericAlias) or isinstance(_type, typing._SpecialForm)
        except:
            return False

    @classmethod
    def toSQL(cls, value, _type, dbtype: str = None):
        v = f"{value!s}" if Entity.is_date(_type) else value
        if Entity.is_typing(_type):
            v = orjson.dumps(value).decode('utf-8') if value else None
            # v = f"{v!s}" if dbtype == 'jsonb' else v
            v = "NULL" if (v in ["None", "null"]) else v
            return v
        v = f"{value!s}" if Entity.is_string(_type) and value is not None else v
        v = value if Entity.is_number(_type) else v
        v = str(value) if isinstance(value, UUID) else v
        # json conversion
        v = orjson.dumps(value.encode('utf-8')) if _type in [dict, Dict] else v
        v = f"{value!s}" if dbtype == "array" and value is not None else v
        # formatting htstore column
        v = (
            ",".join({"{}=>{}".format(k, v) for k, v in value.items()})
            if isinstance(value, dict) and dbtype == "hstore"
            else v
        )
        v = "NULL" if (value in ["None", "null"]) else v
        v = "NULL" if value is None else v
        return v

    @classmethod
    def escapeLiteral(cls, value, _type, dbtype: str = None):
        v = value if value != "None" or value is not None else ""
        v = f"{value!r}" if Entity.is_string(_type) else v
        v = value if Entity.is_number(_type) else f"{value!r}"
        v = f"array{value!s}" if dbtype == "array" else v
        v = f"{value!r}" if dbtype == "hstore" else v
        v = value if (value in ["None", "null", "NULL"]) else v
        return v

    @classmethod
    def escapeString(cls, value):
        v = value if value != "None" else ""
        v = str(v).replace("'", "''")
        v = "'{}'".format(v) if Entity.is_string(type(value)) else v
        return v

    @classmethod
    def quoteString(cls, value, bool_t no_dblquoting=True):
        v = value if value != 'None' else ""
        if value == 'null' or value == 'NULL':
            return v
        if isinstance(v, bool):
            return str(v)
        if isinstance(v, str):
            # Handle double quotes
            if v.startswith('"') and no_dblquoting:
                v = v.replace('"', "'")

            # Check if the string starts or ends with a single quote
            start_quote = v.startswith("'")
            end_quote = v.endswith("'")

            # Slice the string if it starts/ends with a quote
            v = v[1:-1] if start_quote and end_quote else (v[1:] if start_quote else (v[:-1] if end_quote else v))

            # Escape single quotes
            v = v.replace("'", "''")

            # # Add back the starting and/or ending quote if they were present
            # if start_quote:
            #     v = "'" + v
            # if end_quote:
            #     v += "'"

        v = "'{}'".format(v) if type(v) == str else v
        return v

    @classmethod
    def dblQuoting(cls, value):
        return f'"{value}"'

### Validation of conditions:
cpdef object is_valid(object key, object value, str T = None, bint noquote = False):
    """is_valid.

    Check if a type is a valid Condition for a Query.

    Args:
        key (str): name of attribute Query.
        value (Any): Value to be validated.

    Returns:
        bool: if current field is valid.
    """

    if T:
        try:
            if T == 'literal':
                return escape_string(value) # exactly that we need
            else:
                validator, conv = type_validators[T]
                if validator(value):
                    return conv(value)
        except KeyError:
            pass
    if value == 'null' or value == 'NULL' or value == None or value == 'None':
        return 'null'
    elif is_boolean(value):
        return value
    elif is_integer(value):
        return value
    elif is_udf(str(value).upper()):
        return quoteString(to_udf(value))
    elif is_pgconstant(str(value).upper()):
        return value.upper()
    elif is_pg_function(value):
        return value # return exactly the value
    elif isinstance(value, list) or isinstance(value, dict):
        return value
    else:
        try:
            val = get_config_var(value)
            if val:
                if isinstance(val, str) or T == 'date':
                    return quoteString(val)
                else:
                    return escape_string(val)
        except Exception:
            pass
        try:
            val = to_udf(value)
            if noquote:
                return val
            return quoteString(val)
        except KeyError:
            pass
        except Exception as ex:
            print(f'Valid Error on {key}:{value}, error: {ex}')
        if noquote:
            return value
        return quoteString(value)
