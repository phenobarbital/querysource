"""Integration tests for Join + Column Filter workflows."""
import pandas as pd
import pytest
from querysource.types.dt.filters import create_filter


class TestJoinWithColumnFilters:
    """Test realistic scenarios combining Join and Column Filters."""

    def setup_method(self):
        """Setup test data simulating calls and podcasts."""
        # Calls DataFrame
        self.calls_df = pd.DataFrame({
            'id_llamada': [1, 2, 3, 4],
            'usuario_id': [100, 101, 100, 102],
            'fecha_llamada': pd.to_datetime([
                '2024-01-15', '2024-01-20', '2024-02-01', '2024-02-10'
            ]),
            'duracion_llamada': [45, 30, 60, 25],
            'region': ['LATAM', 'LATAM', 'NA', 'LATAM'],
        })

        # Podcasts DataFrame
        self.podcasts_df = pd.DataFrame({
            'id_podcast': [200, 201, 202, 203],
            'usuario_id': [100, 101, 100, 102],
            'fecha_creacion': pd.to_datetime([
                '2024-01-10', '2024-01-25', '2024-02-02', '2024-02-05'
            ]),
            'duracion_podcast': [90, 45, 60, 120],
            'fecha_escuchado': pd.to_datetime([
                '2024-01-20', '2024-01-30', '2024-02-05', '2024-02-15'
            ]),
        })

    def _perform_join(self):
        """Simulate the Join operator output."""
        return pd.merge(
            self.calls_df,
            self.podcasts_df,
            on='usuario_id',
            how='inner'
        )

    def test_podcast_listened_after_creation(self):
        """Test: podcast was listened AFTER it was created."""
        joined_df = self._perform_join()

        # All podcasts should be listened after creation
        _filter = [
            {
                'column': 'fecha_escuchado',
                'expression': '>=',
                'value': {'$column': 'fecha_creacion'}
            }
        ]

        conditions = create_filter(_filter, joined_df)
        df = joined_df  # Make df available for eval
        result = df.loc[eval(" & ".join(conditions))]

        # All rows pass this condition
        assert len(result) == len(joined_df)

    def test_call_before_podcast_created(self):
        """Test: call was made BEFORE the podcast was created."""
        joined_df = self._perform_join()

        _filter = [
            {
                'column': 'fecha_llamada',
                'expression': '<',
                'value': {'$column': 'fecha_creacion'}
            }
        ]

        conditions = create_filter(_filter, joined_df)
        df = joined_df  # Make df available for eval
        result = df.loc[eval(" & ".join(conditions))]

        # Rows where fecha_llamada < fecha_creacion
        # Row 0: 2024-01-15 < 2024-01-10 ✗
        # Row 1: 2024-01-15 < 2024-01-10 ✗
        # Row 2: 2024-01-20 < 2024-01-25 ✓
        # Row 3: 2024-01-20 < 2024-01-25 ✓
        # Row 4: 2024-01-15 < 2024-02-02 ✓
        # ... (multiple combinations due to join)
        assert len(result) > 0

    def test_call_duration_less_than_podcast(self):
        """Test: call duration is shorter than podcast duration."""
        joined_df = self._perform_join()

        _filter = [
            {
                'column': 'duracion_llamada',
                'expression': '<',
                'value': {'$column': 'duracion_podcast'}
            }
        ]

        conditions = create_filter(_filter, joined_df)
        df = joined_df  # Make df available for eval
        result = df.loc[eval(" & ".join(conditions))]

        # Check that all results satisfy the condition
        for _, row in result.iterrows():
            assert row['duracion_llamada'] < row['duracion_podcast']

    def test_complex_multi_condition_filter_after_join(self):
        """Test: realistic complex filter after join."""
        joined_df = self._perform_join()

        # Conditions:
        # 1. Podcast listened after creation
        # 2. Call duration < podcast duration
        # 3. Region is LATAM (scalar condition)
        _filter = [
            {
                'column': 'fecha_escuchado',
                'expression': '>=',
                'value': {'$column': 'fecha_creacion'}
            },
            {
                'column': 'duracion_llamada',
                'expression': '<',
                'value': {'$column': 'duracion_podcast'}
            },
            {
                'column': 'region',
                'expression': '==',
                'value': 'LATAM'
            }
        ]

        conditions = create_filter(_filter, joined_df)
        df = joined_df  # Make df available for eval
        result = df.loc[eval(" & ".join(conditions))]

        # Verify all conditions are met
        for _, row in result.iterrows():
            assert row['fecha_escuchado'] >= row['fecha_creacion']
            assert row['duracion_llamada'] < row['duracion_podcast']
            assert row['region'] == 'LATAM'

    def test_date_range_between_columns(self):
        """Test: listening date between call and podcast creation dates."""
        joined_df = self._perform_join()

        # fecha_llamada < fecha_escuchado < fecha_podcast_final
        # Using: fecha_escuchado >= fecha_llamada AND fecha_escuchado < fecha_creacion + 30 days
        _filter = [
            {
                'column': 'fecha_escuchado',
                'expression': '>=',
                'value': {'$column': 'fecha_llamada'}
            }
        ]

        conditions = create_filter(_filter, joined_df)
        df = joined_df  # Make df available for eval
        result = df.loc[eval(" & ".join(conditions))]

        # Verify condition
        for _, row in result.iterrows():
            assert row['fecha_escuchado'] >= row['fecha_llamada']

    def test_filter_with_no_results(self):
        """Test: filter that eliminates all rows."""
        joined_df = self._perform_join()

        # Impossible condition: listening before creation
        _filter = [
            {
                'column': 'fecha_escuchado',
                'expression': '<',
                'value': {'$column': 'fecha_creacion'}
            }
        ]

        conditions = create_filter(_filter, joined_df)
        df = joined_df  # Make df available for eval
        result = df.loc[eval(" & ".join(conditions))]

        # Should be empty (no podcast was listened before creation)
        assert len(result) == 0

    def test_user_specific_filter_after_join(self):
        """Test: filter for specific user with cross-table conditions."""
        joined_df = self._perform_join()

        _filter = [
            {
                'column': 'usuario_id',
                'expression': '==',
                'value': 100
            },
            {
                'column': 'duracion_llamada',
                'expression': '<',
                'value': {'$column': 'duracion_podcast'}
            }
        ]

        conditions = create_filter(_filter, joined_df)
        df = joined_df  # Make df available for eval
        result = df.loc[eval(" & ".join(conditions))]

        # All results should be for usuario_id 100
        assert all(result['usuario_id'] == 100)
        # All should satisfy duration condition
        for _, row in result.iterrows():
            assert row['duracion_llamada'] < row['duracion_podcast']

    def test_comparison_operators_with_joined_data(self):
        """Test various comparison operators on joined data."""
        joined_df = self._perform_join()

        # Test all comparison operators
        operators = ['<', '<=', '>', '>=', '==', '!=']

        for op in operators:
            _filter = [
                {
                    'column': 'duracion_llamada',
                    'expression': op,
                    'value': {'$column': 'duracion_podcast'}
                }
            ]

            conditions = create_filter(_filter, joined_df)
            df = joined_df  # Make df available for eval
            result = df.loc[eval(" & ".join(conditions))]

            # Just verify it doesn't crash and returns something
            assert isinstance(result, pd.DataFrame)

    def test_numeric_range_between_columns(self):
        """Test: numeric value within range from two columns."""
        # Create DataFrame with min/max values
        df = pd.DataFrame({
            'id': [1, 2, 3, 4],
            'valor_actual': [15, 25, 35, 45],
            'valor_minimo': [10, 20, 30, 40],
            'valor_maximo': [20, 30, 40, 50],
        })

        # Find rows where valor_actual is within [valor_minimo, valor_maximo]
        _filter = [
            {
                'column': 'valor_actual',
                'expression': '>=',
                'value': {'$column': 'valor_minimo'}
            },
            {
                'column': 'valor_actual',
                'expression': '<=',
                'value': {'$column': 'valor_maximo'}
            }
        ]

        conditions = create_filter(_filter, df)
        result = df.loc[eval(" & ".join(conditions))]

        # All rows should satisfy the range condition
        assert len(result) == 4
        for _, row in result.iterrows():
            assert row['valor_minimo'] <= row['valor_actual'] <= row['valor_maximo']

    def test_column_filter_after_multi_join(self):
        """Test: filter after joining multiple DataFrames."""
        # Simulate joining 3 tables: calls, podcasts, user_preferences
        calls_df = self.calls_df.copy()
        podcasts_df = self.podcasts_df.copy()

        user_prefs_df = pd.DataFrame({
            'usuario_id': [100, 101, 102],
            'max_duracion_preferida': [60, 30, 90],
            'idioma': ['es', 'pt', 'es'],
        })

        # Join calls + podcasts
        joined = pd.merge(calls_df, podcasts_df, on='usuario_id', how='inner')
        # Join result + preferences
        joined = pd.merge(joined, user_prefs_df, on='usuario_id', how='inner')

        # Filter: actual duration < preferred maximum AND language match
        _filter = [
            {
                'column': 'duracion_llamada',
                'expression': '<=',
                'value': {'$column': 'max_duracion_preferida'}
            }
        ]

        conditions = create_filter(_filter, joined)
        df = joined  # Make df available for eval
        result = df.loc[eval(" & ".join(conditions))]

        # Verify condition
        for _, row in result.iterrows():
            assert row['duracion_llamada'] <= row['max_duracion_preferida']


class TestJoinOutputWithColumnFilters:
    """Test realistic Join output structure with filters."""

    def test_join_suffix_handling_in_filter(self):
        """Test filtering works correctly with join suffixes."""
        # Simulate two tables with overlapping column names
        df1 = pd.DataFrame({
            'id': [1, 2],
            'usuario_id': [100, 101],
            'duracion': [30, 45],
            'fecha': pd.to_datetime(['2024-01-10', '2024-01-20']),
        })

        df2 = pd.DataFrame({
            'usuario_id': [100, 101],
            'duracion': [60, 90],
            'fecha': pd.to_datetime(['2024-01-15', '2024-01-25']),
        })

        # Merge (which adds suffixes)
        merged = pd.merge(df1, df2, on='usuario_id', how='inner', suffixes=('_left', '_right'))

        # Remove _left columns (like Join operator does)
        merged = merged.drop(columns=[col for col in merged.columns if col.endswith('_left')])
        merged = merged.rename(columns={col: col.replace('_right', '') for col in merged.columns if col.endswith('_right')})

        # Now filter using the final column names
        _filter = [
            {
                'column': 'duracion',
                'expression': '<',
                'value': {'$column': 'duracion'}  # This should work or fail gracefully
            }
        ]

        # Filter should work with merged data
        conditions = create_filter(_filter, merged)
        # This might result in false (duracion < duracion is false), but shouldn't crash
        assert isinstance(conditions, list)
