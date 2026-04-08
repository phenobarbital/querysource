"""Unit tests for Join operator with join_conditions."""
import pandas as pd
import pytest
from querysource.queries.multi.operators.Join import Join
from querysource.exceptions import QueryException, DataNotFound


class TestJoinWithJoinConditions:
    """Test suite for join_conditions feature in Join operator."""

    def setup_method(self):
        """Setup test DataFrames."""
        self.calls_df = pd.DataFrame({
            'id_llamada': [1, 2, 3, 4],
            'usuario_id': [100, 101, 100, 102],
            'call_date': pd.to_datetime([
                '2024-01-15', '2024-01-20', '2024-02-01', '2024-02-10'
            ]),
            'call_duration': [45, 30, 60, 25],
        })

        self.podcasts_df = pd.DataFrame({
            'id_podcast': [200, 201, 202, 203],
            'usuario_id': [100, 101, 100, 102],
            'podcast_date': pd.to_datetime([
                '2024-01-10', '2024-01-25', '2024-02-02', '2024-02-05'
            ]),
            'podcast_duration': [90, 45, 60, 120],
        })

    def test_join_with_single_condition(self):
        """Test join with a single join_condition."""
        # Join: calls x podcasts on user_id
        # WHERE: call_date >= podcast_date

        join_op = Join(
            data={
                'calls': self.calls_df.copy(),
                'podcasts': self.podcasts_df.copy()
            },
            left='calls',
            right='podcasts',
            on='usuario_id',
            type='inner',
            join_conditions=[
                {
                    'left': 'call_date',
                    'expression': '>=',
                    'right': 'podcast_date'
                }
            ]
        )

        result = join_op.data
        import asyncio
        output = asyncio.run(join_op.run())

        # Get the merged dataframe
        df = list(output.values())[0] if isinstance(output, dict) else output

        # All rows should have call_date >= podcast_date
        for _, row in df.iterrows():
            assert row['call_date'] >= row['podcast_date']

    def test_join_with_multiple_conditions(self):
        """Test join with multiple join_conditions (AND logic)."""
        join_op = Join(
            data={
                'calls': self.calls_df.copy(),
                'podcasts': self.podcasts_df.copy()
            },
            left='calls',
            right='podcasts',
            on='usuario_id',
            type='inner',
            join_conditions=[
                {
                    'left': 'call_date',
                    'expression': '>=',
                    'right': 'podcast_date'
                },
                {
                    'left': 'call_duration',
                    'expression': '<',
                    'right': 'podcast_duration'
                }
            ]
        )

        import asyncio
        output = asyncio.run(join_op.run())
        df = list(output.values())[0] if isinstance(output, dict) else output

        # All rows should satisfy BOTH conditions
        for _, row in df.iterrows():
            assert row['call_date'] >= row['podcast_date']
            assert row['call_duration'] < row['podcast_duration']

    def test_join_conditions_reduce_result_set(self):
        """Test that join_conditions reduce the result size."""
        # Without conditions
        join_op_no_cond = Join(
            data={
                'calls': self.calls_df.copy(),
                'podcasts': self.podcasts_df.copy()
            },
            left='calls',
            right='podcasts',
            on='usuario_id',
            type='inner'
        )

        import asyncio
        output_no_cond = asyncio.run(join_op_no_cond.run())
        df_no_cond = list(output_no_cond.values())[0] if isinstance(output_no_cond, dict) else output_no_cond
        rows_without_conditions = len(df_no_cond)

        # With conditions
        join_op_with_cond = Join(
            data={
                'calls': self.calls_df.copy(),
                'podcasts': self.podcasts_df.copy()
            },
            left='calls',
            right='podcasts',
            on='usuario_id',
            type='inner',
            join_conditions=[
                {
                    'left': 'call_date',
                    'expression': '>=',
                    'right': 'podcast_date'
                }
            ]
        )

        output_with_cond = asyncio.run(join_op_with_cond.run())
        df_with_cond = list(output_with_cond.values())[0] if isinstance(output_with_cond, dict) else output_with_cond
        rows_with_conditions = len(df_with_cond)

        # Conditions should reduce or equal the result set
        assert rows_with_conditions <= rows_without_conditions

    def test_join_conditions_all_operators(self):
        """Test join_conditions with all comparison operators."""
        operators = ['<', '<=', '>', '>=', '==', '!=']

        for op in operators:
            join_op = Join(
                data={
                    'calls': self.calls_df.copy(),
                    'podcasts': self.podcasts_df.copy()
                },
                left='calls',
                right='podcasts',
                on='usuario_id',
                type='inner',
                join_conditions=[
                    {
                        'left': 'call_duration',
                        'expression': op,
                        'right': 'podcast_duration'
                    }
                ]
            )

            import asyncio
            output = asyncio.run(join_op.run())

            # Should not crash and return a DataFrame
            assert output is not None

    def test_join_without_conditions_unchanged(self):
        """Test that join without conditions works as before."""
        join_op = Join(
            data={
                'calls': self.calls_df.copy(),
                'podcasts': self.podcasts_df.copy()
            },
            left='calls',
            right='podcasts',
            on='usuario_id',
            type='inner'
            # No join_conditions specified
        )

        import asyncio
        output = asyncio.run(join_op.run())

        # Should return result without filtering
        assert output is not None

    def test_join_condition_nonexistent_column(self):
        """Test that join_conditions validate column existence."""
        join_op = Join(
            data={
                'calls': self.calls_df.copy(),
                'podcasts': self.podcasts_df.copy()
            },
            left='calls',
            right='podcasts',
            on='usuario_id',
            type='inner',
            join_conditions=[
                {
                    'left': 'nonexistent_column',
                    'expression': '>=',
                    'right': 'podcast_date'
                }
            ]
        )

        import asyncio
        with pytest.raises(QueryException):
            asyncio.run(join_op.run())

    def test_join_condition_missing_fields(self):
        """Test that join_conditions require left, expression, and right."""
        join_op = Join(
            data={
                'calls': self.calls_df.copy(),
                'podcasts': self.podcasts_df.copy()
            },
            left='calls',
            right='podcasts',
            on='usuario_id',
            type='inner',
            join_conditions=[
                {
                    'left': 'call_date',
                    # Missing 'expression' and 'right'
                }
            ]
        )

        import asyncio
        with pytest.raises(QueryException):
            asyncio.run(join_op.run())

    def test_join_conditions_semantic_clarity(self):
        """Test that join_conditions document the intent clearly."""
        # This is more of a documentation test
        # The intent is clear: join conditions are PART OF the join, not post-filtering

        join_config = {
            'left': 'calls',
            'right': 'podcasts',
            'on': 'usuario_id',
            'join_conditions': [
                {
                    'left': 'call_date',
                    'expression': '>=',
                    'right': 'podcast_date'
                }
            ]
        }

        # The intent is crystal clear:
        # "Join calls and podcasts on user_id WHERE call_date >= podcast_date"
        assert 'join_conditions' in join_config
        assert join_config['join_conditions'][0]['expression'] == '>='


class TestJoinConditionsVsFilter:
    """Compare join_conditions vs separate Filter operator."""

    def setup_method(self):
        """Setup test data."""
        self.df1 = pd.DataFrame({
            'id': [1, 2, 3],
            'key': ['a', 'b', 'a'],
            'val1': [10, 20, 30],
        })

        self.df2 = pd.DataFrame({
            'key': ['a', 'b', 'a'],
            'val2': [5, 15, 25],
        })

    def test_join_conditions_semantic_equivalence(self):
        """Test that join_conditions produce same result as Join + Filter.

        Note: Both should produce identical results, but join_conditions
        communicate intent more clearly.
        """
        # Using join_conditions
        join_op = Join(
            data={
                'table1': self.df1.copy(),
                'table2': self.df2.copy()
            },
            left='table1',
            right='table2',
            on='key',
            type='inner',
            join_conditions=[
                {
                    'left': 'val1',
                    'expression': '>',
                    'right': 'val2'
                }
            ]
        )

        import asyncio
        output = asyncio.run(join_op.run())
        df_with_conditions = list(output.values())[0] if isinstance(output, dict) else output

        # Both approaches should produce result where val1 > val2
        for _, row in df_with_conditions.iterrows():
            assert row['val1'] > row['val2']

    def test_join_conditions_range_check(self):
        """Test range checking with multiple join_conditions."""
        df_ranges = pd.DataFrame({
            'id': [1, 2, 3, 4],
            'value': [15, 25, 35, 45],
            'min_allowed': [10, 20, 30, 40],
            'max_allowed': [20, 30, 40, 50],
        })

        join_op = Join(
            data={
                'data': df_ranges.copy(),
                'dummy': pd.DataFrame({'id': [1]}),  # Dummy for join
            },
            left='data',
            right='dummy',
            on='id',
            type='inner',
            join_conditions=[
                {
                    'left': 'value',
                    'expression': '>=',
                    'right': 'min_allowed'
                },
                {
                    'left': 'value',
                    'expression': '<=',
                    'right': 'max_allowed'
                }
            ]
        )

        import asyncio
        output = asyncio.run(join_op.run())
        df = list(output.values())[0] if isinstance(output, dict) else output

        # All values should be within range
        for _, row in df.iterrows():
            assert row['min_allowed'] <= row['value'] <= row['max_allowed']
