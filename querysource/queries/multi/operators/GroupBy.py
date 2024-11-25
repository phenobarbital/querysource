from pandas import DataFrame
from ....exceptions import (
    DriverError,
    QueryException
)
from .abstract import AbstractOperator

class GroupBy(AbstractOperator):
    """
    GroupBy making the aggregation of columns based on a list of columns.

    Available Functions:
    +----------+----------------------------------------------+
    | Function | Description                                  |
    +----------+----------------------------------------------+
    | count    | Number of non-null observations              |
    | sum      | Sum of values                                |
    | mean     | Mean of values                               |
    | mad      | Mean absolute deviation                      |
    | median   | Arithmetic median of values                  |
    | min      | Minimum                                      |
    | max      | Maximum                                      |
    | mode     | Mode, Most frequent value(s).                |
    | size     | Total number of values, including nulls.     |
    | abs      | Absolute Value                               |
    | prod     | Product of values                            |
    | std      | Unbiased standard deviation                  |
    | var      | Unbiased variance (Variance of values.)      |
    | sem      | Unbiased standard error of the mean          |
    | nunique  | Count of unique values.                      |
    | unique   | List of unique values.                       |
    | first    | First value in a column.                     |
    | last     | Last value in a column.                      |
    | idxmax   | Index of the first occurrence of the maximum |
    | idxmin   | Index of the first occurrence of the minimum |
    +----------+----------------------------------------------+

    Will be supported on next version (functions with arguments)
    +----------+----------------------------------------------+
    | Function | Description                                  |
    +----------+----------------------------------------------+
    | skew     | Unbiased skewness (3rd moment)               |
    | kurt     | Unbiased kurtosis (4th moment)               |
    | quantile | Sample quantile (value at %)                 |
    | cumsum   | Cumulative sum                               |
    | cumprod  | Cumulative product                           |
    | cummax   | Cumulative maximum                           |
    | cummin   | Cumulative minimum                           |
    +----------+----------------------------------------------+
    """
    def __init__(self, data: dict, **kwargs) -> None:
        self._columns: dict = kwargs.get('columns', {})
        self._by: list = kwargs.get('by', [])
        super(GroupBy, self).__init__(data, **kwargs)

    async def start(self):
        if not isinstance(self.data, DataFrame):
            raise DriverError(
                f'Wrong type of data for GroupBy, required a Pandas dataframe: {type(self.data)}'
            )

    async def run(self):
        # Let's Ensure all columns to group by exist in the DataFrame
        missed_cols = [col for col in self._by if col not in self.data.columns]
        if missed_cols:
            raise KeyError(
                f"Grouping columns {missed_cols} not found in the DataFrame."
            )

        # Ignore any column that is not in the DataFrame
        agg_cols = {col: agg for col, agg in self._columns.items() if col in self.data.columns}

        # Prepare aggregation dictionary for groupby
        agg_dict = {col: ([func] if isinstance(func, str) else func) for col, func in agg_cols.items()}

        # Perform grouping and aggregation
        df = self.data[self._by + list(agg_cols.keys())].groupby(self._by).agg(agg_dict)

        # Flatten multi-index columns and generate clean names
        df.columns = [
            f"{col[0]}_{col[1]}" if col[1] else col[0]
            for col in df.columns
        ]
        # Reset index to make grouped columns regular columns
        return df.reset_index()
