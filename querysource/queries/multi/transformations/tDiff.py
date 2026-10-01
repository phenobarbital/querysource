"""tDiff — Detect changes between two DataFrames that share a primary key.

Rows are classified as new, modified, deleted, or unchanged (label column
``_change``) so downstream steps can filter on them. The first source in the
data dict is the current DataFrame (A), the second is the previous one (B).

Auto-discovered by ``get_transform_module("tDiff")`` and
``ComponentRegistry.discover_all()`` because the module stem equals the
class name (``tDiff.py`` → ``tDiff``).

Usage example:

    {
        "Transform": [
            {
                "tDiff": {
                    "pk": "user_id",
                    "compare_columns": ["nombre", "email"],
                    "include": ["new", "modified", "deleted"]
                }
            }
        ]
    }
"""
import warnings
from typing import Any, Optional, Union

import numpy as np
import pandas as pd
from pandas import DataFrame

from ....exceptions import (
    DataNotFound,
    DriverError,
    QueryException,
)
from .abstract import AbstractTransform

CHANGE_TYPES: tuple[str, ...] = ("new", "modified", "deleted", "unchanged")
METHODS: tuple[str, ...] = ("compare", "hash")
DTYPE_POLICIES: tuple[str, ...] = ("strict", "coerce")


def _is_null(value: Any) -> bool:
    """Return True when a scalar cell is null (None, NaN, pd.NA, NaT)."""
    return bool(pd.api.types.is_scalar(value) and pd.isna(value))


def _safe_equal(left: Any, right: Any) -> bool:
    """Null-safe, exception-safe equality between two cells."""
    left_null, right_null = _is_null(left), _is_null(right)
    if left_null or right_null:
        return left_null and right_null
    try:
        return bool(left == right)
    except (ValueError, TypeError):
        try:
            return bool(np.array_equal(left, right))
        except Exception:
            return False


def _family(dtype: Any) -> str:
    """Classify a dtype into a strict-compatibility family."""
    if pd.api.types.is_bool_dtype(dtype):
        return "bool"
    if pd.api.types.is_integer_dtype(dtype):
        return "int"
    if pd.api.types.is_float_dtype(dtype):
        return "float"
    if pd.api.types.is_datetime64_any_dtype(dtype):
        return "datetime"
    if pd.api.types.is_object_dtype(dtype) or isinstance(dtype, pd.StringDtype):
        return "text"
    return str(dtype)


class tDiff(AbstractTransform):
    """Detect changes between two DataFrames that share a primary key.

    Rows are classified as new, modified, deleted, or unchanged (label column
    ``_change``).  The input ``data`` dict must contain exactly two DataFrames:
    the first is the *current* snapshot (A), the second is the *previous* (B).
    You can override the order with the ``current`` and ``previous`` kwargs
    (dict keys).

    Usage: Use in a MultiQuery ``Transform`` step to diff two query results.

    Attributes:
        pk: Primary key column name or list of column names (composite key).
            **Required.**
        compare_columns: Columns whose change marks a row as modified.
            Default: all common columns minus pk and ``ignore_columns``.
        ignore_columns: Columns that are never compared.
        include: Change types emitted — any of ``new``, ``modified``,
            ``deleted``, ``unchanged``.  Default: ``["new", "modified",
            "deleted"]``.
        change_column: Name of the label column added to the output.
            Default: ``_change``.
        changed_columns: When ``True``, adds a ``_changed_columns`` list column
            on modified rows; a string names that column.
        method: ``compare`` (vectorised per column, default) or ``hash``
            (per-row hash).
        float_tolerance: Dict with ``rtol``/``atol`` used with
            ``numpy.isclose`` on float columns.  Incompatible with
            ``method='hash'``.
        strip_strings: Strip whitespace before comparing.  Default: ``False``.
        case_sensitive: When ``False``, strings are compared with
            ``casefold()``.  Default: ``True``.
        dtype_policy: ``strict`` fails on incompatible dtypes; ``coerce``
            casts B to A's dtype.  Default: ``strict``.
        raise_on_empty: Raise ``DataNotFound`` when the result is empty.
            Default: ``False``.
        current: Dict key identifying the current (A) DataFrame.
        previous: Dict key identifying the previous (B) DataFrame.

    Example:
        {
            "Transform": [
                {
                    "tDiff": {
                        "pk": "user_id",
                        "compare_columns": ["nombre", "email"],
                        "include": ["new", "modified", "deleted"]
                    }
                }
            ]
        }
    """

    def __init__(self, data: Union[dict, DataFrame], **kwargs) -> None:
        """Initialise tDiff and pop transform-specific kwargs."""
        self.pk: Union[str, list[str], None] = kwargs.pop("pk", None)
        self.compare_columns: Optional[list[str]] = kwargs.pop("compare_columns", None)
        self.ignore_columns: list[str] = kwargs.pop("ignore_columns", [])
        self.include: list[str] = kwargs.pop("include", ["new", "modified", "deleted"])
        self.change_column: str = kwargs.pop("change_column", "_change")
        self.changed_columns: Union[bool, str] = kwargs.pop("changed_columns", False)
        self.method: str = kwargs.pop("method", "compare")
        self.float_tolerance: Optional[dict[str, float]] = kwargs.pop("float_tolerance", None)
        self.strip_strings: bool = kwargs.pop("strip_strings", False)
        self.case_sensitive: bool = kwargs.pop("case_sensitive", True)
        self.dtype_policy: str = kwargs.pop("dtype_policy", "strict")
        self.raise_on_empty: bool = kwargs.pop("raise_on_empty", False)
        self._current_key: Optional[str] = kwargs.pop("current", None)
        self._previous_key: Optional[str] = kwargs.pop("previous", None)

        self._pk: list[str] = []
        self._schema_drift: dict[str, list[str]] = {
            "only_in_current": [],
            "only_in_previous": [],
        }

        if not self.pk:
            raise DriverError(
                "tDiff: 'pk' is a required parameter — specify the primary key column(s)."
            )

        super().__init__(data, **kwargs)

    # ------------------------------------------------------------------
    # Public lifecycle
    # ------------------------------------------------------------------

    async def run(self) -> DataFrame:
        """Validate inputs, run the diff, and return the labelled DataFrame."""
        await self.start()
        self._validate_config()
        df1, df2 = self._extract_frames()

        try:
            result = self._diff(df1, df2)
        except (DataNotFound, DriverError):
            raise
        except (ValueError, KeyError) as err:
            raise DriverError(f"tDiff Error: {err!s}") from err
        except Exception as err:
            raise QueryException(f"tDiff Exception: {err!s}") from err

        if result.empty and self.raise_on_empty:
            raise DataNotFound(
                "tDiff: no changes found for the selected include types."
            )
        return result

    # ------------------------------------------------------------------
    # Config validation
    # ------------------------------------------------------------------

    def _validate_config(self) -> None:
        """Validate configuration parameters."""
        if self.method not in METHODS:
            raise DriverError(
                f"tDiff: invalid method '{self.method}', valid values: {list(METHODS)}"
            )
        if self.dtype_policy not in DTYPE_POLICIES:
            raise DriverError(
                f"tDiff: invalid dtype_policy '{self.dtype_policy}', "
                f"valid values: {list(DTYPE_POLICIES)}"
            )
        if isinstance(self.include, str):
            self.include = [self.include]
        invalid = [x for x in self.include if x not in CHANGE_TYPES]
        if invalid:
            raise DriverError(
                f"tDiff: invalid include value(s) {invalid}, "
                f"valid values: {list(CHANGE_TYPES)}"
            )
        if self.method == "hash" and self.float_tolerance:
            raise DriverError(
                "tDiff: method 'hash' is incompatible with float_tolerance"
            )
        self._pk = [self.pk] if isinstance(self.pk, str) else list(self.pk)
        if isinstance(self.compare_columns, str):
            self.compare_columns = [self.compare_columns]
        if isinstance(self.ignore_columns, str):
            self.ignore_columns = [self.ignore_columns]

    def _extract_frames(self) -> tuple[DataFrame, DataFrame]:
        """Extract exactly two DataFrames from ``self.data``.

        Returns:
            Tuple of (current, previous) DataFrames.

        Raises:
            DriverError: If the data is not a dict with exactly 2 DataFrames.
        """
        if not isinstance(self.data, dict):
            raise DriverError(
                "tDiff requires a dict of exactly 2 DataFrames "
                "(current and previous); got a single DataFrame."
            )
        if len(self.data) != 2:
            raise DriverError(
                f"tDiff requires exactly 2 DataFrames in data dict "
                f"(current and previous); got {len(self.data)}."
            )

        if self._current_key and self._previous_key:
            for key in (self._current_key, self._previous_key):
                if key not in self.data:
                    raise DriverError(
                        f"tDiff: key '{key}' not found in data dict. "
                        f"Available keys: {list(self.data.keys())}"
                    )
            return self.data[self._current_key], self.data[self._previous_key]

        keys = list(self.data.keys())
        return self.data[keys[0]], self.data[keys[1]]

    # ------------------------------------------------------------------
    # PK validation
    # ------------------------------------------------------------------

    def _validate_pk(self, df: DataFrame, side: str) -> None:
        """Raise on missing pk columns, null keys, or duplicate keys."""
        for col in self._pk:
            if col not in df.columns:
                raise DriverError(
                    f"tDiff: pk column '{col}' is missing in {side} DataFrame"
                )
        nulls = int(df[self._pk].isna().any(axis=1).sum())
        if nulls:
            raise DriverError(
                f"tDiff: pk has nulls in {side} DataFrame ({nulls} rows)"
            )
        dup = df.duplicated(subset=self._pk, keep=False)
        if dup.any():
            keys = df.loc[dup, self._pk].drop_duplicates().head(5)
            if len(self._pk) == 1:
                examples = keys[self._pk[0]].tolist()
            else:
                examples = [
                    tuple(row) for row in keys.itertuples(index=False, name=None)
                ]
            raise DriverError(
                f"tDiff: pk is duplicated in {side} DataFrame "
                f"({int(dup.sum())} rows), examples: {examples}"
            )

    # ------------------------------------------------------------------
    # Schema resolution
    # ------------------------------------------------------------------

    def _resolve_compare_columns(
        self, a: DataFrame, b: DataFrame
    ) -> list[str]:
        """Return the compared columns and record schema drift."""
        ignore = set(self.ignore_columns or [])
        only_a = [c for c in a.columns if c not in b.columns]
        only_b = [c for c in b.columns if c not in a.columns]
        self._schema_drift = {
            "only_in_current": only_a,
            "only_in_previous": only_b,
        }
        if only_a or only_b:
            self.logger.warning(
                "tDiff schema drift: only in current=%s, "
                "only in previous=%s (not compared)",
                only_a, only_b,
            )
        if self.compare_columns:
            for col in self.compare_columns:
                if col not in a.columns:
                    raise DriverError(
                        f"tDiff: compare column '{col}' is missing "
                        f"in current DataFrame"
                    )
                if col not in b.columns:
                    raise DriverError(
                        f"tDiff: compare column '{col}' is missing "
                        f"in previous DataFrame"
                    )
            return [
                c for c in self.compare_columns
                if c not in self._pk and c not in ignore
            ]
        return [
            c for c in a.columns
            if c in b.columns and c not in self._pk and c not in ignore
        ]

    # ------------------------------------------------------------------
    # Dtype alignment
    # ------------------------------------------------------------------

    def _align_dtypes(self, ca: DataFrame, cb: DataFrame) -> DataFrame:
        """Return a copy of cb aligned to ca per dtype_policy."""
        out = cb.copy()
        for col in ca.columns:
            if col not in cb.columns:
                continue
            da, db = ca[col].dtype, cb[col].dtype
            fa, fb = _family(da), _family(db)
            if fa == "datetime" and fb == "datetime":
                tz_a = getattr(da, "tz", None)
                tz_b = getattr(db, "tz", None)
                if (tz_a is None) != (tz_b is None):
                    raise DriverError(
                        f"tDiff: column '{col}' mixes tz-aware and naive "
                        f"datetimes ({da} vs {db})"
                    )
                if str(tz_a) != str(tz_b):
                    raise DriverError(
                        f"tDiff: column '{col}' has different timezones "
                        f"({da} vs {db})"
                    )
            if fa != fb:
                if self.dtype_policy == "strict":
                    raise DriverError(
                        f"tDiff: incompatible dtypes in column "
                        f"'{col}': {da} vs {db}"
                    )
                if (
                    fa == "datetime"
                    and fb in ("datetime", "text")
                    and getattr(da, "tz", None) is not None
                ):
                    raise DriverError(
                        f"tDiff: column '{col}' cannot be coerced "
                        f"to tz-aware {da}"
                    )
                try:
                    out[col] = self._coerce(cb[col], da, fa)
                except (ValueError, TypeError, OverflowError) as err:
                    raise DriverError(
                        f"tDiff: cannot coerce column '{col}' "
                        f"({db} -> {da}): {err!s}"
                    ) from err
            elif da != db:
                try:
                    out[col] = cb[col].astype(da)
                except (ValueError, TypeError):
                    pass
        return out

    @staticmethod
    def _coerce(series: pd.Series, dtype: Any, family: str) -> pd.Series:
        """Cast a series to the dtype of A."""
        if family in ("int", "float"):
            return pd.to_numeric(series, errors="raise").astype(dtype)
        if family == "datetime":
            return pd.to_datetime(series, errors="raise").astype(dtype)
        return series.astype(dtype)

    # ------------------------------------------------------------------
    # String normalization
    # ------------------------------------------------------------------

    def _normalize(self, df: DataFrame) -> DataFrame:
        """Return a copy with strip/casefold applied to string-like columns."""
        out = df.copy()
        if not self.strip_strings and self.case_sensitive:
            return out

        def _fix(value: Any) -> Any:
            if isinstance(value, str):
                if self.strip_strings:
                    value = value.strip()
                if not self.case_sensitive:
                    value = value.casefold()
            return value

        for col in out.columns:
            if _family(out[col].dtype) == "text":
                out[col] = out[col].astype(object).map(_fix)
        return out

    # ------------------------------------------------------------------
    # Core diff logic
    # ------------------------------------------------------------------

    def _diff(self, a: DataFrame, b: DataFrame) -> DataFrame:
        """Classify rows into new/modified/deleted/unchanged and assemble output."""
        pk = self._pk
        self._validate_pk(a, "current")
        self._validate_pk(b, "previous")
        cols = self._resolve_compare_columns(a, b)

        b_pk = self._align_dtypes(a[pk], b[pk])
        if len(pk) == 1:
            a_keys = pd.Index(a[pk[0]])
            b_keys = pd.Index(b_pk[pk[0]])
        else:
            a_keys = pd.MultiIndex.from_frame(a[pk])
            b_keys = pd.MultiIndex.from_frame(b_pk)

        new_mask = ~np.asarray(a_keys.isin(b_keys))
        deleted_pos = np.flatnonzero(~np.asarray(b_keys.isin(a_keys)))
        b_pos = np.asarray(b_keys.get_indexer(a_keys))
        common_a = np.flatnonzero(b_pos >= 0)
        common_b = b_pos[common_a]

        ca = a[cols].iloc[common_a].reset_index(drop=True)
        cb_raw = b[cols].iloc[common_b].reset_index(drop=True)
        cb = self._align_dtypes(ca, cb_raw)
        ca, cb = self._normalize(ca), self._normalize(cb)

        eq: Optional[DataFrame] = None
        if not cols or len(common_a) == 0:
            modified = np.zeros(len(common_a), dtype=bool)
        elif self.method == "hash":
            modified = np.asarray(self._hash_modified(ca, cb), dtype=bool)
        else:
            eq = self._equal_matrix(ca, cb)
            modified = ~eq.all(axis=1).to_numpy(dtype=bool)

        labels = np.full(len(a), "new", dtype=object)
        labels[common_a] = np.where(modified, "modified", "unchanged")

        # changed columns tracking
        cc_name: Optional[str] = None
        if self.changed_columns:
            cc_name = (
                self.changed_columns
                if isinstance(self.changed_columns, str)
                else "_changed_columns"
            )
        changed_lists: Optional[list[list[str]]] = None
        if cc_name is not None:
            changed_lists = [[] for _ in range(len(a))]
            if modified.any():
                if eq is None:
                    eq_mod = self._equal_matrix(
                        ca[modified].reset_index(drop=True),
                        cb[modified].reset_index(drop=True),
                    )
                else:
                    eq_mod = eq[modified]
                flags = ~eq_mod.to_numpy(dtype=bool)
                for pos, row in zip(common_a[modified], flags, strict=False):
                    changed_lists[pos] = [
                        c for c, diff in zip(cols, row, strict=False) if diff
                    ]

        n_new = int(new_mask.sum())
        n_mod = int(modified.sum())
        n_del = int(len(deleted_pos))
        n_unch = int(len(common_a) - n_mod)

        self.logger.debug(
            "tDiff: new=%d, modified=%d, deleted=%d, unchanged=%d",
            n_new, n_mod, n_del, n_unch,
        )
        if self._schema_drift["only_in_current"] or self._schema_drift["only_in_previous"]:
            self.logger.debug("tDiff: schema_drift=%s", self._schema_drift)

        # assemble output
        keep = np.flatnonzero(
            np.isin(labels, [x for x in self.include if x != "deleted"])
        )
        parts: list[DataFrame] = []
        if len(keep):
            part = a.iloc[keep].copy()
            part[self.change_column] = labels[keep]
            if cc_name is not None:
                part[cc_name] = pd.Series(
                    [changed_lists[i] for i in keep],
                    index=part.index,
                    dtype=object,
                )
            parts.append(part)
        if "deleted" in self.include and n_del:
            dele = b.iloc[deleted_pos].reindex(columns=a.columns).copy()
            dele[self.change_column] = "deleted"
            if cc_name is not None:
                dele[cc_name] = pd.Series(
                    [[] for _ in range(n_del)],
                    index=dele.index,
                    dtype=object,
                )
            parts.append(dele)
        if not parts:
            out = self._empty_output(a)
        elif len(parts) == 1:
            out = parts[0].reset_index(drop=True)
        else:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", FutureWarning)
                out = pd.concat(parts, ignore_index=True)
        return out

    # ------------------------------------------------------------------
    # Comparison helpers
    # ------------------------------------------------------------------

    def _equal_matrix(self, ca: DataFrame, cb: DataFrame) -> DataFrame:
        """Boolean frame — True where values are equal (null-safe)."""
        tol = self.float_tolerance or None
        result: dict[str, np.ndarray] = {}
        for col in ca.columns:
            sa, sb = ca[col], cb[col]
            nulls = sa.isna().to_numpy() & sb.isna().to_numpy()
            if (
                tol
                and pd.api.types.is_float_dtype(sa.dtype)
                and pd.api.types.is_float_dtype(sb.dtype)
            ):
                va = sa.to_numpy(dtype=float, na_value=np.nan)
                vb = sb.to_numpy(dtype=float, na_value=np.nan)
                same = np.isclose(
                    va, vb,
                    rtol=tol.get("rtol", 1e-5),
                    atol=tol.get("atol", 1e-8),
                )
            elif pd.api.types.is_object_dtype(sa.dtype) or pd.api.types.is_object_dtype(sb.dtype):
                try:
                    same = (sa == sb).to_numpy(dtype=bool, na_value=False)
                except (ValueError, TypeError):
                    same = np.fromiter(
                        (_safe_equal(x, y) for x, y in zip(sa, sb, strict=False)),
                        dtype=bool,
                        count=len(sa),
                    )
            else:
                same = (sa == sb).to_numpy(dtype=bool, na_value=False)
            result[col] = same | nulls
        return pd.DataFrame(result, index=ca.index, columns=list(ca.columns))

    def _hash_modified(self, ca: DataFrame, cb: DataFrame) -> pd.Series:
        """Per-row hash comparison; True where the row changed."""
        try:
            ha = pd.util.hash_pandas_object(ca, index=False)
            hb = pd.util.hash_pandas_object(cb, index=False)
        except TypeError as err:
            bad = None
            for col in ca.columns:
                try:
                    pd.util.hash_pandas_object(ca[col], index=False)
                    pd.util.hash_pandas_object(cb[col], index=False)
                except TypeError:
                    bad = col
                    break
            where = f" in column '{bad}'" if bad else ""
            raise DriverError(
                f"tDiff hash method cannot hash values{where}: {err!s}. "
                f"Use method: compare for nested cells"
            ) from err
        return pd.Series(ha.to_numpy() != hb.to_numpy(), index=ca.index)

    def _empty_output(self, a: DataFrame) -> DataFrame:
        """Zero-row frame with A's columns plus the label column."""
        out = a.iloc[0:0].copy()
        out[self.change_column] = pd.Series([], dtype=object)
        if self.changed_columns:
            name = (
                self.changed_columns
                if isinstance(self.changed_columns, str)
                else "_changed_columns"
            )
            out[name] = pd.Series([], dtype=object)
        return out.reset_index(drop=True)
