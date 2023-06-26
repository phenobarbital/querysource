# cython: language_level=3, embedsignature=True, boundscheck=False, wraparound=True, initializedcheck=False
# Copyright (C) 2018-present Jesus Lara
#
"""QuerySource Exceptions."""
cdef class QueryException(Exception):
    """Base class for other exceptions"""

## Other Errors:
cdef class ConfigError(QueryException):
    pass

cdef class QueryError(QueryException):
    pass

cdef class SlugNotFound(QueryException):
    pass

cdef class EmptySentence(QueryException):
    pass

cdef class DataNotFound(QueryException):
     pass

cdef class QueryNotFound(QueryException):
    pass

cdef class DriverError(QueryException):
    pass

cdef class DriverException(DriverError):
    pass

cdef class CacheException(QueryException):
    pass

cdef class ParserError(QueryException):
    pass

cdef class OutputError(QueryException):
    pass
