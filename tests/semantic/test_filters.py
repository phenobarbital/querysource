import pytest

from querysource.semantic.exceptions import SemanticRequestError
from querysource.semantic.filters import FilterValidator
from querysource.semantic.models import FILTER_OPS, CatalogField, SemanticFilter

DIMS = {
    "orders.region": CatalogField(name="orders.region", kind="dimension", type="categorical"),
    "orders.order_date": CatalogField(name="orders.order_date", kind="dimension", type="time"),
}


@pytest.fixture
def validator():
    return FilterValidator(DIMS, dialect="postgres", max_filters=4)


def test_filter_structured_ops_and_unknown_dimension(validator):
    values = {
        "=": "ES",
        "!=": "US",
        ">": 1,
        ">=": 1,
        "<": 10,
        "<=": 10,
        "in": ["ES", "US"],
        "not in": ["CN"],
        "between": [1, 10],
        "like": "E%",
        "ilike": "e%",
        "is null": None,
        "is not null": None,
    }
    rendered = [validator.render([SemanticFilter(field="region", op=op, value=value)])[0] for op, value in values.items()]
    assert len(rendered) == len(FILTER_OPS)
    with pytest.raises(SemanticRequestError):
        validator.render([SemanticFilter(field="nope", op="=", value="x")])


def test_filter_string_rejects_subquery_and_function(validator):
    for bad in ("region = (SELECT 1)", "pg_sleep(1) = 1"):
        with pytest.raises(SemanticRequestError):
            validator.render([bad])
    assert validator.render(["lower(region) = 'es'"])


def test_filter_string_literal_quoting(validator):
    out = validator.render([SemanticFilter(field="region", op="=", value="1 OR 1=1")])
    assert "'1 OR 1=1'" in out[0]


def test_filter_renders_qualified_column_not_dotted_identifier(validator):
    """``orders.region`` must render as table.column, never as the quoted ``"orders.region"``."""
    structured = validator.render([SemanticFilter(field="region", op="=", value="ES")])[0]
    string = validator.render(["region = 'ES'"])[0]
    for rendered in (structured, string):
        assert '"orders.region"' not in rendered
        assert "orders.region" in rendered
