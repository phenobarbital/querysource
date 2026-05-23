import json
import pandas as pd
from pandas import DataFrame
from typing import Union
from datamodel.parsers.json import json_encoder
from ....exceptions import DriverError, QueryException
from .abstract import AbstractOperator

EDA_COLUMNS = [
    "column_name", "dtype", "non_null_count", "null_count", "null_percent",
    "unique_count", "duplicate_percent", "min", "max", "mean", "std",
    "median", "mode", "skewness", "kurtosis", "q1", "q3",
    "memory_usage", "sample_values",
]


class Info(AbstractOperator):
    """Exploratory Data Analysis operator for MultiQuery pipelines.

    Computes extended EDA statistics for every column in every DataFrame
    in the data dictionary. Returns one EDA DataFrame per source (keyed by
    source name), where each row represents one column's profile.

    Usage: Use in a MultiQuery pipeline to profile the shape, types, and
    statistical distribution of intermediate DataFrames. Results are
    returned as a dict of DataFrames compatible with downstream pipeline
    steps (Transform, Filter, Output).

    Attributes:
        output_format (str): Output mode. ``"dataframe"`` (default) returns
            ``dict[str, pd.DataFrame]`` — one EDA DataFrame per source.
            ``"json"`` returns a JSON-serializable dict (legacy-compatible).

    Example:
        {
            "Info": {}
        }
        {
            "Info": {"output_format": "json"}
        }
    """

    def __init__(self, data: dict, **kwargs) -> None:
        self.output_format = kwargs.pop('output_format', 'dataframe')
        super().__init__(data, **kwargs)

    def _compute_column_eda(self, series: pd.Series) -> dict:
        """Compute EDA statistics for a single column (Series)."""
        total = len(series)
        null_count = int(series.isna().sum())
        non_null_count = total - null_count
        null_percent = (null_count / total * 100.0) if total > 0 else 0.0
        unique_count = int(series.nunique(dropna=True))
        duplicate_count = total - unique_count
        duplicate_percent = (duplicate_count / total * 100.0) if total > 0 else 0.0

        is_numeric = pd.api.types.is_numeric_dtype(series)

        # min / max — work for numeric and datetime; None for other types
        try:
            col_min = series.min(skipna=True) if non_null_count > 0 else None
            col_max = series.max(skipna=True) if non_null_count > 0 else None
            # Convert to Python native types for safety
            if col_min is not None and pd.isna(col_min):
                col_min = None
            if col_max is not None and pd.isna(col_max):
                col_max = None
        except Exception:
            col_min = None
            col_max = None

        # Numeric-only stats
        mean = std = median = skewness = kurtosis = q1 = q3 = None
        if is_numeric and non_null_count > 0:
            try:
                mean = float(series.mean(skipna=True))
            except Exception:
                mean = None
            try:
                std = float(series.std(skipna=True))
            except Exception:
                std = None
            try:
                median = float(series.median(skipna=True))
            except Exception:
                median = None
            try:
                skewness = float(series.skew(skipna=True))
            except Exception:
                skewness = None
            try:
                kurtosis = float(series.kurtosis(skipna=True))
            except Exception:
                kurtosis = None
            try:
                q1 = float(series.quantile(0.25))
            except Exception:
                q1 = None
            try:
                q3 = float(series.quantile(0.75))
            except Exception:
                q3 = None

        # mode — works for any type
        try:
            mode_vals = series.mode(dropna=True)
            mode_val = mode_vals.iloc[0] if len(mode_vals) > 0 else None
        except Exception:
            mode_val = None

        # memory_usage (deep)
        mem = int(series.memory_usage(deep=True))

        # sample_values — up to 5 non-null values, truncated to 200 chars each
        try:
            samples = series.dropna().head(5).tolist()
            sample_strs = [str(s)[:200] for s in samples]
            sample_values = json.dumps(sample_strs)
        except Exception:
            sample_values = "[]"

        return {
            "column_name": series.name,
            "dtype": str(series.dtype),
            "non_null_count": non_null_count,
            "null_count": null_count,
            "null_percent": round(null_percent, 4),
            "unique_count": unique_count,
            "duplicate_percent": round(duplicate_percent, 4),
            "min": col_min,
            "max": col_max,
            "mean": mean,
            "std": std,
            "median": median,
            "mode": mode_val,
            "skewness": skewness,
            "kurtosis": kurtosis,
            "q1": q1,
            "q3": q3,
            "memory_usage": mem,
            "sample_values": sample_values,
        }

    async def start(self) -> None:
        """Validate that all data dict values are DataFrames."""
        for name, data in self.data.items():
            if not isinstance(data, DataFrame):
                raise DriverError(
                    f'Wrong type of data for Info, required a Pandas dataframe: {type(data)}'
                )

    async def run(self) -> Union[dict, object]:
        """Compute EDA statistics for each DataFrame in self.data."""
        try:
            eda_results = {}
            for source_name, df in self.data.items():
                if len(df.columns) == 0:
                    # Return an empty EDA DataFrame with correct columns
                    eda_results[source_name] = self._pd.DataFrame(columns=EDA_COLUMNS)
                    continue
                rows = []
                for col in df.columns:
                    rows.append(self._compute_column_eda(df[col]))
                eda_df = self._pd.DataFrame(rows, columns=EDA_COLUMNS)
                # Cast nullable numeric-stat columns to object dtype to preserve
                # Python None (pandas would otherwise coerce None → np.nan in
                # float64 columns, breaking `is None` checks downstream).
                nullable_stat_cols = [
                    "min", "max", "mean", "std", "median", "mode",
                    "skewness", "kurtosis", "q1", "q3",
                ]
                for stat_col in nullable_stat_cols:
                    if stat_col in eda_df.columns:
                        eda_df[stat_col] = eda_df[stat_col].astype(object).where(
                            eda_df[stat_col].notna(), other=None
                        )
                eda_results[source_name] = eda_df

            if self.output_format == 'json':
                # Convert each DataFrame to dict for JSON encoding
                json_ready = {}
                for name, eda_df in eda_results.items():
                    json_ready[name] = eda_df.to_dict(orient='records')
                return json_encoder(json_ready)

            return eda_results

        except (DriverError, QueryException):
            raise
        except Exception as err:
            raise QueryException(
                f"Error computing EDA statistics: {err!s}"
            ) from err
