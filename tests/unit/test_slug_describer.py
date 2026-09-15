"""FEAT-148 TASK-736 — pure slug describer."""
import pytest

from querysource.queries.describe import (
    ADMIN_FIELDS,
    DescribeGrants,
    build_variables,
    describe_slug,
    extract_placeholders,
    is_json_dialect,
    normalize_type,
)


@pytest.fixture
def sample_definition():
    return {
        "query_slug": "epson_field_activity", "provider": "pg", "parser": "SQLParser",
        "program_slug": "epson",
        "query_raw": "SELECT {fields} FROM t WHERE visit_date BETWEEN {firstdate} AND {lastdate} "
                     "AND store_id = {store_id} AND region = {region} {and_cond}",
        "conditions": {"store_id": 10},
        "cond_definition": {"firstdate": "date", "lastdate": "DATE", "store_id": "integer", "extra": "STRING"},
        "dwh_info": {"x": 1}, "cache_options": {"y": 2}, "created_by": 1, "updated_by": 2,
    }


def test_extract_placeholders_order_and_grammar():
    query_raw = "SELECT {fields} FROM t WHERE {store_id} AND {store_id} {{escaped}} {a b} {and_cond}"
    names, error = extract_placeholders(query_raw)
    assert error is None
    # dedupe (store_id appears twice, kept once), escaped {{}} not a placeholder,
    # 'a b' fails the grammar and is dropped, order preserved (fields, store_id, and_cond).
    assert names == ["fields", "store_id", "and_cond"]


def test_extract_placeholders_malformed():
    names, error = extract_placeholders("SELECT {")
    assert names is None
    assert error is not None
    assert "Single '{'" in error or "{" in error


def test_extract_placeholders_empty_input():
    assert extract_placeholders(None) == ([], None)
    assert extract_placeholders("") == ([], None)


def test_json_dialect_unsupported():
    assert is_json_dialect('{"$match": {"a": 1}}') is True
    assert is_json_dialect("SELECT * FROM t") is False
    assert is_json_dialect(None) is False
    assert is_json_dialect("{not valid json") is False


def test_normalize_type_aliases_and_unknown():
    assert normalize_type("int") == "integer"
    assert normalize_type("VARCHAR") == "string"
    assert normalize_type("DATE") == "date"
    assert normalize_type(None) is None
    assert normalize_type("bogus") is None


def test_structural_placeholders_reported(sample_definition):
    result = build_variables(
        sample_definition["query_raw"],
        sample_definition["conditions"],
        sample_definition["cond_definition"],
    )
    assert set(result["structural_placeholders"]) == {"fields", "and_cond"}
    var_names = {v.name for v in result["variables"]}
    assert "fields" not in var_names
    assert "and_cond" not in var_names


def test_reserved_in_cond_definition_warns():
    result = build_variables(
        "SELECT {fields} FROM t",
        {},
        {"fields": "string"},
    )
    assert result["structural_placeholders"] == ["fields"]
    assert any("reserved placeholder 'fields'" in w for w in result["warnings"])


def test_variable_types_and_defaults(sample_definition):
    result = build_variables(
        sample_definition["query_raw"],
        sample_definition["conditions"],
        sample_definition["cond_definition"],
    )
    by_name = {v.name: v for v in result["variables"]}
    assert by_name["firstdate"].type == "date"
    assert by_name["firstdate"].default == "current_date"
    assert by_name["firstdate"].required is False
    assert by_name["lastdate"].type == "date"  # DATE normalized lowercase
    assert by_name["store_id"].type == "integer"
    assert by_name["extra"].type == "string"


def test_unknown_type_warns():
    result = build_variables(
        "SELECT {x}",
        {},
        {"x": "not_a_type"},
    )
    by_name = {v.name: v for v in result["variables"]}
    assert by_name["x"].type is None
    assert any("unknown type 'not_a_type'" in w for w in result["warnings"])


def test_variable_required_and_source(sample_definition):
    result = build_variables(
        sample_definition["query_raw"],
        sample_definition["conditions"],
        sample_definition["cond_definition"],
    )
    by_name = {v.name: v for v in result["variables"]}
    assert by_name["region"].required is True
    assert by_name["region"].source == "placeholder"
    # 'extra' is cond_definition-only (not a placeholder) and appended.
    assert by_name["extra"].source == "cond_definition"
    assert by_name["extra"].required is True


def test_effective_cond_definition_merge_order():
    result = describe_slug(
        {
            "query_slug": "s",
            "query_raw": "SELECT {x}",
            "conditions": {"cond_definition": {"x": "string", "y": "integer"}},
            "cond_definition": {"x": "date"},
        },
        DescribeGrants(raw=True, admin=True),
        columns_link="/c",
        vocabulary_link="/v",
    )
    # top-level cond_definition wins on overlapping keys ('x'); nested keys merge in.
    assert result["derived"]["effective_cond_definition"] == {"x": "date", "y": "integer"}


def test_json_dialect_no_variables():
    result = build_variables('{"$match": {"a": 1}}', {}, {})
    assert result["variables"] is None
    assert result["variables_supported"] is False
    assert result["structural_placeholders"] == []


def test_malformed_placeholder_error():
    result = build_variables("SELECT {", {}, {})
    assert result["variables"] is None
    assert result["variables_supported"] is True
    assert "variables_error" in result


@pytest.mark.parametrize("raw,admin", [(False, False), (True, False), (False, True), (True, True)])
def test_redaction_matrix(sample_definition, raw, admin):
    result = describe_slug(
        sample_definition,
        DescribeGrants(raw=raw, admin=admin),
        columns_link="/c",
        vocabulary_link="/v",
    )
    expected_redacted = set()
    if not raw:
        expected_redacted.add("query_raw")
    if not admin:
        expected_redacted.update(ADMIN_FIELDS)
    assert set(result["redacted"]) == expected_redacted
    for name in expected_redacted:
        assert name not in result  # omitted, never null
    if raw:
        assert "query_raw" in result
    if admin:
        for name in ADMIN_FIELDS:
            assert name in result


def test_describe_links_and_capabilities(sample_definition):
    result = describe_slug(
        sample_definition,
        DescribeGrants(raw=False, admin=False),
        columns_link="/api/v1/queries/epson_field_activity/columns",
        vocabulary_link="/api/v1/queries/vocabulary",
    )
    derived = result["derived"]
    assert derived["capabilities"]["refresh_param"] == "refresh"
    assert derived["capabilities"]["fields"] == []
    assert derived["capabilities"]["filtering"] == {}
    assert derived["links"] == {
        "columns": "/api/v1/queries/epson_field_activity/columns",
        "vocabulary": "/api/v1/queries/vocabulary",
    }
    # None fields normalized to their capability defaults, not left as None.
    assert derived["capabilities"]["ordering"] == []
    assert derived["capabilities"]["grouping"] == []
