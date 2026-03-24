# Copyright (C) 2018-present Jesus Lara
#
"""QuerySource Exceptions."""


class QueryException(Exception):
    """Base class for other exceptions."""

    code: int = 0

    def __init__(self, message: str, code: int = 0, **kwargs):
        super().__init__(message)
        self.stacktrace = kwargs.get('stacktrace', None)
        self.message = message
        self.args = kwargs
        self.code = int(code)

    def __repr__(self):
        return f"{self.message}, code: {self.code}"

    def __str__(self):
        return f"{self.message!s}"

    def get(self):
        return self.message


class ConfigError(QueryException):

    def __init__(self, message: str = None):
        super().__init__(message or "QS Configuration Error.", code=500)


class SlugNotFound(QueryException):

    def __init__(self, message: str = None):
        super().__init__(message, code=404)


class EmptySentence(QueryException):
    pass


class QueryError(QueryException):
    pass


class DataNotFound(QueryException):
    pass


class QueryNotFound(QueryException):

    def __init__(self, message: str = None):
        super().__init__(message, code=404)


class DriverError(QueryException):
    pass


class DriverException(DriverError):
    pass


class CacheException(QueryException):
    pass


class ParserError(QueryException):
    pass


class OutputError(QueryException):
    pass
