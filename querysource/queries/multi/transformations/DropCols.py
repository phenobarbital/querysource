import fnmatch
import re
from typing import Union

import pandas as pd

from ....exceptions import DataNotFound, DriverError
from .abstract import AbstractTransform


class DropCols(AbstractTransform):
    """Drop the columns matching any of the specified selectors.

    Applies a blacklist-style column filter to a DataFrame, removing all
    columns that match at least one of the provided selectors. Supports
    five matching modes which are all optional but at least one is required;
    matches from all modes are unioned together.

    Exact column names that are not found in the DataFrame are silently
    ignored. Pattern/regex/prefix/suffix modes also silently skip non-matches.

    Usage: Use in a MultiQuery ``Transform`` step to remove unwanted columns
    from a DataFrame via exact names, glob patterns, regex, or
    prefix/suffix matching.

    Attributes:
        columns: List of exact column names to drop. Optional.
            Missing columns are silently ignored.
        pattern: Glob/fnmatch pattern string (e.g. ``"debug_*"``). Optional.
            Non-matching patterns are silently skipped.
        regex: Regular expression pattern string (e.g. ``"^tmp_"``). Optional.
            Raises ``DriverError`` for invalid regex.
        startswith: List of prefix strings. Optional.
            Columns starting with any prefix are dropped.
        endswith: List of suffix strings. Optional.
            Columns ending with any suffix are dropped.

    Example:
        {"Transform": [{"DropCols": {"columns": ["internal_id", "debug_flag"]}}]}
        {"Transform": [{"DropCols": {"startswith": ["debug_"], "endswith": ["_tmp"]}}]}
        {"Transform": [{"DropCols": {"regex": "^tmp_.*$"}}]}
    """

    def __init__(self, data: Union[dict, pd.DataFrame], **kwargs) -> None:
        # Pop all five selectors BEFORE super().__init__ so introspection works
        self.columns = kwargs.pop('columns', None)
        self.pattern = kwargs.pop('pattern', None)
        self.regex = kwargs.pop('regex', None)
        self.startswith = kwargs.pop('startswith', None)
        self.endswith = kwargs.pop('endswith', None)
        super(DropCols, self).__init__(data, **kwargs)
        # Tracks whether start() has been called; prevents a redundant second
        # call when using ``async with obj as o: await o.run()``.
        self._started: bool = False
        # Validate that at least one selector is provided
        if not any([self.columns, self.pattern, self.regex, self.startswith, self.endswith]):
            raise DriverError(
                "DropCols: At least one column selector is required "
                "(columns, pattern, regex, startswith, or endswith)."
            )

    async def start(self) -> None:
        """Delegate to parent start() and mark this instance as started."""
        await super().start()
        self._started = True

    def _resolve_columns(self, df: pd.DataFrame) -> list[str]:
        """Return the union of columns to drop, matched by all provided selectors.

        Exact column names not present in the DataFrame are silently ignored.
        Raises ``DriverError`` for invalid regex patterns.
        """
        matched: set = set()

        # Exact column names — silently ignore missing ones
        if self.columns:
            matched.update(c for c in self.columns if c in df.columns)

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
                    f"DropCols: Invalid regex pattern '{self.regex}': {err}"
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

        return list(matched)

    def _apply_drop(self, df: pd.DataFrame) -> pd.DataFrame:
        """Apply column dropping to a single DataFrame."""
        to_drop = self._resolve_columns(df)
        result = df.drop(columns=to_drop, errors='ignore')
        if len(result.columns) == 0:
            raise DataNotFound(
                "DropCols: All columns were dropped — result has no columns."
            )
        return result

    async def run(self) -> Union[dict, pd.DataFrame]:
        """Execute the DropCols transformation."""
        # Only call start() if __aenter__ hasn't already done so.
        if not self._started:
            await self.start()
        # AbstractTransform.start() validates empty DFs for dict inputs only;
        # check single-DataFrame emptiness here.
        if isinstance(self.data, pd.DataFrame) and self.data.empty:
            raise DataNotFound("DropCols: Empty DataFrame input.")
        try:
            if isinstance(self.data, dict):
                return {
                    name: self._apply_drop(df)
                    for name, df in self.data.items()
                }
            return self._apply_drop(self.data)
        except (DataNotFound, DriverError):
            raise
        except Exception as err:
            raise DriverError(
                f"DropCols: Unexpected error during column drop: {err}"
            ) from err
