"""Unit tests for the FEAT-175 semantic-model public contract."""
from __future__ import annotations

import pytest

from querysource.semantic import (
    SemanticCompilerUnavailable,
    SemanticFieldDenied,
    SemanticQuery,
    SemanticRequestError,
    SemanticResource,
    SemanticResourceError,
)


def test_semantic_query_from_conditions_strips_reserved_keys() -> None:
    """Transport-only QuerySource conditions do not enter the semantic request."""
    query = SemanticQuery.from_conditions(
        {"metrics": ["revenue"], "refresh": True, "_output_options": {}, "queryformat": "json"}
    )
    assert query.metrics == ["revenue"]
    with pytest.raises(SemanticRequestError):
        SemanticQuery.from_conditions({"metrics": ["revenue"], "sql": "select 1"})


def test_semantic_query_rejects_bad_grain_and_limit() -> None:
    """Invalid grain, limit, and offset values become semantic request errors."""
    for invalid in ({"time_grain": "hour"}, {"limit": -1}, {"offset": -5}):
        with pytest.raises(SemanticRequestError):
            SemanticQuery.from_conditions(invalid)


def test_semantic_resource_from_yaml_inline_and_path(inline_resource_yaml: str) -> None:
    """Both inline and path definitions validate, while malformed resources fail as 400."""
    assert isinstance(SemanticResource.from_yaml(inline_resource_yaml).semantic.definition, dict)
    path_resource = SemanticResource.from_yaml(
        """\
name: epson.sales
kind: semantic
semantic:
  definition: models/epson_sales.ossie.yaml
"""
    )
    assert isinstance(path_resource.semantic.definition, str)
    with pytest.raises(SemanticResourceError):
        SemanticResource.from_yaml("name: epson.sales\nkind: other\nsemantic: {definition: model.yaml}")
    with pytest.raises(SemanticResourceError):
        SemanticResource.from_yaml("::: not yaml")


def test_error_codes() -> None:
    """Public semantic exceptions retain their fixed HTTP status codes."""
    assert SemanticRequestError().code == 400
    assert SemanticFieldDenied("margin").code == 403
    assert "querysource[semantic]" in str(SemanticCompilerUnavailable())
