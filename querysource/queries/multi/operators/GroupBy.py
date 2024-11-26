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
    supported_functions = [
        'avg_first_last'
    ]

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

        # Separate normal and special aggregations
        agg_cols = {}
        special_agg_cols = {}

        for col, funcs in self._columns.items():
            if col in self.data.columns:
                if not isinstance(funcs, list):
                    funcs = [funcs]
                normal_funcs = [func for func in funcs if func not in self.supported_functions]
                special_funcs = [func for func in funcs if func in self.supported_functions]
                if normal_funcs:
                    agg_cols[col] = normal_funcs
                if special_funcs:
                    special_agg_cols[col] = special_funcs

        print('COLS > ', agg_cols)
        print('SP COLS > ', special_agg_cols)

        # Prepare normal aggregation dictionary
        agg_dict = {col: funcs for col, funcs in agg_cols.items()}

        # Performing the regular grouping and aggregation
        grouped = self.data[self._by + list(agg_cols.keys())].groupby(self._by)
        df = grouped.agg(agg_dict)

        # Handle special aggregations
        for col, funcs in special_agg_cols.items():
            for func in funcs:
                if func == "avg_first_last":
                    special_result = grouped[col].apply(lambda x: (x.iloc[0] + x.iloc[-1]) / 2)
                    df[f"{col}_avg_first_last"] = special_result

        # Flatten multi-index columns and generate clean names
        df.columns = [
            f"{col[0]}_{col[1]}" if isinstance(col, tuple) and col[1] else col[0]
            for col in df.columns
        ]
        # Reset index to make grouped columns regular columns
        return df.reset_index()
