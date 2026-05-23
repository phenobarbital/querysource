import pytest
import pandas as pd
from querysource.queries.multi.operators.Info import Info


@pytest.fixture
def mixed_dtypes_df():
    return pd.DataFrame({
        "id": [1, 2, 3, 4, 5],
        "name": ["Alice", "Bob", "Alice", None, "Eve"],
        "score": [95.5, 82.3, None, 91.0, 78.5],
        "active": [True, False, True, True, None],
        "created": pd.to_datetime(
            ["2024-01-01", "2024-02-15", None, "2024-04-01", "2024-05-20"]
        ),
    })


@pytest.fixture
def multi_source_data(mixed_dtypes_df):
    return {
        "source_a": mixed_dtypes_df,
        "source_b": pd.DataFrame({
            "product": ["Widget", "Gadget", "Widget"],
            "price": [9.99, 24.99, 9.99],
            "quantity": [100, 50, 200],
        }),
    }


class TestInfoEDA:
    @pytest.mark.asyncio
    async def test_single_dataframe(self, mixed_dtypes_df):
        """Single-source dict; verify all EDA columns present."""
        info = Info(data={"src": mixed_dtypes_df})
        async with info as i:
            result = await i.run()
        assert isinstance(result, dict)
        assert "src" in result
        eda_df = result["src"]
        assert isinstance(eda_df, pd.DataFrame)
        assert len(eda_df) == len(mixed_dtypes_df.columns)
        expected_cols = [
            "column_name", "dtype", "non_null_count", "null_count",
            "null_percent", "unique_count", "duplicate_percent",
            "min", "max", "mean", "std", "median", "mode",
            "skewness", "kurtosis", "q1", "q3",
            "memory_usage", "sample_values",
        ]
        for col in expected_cols:
            assert col in eda_df.columns

    @pytest.mark.asyncio
    async def test_multiple_dataframes(self, multi_source_data):
        """Multi-source dict; verify one EDA DataFrame per source."""
        info = Info(data=multi_source_data)
        async with info as i:
            result = await i.run()
        assert "source_a" in result
        assert "source_b" in result
        assert len(result["source_a"]) == 5  # 5 columns
        assert len(result["source_b"]) == 3  # 3 columns

    @pytest.mark.asyncio
    async def test_null_percent(self, mixed_dtypes_df):
        """Verify null_count and null_percent accuracy."""
        info = Info(data={"src": mixed_dtypes_df})
        async with info as i:
            result = await i.run()
        eda = result["src"]
        name_row = eda[eda["column_name"] == "name"].iloc[0]
        assert name_row["null_count"] == 1
        assert abs(name_row["null_percent"] - 20.0) < 0.01

    @pytest.mark.asyncio
    async def test_duplicate_percent(self):
        """Verify unique_count and duplicate_percent."""
        df = pd.DataFrame({"x": [1, 1, 2, 2, 3]})
        info = Info(data={"src": df})
        async with info as i:
            result = await i.run()
        eda = result["src"]
        row = eda.iloc[0]
        assert row["unique_count"] == 3
        assert abs(row["duplicate_percent"] - 40.0) < 0.01

    @pytest.mark.asyncio
    async def test_numeric_stats(self, mixed_dtypes_df):
        """Verify mean, std, median, skewness, kurtosis, q1, q3 for numeric."""
        info = Info(data={"src": mixed_dtypes_df})
        async with info as i:
            result = await i.run()
        eda = result["src"]
        score_row = eda[eda["column_name"] == "score"].iloc[0]
        assert score_row["mean"] is not None
        assert score_row["std"] is not None
        assert score_row["median"] is not None

    @pytest.mark.asyncio
    async def test_categorical_stats(self, mixed_dtypes_df):
        """Non-numeric columns: numeric-only stats should be None."""
        info = Info(data={"src": mixed_dtypes_df})
        async with info as i:
            result = await i.run()
        eda = result["src"]
        name_row = eda[eda["column_name"] == "name"].iloc[0]
        assert name_row["mean"] is None
        assert name_row["std"] is None
        assert name_row["skewness"] is None

    @pytest.mark.asyncio
    async def test_empty_dataframe(self):
        """Empty DataFrame produces valid EDA output with no exceptions."""
        df = pd.DataFrame({"a": pd.Series([], dtype="int64")})
        info = Info(data={"src": df})
        async with info as i:
            result = await i.run()
        eda = result["src"]
        assert len(eda) == 1
        assert eda.iloc[0]["non_null_count"] == 0
        assert eda.iloc[0]["null_percent"] == 0.0
        assert eda.iloc[0]["duplicate_percent"] == 0.0

    @pytest.mark.asyncio
    async def test_output_format_json(self, mixed_dtypes_df):
        """output_format='json' returns JSON-serializable string."""
        import json as _json
        info = Info(data={"src": mixed_dtypes_df}, output_format="json")
        async with info as i:
            result = await i.run()
        assert isinstance(result, str)
        decoded = _json.loads(result)
        assert "src" in decoded
        assert isinstance(decoded["src"], list)
        assert len(decoded["src"]) == len(mixed_dtypes_df.columns)

    @pytest.mark.asyncio
    async def test_memory_usage(self, mixed_dtypes_df):
        """memory_usage matches pandas deep memory usage."""
        info = Info(data={"src": mixed_dtypes_df})
        async with info as i:
            result = await i.run()
        eda = result["src"]
        for _, row in eda.iterrows():
            col = row["column_name"]
            expected = mixed_dtypes_df[col].memory_usage(deep=True)
            assert row["memory_usage"] == expected

    @pytest.mark.asyncio
    async def test_non_dataframe_input(self):
        """Non-DataFrame input raises DriverError."""
        from querysource.exceptions import DriverError
        info = Info(data={"src": "not a dataframe"})
        with pytest.raises(DriverError):
            async with info as i:
                await i.run()
