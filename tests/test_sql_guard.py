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
    ("SET session_authorization = bob", "role"),
    ("SET \"role\" TO bob", "role"),
    ("SET standard_conforming_strings = off", "setting"),
    ("SET LOCAL statement_timeout = 0", "setting"),
    ("PREPARE TRANSACTION 'x'", "transaction_control"),
    ("ALTER DEFAULT PRIVILEGES GRANT ALL ON TABLES TO x", "privilege"),
    ("CREATE SCHEMA s GRANT ALL ON t TO u", "privilege"),
    ("SELECT 1 -- c\r; DROP TABLE t", "drop"),
    ("COPY t FROM PROGRAM 'curl http://x'", "copy"),
    ("COPY t TO STDOUT", "copy"),
    ("copy t from stdin", "copy"),
    ("CREATE FUNCTION f() RETURNS int AS $$ SELECT 1 $$ LANGUAGE sql", "executable_object"),
    ("create or replace procedure p() language sql as 'select 1'", "executable_object"),
    ("CREATE CONSTRAINT TRIGGER tr AFTER INSERT ON t FOR EACH ROW EXECUTE FUNCTION f()", "executable_object"),
    ("CREATE EXTENSION dblink", "executable_object"),
    ("CREATE EVENT TRIGGER e ON ddl_command_start EXECUTE FUNCTION f()", "executable_object"),
    ("CREATE RULE r AS ON INSERT TO t DO INSTEAD NOTHING", "executable_object"),
    ("CREATE CAST (text AS int) WITH FUNCTION f(text)", "executable_object"),
    ("ALTER FUNCTION f() SECURITY DEFINER", "executable_object"),
    ("ALTER EXTENSION e UPDATE", "executable_object"),
    ("RESET ALL", "setting"),
    ("reset statement_timeout", "setting"),
    ("RESET ROLE", "role"),
    ("SELECT set_config('statement_timeout', '0', false)", "dangerous_function"),
    ("SELECT PG_CATALOG.Set_Config ('lock_timeout', '0', true)", "dangerous_function"),
    ("SELECT set_config /* c */ ('a', 'b', false)", "dangerous_function"),
    ("SELECT \"set_config\"('a', 'b', false)", "dangerous_function"),
    ("SELECT * FROM dblink('host=x', 'DROP TABLE t') AS r(a int)", "dangerous_function"),
    ("SELECT dblink_exec('DROP TABLE t')", "dangerous_function"),
    ("UPDATE t SET a = pg_read_file('/etc/passwd')", "dangerous_function"),
    ("INSERT INTO t SELECT lo_import('/etc/passwd')", "dangerous_function"),
    ("SELECT pg_terminate_backend(123)", "dangerous_function"),
    ("BEGIN", "transaction_control"),
    ("COMMIT", "transaction_control"),
    ("ROLLBACK", "transaction_control"),
]


@pytest.mark.parametrize("sql,kind", BLOCKED)
def test_sql_guard_blocks_each_kind(sql: str, kind: str) -> None:
    with pytest.raises(ValueError, match=rf"statement \d: {kind} is not allowed"):
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


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE TABLE t (a int, event text, trigger_name text)",
        "CREATE INDEX i ON t (a)",
        "CREATE OR REPLACE VIEW v AS SELECT 1",
        "CREATE MATERIALIZED VIEW m AS SELECT 1",
        "CREATE SCHEMA s",
        "CREATE SEQUENCE seq",
        "ALTER TABLE t ADD COLUMN c int",
        "CALL s.refresh_profile(1, 'x')",
    ],
)
def test_sql_guard_allows_non_executable_ddl(sql: str) -> None:
    assert sql_guard(sql) == [sql]


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT set_config FROM t",
        "SELECT t.dblink, t.pg_read_file FROM t WHERE lo_import = 1",
        "SELECT 'set_config(''a'', ''b'', false)'",
        "SELECT $$ pg_read_file('/etc/passwd') $$",
        "SELECT 1 -- set_config('a', 'b', false)",
        "SELECT /* dblink('x') */ 1",
        "SELECT \"set_config\" FROM t",
        "SELECT my_set_config('a'), set_configuration('b')",
        "SELECT current_setting('statement_timeout')",
    ],
)
def test_sql_guard_allows_dangerous_names_without_call(sql: str) -> None:
    assert sql_guard(sql) == [sql]
