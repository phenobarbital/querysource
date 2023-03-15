# cython: language_level=3, embedsignature=True, boundscheck=False, wraparound=True, initializedcheck=False
# Copyright (C) 2018-present Jesus Lara
#
"""QuerySource Exceptions."""
cdef class QueryException(Exception):
    """Base class for other exceptions"""

    code: int = 0

    def __init__(self, str message, int code = 0, **kwargs):
        super().__init__(message)
        self.stacktrace = None
        if 'stacktrace' in kwargs:
            self.stacktrace = kwargs['stacktrace']
        self.message = message
        self.args = kwargs
        self.code = int(code)

    def __repr__(self):
        return f"{self.message}, code: {self.code}"

    def __str__(self):
        return f"{self.message!s}"

    def get(self):
        return self.message

## Other Errors:
cdef class ConfigError(QueryException):

    def __init__(self, str message = None):
        super().__init__(message or f"QS Configuration Error.", code=500)

cdef class SlugNotFound(QueryException):
    def __init__(self, str message = None):
        super().__init__(message, code=404)

cdef class EmptySentence(QueryException):
    pass

cdef class QueryError(QueryException):
    pass

cdef class DataNotFound(QueryException):
     pass

cdef class QueryNotFound(QueryException):
    def __init__(self, str message = None):
        super().__init__(message, code=404)

cdef class DriverError(QueryException):
    pass

cdef class DriverException(DriverError):
    pass

cdef class CacheException(QueryException):
    pass

cdef class ParserError(QueryException):
    pass
