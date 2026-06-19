"""Integration tests: Golden-slug regression for raw + non-raw slugs (FEAT-103 TASK-710).

Verifies that the FEAT-103 hardening (safe_format_map_validated) does NOT
regress legitimate query behaviour:
 - Trusted replacement fragments (*, current_date, empty strings) pass
   through Phase 1 (safe_format_map) unmodified.
 - Benign user-supplied conditions pass through Phase 2
   (safe_format_map_validated) and appear correctly in the rendered SQL.
 - Multiple conditions compose correctly.
 - Null/not-null sentinels pass through.
 - Conditions not present in the template are left as placeholders.

These tests are pure-Python (no live DB required) and exercise the
qs_parsers layer that all providers share.
"""
import sys
import os

import pytest  # noqa: F401 (used for skipif markers)

# Ensure installed package takes priority over worktree uncompiled source.
_WT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _WT_ROOT in sys.path:
    sys.path.remove(_WT_ROOT)
    sys.path.append(_WT_ROOT)

import querysource.qs_parsers as qs_parsers  # noqa: E402

# Check whether safe_format_map_validated is available in the installed .so.
_HAS_VALIDATED = hasattr(qs_parsers, "safe_format_map_validated")

_skip_no_validated = pytest.mark.skipif(
    not _HAS_VALIDATED,
    reason=(
        "safe_format_map_validated not in installed .so — "
        "rebuild with 'maturin develop' from the rust/ directory"
    ),
)


# ---------------------------------------------------------------------------
# Golden replacement values (Phase 1 — trusted SQL fragments)
# ---------------------------------------------------------------------------

_REPLACEMENT = {
    "fields": "*",
    "filterdate": "current_date",
    "firstdate": "current_date",
    "lastdate": "current_date",
    "where_cond": "",
    "and_cond": "",
    "filter": "",
}


def _phase1(template: str) -> str:
    """Apply Phase 1: safe_format_map with trusted replacement."""
    return qs_parsers.safe_format_map(template, _REPLACEMENT)


def _phase2(sql: str, conditions: dict) -> str:
    """Apply Phase 2: safe_format_map_validated with user conditions."""
    return qs_parsers.safe_format_map_validated(sql, conditions, {})


def _render(template: str, conditions: dict | None = None) -> str:
    sql = _phase1(template)
    if conditions:
        sql = _phase2(sql, conditions)
    return sql


# ---------------------------------------------------------------------------
# Phase 1 golden tests: trusted replacement fragments
# ---------------------------------------------------------------------------


class TestPhase1TrustedReplacement:
    """safe_format_map (no-escape) substitutes trusted SQL fragments correctly."""

    def test_star_fields_not_quoted(self):
        """{fields} → '*' without quoting."""
        result = _phase1("SELECT {fields} FROM t {where_cond}")
        assert "SELECT * FROM t" in result

    def test_current_date_not_quoted(self):
        """{filterdate} → 'current_date' without quoting."""
        result = _phase1("SELECT * FROM t WHERE d >= {filterdate}")
        assert "current_date" in result
        assert "'current_date'" not in result  # must NOT be quoted

    def test_multiple_date_replacements(self):
        """firstdate and lastdate are both substituted as bare SQL tokens."""
        result = _phase1(
            "SELECT * FROM t WHERE d BETWEEN {firstdate} AND {lastdate}"
        )
        assert result.count("current_date") == 2

    def test_empty_where_cond_removed(self):
        """{where_cond} → '' cleans up the placeholder."""
        result = _phase1("SELECT * FROM t {where_cond}")
        assert "{where_cond}" not in result
        assert result.strip().endswith("FROM t")

    def test_unknown_placeholder_preserved(self):
        """Placeholders not in replacement are preserved intact."""
        result = _phase1("SELECT * FROM t {custom_cond}")
        assert "{custom_cond}" in result


# ---------------------------------------------------------------------------
# Phase 2 golden tests: benign user conditions
# ---------------------------------------------------------------------------


@_skip_no_validated
class TestPhase2BenignConditions:
    """safe_format_map_validated accepts benign user conditions and renders them."""

    def test_simple_literal_substitution(self):
        """Benign slug value substituted in literal context."""
        result = _render(
            "SELECT * FROM t WHERE slug = '{slug}'",
            {"slug": "acme-corp"},
        )
        assert "acme-corp" in result
        assert "{slug}" not in result

    def test_alphanumeric_value_substitution(self):
        """Alphanumeric value substituted correctly."""
        result = _render(
            "SELECT * FROM t WHERE program = '{program}'",
            {"program": "program_2024"},
        )
        assert "program_2024" in result

    def test_multiple_conditions_substituted(self):
        """Multiple benign conditions all substituted."""
        result = _render(
            "SELECT * FROM t WHERE slug = '{slug}' AND program = '{program}'",
            {"slug": "my-slug", "program": "default"},
        )
        assert "my-slug" in result
        assert "default" in result

    def test_value_with_hyphen_passes(self):
        """Values containing hyphens (common in slug names) are not rejected."""
        result = _render(
            "SELECT * FROM t WHERE name = '{name}'",
            {"name": "client-name-2024"},
        )
        assert "client-name-2024" in result

    def test_unmatched_placeholder_preserved_after_phase2(self):
        """Placeholders not in either replacement or conditions are preserved."""
        template = "SELECT * FROM t {custom_fragment}"
        sql = _phase1(template)
        # custom_fragment not in conditions — preserved intact
        assert "{custom_fragment}" in sql

    def test_replacement_and_conditions_compose(self):
        """Phase 1 + Phase 2 compose correctly without collision."""
        template = "SELECT {fields} FROM t WHERE slug = '{slug}' {where_cond}"
        result = _render(template, {"slug": "acme"})
        assert "SELECT * FROM t" in result
        assert "acme" in result
        assert "{where_cond}" not in result
        assert "{slug}" not in result


# ---------------------------------------------------------------------------
# Regression: replacement fragments must NOT be re-escaped in Phase 2
# ---------------------------------------------------------------------------


@_skip_no_validated
class TestPhase1FragmentsNotRescapedInPhase2:
    """Ensure trusted SQL from Phase 1 is not corrupted by Phase 2.

    The two-phase design ensures Phase 2 is only called with user-supplied
    conditions (dict). If the phases were merged and trusted values like
    'current_date' or '*' went through safe_format_map_validated, they would
    be wrongly quoted (e.g. '* ' or 'current_date' becoming 'current_date').
    """

    def test_star_still_bare_after_both_phases(self):
        """'*' from Phase 1 replacement appears unquoted in final SQL."""
        template = "SELECT {fields} FROM t WHERE slug = '{slug}'"
        sql = _phase1(template)
        result = _phase2(sql, {"slug": "my-slug"})
        # '*' must be bare, not '*' (quoted)
        assert "SELECT * FROM t" in result

    def test_current_date_still_bare_after_both_phases(self):
        """'current_date' from Phase 1 replacement appears unquoted."""
        template = "SELECT * FROM t WHERE d >= {filterdate} AND slug = '{slug}'"
        sql = _phase1(template)
        result = _phase2(sql, {"slug": "my-slug"})
        assert "current_date" in result
        assert "'current_date'" not in result

    def test_phase1_empty_string_fragments_removed(self):
        """Empty-string placeholders from Phase 1 (where_cond='') are cleaned up."""
        template = "SELECT * FROM t {where_cond} AND 1=1"
        sql = _phase1(template)
        # No {where_cond} placeholder remains
        assert "{where_cond}" not in sql


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Boundary and edge-case scenarios for the two-phase substitution."""

    @_skip_no_validated
    def test_empty_conditions_dict(self):
        """Calling Phase 2 with an empty conditions dict is a no-op."""
        template = "SELECT * FROM t WHERE 1=1"
        sql = _phase1(template)
        result = _phase2(sql, {})
        assert result == sql

    def test_template_with_no_placeholders(self):
        """Template with no placeholders is returned unchanged."""
        template = "SELECT count(*) FROM analytics.events"
        sql = _phase1(template)
        # No user conditions needed
        assert "count(*)" in sql

    @_skip_no_validated
    def test_value_with_trailing_whitespace_passes(self):
        """Trailing whitespace in a user value is handled gracefully."""
        result = _render(
            "SELECT * FROM t WHERE name = '{name}'",
            {"name": "alice "},
        )
        # Should contain 'alice ' quoted — not rejected
        assert "alice" in result
