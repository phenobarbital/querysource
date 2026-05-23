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
        """Info with output_format='json' returns a JSON string, not DataFrames."""
        import json as _json
        from querysource.queries.multi.operators.Info import Info

        data = {
            "src": pd.DataFrame({"x": [1, 2, 3]})
        }
        info = Info(data=data, output_format="json")
        async with info as i:
            result = await i.run()
        assert isinstance(result, str)
        decoded = _json.loads(result)
        assert "src" in decoded
        assert isinstance(decoded["src"], list)

    @pytest.mark.asyncio
    async def test_info_options_popped(self):
        """Verify Info key is popped from options dict."""
        options = {"Info": {"output_format": "dataframe"}, "other": "value"}
        _info = options.pop("Info", {})
        assert "Info" not in options
        assert _info == {"output_format": "dataframe"}

    @pytest.mark.asyncio
    async def test_multiqs_dispatch_no_early_return(self):
        """MultiQS pipeline with Info: EDA DataFrames flow through, not JSON str."""
        from querysource.queries.multi import MultiQS

        # Build a MultiQS instance with return_all=True so the dict is not
        # collapsed to a single DataFrame by Step 4 when there's only one source.
        # query= dict is consumed by __init__: queries key sets _queries (truthy, passes guard).
        # We then override _queries = {} after init so the thread-based source loop is skipped.
        qs = MultiQS(
            query={
                "queries": {"placeholder": {}},
                "Info": {},
            },
            return_all=True,
        )
        # Manually inject pre-built DataFrames into the queue so no real DB call happens
        test_df = pd.DataFrame({"x": [1, 2, 3], "y": ["a", "b", "c"]})
        await qs._queue.put({"src": test_df})

        # Override _queries to empty so the thread-based source loop is skipped
        qs._queries = {}

        result, options = await qs.query()

        # Info result should be dict[str, DataFrame], not a JSON string
        assert isinstance(result, dict)
        assert "src" in result
        assert isinstance(result["src"], pd.DataFrame)
        # EDA frame has one row per column (2 columns: x, y)
        assert len(result["src"]) == 2
        assert "column_name" in result["src"].columns
