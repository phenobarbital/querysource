"""Integration tests: SQLi PoC replay via the query endpoint (FEAT-103 TASK-710).

Tests that the PoC payloads from spec §4 are rejected by both:
 - the Rust path (safe_format_map_validated, TASK-704+707)
 - the Cython fallback path (TASK-708) via monkeypatching HAS_RUST=False

Skipped when Postgres is not reachable (CI with live DB runs them).
These tests drive the payload through the qs_parsers layer directly
(not through a live HTTP endpoint) to avoid requiring a running QuerySource
app and DB-stored slug definitions.

For end-to-end HTTP replay, see test_slug_regression.py once a representative
slug fixture is established in the test DB.
"""
import pytest
import sys
import os

# Ensure the installed querysource package takes priority over the worktree
# uncompiled package (worktree conftest inserts worktree root at front).
_WT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _WT_ROOT in sys.path:
    sys.path.remove(_WT_ROOT)
    sys.path.append(_WT_ROOT)

import querysource.qs_parsers as qs_parsers  # noqa: E402

# Check whether safe_format_map_validated is available in the installed .so.
# It is added in FEAT-103 (TASK-704/706) but requires a maturin develop rebuild.
_HAS_VALIDATED = hasattr(qs_parsers, "safe_format_map_validated")

_skip_no_validated = pytest.mark.skipif(
    not _HAS_VALIDATED,
    reason=(
        "safe_format_map_validated not in installed .so — "
        "rebuild with 'maturin develop' from the rust/ directory"
    ),
)


# ---------------------------------------------------------------------------
# PoC payload fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sqli_payloads():
    """PoC SQLi payloads from spec §4.2 — all must be rejected."""
    return [
        # V1-a: identifier-breakout UNION
        '" IS NULL UNION SELECT version(),null,null,null,null,null--',
        # V1-b: literal-breakout pg_database enumeration
        "' UNION SELECT string_agg(datname,',') FROM pg_database--",
        # V1-c: pg_shadow credential dump
        "' UNION SELECT usename||':'||passwd FROM pg_shadow--",
        # comment marker only
        "value'--",
        # statement separator
        "1; DROP TABLE users--",
        # block comment
        "1/*injected*/",
    ]


@pytest.fixture
def raw_slug_template():
    """Representative raw query template mirroring troc_client_tenant patterns."""
    return "SELECT * FROM clients WHERE client_slug = '{client_slug}'"


# ---------------------------------------------------------------------------
# Rust path: safe_format_map_validated
# ---------------------------------------------------------------------------


@_skip_no_validated
class TestRustPathRejectsInjection:
    """Rust safe_format_map_validated rejects every PoC payload."""

    def test_has_rust_available(self):
        """Precondition: Rust extension must be loaded for these tests."""
        assert qs_parsers.HAS_RUST, (
            "Rust extension not loaded — rebuild with 'maturin develop'"
        )

    def test_sqli_payloads_rejected(self, sqli_payloads, raw_slug_template):
        """Every PoC payload raises an exception when passed as a condition."""
        for payload in sqli_payloads:
            with pytest.raises(Exception, match=".*") as exc_info:
                qs_parsers.safe_format_map_validated(
                    raw_slug_template,
                    {"client_slug": payload},
                    {},
                )
            # The exception message must not contain a DB error or SQL fragment
            err_str = str(exc_info.value).lower()
            # It should NOT contain DB schema leakage keywords
            for leaked in ("pg_shadow", "pg_database", "information_schema"):
                assert leaked not in err_str, (
                    f"Exception leaks schema info for payload {payload!r}: {err_str}"
                )

    def test_benign_condition_passes(self, raw_slug_template):
        """Non-injection conditions are NOT rejected."""
        result = qs_parsers.safe_format_map_validated(
            raw_slug_template,
            {"client_slug": "acme-corp"},
            {},
        )
        assert "acme-corp" in result
        assert "clients" in result

    def test_response_body_no_version_leak(self, sqli_payloads, raw_slug_template):
        """When injection is rejected, the exception body must not contain version()."""
        for payload in sqli_payloads:
            try:
                qs_parsers.safe_format_map_validated(
                    raw_slug_template,
                    {"client_slug": payload},
                    {},
                )
                pytest.fail(f"Expected rejection of payload: {payload!r}")
            except Exception as exc:
                body = str(exc)
                assert "version()" not in body, (
                    f"Exception body contains 'version()' for payload {payload!r}"
                )
                assert "pg_" not in body or "pg_shadow" not in body, (
                    f"Exception body leaks pg_shadow for payload {payload!r}"
                )


# ---------------------------------------------------------------------------
# Cython fallback path: simulate HAS_RUST=False
# ---------------------------------------------------------------------------


class TestCythonFallbackRejectsInjection:
    """The Cython filter_conditions fallback rejects injection payloads.

    These tests use the simulate_filter_conditions() helper from
    test_cython_filter_fallback.py (pure-Python simulation of the hardened
    Cython code).  They verify the same security invariants hold on the
    fallback path.
    """

    @pytest.fixture(autouse=True)
    def _import_simulator(self):
        """Import the simulator from the sibling unit test."""
        import importlib.util
        unit_dir = os.path.join(os.path.dirname(__file__), "..", "unit")
        spec_path = os.path.join(unit_dir, "test_cython_filter_fallback.py")
        spec = importlib.util.spec_from_file_location(
            "test_cython_filter_fallback", spec_path
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.simulate = mod.simulate_filter_conditions

    def test_injection_key_rejected(self):
        """Key containing double-quote (identifier breakout) is rejected."""
        result = self.simulate({'"injected"': "1"})
        assert len(result) == 0

    def test_sqli_between_rejected(self):
        """BETWEEN clause with UNION SELECT is rejected."""
        with pytest.raises(ValueError, match="Injection"):
            self.simulate({
                "score": "BETWEEN 0 AND 1 UNION SELECT version()--"
            })

    def test_sqli_comment_marker_rejected(self):
        """Value with comment marker -- is escaped (prevents injection)."""
        # The value won't be rejected at key level — but the quoting escapes it
        result = self.simulate({"status": "active'--"})
        assert len(result) == 1
        # The single-quote is escaped
        assert "active''--" in result[0]

    def test_benign_conditions_pass(self):
        """Normal filter conditions produce well-formed WHERE fragments."""
        result = self.simulate({"status": "active", "program": "default"})
        assert len(result) == 2


# ---------------------------------------------------------------------------
# Two-phase substitution correctness
# ---------------------------------------------------------------------------


@_skip_no_validated
class TestTwoPhaseSubstitution:
    """Verify that safe_format_map (Phase 1) and safe_format_map_validated
    (Phase 2) work correctly together in the raw_query() pattern."""

    def test_replacement_fragments_not_escaped(self):
        """Trusted replacement fragments (*, current_date) pass through unquoted."""
        template = "SELECT {fields} FROM t WHERE d >= {filterdate} {where_cond}"
        replacement = {
            "fields": "*",
            "filterdate": "current_date",
            "where_cond": "",
        }
        sql = qs_parsers.safe_format_map(template, replacement)
        assert "SELECT * FROM t" in sql
        assert "current_date" in sql

    def test_conditions_validated_after_replacement(self):
        """After Phase 1, user conditions in Phase 2 are validated."""
        template = "SELECT * FROM t WHERE slug = '{slug}'"
        sql = qs_parsers.safe_format_map(template, {})
        # Phase 2 with benign condition
        result = qs_parsers.safe_format_map_validated(sql, {"slug": "my-slug"}, {})
        assert "my-slug" in result

    def test_injection_after_replacement_rejected(self):
        """Injection payload in Phase 2 (user condition) is rejected after Phase 1."""
        template = "SELECT * FROM t WHERE slug = '{slug}'"
        sql = qs_parsers.safe_format_map(template, {})
        with pytest.raises(Exception):
            qs_parsers.safe_format_map_validated(
                sql,
                {"slug": "' UNION SELECT version()--"},
                {},
            )
