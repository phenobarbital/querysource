import pandas as pd
from pandas import DataFrame
from ....exceptions import (
    DriverError,
    QueryException
)
from .abstract import AbstractOperator

# Mapping of agg functions to numeric requirement
numeric_required = {
    'count': False,
    'sum': True,
    'mean': True,
    'mad': True,
    'median': True,
    'min': True,
    'max': True,
    'mode': False,
    'size': False,
    'abs': True,
    'prod': True,
    'std': True,
    'var': True,
    'sem': True,
    'nunique': False,
    'unique': False,
    'first': False,
    'last': False,
    'idxmax': True,
    'idxmin': True,
}

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
        self._nan_by_with: str = kwargs.get('by_replace_nan', None)
        super(GroupBy, self).__init__(data, **kwargs)

    async def start(self):
        if not isinstance(self.data, DataFrame):
            raise DriverError(
                f'Wrong type of data for GroupBy, required a Pandas dataframe: {type(self.data)}'
            )

    def check_missing_values(self, agg_cols):
        warnings = []

        # Check grouping columns for missing values
        for col in self._by:
            missing_count = self.data[col].isna().sum()
            if missing_count > 0:
                warnings.append(
                    f"Warning: Grouping column '{col}' has {missing_count} missing value(s)."
                )

        # Check aggregation columns for missing values
        for col in agg_cols.keys():
            missing_count = self.data[col].isna().sum()
            if missing_count > 0:
                warnings.append(
                    f"Warning: Aggregation column '{col}' has {missing_count} missing value(s)."
                )

        # Print all warnings
        if warnings:
            warning = "\n".join(warnings)
            raise ValueError(f'Missing Values on Grouping Columns: {warning}')

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

        # Separate numeric and non-numeric columns
        non_numeric_cols = []
        numeric_cols = []
        for col, funcs in agg_cols.items():
            if all(numeric_required[func] for func in funcs):  # All funcs require numeric
                numeric_cols.append(col)
            elif any(not numeric_required[func] for func in funcs):  # At least one func does not require numeric
                non_numeric_cols.append(col)

        # Convert only required numeric columns
        for col in numeric_cols:
            self.data[col] = pd.to_numeric(self.data[col], errors='coerce')
        self.data[list(numeric_cols)] = self.data[list(numeric_cols)].fillna(0)

        # Prepare normal aggregation dictionary
        agg_dict = {col: funcs for col, funcs in agg_cols.items()}

        if self._nan_by_with is not None:
            # fill self._by columns with the value of self._nan_by_with
            self.data[self._by] = self.data[self._by].fillna(self._nan_by_with)

        # Performing the regular grouping and aggregation
        self.check_missing_values(agg_cols)

        grouped = self.data[self._by + list(agg_cols.keys())].groupby(self._by)
        df = grouped.agg(agg_dict).reset_index()

        # Handle special aggregations
        if special_agg_cols:
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
        return df
