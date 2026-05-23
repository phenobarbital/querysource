import pytest
import pandas as pd


@pytest.fixture
def multi_source_data():
    return {
        "sales": pd.DataFrame({
            "region": ["North", "South", "North", None, "East"],
            "revenue": [1000.0, 2500.5, None, 1800.0, 3200.0],
            "units": [10, 25, 15, 18, 32],
        }),
        "inventory": pd.DataFrame({
            "sku": ["A001", "A002", "A001", "A003"],
            "stock": [100, 50, 100, 75],
            "price": [9.99, 24.99, 9.99, 14.99],
        }),
    }


class TestInfoEDAEndToEnd:
    @pytest.mark.asyncio
    async def test_info_standalone(self, multi_source_data):
        """Info with no downstream steps returns dict of EDA DataFrames."""
        from querysource.queries.multi.operators.Info import Info

        info = Info(data=multi_source_data)
        async with info as i:
            result = await i.run()
        assert isinstance(result, dict)
        assert set(result.keys()) == {"sales", "inventory"}
        # Sales has 3 columns → 3 EDA rows
        assert len(result["sales"]) == 3
        # Inventory has 3 columns → 3 EDA rows
        assert len(result["inventory"]) == 3

    @pytest.mark.asyncio
    async def test_info_default_options(self, multi_source_data):
        """'Info': {} (empty options) produces DataFrame output by default."""
        from querysource.queries.multi.operators.Info import Info

        info = Info(data=multi_source_data)
        async with info as i:
            result = await i.run()
        for name, eda_df in result.items():
            assert isinstance(eda_df, pd.DataFrame)
            assert "column_name" in eda_df.columns
            assert "null_percent" in eda_df.columns

    @pytest.mark.asyncio
    async def test_info_json_mode(self, multi_source_data):
        """Info with output_format='json' returns a JSON string."""
        import json as _json
        from querysource.queries.multi.operators.Info import Info

        info = Info(data=multi_source_data, output_format="json")
        async with info as i:
            result = await i.run()
        assert isinstance(result, str)
        decoded = _json.loads(result)
        assert set(decoded.keys()) == {"sales", "inventory"}
        assert isinstance(decoded["sales"], list)
        assert len(decoded["sales"]) == 3  # 3 columns in sales

    @pytest.mark.asyncio
    async def test_info_eda_data_quality(self, multi_source_data):
        """Verify EDA stats are correct for known data."""
        from querysource.queries.multi.operators.Info import Info

        info = Info(data=multi_source_data)
        async with info as i:
            result = await i.run()
        sales_eda = result["sales"]
        # 'region' column: 1 null out of 5
        region_row = sales_eda[sales_eda["column_name"] == "region"].iloc[0]
        assert region_row["null_count"] == 1
        assert abs(region_row["null_percent"] - 20.0) < 0.01
        # 'revenue' column: 1 null out of 5
        revenue_row = sales_eda[sales_eda["column_name"] == "revenue"].iloc[0]
        assert revenue_row["null_count"] == 1
        # 'units' column: 0 nulls, all unique
        units_row = sales_eda[sales_eda["column_name"] == "units"].iloc[0]
        assert units_row["null_count"] == 0
        assert units_row["unique_count"] == 5

    @pytest.mark.asyncio
    async def test_info_backward_compat_invocation(self, multi_source_data):
        """Existing 'Info': {} invocation still works (no required params)."""
        from querysource.queries.multi.operators.Info import Info

        # No kwargs at all — should default to output_format="dataframe"
        info = Info(data=multi_source_data)
        async with info as i:
            result = await i.run()
        assert isinstance(result, dict)
        for v in result.values():
            assert isinstance(v, pd.DataFrame)
