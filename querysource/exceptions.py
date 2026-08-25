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
    """Raised when a MultiQuery Output/destination fails.

    Optionally carries the failing destination ``step_name`` and an error
    ``category`` (``"data"`` | ``"infra"``) so the HTTP handler layer can
    pick an appropriate status code (422 vs 500). Both are backwards
    compatible: existing ``OutputError(message)`` call sites keep working
    unchanged.
    """

    def __init__(
        self,
        message: str = "",
        code: int = 0,
        *,
        step_name: str = None,
        category: str = None,
        **kwargs,
    ):
        super().__init__(message, code=code, **kwargs)
        self.step_name = step_name
        self.category = category
