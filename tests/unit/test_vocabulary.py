"""FEAT-148 TASK-739 — date keyword vocabulary."""
from querysource.types.validators import pg_constants, pg_udfs, udf_keywords
from querysource.utils.functions import fdom
from querysource.utils.vocabulary import (
    FUNCTION_REGISTRY,
    build_vocabulary,
    keyword_example,
)


def test_build_vocabulary_matches_udf_list():
    """Keywords should match udf_keywords() order; constants/pg_functions are copies."""
    udf_list = udf_keywords()
    constants = pg_constants()
    pg_funcs = pg_udfs()

    result = build_vocabulary(udf_list, constants, pg_funcs)

    # Check version and case_insensitive
    assert result["version"] == "1.0"
    assert result["case_insensitive"] is True

    # Check keywords match udf_list order
    keyword_names = [kw["name"] for kw in result["keywords"]]
    assert keyword_names == udf_list

    # Check constants and pg_functions are copies
    assert result["constants"] == constants
    assert result["pg_functions"] == pg_funcs


def test_unknown_keyword_has_null_description():
    """Unknown keywords should have description: None."""
    result = build_vocabulary(["TODAY", "NEXT_WEEK"], [], [])

    # TODAY should have a description
    today_entry = next(k for k in result["keywords"] if k["name"] == "TODAY")
    assert today_entry["description"] is not None

    # NEXT_WEEK should have null description
    next_week_entry = next(k for k in result["keywords"] if k["name"] == "NEXT_WEEK")
    assert next_week_entry["description"] is None


def test_functions_informational_only():
    """Functions should be exactly 4 helpers, all invocable=False, previous_year absent."""
    function_names = [f.name for f in FUNCTION_REGISTRY]

    # Exactly 4 functions in specific order
    assert len(FUNCTION_REGISTRY) == 4
    assert function_names == ["date_diff", "date_sum", "days_ago", "previous_month"]

    # All are not invocable
    for func in FUNCTION_REGISTRY:
        assert func.invocable is False

    # previous_year should not be in the registry
    assert "previous_year" not in function_names


def test_keyword_example_resolves():
    """keyword_example should resolve known keywords and return None for unknown."""
    # Known keyword should resolve
    example = keyword_example("FDOM")
    assert example == fdom()

    # Unknown keyword should return None
    assert keyword_example("NOPE") is None
    assert keyword_example("") is None


def test_vocabulary_week_keywords():
    """FEAT-167: FDOW/LDOW/LDOPM are keywords with descriptions and examples (AC2)."""
    from querysource.utils.functions import fdow, ldopm, ldow

    result = build_vocabulary(udf_keywords(), pg_constants(), pg_udfs())
    by_name = {k["name"]: k for k in result["keywords"]}
    assert [k["name"] for k in result["keywords"]] == udf_keywords()
    assert len(result["functions"]) == 4
    for name, fn in (("FDOW", fdow), ("LDOW", ldow), ("LDOPM", ldopm)):
        assert by_name[name]["returns"] == "date-string"
        assert by_name[name]["description"]
        assert by_name[name]["example"] == fn()
