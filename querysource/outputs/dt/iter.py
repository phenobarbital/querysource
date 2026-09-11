"""
Iterable.

Output format returning a simple list of dictionaries.
"""
import pandas
try:
    from google.cloud.bigquery.table import RowIterator
except ImportError:
    RowIterator = None
from ...utils.dataframes import df_to_records
from .abstract import OutputFormat


class iterFormat(OutputFormat):
    """
    Most Basic Definition of Format.
    """
    async def serialize(self, result, error, *args, **kwargs):
        if isinstance(result, pandas.DataFrame):
            # pandas-backed providers (bigquery, deltatbl, iceberg) return a
            # DataFrame. Iterating it yields column names, not rows, so every
            # row-oriented consumer (csv, tsv, txt, report/pdf) has to receive
            # records here — this format's contract is a list of dictionaries.
            data = df_to_records(result)
        elif isinstance(result, (RowIterator, list)):
            data = [dict(row) for row in result]
        else:
            data = dict(result)
        return (data, error)
