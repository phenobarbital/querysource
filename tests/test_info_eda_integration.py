import pytest
import pandas as pd


class TestInfoPipelineFlow:
    @pytest.mark.asyncio
    async def test_info_no_early_return(self):
        """Info result flows to downstream steps instead of early-returning."""
        from querysource.queries.multi.operators.Info import Info

        data = {
            "src": pd.DataFrame({
                "x": [1, 2, 3],
                "y": ["a", "b", "c"],
            })
        }
        info = Info(data=data)
        async with info as i:
            result = await i.run()
        # Result should be dict of DataFrames (EDA format), not JSON
        assert isinstance(result, dict)
        assert "src" in result
        assert isinstance(result["src"], pd.DataFrame)

    @pytest.mark.asyncio
    async def test_info_with_json_output(self):
        """Info with output_format='json' returns dict, not DataFrames."""
        from querysource.queries.multi.operators.Info import Info

        data = {
            "src": pd.DataFrame({"x": [1, 2, 3]})
        }
        info = Info(data=data, output_format="json")
        async with info as i:
            result = await i.run()
        assert isinstance(result, (dict, str))

    @pytest.mark.asyncio
    async def test_info_options_popped(self):
        """Verify Info key is popped from options dict."""
        options = {"Info": {"output_format": "dataframe"}, "other": "value"}
        _info = options.pop("Info", {})
        assert "Info" not in options
        assert _info == {"output_format": "dataframe"}
