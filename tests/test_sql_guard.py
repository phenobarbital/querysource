"""Python-level tests for the Rust ``sql_guard`` (FEAT-156, TASK-819)."""
import pytest

from querysource.qs_parsers import HAS_RUST

pytestmark = pytest.mark.skipif(not HAS_RUST, reason="qs_parsers Rust extension not installed")

if HAS_RUST:
    from querysource.qs_parsers import sql_guard

REFRESH_CTE = (
    "WITH params AS (SELECT (date_trunc('month', CURRENT_DATE) - INTERVAL '5 months')::date AS start_date), "
    "deleted AS (DELETE FROM wm_assembly.employee_detail_profile p USING params "
    "WHERE p.activity_date >= params.start_date AND p.activity_date < CURRENT_DATE RETURNING 1) "
    "INSERT INTO wm_assembly.employee_detail_profile (employee_id, activity_date) "
    "SELECT ad.employee_id, ad.activity_date FROM activity_days ad, params "
    "WHERE ad.activity_date >= params.start_date"
)

BLOCKED = [
    ("DROP TABLE x", "drop"),
    ("TRUNCATE wm_assembly.t", "truncate"),
    ("ALTER TABLE t DROP COLUMN c", "alter_drop"),
    ("ALTER TABLE t DROP CONSTRAINT t_pk", "alter_drop"),
    ("DO $$ BEGIN EXECUTE 'DROP TABLE x'; END $$", "do_block"),
    ("GRANT SELECT ON t TO bob", "privilege"),
    ("REVOKE SELECT ON t FROM bob", "privilege"),
    ("CREATE ROLE evil", "role"),
    ("ALTER USER bob WITH SUPERUSER", "role"),
    ("SET ROLE admin", "role"),
    ("SET SESSION AUTHORIZATION bob", "role"),
    ("COPY t FROM PROGRAM 'curl http://x'", "copy_program"),
    ("BEGIN", "transaction_control"),
    ("COMMIT", "transaction_control"),
    ("ROLLBACK", "transaction_control"),
]


@pytest.mark.parametrize("sql,kind", BLOCKED)
def test_sql_guard_blocks_each_kind(sql: str, kind: str) -> None:
    with pytest.raises(ValueError, match=rf"statement 1: {kind} is not allowed"):
        sql_guard(sql)


def test_sql_guard_allows_dml_and_cte() -> None:
    assert sql_guard(REFRESH_CTE) == [REFRESH_CTE]


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 'drop table x'",
        "SELECT 1 -- DROP",
        "SELECT /* DROP */ 1",
        "SELECT $$DROP$$",
    ],
)
def test_sql_guard_ignores_keywords_in_literals(sql: str) -> None:
    assert len(sql_guard(sql)) == 1


def test_sql_guard_splits() -> None:
    result = sql_guard("DELETE FROM t WHERE a = 1; INSERT INTO t VALUES (';');")
    assert len(result) == 2
    assert "';'" in result[1]


def test_sql_guard_unterminated() -> None:
    with pytest.raises(ValueError, match="unterminated"):
        sql_guard("SELECT 'abc")


def test_sql_guard_reports_statement_index() -> None:
    with pytest.raises(ValueError, match=r"^statement 2: drop"):
        sql_guard("SELECT 1; DROP TABLE x")


def test_sql_guard_allows_plain_set() -> None:
    assert sql_guard("SET search_path = x") == ["SET search_path = x"]
