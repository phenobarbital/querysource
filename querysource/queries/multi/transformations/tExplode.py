"""tExplode — Explode a column of lists or dicts into multiple rows.

This transformation converts a column containing Python lists or dictionaries
into multiple rows. It supports two execution modes:

- **Standard mode** (default): calls ``DataFrame.explode()``, optionally
  normalises dict values via ``pandas.json_normalize``, and optionally drops
  the source column.
- **Advanced mode** (``advanced_mode=True``): tracks parent row indices, only
  explodes non-empty lists, normalises dicts, propagates selected parent
  column values to child rows, and concatenates parent + child DataFrames.

Auto-discovered by ``get_transform_module("tExplode")`` and
``ComponentRegistry.discover_all()`` because the module stem equals the
class name (``tExplode.py`` → ``tExplode``).

Usage example:

    {
        "Transform": [
            {
                "tExplode": {
                    "column": "tags",
                    "drop_original": false,
                    "explode_dataset": true,
                    "advanced_mode": false,
                    "propagate_columns": []
                }
            }
        ]
    }
"""
from typing import Union

import pandas as pd
from pandas import json_normalize

from ....exceptions import (
    DataNotFound,
    DriverError,
    QueryException,
)
from .abstract import AbstractTransform


class tExplode(AbstractTransform):
    """Explode a column of lists or dicts into multiple rows.

    Standard mode calls ``DataFrame.explode()`` and, when values are dicts,
    normalises them into columns via ``pandas.json_normalize``. Advanced mode
    tracks parent row indices, preserves rows with empty lists, propagates
    selected parent columns onto child rows, and concatenates parent + child
    DataFrames.

    Usage: Use in a MultiQuery ``Transform`` step to expand a list- or
    dict-valued column into multiple rows.

    Attributes:
        column: Name of the column to explode. **Required.**
        drop_original: When ``True``, remove the source column from the
            result. Default: ``False``.
        explode_dataset: When ``True`` and the column contains dictionaries,
            expand the dicts into separate columns via
            ``pandas.json_normalize``. Default: ``True``.
        advanced_mode: Enable enhanced processing — parent-index tracking,
            empty-list preservation, and column propagation. Default: ``False``.
        propagate_columns: List of column names whose values should be copied
            from the parent row to each of its child rows (only in
            ``advanced_mode``). Default: ``[]``.

    Example:
        {
            "Transform": [
                {
                    "tExplode": {
                        "column": "tags",
                        "drop_original": false,
                        "explode_dataset": true,
                        "advanced_mode": false,
                        "propagate_columns": []
                    }
                }
            ]
        }
    """

    def __init__(self, data: Union[dict, pd.DataFrame], **kwargs) -> None:
        """Initialise tExplode and pop transform-specific kwargs."""
        # Pop all tExplode-specific kwargs BEFORE calling super().__init__()
        # so they are not forwarded to AbstractMulti.__init__() as setattr().
        self.column: str = kwargs.pop("column", None)
        self.drop_original: bool = kwargs.pop("drop_original", False)
        self.explode_dataset: bool = kwargs.pop("explode_dataset", True)
        self.advanced_mode: bool = kwargs.pop("advanced_mode", False)
        self.propagate_columns: list = kwargs.pop("propagate_columns", [])

        # Validate required parameter before calling super().__init__()
        if not self.column:
            raise DriverError(
                "tExplode: 'column' is a required parameter — specify the column to explode."
            )

        super().__init__(data, **kwargs)

    # ------------------------------------------------------------------
    # Public lifecycle
    # ------------------------------------------------------------------

    async def run(self) -> Union[dict, pd.DataFrame]:
        """Validate input, explode the column, and return the result.

        Dispatches to :meth:`_explode_dataframe` for each DataFrame in
        the input (handles both single-DataFrame and dict-of-DataFrames).
        """
        await self.start()

        try:
            if isinstance(self.data, dict):
                result: dict[str, pd.DataFrame] = {}
                for key, df in self.data.items():
                    result[key] = self._explode_dataframe(df)
                return result

            return self._explode_dataframe(self.data)

        except (DataNotFound, DriverError):
            raise
        except Exception as err:
            raise QueryException(
                f"tExplode: unexpected error processing DataFrame: {err}"
            ) from err

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _explode_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        """Apply the explosion logic to a single DataFrame.

        Parameters
        ----------
        df:
            The DataFrame to transform.

        Returns
        -------
        pd.DataFrame
            The exploded DataFrame.

        Raises
        ------
        DriverError
            If ``self.column`` does not exist in ``df``.
        DataNotFound
            If the resulting DataFrame is empty.
        """
        if self.column not in df.columns:
            raise DriverError(
                f"tExplode: column '{self.column}' not found in DataFrame. "
                f"Available columns: {list(df.columns)}"
            )

        if self.advanced_mode:
            result = self._explode_advanced(df)
        else:
            result = self._explode_standard(df)

        if result.empty:
            raise DataNotFound("tExplode: Result is an empty DataFrame.")

        # Warn when the result is very large relative to the input
        input_rows = len(df)
        output_rows = len(result)
        if input_rows > 0 and output_rows > input_rows * 10:
            self.logger.warning(
                "tExplode: result row count (%d) is more than 10× the input (%d). "
                "Consider whether this is expected.",
                output_rows,
                input_rows,
            )

        self.logger.debug(
            "tExplode: exploded '%s' — input rows: %d, output rows: %d",
            self.column,
            input_rows,
            output_rows,
        )
        return result

    def _column_contains_dicts(self, df: pd.DataFrame) -> bool:
        """Return True if the first non-null value in ``self.column`` is a dict.

        This distinguishes direct dict columns (use json_normalize) from list
        columns whose exploded values may be dicts (use explode + json_normalize).
        """
        non_null = df[self.column].dropna()
        if non_null.empty:
            return False
        return isinstance(non_null.iloc[0], dict)

    def _explode_standard(self, df: pd.DataFrame) -> pd.DataFrame:
        """Standard-mode explosion.

        Behaviour depends on the column's value type:

        **List column** (most common):
        1. ``DataFrame.explode(column)`` to expand list cells into rows.
        2. Optionally normalise dict values via ``json_normalize``
           (when ``explode_dataset=True`` and exploded values are dicts).
        3. Optionally drop the source column (when ``drop_original=True``).

        **Dict column** (column contains dicts directly, not inside lists):
        - With ``explode_dataset=True``: apply ``json_normalize`` to expand
          dict keys into columns. The original dict column is replaced/dropped.
        - With ``explode_dataset=False``: return the DataFrame unchanged
          (dicts stay as values).

        Note: If a list column contains a mix of lists and scalars,
        ``DataFrame.explode()`` handles this gracefully — scalar values remain
        as-is and are not duplicated.
        """
        # Detect whether the column contains dicts directly (not in lists).
        if self._column_contains_dicts(df):
            return self._normalize_dict_column(df)

        # Step 1: Explode the list column
        exploded = df.explode(self.column).reset_index(drop=True)

        if self.explode_dataset:
            # Step 2: Normalise dict values into separate columns.
            # Drop NaN entries before normalising to avoid type errors.
            valid_mask = exploded[self.column].notna()
            valid_items = exploded.loc[valid_mask, self.column]

            if not valid_items.empty and isinstance(valid_items.iloc[0], dict):
                normalized = json_normalize(valid_items.tolist())
                normalized.index = valid_items.index

                if self.drop_original:
                    exploded = exploded.drop(columns=[self.column])

                result = pd.concat([exploded, normalized], axis=1)
            else:
                # Values after explode are not dicts — nothing to normalise.
                if self.drop_original:
                    exploded = exploded.drop(columns=[self.column])
                result = exploded
        else:
            if self.drop_original:
                exploded = exploded.drop(columns=[self.column])
            result = exploded

        return result

    def _normalize_dict_column(self, df: pd.DataFrame) -> pd.DataFrame:
        """Handle the case where the column directly contains dict values.

        With ``explode_dataset=True``: expand dict keys into separate columns
        via ``json_normalize``, keeping all other columns.
        With ``explode_dataset=False``: return the DataFrame as-is (dicts
        remain as column values — no explosion occurs).

        Parameters
        ----------
        df:
            The input DataFrame whose ``self.column`` contains dict values.
        """
        if not self.explode_dataset:
            # Dicts stay as values — return unchanged.
            return df.copy()

        # Expand dict keys into columns
        valid_mask = df[self.column].notna()
        valid_items = df.loc[valid_mask, self.column]
        normalized = json_normalize(valid_items.tolist())
        normalized.index = valid_items.index

        # Build the result: other columns + normalized dict columns
        other_cols = df.drop(columns=[self.column])
        result = pd.concat([other_cols, normalized], axis=1).reset_index(drop=True)
        return result

    def _explode_advanced(self, df: pd.DataFrame) -> pd.DataFrame:
        """Advanced-mode explosion with parent tracking and column propagation.

        Steps:
        1. Add ``_parent_idx`` helper column to track parent row identity.
        2. Identify rows with non-empty lists (rows with empty lists are kept
           in the parent DataFrame and are NOT exploded, preserving data).
        3. Explode filtered rows and normalise dicts (when ``explode_dataset=True``).
        4. Build the union of parent + JSON column names.
        5. For ``propagate_columns``: copy parent values into child rows.
        6. Remove ``_parent_idx`` helper.
        7. Concatenate parent (original) + child (exploded) DataFrames.
        8. Optionally drop the source column.
        """
        if not self.explode_dataset:
            # Advanced mode without dict normalisation — simple explode
            # For dict columns, just return as-is; for list columns, explode.
            if self._column_contains_dicts(df):
                return df.copy()
            result = df.explode(self.column).reset_index(drop=True)
            if self.drop_original:
                result = result.drop(columns=[self.column])
            return result

        # If the column contains dicts directly (not in lists), delegate to
        # the dict normalization path with parent tracking.
        if self._column_contains_dicts(df):
            return self._advanced_normalize_dict_column(df)

        # Step 1: Add parent index tracker
        working_df = df.copy()
        working_df["_parent_idx"] = working_df.index

        # Step 2: Select rows with non-empty lists to explode
        non_empty_mask = working_df[self.column].apply(
            lambda x: isinstance(x, list) and len(x) > 0
        )
        to_explode = working_df[non_empty_mask]

        # Step 3: Explode non-empty rows and normalise dicts
        exploded_df = to_explode.explode(self.column).reset_index(drop=True)
        parent_idx_series = exploded_df["_parent_idx"]

        valid_mask = exploded_df[self.column].notna()
        valid_items = exploded_df.loc[valid_mask, self.column]

        if not valid_items.empty:
            normalized_df = json_normalize(valid_items.tolist())
            normalized_df.index = valid_items.index

            # Step 4: Build union of all column names (excluding helper)
            parent_columns = set(working_df.columns) - {"_parent_idx"}
            json_columns = set(normalized_df.columns)
            all_columns = sorted(parent_columns | json_columns)

            # Ensure all target columns exist in exploded_df
            for col in all_columns:
                if col not in exploded_df.columns:
                    exploded_df[col] = None

            # Step 5: Assign values for child rows
            child_idx = normalized_df.index

            for col in all_columns:
                if col in normalized_df.columns:
                    # Fill from JSON-normalised data
                    exploded_df.loc[child_idx, col] = normalized_df[col].values
                elif self.propagate_columns and col in self.propagate_columns:
                    # Step 5 (propagation): copy parent row value to child
                    for idx in child_idx:
                        parent_row_idx = parent_idx_series.loc[idx]
                        if col in df.columns:
                            exploded_df.at[idx, col] = df.at[parent_row_idx, col]
                        else:
                            exploded_df.at[idx, col] = None
                else:
                    # Column exists in parent but not in child JSON — set to None
                    if col not in exploded_df.columns or col not in (parent_columns - json_columns):
                        pass  # Already None or already correct from explode
        else:
            # No valid items to normalise — just use exploded_df as-is
            pass

        # Step 6: Remove _parent_idx helper from both DataFrames
        if "_parent_idx" in exploded_df.columns:
            exploded_df = exploded_df.drop(columns=["_parent_idx"])
        parent_df = working_df.drop(columns=["_parent_idx"])

        # Step 7: Concatenate parent (original) + child (exploded) DataFrames
        result = pd.concat([parent_df, exploded_df], ignore_index=True)

        # Step 8: Optionally drop source column
        if self.drop_original and self.column in result.columns:
            result = result.drop(columns=[self.column])

        return result

    def _advanced_normalize_dict_column(self, df: pd.DataFrame) -> pd.DataFrame:
        """Advanced mode for columns containing dicts directly (not in lists).

        In this case there is no list to explode — instead we normalize the
        dict values into columns and concatenate parent + child rows.
        ``propagate_columns`` values are copied from the parent to the child row.

        Each parent row produces one child row (its dict expanded into columns),
        so the result has 2× as many rows as the input: parent rows (original)
        + child rows (expanded dicts).

        Parameters
        ----------
        df:
            The input DataFrame whose ``self.column`` contains dict values.
        """
        working_df = df.copy()
        working_df["_parent_idx"] = working_df.index

        valid_mask = working_df[self.column].notna()
        valid_items = working_df.loc[valid_mask, self.column]

        if valid_items.empty:
            working_df = working_df.drop(columns=["_parent_idx"])
            if self.drop_original and self.column in working_df.columns:
                working_df = working_df.drop(columns=[self.column])
            return working_df

        # Normalise dicts into columns
        normalized_df = json_normalize(valid_items.tolist())
        normalized_df.index = valid_items.index

        # Build child DataFrame: start from the exploded (non-dict) rows,
        # add the normalized dict columns.
        child_df = working_df.loc[valid_mask].copy()

        for col in normalized_df.columns:
            child_df[col] = normalized_df[col].values

        # Apply propagate_columns: copy parent values if not already in child
        if self.propagate_columns:
            for col in self.propagate_columns:
                if col in df.columns and col not in normalized_df.columns:
                    for idx in valid_items.index:
                        parent_row_idx = working_df.at[idx, "_parent_idx"]
                        child_df.at[idx, col] = df.at[parent_row_idx, col]

        # Remove helper column
        parent_df = working_df.drop(columns=["_parent_idx"])
        if "_parent_idx" in child_df.columns:
            child_df = child_df.drop(columns=["_parent_idx"])

        # Concatenate parent + child
        result = pd.concat([parent_df, child_df], ignore_index=True)

        # Optionally drop source column
        if self.drop_original and self.column in result.columns:
            result = result.drop(columns=[self.column])

        return result
