"""Unit tests for safe_format_map_validated (FEAT-103 TASK-706).

Verifies:
- Symbol is present and callable after rebuild.
- Benign values substitute correctly.
- SQL injection payloads are rejected.
- HAS_RUST is still True after rebuild.
- Existing exports are unaffected.
"""
import pytest
import querysource.qs_parsers as q


def test_has_rust_true():
    """Extension must be loaded (HAS_RUST flag True)."""
    assert q.HAS_RUST is True


def test_symbol_present():
    """safe_format_map_validated must be exported from the package."""
    assert hasattr(q, "safe_format_map_validated"), (
        "safe_format_map_validated not found in querysource.qs_parsers"
    )


def test_symbol_callable():
    """safe_format_map_validated must be callable."""
    assert callable(q.safe_format_map_validated)


def test_benign_literal_value_renders():
    """A benign value in literal context must substitute correctly."""
    out = q.safe_format_map_validated(
        "WHERE client_slug = '{client_slug}'",
        {"client_slug": "acme"},
        {},
    )
    assert out == "WHERE client_slug = 'acme'"


def test_benign_bare_context_renders():
    """A benign value in bare context must be quoted."""
    out = q.safe_format_map_validated(
        "WHERE x = {val}",
        {"val": "hello"},
        {},
    )
    assert out == "WHERE x = 'hello'"


def test_unmatched_placeholder_preserved():
    """Unmatched placeholders must be left intact (like safe_format_map)."""
    out = q.safe_format_map_validated(
        "SELECT {fields} FROM t",
        {},
        {},
    )
    assert out == "SELECT {fields} FROM t"


def test_injection_identifier_breakout_rejected():
    """PoC identifier breakout payload must be rejected."""
    with pytest.raises(Exception):
        q.safe_format_map_validated(
            'WHERE "{k}" = 1',
            {"k": 'client_slug" IS NULL UNION SELECT version()--'},
            {},
        )


def test_injection_union_select_rejected():
    """UNION SELECT payload in literal context must be rejected."""
    with pytest.raises(Exception):
        q.safe_format_map_validated(
            "WHERE slug = '{slug}'",
            {"slug": "' UNION SELECT version(),null,null--"},
            {},
        )


def test_injection_pg_database_rejected():
    """pg_database enumeration payload must be rejected."""
    with pytest.raises(Exception):
        q.safe_format_map_validated(
            "WHERE slug = '{slug}'",
            {"slug": "' UNION SELECT string_agg(datname,',') FROM pg_database--"},
            {},
        )


def test_injection_comment_marker_rejected():
    """SQL comment marker `--` in value must be rejected."""
    with pytest.raises(Exception):
        q.safe_format_map_validated(
            "WHERE x = '{x}'",
            {"x": "1'--"},
            {},
        )


def test_injection_statement_separator_rejected():
    """SQL statement separator `;` in value must be rejected."""
    with pytest.raises(Exception):
        q.safe_format_map_validated(
            "WHERE x = '{x}'",
            {"x": "1; DROP TABLE users"},
            {},
        )


def test_existing_safe_format_map_unchanged():
    """The existing safe_format_map (no-escape) must behave identically."""
    result = q.safe_format_map(
        "SELECT {fields} FROM t {filter}",
        {"fields": "a, b", "filter": "WHERE x = 1"},
    )
    assert result == "SELECT a, b FROM t WHERE x = 1"


def test_existing_exports_present():
    """Pre-existing exports must still be available after rebuild."""
    for symbol in [
        "safe_format_map",
        "filter_conditions",
        "pgsql_filter_conditions",
        "escape_string",
        "quote_string",
        "is_valid",
    ]:
        assert hasattr(q, symbol), f"{symbol} missing after rebuild"
