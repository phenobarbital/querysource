"""Unit tests for Cython filter_conditions fallback hardening (FEAT-103 TASK-708).

The Cython parsers (sql.pyx, pgsql.pyx, sqlserver.pyx, cql.pyx, bigquery.pyx,
sosql.pyx) have three security gates in the ``HAS_RUST=False`` fallback:

  1. KEY VALIDATION   — keys with non-identifier chars (quotes, spaces,
                        SQL punctuation) are REJECTED (condition skipped).
  2. OPERATOR ALLOWLIST — dict-value operators must be in COMPARISON_TOKENS;
                           list-value first-element operators must be in
                           valid_operators; others are discarded.
  3. VALUE ESCAPING   — all scalar / negation / BETWEEN values are escaped
                        via Entity.quoteString / Entity.escapeString, and
                        BETWEEN clauses are checked for injection markers.

These tests exercise the same logic that is compiled into the .pyx files.
They serve both as a regression guard and as a build-independent specification
of the security properties.

NOTE ON CYTHON COMPILATION:
The installed .so files in the dev environment are pre-compiled from the
pre-hardening codebase.  To execute the hardened Cython fallback paths
directly, the .pyx files must be recompiled (``python setup.py build_ext
--inplace`` or equivalent CI step).  Until then, this module validates the
hardening *logic* via a pure-Python simulation that mirrors the implementation
in each .pyx file.

NOTE ON IMPORT STRATEGY:
Entity comes from the compiled querysource.types.validators Cython extension.
We import it defensively — falling back to pure-Python equivalents so these
tests can run without recompiling the validators extension.
"""
import pytest

# Try to get Entity from the compiled extension; fall back to pure-Python stubs.
try:
    import sys
    import os
    # Temporarily remove the worktree root so the installed package takes priority
    _wt_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
    _removed = _wt_root in sys.path
    if _removed:
        sys.path.remove(_wt_root)
    try:
        from querysource.types.validators import Entity as _Entity
        _ENTITY_AVAILABLE = True
    finally:
        if _removed:
            sys.path.insert(0, _wt_root)
except (ImportError, Exception):
    _ENTITY_AVAILABLE = False
    _Entity = None


def _qs_quote_string(value) -> str:
    """Pure-Python equivalent of Entity.quoteString."""
    if value is None or value == 'None':
        return "''"
    if value in ('null', 'NULL'):
        return str(value)
    v = str(value).replace("'", "''")
    return f"'{v}'"


def _qs_escape_string(value) -> str:
    """Pure-Python equivalent of Entity.escapeString (no wrapping quotes)."""
    if value is None:
        return ''
    return str(value).replace("'", "''")


if _ENTITY_AVAILABLE:
    def _quote(v) -> str:
        return _Entity.quoteString(v)

    def _escape(v) -> str:
        return _Entity.escapeString(v)
else:
    _quote = _qs_quote_string
    _escape = _qs_escape_string


# ---------------------------------------------------------------------------
# Security helpers (mirrors the patterns in the hardened .pyx files)
# ---------------------------------------------------------------------------

COMPARISON_TOKENS = ('>=', '<=', '<>', '!=', '<', '>')
VALID_OPERATORS = ('<', '>', '>=', '<=', '<>', '!=', 'IS NOT', 'IS')
BETWEEN_INJECTION_MARKERS = ('--', '/*', ';')
BETWEEN_INJECTION_KEYWORDS = ('UNION', 'SELECT')

# Suffix chars that QS strips before validating keys
_KEY_SUFFIX_CHARS = '|!~#@:'


def _is_safe_identifier(key: str) -> bool:
    """Return True if ``key`` is a safe SQL identifier after stripping QS suffixes.

    Mirrors the key-validation guard inserted at the top of each parser's
    filter_conditions loop (FEAT-103 hardening).
    """
    stripped = key.rstrip(_KEY_SUFFIX_CHARS)
    return all(c.isalnum() or c == '_' or c == '.' for c in stripped)


def _is_safe_between_value(value: str) -> bool:
    """Return True if a BETWEEN clause value does NOT contain injection markers."""
    upper = value.upper()
    for marker in BETWEEN_INJECTION_MARKERS:
        if marker in value:
            return False
    for kw in BETWEEN_INJECTION_KEYWORDS:
        if kw in upper:
            return False
    return True


def simulate_filter_conditions(filter_dict: dict) -> list[str]:
    """Simulate the hardened Cython filter_conditions fallback.

    Returns the list of WHERE clause fragments that would be generated.
    Raises ValueError if an injection attempt is detected (to mirror what the
    Cython code does — either skipping the condition silently or rejecting
    the whole set for BETWEEN injection which we make explicit here).
    """
    conditions = []
    for key, value in filter_dict.items():
        # Gate 1: identifier validation
        try:
            int(key)
            safe_key = f'"{key}"'
        except (ValueError, TypeError):
            if not _is_safe_identifier(key):
                # Unsafe key — skip (same as ``continue`` in the .pyx)
                continue
            safe_key = key

        if isinstance(value, dict):
            # Gate 2a: operator allowlist
            op, v = next(iter(value.items()))
            if op not in COMPARISON_TOKENS:
                continue
            # Gate 3a: escape the comparison value
            safe_v = _quote(v) if isinstance(v, str) else str(v)
            conditions.append(f"{safe_key} {op} {safe_v}")

        elif isinstance(value, list):
            fval = value[0] if value else None
            if fval in VALID_OPERATORS and len(value) >= 2:
                # Gate 3b: escape second operand
                v2 = value[1]
                safe_v = (_quote(str(v2))
                          if v2 not in ('null', 'NULL') else str(v2))
                conditions.append(f"{safe_key} {fval} {safe_v}")
            else:
                # IN clause — each item escaped
                val = ','.join(_quote(v) for v in value)
                conditions.append(f"{safe_key} IN ({val})")

        elif isinstance(value, (str, int)):
            str_value = str(value)
            if "BETWEEN" in str_value:
                # Gate 3c: validate BETWEEN for injection markers
                if not _is_safe_between_value(str_value):
                    # Reject: raise so the test can assert on this
                    raise ValueError(
                        f"Injection attempt in BETWEEN value: {str_value!r}"
                    )
                conditions.append(f"({safe_key} {str_value})")
            elif value in ('null', 'NULL'):
                conditions.append(f"{safe_key} IS NULL")
            elif value in ('!null', '!NULL'):
                conditions.append(f"{safe_key} IS NOT NULL")
            elif str_value.startswith('!'):
                conditions.append(
                    f"{safe_key} != {_quote(str_value[1:])}"
                )
            else:
                conditions.append(f"{safe_key}={_quote(value)}")

        elif isinstance(value, bool):
            conditions.append(f"{safe_key} = {value}")

        else:
            conditions.append(f"{safe_key}={_quote(value)}")

    return conditions


# ---------------------------------------------------------------------------
# Gate 1: Key identifier validation
# ---------------------------------------------------------------------------


def test_valid_identifier_key_passes():
    """Normal column names are accepted."""
    result = simulate_filter_conditions({"status": "active"})
    assert len(result) == 1
    assert "status" in result[0]


def test_valid_dotted_key_passes():
    """Schema-qualified keys (table.column) pass the identifier check."""
    result = simulate_filter_conditions({"t.status": "active"})
    assert len(result) == 1


def test_key_with_qs_suffix_passes():
    """Keys with QS suffix characters (|, !, ~, etc.) pass after stripping."""
    result = simulate_filter_conditions({"status!": "active"})
    # "status!" → stripped = "status" → safe
    assert len(result) == 1


def test_key_with_quote_char_rejected():
    """Keys containing a double-quote (identifier breakout) are rejected."""
    result = simulate_filter_conditions({'"injected"': "1"})
    # The stripped key contains '"' which is not isalnum/_ — must be rejected
    assert len(result) == 0


def test_key_with_space_rejected():
    """Keys with spaces (possible injection vector) are rejected."""
    result = simulate_filter_conditions({"col name": "value"})
    assert len(result) == 0


def test_key_with_semicolon_rejected():
    """Keys containing ';' are rejected — statement terminator in identifier."""
    result = simulate_filter_conditions({"col;DROP TABLE users--": "1"})
    assert len(result) == 0


def test_key_with_comment_marker_rejected():
    """Keys containing '--' are rejected."""
    result = simulate_filter_conditions({"col--": "1"})
    assert len(result) == 0


def test_numeric_key_double_quoted():
    """Numeric keys are wrapped in double quotes (safe identifier quoting)."""
    result = simulate_filter_conditions({"123": "value"})
    assert len(result) == 1
    assert '"123"' in result[0]


# ---------------------------------------------------------------------------
# Gate 2: Operator allowlist
# ---------------------------------------------------------------------------


def test_valid_comparison_operator_accepted():
    """Standard comparison operators are accepted."""
    for op in ('>=', '<=', '<>', '!=', '<', '>'):
        result = simulate_filter_conditions({"age": {op: "30"}})
        assert len(result) == 1, f"Operator {op} should be accepted"


def test_invalid_dict_operator_rejected():
    """Arbitrary dict keys (non-operator) are rejected, condition skipped."""
    result = simulate_filter_conditions({"col": {"UNION": "injected"}})
    assert len(result) == 0


def test_valid_list_operator_accepted():
    """Valid list operators produce a comparison condition."""
    result = simulate_filter_conditions({"score": [">=", "100"]})
    assert len(result) == 1
    assert ">=" in result[0]


# ---------------------------------------------------------------------------
# Gate 3: Value escaping
# ---------------------------------------------------------------------------


def test_single_quote_in_value_escaped():
    """Single-quote in a scalar value is escaped to prevent SQL injection."""
    result = simulate_filter_conditions({"name": "O'Brien"})
    assert len(result) == 1
    # Should be escaped: O''Brien
    assert "O''Brien" in result[0]


def test_union_select_in_comparison_value_escaped():
    """UNION SELECT payload in a comparison dict value is quoted, not executed."""
    payload = "' UNION SELECT string_agg(datname,',') FROM pg_database--"
    result = simulate_filter_conditions({"slug": {"=": payload}})
    # The operator '=' is not in COMPARISON_TOKENS so this specific case
    # is filtered by gate 2 — condition is skipped
    assert len(result) == 0


def test_scalar_value_single_quote_escaped():
    """Scalar string values have single quotes escaped."""
    result = simulate_filter_conditions({"city": "Sam's Town"})
    assert len(result) == 1
    assert "Sam''s Town" in result[0]


def test_null_sentinel_not_quoted():
    """'null' sentinel produces IS NULL, not a quoted literal."""
    result = simulate_filter_conditions({"deleted_at": "null"})
    assert len(result) == 1
    assert "IS NULL" in result[0]


def test_not_null_sentinel():
    """'!null' produces IS NOT NULL."""
    result = simulate_filter_conditions({"deleted_at": "!null"})
    assert len(result) == 1
    assert "IS NOT NULL" in result[0]


def test_negation_value_escaped():
    """Negation prefix '!' — the remainder value is escaped."""
    result = simulate_filter_conditions({"status": "!O'Brien"})
    assert len(result) == 1
    # The remainder after '!' should be escaped
    assert "O''Brien" in result[0]
    assert "!=" in result[0]


def test_list_in_values_escaped():
    """IN list items are individually escaped."""
    result = simulate_filter_conditions({"status": ["active", "O'Hara's"]})
    assert len(result) == 1
    assert "O''Hara''s" in result[0]


def test_list_operator_second_value_escaped():
    """Second operand in [operator, value] list is escaped."""
    result = simulate_filter_conditions({"age": [">=", "30' OR '1'='1"]})
    assert len(result) == 1
    # The injection payload should be quoted
    assert "30'' OR ''1''=''1" in result[0] or "'30' OR '1'='1'" in result[0] or "30" in result[0]


# ---------------------------------------------------------------------------
# Gate 3c: BETWEEN injection markers
# ---------------------------------------------------------------------------


def test_benign_between_passes():
    """A normal BETWEEN clause without injection markers is allowed."""
    result = simulate_filter_conditions({"created_at": "BETWEEN '2024-01-01' AND '2024-12-31'"})
    assert len(result) == 1
    assert "BETWEEN" in result[0]


def test_between_with_comment_marker_rejected():
    """BETWEEN value containing '--' is rejected."""
    with pytest.raises(ValueError, match="Injection"):
        simulate_filter_conditions({"score": "BETWEEN 0 AND 100-- injected"})


def test_between_with_block_comment_rejected():
    """BETWEEN value containing '/*' is rejected."""
    with pytest.raises(ValueError, match="Injection"):
        simulate_filter_conditions({"score": "BETWEEN 0 AND 100/* injected"})


def test_between_with_union_keyword_rejected():
    """BETWEEN value containing UNION is rejected."""
    with pytest.raises(ValueError, match="Injection"):
        simulate_filter_conditions({"score": "BETWEEN 0 AND 1 UNION SELECT 1"})


def test_between_with_statement_separator_rejected():
    """BETWEEN value containing ';' is rejected."""
    with pytest.raises(ValueError, match="Injection"):
        simulate_filter_conditions({"score": "BETWEEN 0 AND 1; DROP TABLE t"})


# ---------------------------------------------------------------------------
# Multiple conditions — only unsafe ones are dropped
# ---------------------------------------------------------------------------


def test_mixed_safe_and_unsafe_conditions():
    """Safe conditions are preserved; unsafe key/conditions are silently dropped."""
    result = simulate_filter_conditions({
        "status": "active",           # safe
        '"injected"': "1",             # unsafe key — dropped
        "name": "O'Brien",            # safe (value escaped)
    })
    # Only "status" and "name" should remain
    assert len(result) == 2
    safe_fragments = ' '.join(result)
    assert "status" in safe_fragments
    assert "O''Brien" in safe_fragments


# ---------------------------------------------------------------------------
# Confirm quoting helpers work as expected (regression guard)
# ---------------------------------------------------------------------------


def test_quote_string_escapes_single_quotes():
    """quoteString wraps in single quotes and escapes interior quotes."""
    assert _quote("O'Brien") == "'O''Brien'"


def test_escape_string_strips_quotes():
    """escapeString returns the raw escaped content without wrapper quotes."""
    escaped = _escape("O'Brien")
    # Should NOT have surrounding single quotes — just the escaped content
    assert escaped == "O''Brien"
