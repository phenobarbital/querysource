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


class QueryAccessDenied(QueryException):
    """Principal may not run this query, or the query/tenant is not available to it.

    Message is generic and never names the matched policy; code 404 so HTTP
    layers that surface it keep the same not-found semantics as handlers.
    """

    def __init__(self, message: str = None):
        super().__init__(message or "Query not available.", code=404)


class DriverError(QueryException):
    pass


class DriverException(DriverError):
    pass


class CacheException(QueryException):
    pass


class ParserError(QueryException):
    pass


class RawQueryPlaceholderError(ParserError):
    """A raw query still carries ``{placeholder}`` replacements it can never fill.

    Raw queries (``is_raw=True`` definitions, ``QS(raw_query=...)``) bypass the
    parser, so any placeholder left in them would reach the database verbatim.
    This is an operational error in the query definition (or a missing
    condition), reported with code 422 and the offending ``placeholders``.
    """

    def __init__(self, message: str, placeholders: list[str] | None = None):
        super().__init__(message, code=422)
        self.placeholders: list[str] = list(placeholders or [])


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
