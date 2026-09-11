"""
DataFrame helpers.

Shared normalisation used by the ``iter`` output format and by the row-oriented
writers, so a pandas-backed provider (``bigquery``, ``deltatbl``, ``iceberg``)
and a plain ``list[dict]`` provider hand the same shape downstream.
"""
from typing import Any


def is_dataframe(data: Any) -> bool:
    """Duck-typed check for a pandas-like DataFrame.

    Avoids importing pandas just to test the type, matching the check already
    used by :meth:`querysource.outputs.writers.abstract.AbstractWriter.get_buffer`.

    Args:
        data: Object to inspect.

    Returns:
        bool: True when *data* exposes both ``to_dict`` and ``columns``.
    """
    return hasattr(data, 'to_dict') and hasattr(data, 'columns')


def df_to_records(df: Any) -> list[dict]:
    """Convert a DataFrame into a list of row dictionaries.

    Iterating a DataFrame yields its *column names*, not its rows, so any
    consumer expecting an iterable of mappings (``csv.DictWriter``,
    ``aiocsv.AsyncDictWriter``, Jinja2 report templates) silently receives
    garbage. Missing values (``NaN``/``NaT``/``pandas.NA``) become ``None`` so
    they serialise as empty/null instead of the string ``"nan"``.

    Args:
        df: A pandas DataFrame (or DataFrame-like object).

    Returns:
        list[dict]: One dictionary per row, keyed by column name.
    """
    return df.astype(object).where(df.notna(), None).to_dict(orient='records')
