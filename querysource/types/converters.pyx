# cython: language_level=3, embedsignature=True, boundscheck=False, wraparound=True, initializedcheck=False
# Copyright (C) 2018-present Jesus Lara
#
import re
from uuid import UUID
from dateutil import parser
from cpython cimport datetime
from dataclasses import _MISSING_TYPE
from numpy import int64, ndarray
from decimal import Decimal
from .validators import is_udf
from .typedefs import strtobool


# cpdef object to_uuid(object obj):
#     """Returns a UUID version of a str column.
#     """
#     if isinstance(obj, UUID):
#         # already an uuid
#         return obj
#     try:
#         return UUID(str(obj))
#     except ValueError:
#         raise ValueError(
#             f"Error: conversion of *{obj}* to UUID"
#         )


cpdef object to_integer(object obj):
    """to_integer.
    Returns object converted to integer.
    """
    if isinstance(obj, (int, int64)):
        return obj
    else:
        try:
            return int(obj)
        except (TypeError, ValueError) as e:
            return ValueError(
                f"Error: Conversion of {obj} to Integer: {e}"
            )


cpdef object to_boolean(object obj):
    """to_boolean.
    Convert and returns any object value to boolean version.
    """
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, (bytes, bytearray)):
        obj = obj.decode("ascii")
    if isinstance(obj, str):
        return strtobool(obj)
    else:
        return bool(obj)


cpdef datetime.date to_date(object obj):
    """to_date.
    Returns obj converted to date.
    """
    if isinstance(obj, (datetime.date, datetime.datetime)):
        return obj
    else:
        if isinstance(obj, (bytes, bytearray)):
            obj = obj.decode("ascii")
        try:
            return parser.parse(obj).date()
        except (ValueError, TypeError):
            raise ValueError(
                f"Error: Conversion of *{obj}* to date"
            )


cpdef datetime.datetime to_datetime(object obj):
    """to_datetime.
    Returns obj converted to datetime.
    """
    if isinstance(obj, (datetime.date, datetime.datetime)):
        return obj
    elif isinstance(obj, _MISSING_TYPE):
        return None
    else:
        if isinstance(obj, (bytes, bytearray)):
            obj = obj.decode("ascii")
        try:
            return parser.parse(obj)
        except (ValueError, TypeError):
            raise ValueError(
                f"Can't convert invalid data *{obj}* to datetime"
            )


cpdef object to_double(object obj):
    if isinstance(obj, int):
        return float(obj)
    elif "," in obj:
        val = obj.replace(",", ".")
    else:
        val = obj
    try:
        return Decimal(val)
    except Exception as e:
        try:
            return float(val)
        except (ValueError, TypeError) as err:
            return None
