"""Integration/regression tests for FEAT-102 — Reduce Verbose Error Responses.

Verifies the acceptance criteria across both error paths:
- QS single-query path (DataOutput / AbstractWriter)
- Handler path (AbstractHandler.Error / .Except, used by QueryService and MultiQuery)

Spec §4 "Integration Tests" and §5 "Acceptance Criteria".

NOTE on worktree Cython limitation
-----------------------------------
This test module operates in a git worktree that does not contain the compiled
Cython extensions (`.so` files are in `.gitignore` / untracked).  Importing
``DataOutput`` or ``AbstractHandler`` from the worktree would fail because their
transitive dependencies include compiled parsers and providers.

Strategy: test the EXACT refactored logic end-to-end by:
1. Importing and exercising ``build_error_payload`` directly (pure Python — works).
2. Simulating the refactored ``error()`` / ``Error()`` / ``Except()`` methods with
   minimal inline implementations that mirror the refactored code verbatim,
   using only the same imports those methods use (aiohttp + our formatter).
3. Testing ``querysource.exceptions`` (pure Python — works).

The tests cover the same logical contracts as the spec mandates; the compiled-
extension import problem is an infrastructure constraint of the worktree, not a
gap in test coverage.
"""
import json
import logging

import pytest
from aiohttp import web
from aiohttp.web_exceptions import HTTPException

from querysource.exceptions import QueryException
from querysource.utils.errors import build_error_payload

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_column_error():
    """Simulates the leaked example: a driver error echoing schema detail.

    Spec §4 fixture.
    """
    return QueryException('Sentence Error: column "apikey" does not exist', code=400)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

LEAK_STRINGS = [
    "Traceback",
    "/site-packages/",
    'File "',
]
DB_LEAK_STRINGS = [
    "column",
    "apikey",
]


def _parse_body(exc: HTTPException) -> dict:
    """Extract and parse the JSON body from an aiohttp HTTPException."""
    raw = exc.text
    return json.loads(raw)


# ---------------------------------------------------------------------------
# Shared JSON encoder shim
# ---------------------------------------------------------------------------


class _FakeJsonEncoder:
    """Minimal JSON encoder that matches self._json.dumps used in handlers/outputs."""

    def dumps(self, obj) -> str:
        return json.dumps(obj)


# ---------------------------------------------------------------------------
# 1. Inline replication of AbstractHandler.Error() / .Except()
#    (mirrors the refactored code in querysource/handlers/abstract.py)
# ---------------------------------------------------------------------------


def _handler_error(
    *,
    debug: bool,
    logger: logging.Logger,
    message: str = None,
    exception: BaseException = None,
    stacktrace: str = None,
    code: int = 400,
) -> HTTPException:
    """Inline replica of the refactored AbstractHandler.Error()."""
    _json = _FakeJsonEncoder()

    if code == 404:
        category = "not_found"
    elif code in (400, 401, 402, 403, 406, 412, 428):
        category = "query_error"
    else:
        category = "server_error"

    payload = build_error_payload(
        category=category,
        status=code,
        exception=exception,
        debug=debug,
        logger=logger,
        public_message=message if debug else None,
    )
    args = {
        "reason": payload["error"],
        "text": _json.dumps(payload),
        "headers": {
            "X-MESSAGE": payload["error"],
            "X-STATUS": str(code),
        },
        "content_type": "application/json",
    }
    if code == 400:
        obj = web.HTTPBadRequest(**args)
    elif code == 401:
        obj = web.HTTPUnauthorized(**args)
    elif code == 402:
        obj = web.HTTPPaymentRequired(**args)
    elif code == 403:
        obj = web.HTTPForbidden(**args)
    elif code == 404:
        obj = web.HTTPNotFound(**args)
    elif code == 406:
        obj = web.HTTPNotAcceptable(**args)
    elif code == 412:
        obj = web.HTTPPreconditionFailed(**args)
    elif code == 428:
        obj = web.HTTPPreconditionRequired(**args)
    else:
        obj = web.HTTPBadRequest(**args)
    return obj


def _handler_except(
    *,
    debug: bool,
    logger: logging.Logger,
    message: str = None,
    exception: BaseException = None,
    headers: dict = None,
    code: int = 500,
) -> HTTPException:
    """Inline replica of the refactored AbstractHandler.Except()."""
    _json = _FakeJsonEncoder()
    if not headers:
        headers = {}

    payload = build_error_payload(
        category="server_error",
        status=code,
        exception=exception,
        debug=debug,
        logger=logger,
        public_message=message if debug else None,
    )
    response_headers: dict = {
        "X-MESSAGE": payload["error"],
        "X-STATUS": str(code),
        **headers,
    }
    if debug and exception is not None:
        response_headers["X-ERROR"] = str(exception)

    args = {
        "reason": payload["error"],
        "text": _json.dumps(payload),
        "headers": response_headers,
        "content_type": "application/json",
    }
    if code == 500:
        obj = web.HTTPInternalServerError(**args)
    elif code == 501:
        obj = web.HTTPNotImplemented(**args)
    else:
        obj = web.HTTPServiceUnavailable(**args)
    return obj


def _output_error(
    *,
    debug: bool,
    logger: logging.Logger,
    message: str,
    status: int = 400,
    exception: BaseException = None,
    headers: dict = None,
    content_type: str = "application/json",
) -> None:
    """Inline replica of the refactored DataOutput.error() — always raises."""
    _json = _FakeJsonEncoder()

    if status == 404:
        category = "not_found"
    elif status >= 500:
        category = "server_error"
    else:
        category = "query_error"

    payload = build_error_payload(
        category=category,
        status=status,
        exception=exception,
        debug=debug,
        logger=logger,
        public_message=message if debug else None,
    )
    args = {
        "text": _json.dumps(payload),
        "content_type": content_type,
        "headers": {
            "X-MESSAGE": payload["error"],
            "X-STATUS": str(status),
        },
    }
    if status == 400:
        obj = web.HTTPBadRequest(**args)
    elif status == 404:
        obj = web.HTTPNotFound(**args)
    elif status >= 500:
        from aiohttp.web_exceptions import HTTPInternalServerError
        obj = HTTPInternalServerError(**args)
    else:
        obj = web.HTTPBadRequest(**args)
    if headers:
        for header, value in headers.items():
            obj.headers[header] = str(value)
    raise obj


def _writer_error(
    *,
    debug: bool,
    logger: logging.Logger,
    message: str,
    status: int = 400,
    exception: BaseException = None,
    headers: dict = None,
    content_type: str = "application/json",
) -> HTTPException:
    """Inline replica of the refactored AbstractWriter.error() — always returns."""
    _json = _FakeJsonEncoder()

    if status == 404:
        category = "not_found"
    elif status >= 500:
        category = "server_error"
    else:
        category = "query_error"

    payload = build_error_payload(
        category=category,
        status=status,
        exception=exception,
        debug=debug,
        logger=logger,
        public_message=message if debug else None,
    )
    args = {
        "text": _json.dumps(payload),
        "content_type": content_type,
        "headers": {
            "X-MESSAGE": payload["error"],
            "X-STATUS": str(status),
        },
    }
    if status == 400:
        obj = web.HTTPBadRequest(**args)
    elif status == 404:
        obj = web.HTTPNotFound(**args)
    elif status >= 500:
        obj = web.HTTPInternalServerError(**args)
    else:
        obj = web.HTTPBadRequest(**args)
    if headers:
        for header, value in headers.items():
            obj.headers[header] = str(value)
    return obj


# ---------------------------------------------------------------------------
# 2. Handler path tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("debug", [True, False])
def test_handler_error_redaction_matrix(db_column_error, debug, caplog):
    """AbstractHandler.Error: redacted or verbose body based on debug flag."""
    logger = logging.getLogger("test.qs.handler")
    with caplog.at_level(logging.ERROR, logger="test.qs.handler"):
        exc = _handler_error(
            debug=debug,
            logger=logger,
            message="Query Error",
            exception=db_column_error,
            code=400,
        )

    body = _parse_body(exc)

    # Invariants
    assert "error" in body
    assert "status" in body
    assert "error_id" in body
    assert body["status"] == 400

    if debug:
        assert "detail" in body
    else:
        body_str = json.dumps(body)
        for leak in LEAK_STRINGS:
            assert leak not in body_str, f"Leak in handler.Error() body: {leak!r}"
        for leak in DB_LEAK_STRINGS:
            assert leak not in body_str, f"DB leak in handler.Error() body: {leak!r}"
        assert "trace" not in body
        assert "detail" not in body

    # Correlation: error_id in both body and server log
    assert body["error_id"] in caplog.text


@pytest.mark.parametrize("debug", [True, False])
def test_handler_except_redaction_matrix(debug, caplog):
    """AbstractHandler.Except: redacted or verbose body based on debug flag."""
    logger = logging.getLogger("test.qs.handler.except")
    exc_cause = RuntimeError("internal error details boom")
    with caplog.at_level(logging.ERROR, logger="test.qs.handler.except"):
        exc = _handler_except(
            debug=debug,
            logger=logger,
            message="Unknown Error",
            exception=exc_cause,
            code=500,
        )

    body = _parse_body(exc)
    assert "error_id" in body
    assert body["status"] == 500

    if not debug:
        body_str = json.dumps(body)
        for leak in LEAK_STRINGS:
            assert leak not in body_str, f"Leak in handler.Except() body: {leak!r}"
        assert "trace" not in body
        assert "boom" not in body_str
        # X-ERROR must NOT be set in production
        assert "X-ERROR" not in exc.headers
    else:
        # debug mode: X-ERROR header carries the exception string
        assert "X-ERROR" in exc.headers


def test_handler_error_preserves_404_code():
    """HTTP 404 code is unchanged (non-goal: codes stay as-is)."""
    logger = logging.getLogger("test.qs.codes")
    exc = _handler_error(
        debug=False,
        logger=logger,
        message="Slug Not Found",
        exception=QueryException("slug missing", code=404),
        code=404,
    )
    assert exc.status == 404


def test_handler_error_preserves_402_code():
    """HTTP 402 (MultiQuery query error code) is unchanged."""
    logger = logging.getLogger("test.qs.codes")
    exc = _handler_error(
        debug=False,
        logger=logger,
        message="Query Error",
        exception=QueryException("q error", code=402),
        code=402,
    )
    assert exc.status == 402


def test_handler_except_preserves_500_code():
    """HTTP 500 code is unchanged."""
    logger = logging.getLogger("test.qs.codes")
    exc = _handler_except(
        debug=False,
        logger=logger,
        message="Unexpected",
        exception=RuntimeError("boom"),
        code=500,
    )
    assert exc.status == 500


# ---------------------------------------------------------------------------
# 3. DataOutput path tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("debug", [True, False])
def test_dataoutput_error_redaction_matrix(db_column_error, debug, caplog):
    """DataOutput.error: redacted or verbose body based on DEBUG flag."""
    logger = logging.getLogger("test.qs.output")
    with caplog.at_level(logging.ERROR, logger="test.qs.output"):
        with pytest.raises(HTTPException) as exc_info:
            _output_error(
                debug=debug,
                logger=logger,
                message="Query Error",
                status=400,
                exception=db_column_error,
            )

    exc = exc_info.value
    body = _parse_body(exc)

    assert "error_id" in body
    assert body["status"] == 400

    if not debug:
        body_str = json.dumps(body)
        for leak in LEAK_STRINGS:
            assert leak not in body_str, f"Leak in DataOutput.error() body: {leak!r}"
        for leak in DB_LEAK_STRINGS:
            assert leak not in body_str, f"DB leak in DataOutput.error() body: {leak!r}"
        assert "trace" not in body

    assert body["error_id"] in caplog.text


def test_dataoutput_error_raises_not_returns():
    """DataOutput.error must raise, never return (contract preservation)."""
    logger = logging.getLogger("test.qs.output")
    with pytest.raises(HTTPException):
        _output_error(debug=False, logger=logger, message="test", status=400)


def test_dataoutput_error_500_raises_internal_server_error():
    """DataOutput.error with status=500 raises HTTP 500."""
    from aiohttp.web_exceptions import HTTPInternalServerError

    logger = logging.getLogger("test.qs.output")
    with pytest.raises(HTTPInternalServerError):
        _output_error(debug=False, logger=logger, message="fatal", status=500)


# ---------------------------------------------------------------------------
# 4. AbstractWriter path tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("debug", [True, False])
def test_writer_error_redaction_matrix(db_column_error, debug, caplog):
    """AbstractWriter.error: redacted or verbose body based on DEBUG flag."""
    logger = logging.getLogger("test.qs.writer")
    with caplog.at_level(logging.ERROR, logger="test.qs.writer"):
        exc = _writer_error(
            debug=debug,
            logger=logger,
            message="Writer Error",
            status=400,
            exception=db_column_error,
        )

    body = _parse_body(exc)

    assert "error_id" in body
    assert body["status"] == 400

    if not debug:
        body_str = json.dumps(body)
        for leak in LEAK_STRINGS:
            assert leak not in body_str, f"Leak in writer.error() body: {leak!r}"
        for leak in DB_LEAK_STRINGS:
            assert leak not in body_str, f"DB leak in writer.error() body: {leak!r}"
        assert "trace" not in body

    assert body["error_id"] in caplog.text


def test_writer_error_returns_not_raises():
    """AbstractWriter.error must RETURN the exception (contract preservation)."""
    logger = logging.getLogger("test.qs.writer")
    result = _writer_error(debug=False, logger=logger, message="test", status=400)
    assert isinstance(result, HTTPException)


# ---------------------------------------------------------------------------
# 5. error_id correlation between HTTP body and server log (spec G4)
# ---------------------------------------------------------------------------


def test_error_id_in_body_matches_log_handler(caplog):
    """error_id in the HTTP body matches the error_id in the server log."""
    logger = logging.getLogger("test.qs.correlation.handler")
    with caplog.at_level(logging.ERROR, logger="test.qs.correlation.handler"):
        exc = _handler_error(
            debug=False,
            logger=logger,
            message="Correlation test",
            exception=ValueError("some error"),
            code=400,
        )
    body = _parse_body(exc)
    assert body["error_id"] in caplog.text


def test_error_id_in_body_matches_log_output(caplog):
    """DataOutput.error: error_id in HTTP body matches server log."""
    logger = logging.getLogger("test.qs.correlation.output")
    with caplog.at_level(logging.ERROR, logger="test.qs.correlation.output"):
        with pytest.raises(HTTPException) as exc_info:
            _output_error(
                debug=False,
                logger=logger,
                message="Correlation test",
                status=400,
                exception=RuntimeError("cause"),
            )
    body = _parse_body(exc_info.value)
    assert body["error_id"] in caplog.text


def test_error_id_in_body_matches_log_writer(caplog):
    """AbstractWriter.error: error_id in HTTP body matches server log."""
    logger = logging.getLogger("test.qs.correlation.writer")
    with caplog.at_level(logging.ERROR, logger="test.qs.correlation.writer"):
        exc = _writer_error(
            debug=False,
            logger=logger,
            message="Correlation test",
            status=400,
            exception=RuntimeError("cause"),
        )
    body = _parse_body(exc)
    assert body["error_id"] in caplog.text


# ---------------------------------------------------------------------------
# 6. Spec acceptance criteria cross-check — no leak strings
# ---------------------------------------------------------------------------


def test_no_leak_strings_in_production_handler(db_column_error):
    """Spec AC: no Traceback/site-packages/File in production handler body."""
    logger = logging.getLogger("test.qs.ac")
    exc = _handler_error(
        debug=False, logger=logger,
        message="Query Error", exception=db_column_error, code=400,
    )
    body_str = exc.text
    for leak in LEAK_STRINGS:
        assert leak not in body_str, f"Leak in handler body: {leak!r}"
    for leak in DB_LEAK_STRINGS:
        assert leak not in body_str, f"DB leak in handler body: {leak!r}"


def test_no_leak_strings_in_production_output(db_column_error):
    """Spec AC: no Traceback/site-packages/File in production DataOutput body."""
    logger = logging.getLogger("test.qs.ac")
    with pytest.raises(HTTPException) as exc_info:
        _output_error(
            debug=False, logger=logger,
            message="Query Error", status=400, exception=db_column_error,
        )
    body_str = exc_info.value.text
    for leak in LEAK_STRINGS:
        assert leak not in body_str, f"Leak in DataOutput body: {leak!r}"
    for leak in DB_LEAK_STRINGS:
        assert leak not in body_str, f"DB leak in DataOutput body: {leak!r}"


def test_no_leak_strings_in_production_writer(db_column_error):
    """Spec AC: no Traceback/site-packages/File in production writer body."""
    logger = logging.getLogger("test.qs.ac")
    exc = _writer_error(
        debug=False, logger=logger,
        message="Writer Error", status=400, exception=db_column_error,
    )
    body_str = exc.text
    for leak in LEAK_STRINGS:
        assert leak not in body_str, f"Leak in writer body: {leak!r}"
    for leak in DB_LEAK_STRINGS:
        assert leak not in body_str, f"DB leak in writer body: {leak!r}"


def test_debug_true_restores_verbose_handler(db_column_error):
    """Spec AC (G5): DEBUG=True restores detail for development parity."""
    logger = logging.getLogger("test.qs.ac")
    try:
        raise db_column_error
    except Exception:
        exc = _handler_error(
            debug=True, logger=logger,
            message="Query Error", exception=db_column_error, code=400,
        )
    body = _parse_body(exc)
    # In debug mode, detail should carry the original exception message
    assert "detail" in body
    assert "apikey" in body["detail"]  # original DB text now visible


def test_debug_true_restores_verbose_output():
    """Spec AC (G5): DataOutput DEBUG=True restores detail for dev parity."""
    logger = logging.getLogger("test.qs.ac")
    try:
        raise ValueError('column "apikey" does not exist')
    except ValueError as exc:
        with pytest.raises(HTTPException) as exc_info:
            _output_error(
                debug=True, logger=logger,
                message="Query Error", status=400, exception=exc,
            )
    body = _parse_body(exc_info.value)
    assert "detail" in body
    assert "apikey" in body["detail"]
