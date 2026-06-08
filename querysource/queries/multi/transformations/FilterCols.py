from typing import Union

import pandas as pd

from ....exceptions import DataNotFound, DriverError
from .abstract import AbstractTransform

SUPPORTED_EXPRESSIONS = frozenset({"all_null", "all_empty", "constant"})


class FilterCols(AbstractTransform):
    """Drop columns matching a predefined data-quality expression.

    Removes columns from a DataFrame based on the content of their values,
    using one of a fixed set of predefined predicates. Unlike ``PluckCols``
    and ``DropCols`` which filter by column name, ``FilterCols`` filters by
    data quality characteristics.

    Usage: Use in a MultiQuery ``Transform`` step to remove columns
    based on their data content — e.g. columns that are entirely null,
    entirely empty, or contain a single constant value.

    Attributes:
        expression: Predefined expression name. Required.
            Supported values:

            - ``"all_null"`` — drop columns where every value is NaN/None.
            - ``"all_empty"`` — drop columns where every value is NaN, None,
              or an empty string.
            - ``"constant"`` — drop columns where all non-null values are
              identical (``nunique(dropna=True) == 1``).  All-null columns
              (``nunique == 0``) are intentionally NOT dropped here; use
              ``"all_null"`` for that purpose.

    Example:
        {"Transform": [{"FilterCols": {"expression": "all_null"}}]}
        {"Transform": [{"FilterCols": {"expression": "all_empty"}}]}
        {"Transform": [{"FilterCols": {"expression": "constant"}}]}
    """

    @classmethod
    def supported_expressions(cls) -> list[str]:
        """Return the data-quality predicates accepted by ``expression``.

        Single source of truth shared by the runtime validation (against
        ``SUPPORTED_EXPRESSIONS``) and the ``FilterCols.catalog.yaml``
        ``expression`` enum (resolved via the ``enum_from_class`` directive).
        """
        return sorted(SUPPORTED_EXPRESSIONS)

    def __init__(self, data: Union[dict, pd.DataFrame], **kwargs) -> None:
        # Pop expression BEFORE super().__init__ so introspection works
        self.expression: str = kwargs.pop('expression', None)
        super(FilterCols, self).__init__(data, **kwargs)
        # Tracks whether start() has been called; prevents a redundant second
        # call when using ``async with obj as o: await o.run()``.
        self._started: bool = False
        if not self.expression:
            raise DriverError(
                "FilterCols: 'expression' attribute is required."
            )
        if self.expression not in SUPPORTED_EXPRESSIONS:
            raise DriverError(
                f"FilterCols: Unknown expression '{self.expression}'. "
                f"Supported expressions: {', '.join(sorted(SUPPORTED_EXPRESSIONS))}."
            )

    async def start(self) -> None:
        """Delegate to parent start() and mark this instance as started."""
        await super().start()
        self._started = True

    def _apply_filter(self, df: pd.DataFrame) -> pd.DataFrame:
        """Apply the expression filter to a single DataFrame."""
        if self.expression == "all_null":
            result = df.dropna(axis=1, how="all")
        elif self.expression == "all_empty":
            # Drop columns where every value is null or the empty string.
            # Checking per-column dtype avoids FutureWarning from
            # df.replace("", pd.NA) on mixed-dtype DataFrames in pandas 2.x.
            def _is_all_null_or_empty(col: pd.Series) -> bool:
                null_mask = col.isnull()
                if col.dtype == object:
                    null_mask = null_mask | (col == "")
                return bool(null_mask.all())

            result = df.drop(
                columns=[c for c in df.columns if _is_all_null_or_empty(df[c])]
            )
        elif self.expression == "constant":
            # Drop columns where all non-null values are identical.
            # nunique == 0 means all-null; those are intentionally left alone
            # here (they belong to the "all_null" expression).
            cols_to_drop = [
                col for col in df.columns
                if df[col].nunique(dropna=True) == 1
            ]
            result = df.drop(columns=cols_to_drop)
        else:
            # Should not reach here due to __init__ validation
            raise DriverError(
                f"FilterCols: Unknown expression '{self.expression}'."
            )

        if len(result.columns) == 0:
            raise DataNotFound(
                f"FilterCols: All columns were removed by expression "
                f"'{self.expression}' — result has no columns."
            )
        return result

    async def run(self) -> Union[dict, pd.DataFrame]:
        """Execute the FilterCols transformation."""
        # Only call start() if __aenter__ hasn't already done so.
        if not self._started:
            await self.start()
        # AbstractTransform.start() validates empty DFs for dict inputs only;
        # check single-DataFrame emptiness here.
        if isinstance(self.data, pd.DataFrame) and self.data.empty:
            raise DataNotFound("FilterCols: Empty DataFrame input.")
        try:
            if isinstance(self.data, dict):
                return {
                    name: self._apply_filter(df)
                    for name, df in self.data.items()
                }
            return self._apply_filter(self.data)
        except (DataNotFound, DriverError):
            raise
        except Exception as err:
            raise DriverError(
                f"FilterCols: Unexpected error during column filter: {err}"
            ) from err
