import fnmatch
import re
from typing import Union

import pandas as pd

from ....exceptions import DataNotFound, DriverError
from .abstract import AbstractTransform


class PluckCols(AbstractTransform):
    """Keep only the columns matching any of the specified selectors.

    Applies a whitelist-style column filter to a DataFrame, retaining only
    the columns that match at least one of the provided selectors. Supports
    five matching modes which are all optional but at least one is required;
    matches from all modes are unioned together.

    Usage: Use in a MultiQuery ``Transform`` step to reduce a DataFrame to
    only the desired columns via exact names, glob patterns, regex, or
    prefix/suffix matching.

    Attributes:
        columns: List of exact column names to keep. Optional.
            Raises ``DriverError`` if a named column is not found.
        pattern: Glob/fnmatch pattern string (e.g. ``"revenue_*"``). Optional.
            Non-matching patterns are silently skipped.
        regex: Regular expression pattern string (e.g. ``"^(name|email)$"``).
            Optional. Raises ``DriverError`` for invalid regex.
        startswith: List of prefix strings. Optional.
            Columns starting with any prefix are included.
        endswith: List of suffix strings. Optional.
            Columns ending with any suffix are included.

    Example:
        {"Transform": [{"PluckCols": {"columns": ["name", "email"]}}]}
        {"Transform": [{"PluckCols": {"pattern": "revenue_*"}}]}
        {"Transform": [{"PluckCols": {"startswith": ["rev"], "endswith": ["_id"]}}]}
    """

    def __init__(self, data: Union[dict, pd.DataFrame], **kwargs) -> None:
        # Pop all five selectors BEFORE super().__init__ so introspection works
        self.columns = kwargs.pop('columns', None)
        self.pattern = kwargs.pop('pattern', None)
        self.regex = kwargs.pop('regex', None)
        self.startswith = kwargs.pop('startswith', None)
        self.endswith = kwargs.pop('endswith', None)
        super(PluckCols, self).__init__(data, **kwargs)
        # Tracks whether start() has been called; prevents a redundant second
        # call when using ``async with obj as o: await o.run()``.
        self._started: bool = False
        # Validate that at least one selector is provided
        if not any([self.columns, self.pattern, self.regex, self.startswith, self.endswith]):
            raise DriverError(
                "PluckCols: At least one column selector is required "
                "(columns, pattern, regex, startswith, or endswith)."
            )

    async def start(self) -> None:
        """Delegate to parent start() and mark this instance as started."""
        await super().start()
        self._started = True

    def _resolve_columns(self, df: pd.DataFrame) -> list[str]:
        """Return the ordered union of columns matched by all provided selectors.

        Matched columns are returned in the original DataFrame column order.
        Raises ``DriverError`` if an exact column name in ``self.columns`` is
        not present in the DataFrame.
        """
        matched: set = set()

        # Exact column names
        if self.columns:
            missing = [c for c in self.columns if c not in df.columns]
            if missing:
                raise DriverError(
                    f"PluckCols: Column(s) not found in DataFrame: {missing}"
                )
            matched.update(self.columns)

        # Glob/fnmatch pattern
        if self.pattern:
            matched.update(
                col for col in df.columns
                if fnmatch.fnmatch(col, self.pattern)
            )

        # Regular expression
        if self.regex:
            try:
                compiled = re.compile(self.regex)
            except re.error as err:
                raise DriverError(
                    f"PluckCols: Invalid regex pattern '{self.regex}': {err}"
                ) from err
            matched.update(col for col in df.columns if compiled.search(col))

        # Startswith prefixes
        if self.startswith:
            prefixes = tuple(self.startswith)
            matched.update(col for col in df.columns if col.startswith(prefixes))

        # Endswith suffixes
        if self.endswith:
            suffixes = tuple(self.endswith)
            matched.update(col for col in df.columns if col.endswith(suffixes))

        # Preserve original column order
        return [col for col in df.columns if col in matched]

    def _apply_pluck(self, df: pd.DataFrame) -> pd.DataFrame:
        """Apply column selection to a single DataFrame."""
        keep = self._resolve_columns(df)
        if not keep:
            raise DataNotFound(
                "PluckCols: No columns matched the provided selectors — "
                "result would be an empty DataFrame."
            )
        return df[keep]

    async def run(self) -> Union[dict, pd.DataFrame]:
        """Execute the PluckCols transformation."""
        # Only call start() if __aenter__ hasn't already done so.
        if not self._started:
            await self.start()
        # AbstractTransform.start() validates empty DFs for dict inputs only;
        # check single-DataFrame emptiness here.
        if isinstance(self.data, pd.DataFrame) and self.data.empty:
            raise DataNotFound("PluckCols: Empty DataFrame input.")
        try:
            if isinstance(self.data, dict):
                return {
                    name: self._apply_pluck(df)
                    for name, df in self.data.items()
                }
            return self._apply_pluck(self.data)
        except (DataNotFound, DriverError):
            raise
        except Exception as err:
            raise DriverError(
                f"PluckCols: Unexpected error during column selection: {err}"
            ) from err
