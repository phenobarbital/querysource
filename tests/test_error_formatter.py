"""Unit tests for querysource.utils.errors — the shared error-payload formatter.

Covers spec §4 "Unit Tests" and TASK-700 acceptance criteria.
"""
import logging

import pytest

from querysource.utils.errors import GENERIC_MESSAGES, build_error_payload


# ---------------------------------------------------------------------------
# Acceptance-criteria tests
# ---------------------------------------------------------------------------


def test_production_minimal():
    """debug=False returns only error, status, error_id — no trace or detail."""
    payload = build_error_payload(category="query_error", status=400)
    assert set(payload) == {"error", "status", "error_id"}
    assert payload["status"] == 400
    assert payload["error_id"]
    assert "Traceback" not in payload["error"]


def test_debug_verbose():
    """debug=True includes detail and trace; detail contains the original message."""
    try:
        raise ValueError('column "apikey" does not exist')
    except ValueError as exc:
        payload = build_error_payload(
            category="query_error", status=400, exception=exc, debug=True
        )
    assert "detail" in payload and "trace" in payload
    assert "apikey" in payload["detail"]


def test_production_hides_db_text():
    """Raw DB text must NOT appear in the production payload."""
    try:
        raise ValueError('column "apikey" does not exist')
    except ValueError as exc:
        payload = build_error_payload(
            category="query_error", status=400, exception=exc, debug=False
        )
    assert "apikey" not in str(payload)
    assert "trace" not in payload
    assert "detail" not in payload


def test_logs_full_detail_with_error_id(caplog):
    """Full message + traceback are logged, tagged with the same error_id."""
    logger = logging.getLogger("test.qs.errors")
    with caplog.at_level(logging.ERROR, logger="test.qs.errors"):
        payload = build_error_payload(
            category="server_error",
            status=500,
            exception=RuntimeError("boom"),
            logger=logger,
        )
    assert payload["error_id"] in caplog.text
    assert "boom" in caplog.text


def test_production_minimal_keys_only(caplog):
    """Spec: build_error_payload(category='query_error', status=400) returns exactly 3 keys."""
    payload = build_error_payload(category="query_error", status=400)
    assert "error" in payload
    assert "status" in payload
    assert "error_id" in payload
    # Critically: trace and detail MUST NOT be present in production mode
    assert "trace" not in payload
    assert "detail" not in payload


# ---------------------------------------------------------------------------
# GENERIC_MESSAGES map coverage
# ---------------------------------------------------------------------------


def test_generic_messages_map_contains_required_categories():
    """All spec-defined categories must be present in GENERIC_MESSAGES."""
    required = {"bad_request", "query_error", "not_found", "server_error", "output_error"}
    assert required.issubset(set(GENERIC_MESSAGES))


def test_build_payload_uses_category_message():
    """Each category maps to its expected generic public message."""
    expected = {
        "bad_request": "Invalid query request",
        "query_error": "Query execution failed",
        "not_found": "Resource not found",
        "server_error": "Internal query error",
        "output_error": "Output generation failed",
    }
    for cat, msg in expected.items():
        payload = build_error_payload(category=cat, status=400)
        assert payload["error"] == msg, f"Category {cat!r} expected {msg!r}, got {payload['error']!r}"


def test_unknown_category_falls_back_to_server_error():
    """Unknown category falls back to the 'server_error' public message."""
    payload = build_error_payload(category="completely_unknown_category", status=400)
    assert payload["error"] == GENERIC_MESSAGES["server_error"]


# ---------------------------------------------------------------------------
# error_id stability
# ---------------------------------------------------------------------------


def test_error_id_present_and_non_empty():
    """error_id must be present and non-empty in every payload."""
    payload = build_error_payload(category="query_error", status=400)
    assert isinstance(payload["error_id"], str)
    assert len(payload["error_id"]) > 0


def test_error_id_unique_across_calls():
    """Each call generates a distinct error_id."""
    p1 = build_error_payload(category="query_error", status=400)
    p2 = build_error_payload(category="query_error", status=400)
    assert p1["error_id"] != p2["error_id"]


# ---------------------------------------------------------------------------
# public_message override
# ---------------------------------------------------------------------------


def test_public_message_override():
    """Explicit public_message overrides the GENERIC_MESSAGES entry."""
    payload = build_error_payload(
        category="query_error", status=400, public_message="Custom safe message"
    )
    assert payload["error"] == "Custom safe message"


# ---------------------------------------------------------------------------
# No traceback or path leakage in production
# ---------------------------------------------------------------------------


def test_no_traceback_string_in_production_payload():
    """The word 'Traceback' must never appear in a production payload."""
    try:
        raise RuntimeError("internal details")
    except RuntimeError as exc:
        payload = build_error_payload(
            category="server_error", status=500, exception=exc, debug=False
        )
    payload_str = str(payload)
    assert "Traceback" not in payload_str
    assert "site-packages" not in payload_str
    assert 'File "' not in payload_str


def test_trace_present_in_debug_mode():
    """debug=True payload includes a non-empty trace when an exception is active."""
    try:
        raise RuntimeError("debug mode test")
    except RuntimeError as exc:
        payload = build_error_payload(
            category="server_error", status=500, exception=exc, debug=True
        )
    assert "trace" in payload
    # Trace should contain the exception text (captured via traceback.format_exc)
    assert "debug mode test" in payload["trace"] or payload["trace"] != ""


# ---------------------------------------------------------------------------
# spec fixture: db_column_error
# ---------------------------------------------------------------------------


@pytest.fixture
def db_column_error():
    """Simulates the leaked example: a driver error echoing schema detail."""
    from querysource.exceptions import QueryException

    return QueryException('Sentence Error: column "apikey" does not exist', code=400)


def test_build_payload_sanitizes_db_message(db_column_error):
    """Raw DB text from a QueryException does not appear in the production payload."""
    try:
        raise db_column_error
    except Exception as exc:
        payload = build_error_payload(
            category="query_error", status=400, exception=exc, debug=False
        )
    assert "apikey" not in str(payload)
    assert "trace" not in payload
    assert payload["error"] == GENERIC_MESSAGES["query_error"]
