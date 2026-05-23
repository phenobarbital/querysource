import pandas as pd
from pandas import DataFrame
from datamodel.parsers.json import json_encoder
from ....exceptions import (
    DriverError,
    QueryException
)
from .abstract import AbstractOperator

class Info(AbstractOperator):
    """Extract and return detailed schema and sample information about all DataFrames.

    Inspects every DataFrame in the data dictionary and returns a structured
    summary including column names, data types, first-row values, row/column counts,
    and the first 5 rows as sample data.

    Usage: Use in a MultiQuery pipeline as a diagnostic/inspection step to
    understand the shape and contents of intermediate DataFrames. Returns a
    JSON-serializable dict keyed by DataFrame name.

    Attributes:
        No configurable attributes — inspects all DataFrames in the data dict.

    Example:
        {
            "Info": {}
        }
    """
    async def start(self):
        # Validate all inputs are DataFrames
        for name, data in self.data.items():
            if not isinstance(data, DataFrame):
                raise DriverError(
                    f'Wrong type of data for Info, required a Pandas dataframe: {type(data)}'
                )
            self._backend = 'pandas'

    async def run(self):
        try:
            result = {}

            # Process each DataFrame in the input dictionary
            for name, df in self.data.items():
                # Get column information
                columns_info = []

                # Create strings like "column > type > value of first row"
                for col in df.columns:
                    col_type = str(df[col].dtype)
                    first_value = None

                    if not df.empty:
                        first_value = df[col].iloc[0]
                        try:
                            # If it's a scalar, check for null
                            if pd.api.types.is_scalar(first_value):
                                if pd.isnull(first_value):
                                    first_value = "NaN"
                                else:
                                    first_value = str(first_value)
                            else:
                                # If it's a list, array, or something else, stringify safely
                                first_value = str(first_value)
                            if len(first_value) > 100:
                                first_value = first_value[:100] + "..."
                        except Exception as err:
                            first_value = f"Error getting value: {err!s} by {col_type}"
                    column_info = f"{col} > {col_type} > {first_value}"
                    columns_info.append(column_info)

                # Get first 5 rows as a list of dictionaries
                sample_data = []
                if not df.empty:
                    sample_rows = min(5, len(df))
                    sample_df = df.head(sample_rows)
                    for _, row in sample_df.iterrows():
                        row_dict = {}
                        for col in sample_df.columns:
                            row_dict[col] = json_encoder(row[col])
                        sample_data.append(row_dict)

                # Create the result structure for this DataFrame
                df_info = {
                    "columns": columns_info,
                    "num_rows": len(df),
                    "num_columns": len(df.columns),
                    "data": sample_data
                }

                # Add to the overall result
                result[name] = df_info

            return json_encoder(result)

        except Exception as err:
            raise QueryException(
                f"Error getting DataFrame information: {err!s}"
            ) from err
