"""Unit tests for provider raw_query() validating substitution (FEAT-103 TASK-707).

Tests the two-phase substitution logic directly:
  Phase 1: safe_format_map (no-escape) for trusted `replacement` SQL fragments.
  Phase 2: safe_format_map_validated (escaping + injection rejection) for user conditions.

This avoids full querysource package import issues in isolated unit-test environments.

Verifies that:
- Trusted replacement fragments ({fields}, {filter}, etc.) pass through unchanged.
- Benign user conditions are substituted correctly.
- SQL injection payloads raise an exception (→ HTTP 400 via ParserError in prod).
"""
import pytest
import querysource.qs_parsers as q


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

REPLACEMENT = {
    "fields": "*",
    "filterdate": "current_date",
    "firstdate": "current_date",
    "lastdate": "current_date",
    "where_cond": "",
    "and_cond": "",
    "filter": "",
}


def raw_query_simulate(template: str, conditions: dict | None = None) -> str:
    """Simulate the new two-phase raw_query() body.

    Phase 1: safe_format_map (trusted replacement fragments, no escape)
    Phase 2: safe_format_map_validated (user conditions, validated + escaped)
    """
    # Phase 1: trusted fragments
    sql = q.safe_format_map(template, REPLACEMENT)
    # Phase 2: user conditions
    if conditions:
        sql = q.safe_format_map_validated(sql, conditions, {})
    return sql


# ---------------------------------------------------------------------------
# Benign substitution tests
# ---------------------------------------------------------------------------


def test_replacement_fields_preserved():
    """Trusted {fields} replacement substitutes correctly via safe_format_map."""
    template = "SELECT {fields} FROM t {where_cond}"
    result = raw_query_simulate(template)
    # {fields} → "*"; {where_cond} → "" (both from replacement)
    assert result == "SELECT * FROM t "


def test_benign_literal_renders():
    """Benign literal placeholder substitution works correctly."""
    template = "SELECT * FROM clients WHERE client_slug = '{client_slug}'"
    result = raw_query_simulate(template, {"client_slug": "acme"})
    assert result == "SELECT * FROM clients WHERE client_slug = 'acme'"


def test_benign_multiple_conditions():
    """Multiple benign conditions are all substituted."""
    template = "SELECT * FROM t WHERE slug = '{slug}' AND program = '{program}'"
    result = raw_query_simulate(template, {"slug": "my_slug", "program": "default"})
    assert result == "SELECT * FROM t WHERE slug = 'my_slug' AND program = 'default'"


def test_unmatched_user_placeholder_preserved():
    """Unmatched placeholders not in replacement or conditions are left intact."""
    template = "SELECT * FROM t {custom_fragment}"
    # Not in replacement → left intact by safe_format_map Phase 1
    result = raw_query_simulate(template)
    assert "{custom_fragment}" in result


# ---------------------------------------------------------------------------
# Injection rejection tests (PoC payloads from spec §4)
# ---------------------------------------------------------------------------


def test_identifier_breakout_rejected():
    """PoC: identifier breakout payload is rejected."""
    payload = '" IS NULL UNION SELECT version(),null,null,null,null,null--'
    template = 'SELECT * FROM t WHERE "{client_slug}" = 1'
    with pytest.raises(Exception):
        raw_query_simulate(template, {"client_slug": payload})


def test_union_select_literal_rejected():
    """PoC: UNION SELECT payload in literal context is rejected."""
    payload = "' UNION SELECT string_agg(datname,',') FROM pg_database--"
    template = "SELECT * FROM t WHERE slug = '{slug}'"
    with pytest.raises(Exception):
        raw_query_simulate(template, {"slug": payload})


def test_pg_shadow_payload_rejected():
    """PoC: pg_shadow enumeration payload is rejected."""
    payload = "' UNION SELECT usename||':'||passwd FROM pg_shadow--"
    template = "SELECT * FROM t WHERE slug = '{slug}'"
    with pytest.raises(Exception):
        raw_query_simulate(template, {"slug": payload})


def test_comment_marker_rejected():
    """Comment marker -- in literal value is rejected."""
    with pytest.raises(Exception):
        raw_query_simulate("WHERE x = '{x}'", {"x": "1'--"})


def test_statement_separator_rejected():
    """Statement separator ; in value is rejected."""
    with pytest.raises(Exception):
        raw_query_simulate("WHERE x = '{x}'", {"x": "1; DROP TABLE users"})


def test_block_comment_rejected():
    """Block comment markers are rejected."""
    with pytest.raises(Exception):
        raw_query_simulate("WHERE x = '{x}'", {"x": "1/*"})


# ---------------------------------------------------------------------------
# Backward-compat — safe_format_map unchanged
# ---------------------------------------------------------------------------


def test_safe_format_map_no_escape_unchanged():
    """safe_format_map must still do no-escape substitution (regression guard)."""
    result = q.safe_format_map(
        "SELECT {fields} FROM t {filter}",
        {"fields": "a, b", "filter": "WHERE x = 1"},
    )
    assert result == "SELECT a, b FROM t WHERE x = 1"


def test_safe_format_map_sql_fragments_not_escaped():
    """Trusted SQL fragments passed through safe_format_map are NOT quoted."""
    # `*` and `current_date` must appear unquoted
    result = q.safe_format_map(
        "SELECT {fields} FROM t WHERE d >= {filterdate}",
        {"fields": "*", "filterdate": "current_date"},
    )
    assert result == "SELECT * FROM t WHERE d >= current_date"
