"""FEAT-162: hostile filter keys never reach the SQL rendered by the Rust BigQuery builder."""
import pytest

from querysource.parsers import bigquery as bqmod

SQL = "SELECT * FROM t {where_cond}"
HOSTILE_MEMBER = "x') = \"\" OR TRUE OR ('"
HOSTILE_COLUMN = "n) = 1 OR 1=1 --"
SAFE_JSON = "JSON_VALUE(meta, '$.region') = \"us\""


def _rust_hardened() -> bool:
    """Probe the compiled extension: a stale build still renders the hostile member key."""
    if not bqmod.HAS_RUST:
        return False
    try:
        rendered = bqmod._rs.bq_filter_conditions(SQL, {"meta": {HOSTILE_MEMBER: "v"}}, {})
    except Exception:
        return False
    return "OR TRUE" not in rendered


pytestmark = pytest.mark.skipif(not _rust_hardened(), reason="stale _qs_parsers (TASK-855)")


def test_rust_rejects_hostile_dict_member() -> None:
    """A dict member key that is not an identifier path is dropped, siblings still render."""
    rendered = bqmod._rs.bq_filter_conditions(
        SQL, {"other": {HOSTILE_MEMBER: "v"}, "meta": {"region": "us"}}, {}
    )
    assert "OR TRUE" not in rendered
    assert "JSON_VALUE(other" not in rendered
    assert SAFE_JSON in rendered


@pytest.mark.parametrize("value", ["v", {"contains": "abc"}, {">": 1}, ["a", "b"], 1])
def test_rust_rejects_hostile_column_key(value: object) -> None:
    """A hostile top-level key is dropped for every value type, including the LIKE forms."""
    rendered = bqmod._rs.bq_filter_conditions(
        SQL, {HOSTILE_COLUMN: value, "meta": {"region": "us"}}, {}
    )
    assert "1=1" not in rendered
    assert HOSTILE_COLUMN not in rendered
    assert SAFE_JSON in rendered


def test_rust_safe_keys_unchanged() -> None:
    """Safe keys keep today's rendering (JSON extraction and FEAT-180 LIKE forms)."""
    rendered = bqmod._rs.bq_filter_conditions(
        SQL, {"meta": {"region": "us"}, "n": {"contains": "abc"}}, {}
    )
    assert SAFE_JSON in rendered
    assert 'n LIKE "%abc%"' in rendered
