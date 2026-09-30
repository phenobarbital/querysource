"""Unit tests for Parquet DNF filter translation (FEAT-158, TASK-796)."""
import subprocess
import sys

import pyarrow as pa
import pyarrow.dataset as ds
import pytest

from querysource.queries.multi.sources.parquet.filters import (
    ALLOWED_OPERATORS,
    build_filter_expression,
    filter_columns,
)


@pytest.fixture
def dataset():
    """In-memory dataset used to evaluate expressions."""
    table = pa.table({
        "a": [1, 2, 3, None],
        "c": ["US", "CA", "US", "MX"],
    })
    return ds.dataset(table)


def _count(dataset, filters) -> int:
    return dataset.count_rows(filter=build_filter_expression(filters))


class TestBuildFilterExpression:
    def test_none_or_empty(self):
        assert build_filter_expression(None) is None
        assert build_filter_expression([]) is None

    def test_flat_and(self, dataset):
        assert _count(dataset, [["c", "==", "US"], ["a", ">=", 2]]) == 1

    def test_or_of_ands(self, dataset):
        assert _count(dataset, [[["c", "==", "US"]], [["c", "==", "CA"]]]) == 3

    def test_in_requires_list(self):
        with pytest.raises(ValueError, match="list value"):
            build_filter_expression([["a", "in", 1]])

    def test_not_in(self, dataset):
        assert _count(dataset, [["a", "not in", [1, 2]]]) == 2

    @pytest.mark.parametrize("operator", ["like", "eval", "__import__"])
    def test_unknown_operator(self, operator):
        with pytest.raises(ValueError, match=operator):
            build_filter_expression([["a", operator, 1]])

    def test_is_null_forms(self, dataset):
        assert _count(dataset, [["a", "is null"]]) == 1
        assert _count(dataset, [["a", "IS   NULL", None]]) == 1
        assert _count(dataset, [["a", "is not null"]]) == 3
        with pytest.raises(ValueError, match="does not accept a value"):
            build_filter_expression([["a", "is null", 1]])

    @pytest.mark.parametrize("operator", ["=", "=="])
    def test_equals_aliases(self, dataset, operator):
        assert _count(dataset, [["a", operator, 1]]) == 1

    @pytest.mark.parametrize("entry", ["a == 1", ["a"], [1, "==", 1], ["a", "=="]])
    def test_malformed_entry(self, entry):
        with pytest.raises(ValueError):
            build_filter_expression([entry])

    def test_string_filter_rejected(self):
        with pytest.raises(ValueError, match="filters must be a list"):
            build_filter_expression("a > 1")


def test_filter_columns():
    assert filter_columns([["a", ">", 1], ["c", "==", "US"]]) == {"a", "c"}
    assert filter_columns(None) == set()


def test_import_is_lazy():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import querysource.queries.multi.sources.parquet.filters; assert 'pyarrow' not in sys.modules",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_allowed_operators_is_exact():
    assert ALLOWED_OPERATORS == frozenset({
        "=", "==", "!=", "<", "<=", ">", ">=", "in", "not in", "is null", "is not null",
    })
