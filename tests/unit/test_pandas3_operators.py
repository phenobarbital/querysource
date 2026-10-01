"""FEAT-161: MultiQS operators and transformations under pandas 3."""

import pandas as pd
import pytest

from querysource.queries.multi.operators.Concat import Concat
from querysource.queries.multi.operators.GroupBy import GroupBy
from querysource.queries.multi.operators.Join import Join
from querysource.queries.multi.operators.Melt import Melt
from querysource.queries.multi.operators.Merge import Merge
from querysource.queries.multi.operators.filter.flt import Filter
from querysource.queries.multi.transformations.FilterCols import FilterCols
from querysource.queries.multi.transformations.pivot import pivot


@pytest.fixture
def df_pandas3():
    """Return text, nullable, numeric, and non-nanosecond datetime columns."""
    return pd.DataFrame(
        {
            "name": ["a", None, ""],
            "n": [1, None, 3],
            "empty": ["", "", ""],
            "ts": pd.to_datetime(["2026-01-01", None, "2026-01-03"]).as_unit("us"),
        }
    )


@pytest.mark.asyncio
async def test_filtercols_empty_str_column(df_pandas3):
    """FilterCols recognises pandas 3's native string dtype."""
    result = await FilterCols(df_pandas3, expression="all_empty").run()

    assert "empty" not in result.columns
    assert "name" in result.columns


@pytest.mark.asyncio
async def test_flt_datetime_any_resolution(df_pandas3):
    """Filter clean_dates selects datetime columns regardless of resolution."""
    result = await Filter(df_pandas3, clean_dates=True).run()

    assert "ts" in result.columns
    assert str(result["ts"].dtype) == "datetime64[us]"


@pytest.mark.asyncio
async def test_join_pandas3():
    """Join combines nullable string-keyed frames."""
    operator = Join(
        {
            "left": pd.DataFrame({"id": [1, 2], "name": ["a", "b"]}),
            "right": pd.DataFrame({"id": [2, 3], "value": [20, 30]}),
        },
        left="left",
        right="right",
        on="id",
    )

    await operator.start()
    result = await operator.run()

    assert result.to_dict("records") == [{"id": 2, "name": "b", "value": 20}]


@pytest.mark.asyncio
async def test_concat_pandas3():
    """Concat preserves rows from native string columns."""
    operator = Concat(
        {
            "first": pd.DataFrame({"name": ["a"]}),
            "second": pd.DataFrame({"name": [None]}),
        }
    )

    await operator.start()
    result = await operator.run()

    assert result["name"].tolist() == ["a", None]


@pytest.mark.asyncio
async def test_melt_pandas3():
    """Melt handles string-valued wide columns and metadata joins."""
    operator = Melt(
        {
            "wide": pd.DataFrame({"id": [1], "first": ["a"], "second": ["b"]}),
            "metadata": pd.DataFrame(
                {"column_name": ["first", "second"], "label": ["A", "B"]}
            ),
        },
        using="wide",
        id="id",
    )

    await operator.start()
    result = await operator.run()

    assert result["value"].tolist() == ["a", "b"]
    assert result["label"].tolist() == ["A", "B"]


@pytest.mark.asyncio
async def test_groupby_pandas3():
    """GroupBy aggregates rows grouped by a native string column."""
    operator = GroupBy(
        pd.DataFrame({"group": ["a", "a", "b"], "value": [1, 2, 4]}),
        by=["group"],
        columns={"value": "sum"},
    )

    await operator.start()
    result = await operator.run()

    assert result.to_dict("records") == [
        {"group": "a", "value_sum": 3},
        {"group": "b", "value_sum": 4},
    ]


@pytest.mark.asyncio
async def test_merge_pandas3():
    """Merge joins frames containing native string columns."""
    operator = Merge(
        {
            "left": pd.DataFrame({"id": [1, 2], "name": ["a", "b"]}),
            "right": pd.DataFrame({"id": [2], "value": [20]}),
        },
        using="left",
        on="id",
    )

    await operator.start()
    result = await operator.run()

    assert result.to_dict("records") == [{"id": 2, "name": "b", "value": 20}]


@pytest.mark.asyncio
async def test_pivot_pandas3():
    """Pivot reshapes native string keys without dtype-specific failures."""
    operator = pivot(
        pd.DataFrame(
            {
                "product": ["a", "a", "b"],
                "month": ["jan", "feb", "jan"],
                "value": [1, 2, 3],
            }
        ),
        index="product",
        columns="month",
        values="value",
        multilevel=True,
    )

    result = await operator.run()

    assert result.loc[0, "product"] == "a"
    assert result.loc[0, "feb"] == 2
    assert result.loc[0, "jan"] == 1
    assert result.loc[1, "product"] == "b"
    assert pd.isna(result.loc[1, "feb"])
    assert result.loc[1, "jan"] == 3
