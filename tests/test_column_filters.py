"""Unit tests for column-to-column filtering with $column syntax."""
import pandas as pd
import pytest
from querysource.types.dt.filters import create_filter
from querysource.exceptions import QueryException


class TestColumnToColumnFilters:
    """Test suite for column-to-column filtering functionality."""

    def test_column_to_column_gte_dates(self):
        """Test date comparison: fecha_escuchado >= fecha_podcast."""
        df = pd.DataFrame({
            'id': [1, 2, 3, 4],
            'fecha_escuchado': pd.to_datetime([
                '2024-01-10', '2024-01-05', '2024-01-20', '2024-01-15'
            ]),
            'fecha_podcast': pd.to_datetime([
                '2024-01-05', '2024-01-10', '2024-01-15', '2024-01-15'
            ]),
        })

        _filter = [
            {
                'column': 'fecha_escuchado',
                'expression': '>=',
                'value': {'$column': 'fecha_podcast'}
            }
        ]

        conditions = create_filter(_filter, df)
        result = df.loc[eval(" & ".join(conditions))]

        # Expected: rows where fecha_escuchado >= fecha_podcast
        assert len(result) == 3
        assert list(result['id'].values) == [1, 3, 4]

    def test_column_to_column_gt_numeric(self):
        """Test numeric column-to-column comparison."""
        df = pd.DataFrame({
            'id': [1, 2, 3, 4],
            'valor_a': [10, 5, 20, 15],
            'valor_b': [5, 10, 15, 15],
        })

        _filter = [
            {
                'column': 'valor_a',
                'expression': '>',
                'value': {'$column': 'valor_b'}
            }
        ]

        conditions = create_filter(_filter, df)
        result = df.loc[eval(" & ".join(conditions))]

        # Expected: rows where valor_a > valor_b
        assert len(result) == 2
        assert list(result['id'].values) == [1, 3]

    def test_column_to_column_lt(self):
        """Test less-than comparison between columns."""
        df = pd.DataFrame({
            'id': [1, 2, 3],
            'col_a': [5, 10, 15],
            'col_b': [10, 5, 20],
        })

        _filter = [
            {
                'column': 'col_a',
                'expression': '<',
                'value': {'$column': 'col_b'}
            }
        ]

        conditions = create_filter(_filter, df)
        result = df.loc[eval(" & ".join(conditions))]

        # col_a < col_b: rows 1 (5 < 10) and 3 (15 < 20)
        assert len(result) == 2
        assert list(result['id'].values) == [1, 3]

    def test_column_to_column_eq(self):
        """Test equality comparison between columns."""
        df = pd.DataFrame({
            'id': [1, 2, 3],
            'name': ['Alice', 'Bob', 'Charlie'],
            'expected_name': ['Alice', 'Bill', 'Charlie'],
        })

        _filter = [
            {
                'column': 'name',
                'expression': '==',
                'value': {'$column': 'expected_name'}
            }
        ]

        conditions = create_filter(_filter, df)
        result = df.loc[eval(" & ".join(conditions))]

        # Only rows where name == expected_name
        assert len(result) == 2
        assert list(result['id'].values) == [1, 3]

    def test_column_to_column_ne(self):
        """Test inequality comparison between columns."""
        df = pd.DataFrame({
            'id': [1, 2, 3],
            'valor_actual': [10, 10, 15],
            'valor_esperado': [10, 5, 15],
        })

        _filter = [
            {
                'column': 'valor_actual',
                'expression': '!=',
                'value': {'$column': 'valor_esperado'}
            }
        ]

        conditions = create_filter(_filter, df)
        result = df.loc[eval(" & ".join(conditions))]

        # Only row 2 where 10 != 5
        assert len(result) == 1
        assert result['id'].values[0] == 2

    def test_referenced_column_not_found(self):
        """Test that referencing non-existent column raises QueryException."""
        df = pd.DataFrame({
            'id': [1, 2],
            'valor_a': [10, 5],
        })

        _filter = [
            {
                'column': 'valor_a',
                'expression': '>',
                'value': {'$column': 'nonexistent_column'}
            }
        ]

        with pytest.raises(QueryException) as exc_info:
            create_filter(_filter, df)

        assert "nonexistent_column" in str(exc_info.value)

    def test_primary_column_not_found(self):
        """Test that invalid primary column still raises error."""
        df = pd.DataFrame({
            'id': [1, 2],
            'valor_b': [5, 10],
        })

        _filter = [
            {
                'column': 'nonexistent_column',
                'expression': '>',
                'value': {'$column': 'valor_b'}
            }
        ]

        with pytest.raises(QueryException) as exc_info:
            create_filter(_filter, df)

        assert "nonexistent_column" in str(exc_info.value)

    def test_mixed_column_and_scalar_filters(self):
        """Test combining column-to-column and scalar filters."""
        df = pd.DataFrame({
            'id': [1, 2, 3, 4],
            'valor_a': [10, 5, 20, 15],
            'valor_b': [5, 10, 15, 15],
            'status': ['active', 'inactive', 'active', 'active'],
        })

        _filter = [
            {
                'column': 'valor_a',
                'expression': '>',
                'value': {'$column': 'valor_b'}
            },
            {
                'column': 'status',
                'expression': '==',
                'value': 'active'
            }
        ]

        conditions = create_filter(_filter, df)
        result = df.loc[eval(" & ".join(conditions))]

        # valor_a > valor_b AND status == 'active'
        assert len(result) == 2
        assert list(result['id'].values) == [1, 3]

    def test_multiple_column_filters(self):
        """Test multiple column-to-column filters (range check)."""
        df = pd.DataFrame({
            'id': [1, 2, 3, 4, 5],
            'edad': [25, 30, 35, 40, 45],
            'edad_minima': [20, 30, 30, 35, 50],
            'edad_maxima': [30, 35, 40, 45, 50],
        })

        _filter = [
            {
                'column': 'edad',
                'expression': '>=',
                'value': {'$column': 'edad_minima'}
            },
            {
                'column': 'edad',
                'expression': '<=',
                'value': {'$column': 'edad_maxima'}
            }
        ]

        conditions = create_filter(_filter, df)
        result = df.loc[eval(" & ".join(conditions))]

        # edad between edad_minima and edad_maxima
        # Row 1: 25 between 20 and 30 ✓
        # Row 2: 30 between 30 and 35 ✓
        # Row 3: 35 between 30 and 40 ✓
        # Row 4: 40 between 35 and 45 ✓
        # Row 5: 45 between 50 and 50 ✗
        assert len(result) == 4
        assert list(result['id'].values) == [1, 2, 3, 4]

    def test_column_filter_with_null_values(self):
        """Test column filter with NaN values."""
        df = pd.DataFrame({
            'id': [1, 2, 3],
            'col_a': [10.0, None, 20.0],
            'col_b': [5.0, 10.0, 20.0],
        })

        _filter = [
            {
                'column': 'col_a',
                'expression': '>',
                'value': {'$column': 'col_b'}
            }
        ]

        conditions = create_filter(_filter, df)
        result = df.loc[eval(" & ".join(conditions))]

        # Only rows with valid comparisons where col_a > col_b
        # Row 1: 10 > 5 ✓
        # Row 2: NaN > 10 → False (NaN comparisons are False)
        # Row 3: 20 > 20 ✗
        assert len(result) == 1
        assert result['id'].values[0] == 1

    def test_column_filter_with_strings(self):
        """Test column-to-column filter with string columns."""
        df = pd.DataFrame({
            'id': [1, 2, 3],
            'palabra_a': ['apple', 'banana', 'cherry'],
            'palabra_b': ['apple', 'apricot', 'cherry'],
        })

        _filter = [
            {
                'column': 'palabra_a',
                'expression': '==',
                'value': {'$column': 'palabra_b'}
            }
        ]

        conditions = create_filter(_filter, df)
        result = df.loc[eval(" & ".join(conditions))]

        # Only rows where palabras match
        assert len(result) == 2
        assert list(result['id'].values) == [1, 3]


class TestScalarFiltersStillWork:
    """Verify that existing scalar filters still work correctly."""

    def test_scalar_numeric_filter(self):
        """Existing numeric scalar filters should still work."""
        df = pd.DataFrame({
            'id': [1, 2, 3],
            'valor': [10, 20, 30],
        })

        _filter = [
            {
                'column': 'valor',
                'expression': '>',
                'value': 15
            }
        ]

        conditions = create_filter(_filter, df)
        result = df.loc[eval(" & ".join(conditions))]

        assert len(result) == 2
        assert list(result['id'].values) == [2, 3]

    def test_scalar_string_filter(self):
        """Existing string scalar filters should still work."""
        df = pd.DataFrame({
            'id': [1, 2, 3],
            'status': ['active', 'inactive', 'active'],
        })

        _filter = [
            {
                'column': 'status',
                'expression': '==',
                'value': 'active'
            }
        ]

        conditions = create_filter(_filter, df)
        result = df.loc[eval(" & ".join(conditions))]

        assert len(result) == 2
        assert list(result['id'].values) == [1, 3]

    def test_scalar_date_filter(self):
        """Existing date scalar filters should still work."""
        df = pd.DataFrame({
            'id': [1, 2, 3],
            'fecha': pd.to_datetime(['2024-01-10', '2024-02-15', '2024-03-20']),
        })

        _filter = [
            {
                'column': 'fecha',
                'expression': '>=',
                'value': pd.Timestamp('2024-02-01')
            }
        ]

        conditions = create_filter(_filter, df)
        result = df.loc[eval(" & ".join(conditions))]

        assert len(result) == 2
        assert list(result['id'].values) == [2, 3]
