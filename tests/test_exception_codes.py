"""HTTP status defaults for QuerySource exceptions.

Handlers answer with ``exception.code`` as the HTTP status, so no exception
may ever carry the old default of 0.
"""
import pytest

from querysource import exceptions as exc
from querysource.tenant_errors import TenantError


@pytest.mark.parametrize("cls,expected", [
    (exc.QueryException, 500),
    (exc.QueryError, 500),
    (exc.DriverError, 500),
    (exc.DriverException, 500),
    (exc.CacheException, 500),
    (exc.OutputError, 500),
    (exc.ParserError, 400),
    (exc.EmptySentence, 400),
    (exc.DataNotFound, 404),
])
def test_default_code_is_a_valid_http_status(cls: type, expected: int) -> None:
    assert cls("boom").code == expected
    assert cls("boom", code=0).code == expected


@pytest.mark.parametrize("cls", [
    exc.QueryException, exc.QueryError, exc.DriverError, exc.ParserError, exc.DataNotFound,
])
def test_explicit_code_wins(cls: type) -> None:
    assert cls("boom", code=409).code == 409


def test_fixed_code_exceptions_are_unchanged() -> None:
    assert exc.SlugNotFound().code == 404
    assert exc.QueryNotFound().code == 404
    assert exc.QueryAccessDenied().code == 404
    assert exc.ConfigError().code == 500
    assert exc.RawQueryPlaceholderError("x", placeholders=["a"]).code == 422
    assert TenantError("x", error_code="query_not_found").code == 404
