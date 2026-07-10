import re
import unicodedata
from typing import Union

import pandas as pd

from ....exceptions import DataNotFound, DriverError
from .abstract import AbstractTransform


class NormalizeColumns(AbstractTransform):
    """Normalize every column name into a database-safe identifier.

    Human-readable headers coming from Excel/CSV/SharePoint forms (spaces,
    slashes, accents, punctuation, mixed case, names longer than 63 bytes)
    break downstream destinations: PostgreSQL silently truncates identifiers
    to 63 bytes, so a column created as ``"... discussed/trained on:"`` (77
    chars) no longer matches the name the INSERT references, raising
    ``Unconsumed column names``. This transform rewrites every column to a
    safe form so ``TableOutput`` can create and load the table cleanly.

    Each name is: lower/upper-cased (configurable), stripped of accents,
    has every non-alphanumeric run collapsed to ``replacement``, gets a
    leading ``col_`` if it would otherwise start with a digit, and is
    truncated to ``max_length`` bytes. Collisions (two headers that normalize
    to the same identifier) are resolved with a numeric suffix.

    Usage: Drop it as a ``Transform`` step before a table/database
    destination. With no parameters it applies the safe defaults below; use
    ``overrides`` to pin specific columns to an exact name.

    Attributes:
        max_length: Maximum identifier length. Default ``63`` (PostgreSQL limit).
        case: ``"lower"`` (default), ``"upper"`` or ``"preserve"``.
        strip_accents: Remove diacritics (``"Región"`` → ``"region"``).
            Default ``True``.
        replacement: String that replaces runs of non-alphanumeric chars.
            Default ``"_"``.
        overrides: Optional dict ``{original_name: target_name}`` mapping
            specific source columns to an exact name, bypassing normalization
            for those columns. Default ``None``.

    Example:
        {"Transform": [{"NormalizeColumns": {}}]}
        {"Transform": [{"NormalizeColumns": {"max_length": 63, "case": "lower"}}]}
        {"Transform": [{"NormalizeColumns": {"overrides": {"ID": "event_id"}}}]}
    """

    def __init__(self, data: Union[dict, pd.DataFrame], **kwargs) -> None:
        # Pop attributes BEFORE super().__init__ so introspection works.
        self.max_length: int = kwargs.pop('max_length', 63)
        self.case: str = kwargs.pop('case', 'lower')
        self.strip_accents: bool = kwargs.pop('strip_accents', True)
        self.replacement: str = kwargs.pop('replacement', '_')
        self.overrides: dict = kwargs.pop('overrides', None) or {}
        super(NormalizeColumns, self).__init__(data, **kwargs)
        self._started: bool = False
        if self.case not in ('lower', 'upper', 'preserve'):
            raise DriverError(
                "NormalizeColumns: 'case' must be 'lower', 'upper' or 'preserve'."
            )
        if not isinstance(self.max_length, int) or self.max_length < 1:
            raise DriverError(
                "NormalizeColumns: 'max_length' must be a positive integer."
            )

    async def start(self) -> None:
        """Delegate to parent start() and mark this instance as started."""
        await super().start()
        self._started = True

    def _normalize_name(self, name: str, used: set) -> str:
        """Convert a single column name into a unique, safe identifier."""
        text = str(name)
        if self.strip_accents:
            text = unicodedata.normalize('NFKD', text)
            text = text.encode('ascii', 'ignore').decode('ascii')
        text = text.strip()
        if self.case == 'lower':
            text = text.lower()
        elif self.case == 'upper':
            text = text.upper()
        # Collapse every run of non-alphanumeric chars into the replacement.
        esc = re.escape(self.replacement)
        text = re.sub(r'[^A-Za-z0-9]+', self.replacement, text)
        if self.replacement:
            text = re.sub(f'{esc}+', self.replacement, text).strip(self.replacement)
        if not text:
            text = 'column'
        # Identifiers cannot start with a digit.
        if text[0].isdigit():
            text = f'col_{text}'
        text = text[:self.max_length]

        # Resolve collisions by appending a numeric suffix.
        candidate = text
        i = 1
        while candidate in used:
            suffix = f'_{i}'
            candidate = text[:self.max_length - len(suffix)] + suffix
            i += 1
        used.add(candidate)
        return candidate

    def _apply(self, df: pd.DataFrame) -> pd.DataFrame:
        """Return a copy of *df* with normalized column names."""
        used: set = set()
        mapping: dict = {}
        for col in df.columns:
            if col in self.overrides:
                target = self.overrides[col]
                used.add(target)
                mapping[col] = target
            else:
                mapping[col] = self._normalize_name(col, used)
        result = df.rename(columns=mapping)
        if len(result.columns) == 0:
            raise DataNotFound(
                "NormalizeColumns: result has no columns."
            )
        return result

    async def run(self) -> Union[dict, pd.DataFrame]:
        """Execute the NormalizeColumns transformation."""
        if not self._started:
            await self.start()
        if isinstance(self.data, pd.DataFrame) and self.data.empty:
            raise DataNotFound("NormalizeColumns: Empty DataFrame input.")
        try:
            if isinstance(self.data, dict):
                return {
                    name: self._apply(df)
                    for name, df in self.data.items()
                }
            return self._apply(self.data)
        except (DataNotFound, DriverError):
            raise
        except Exception as err:
            raise DriverError(
                f"NormalizeColumns: Unexpected error during normalization: {err}"
            ) from err
